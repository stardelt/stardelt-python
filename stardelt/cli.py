"""``stardelt``: a thin command-line client for Nova's public API (shells, CI, agents).

    export STARDELT_NOVA_URL=https://nova.example.com
    stardelt login                                                   # once; asks Nova which identity provider; or: export STARDELT_TOKEN=... (CI)
    stardelt whoami
    stardelt sql "select 1 as x" --format json
    stardelt apply -f pipeline.json            # create or update a pipeline; --dry-run writes nothing
    stardelt apply -f pipelines/               # every .json, .yaml and .yml file of a directory, in name order
    stardelt export NAMESPACE NAME > p.yaml    # a pipeline made in the interface, as a file `apply` accepts

Results go to stdout; messages and errors go to stderr. The exit code is 0 for success,
1 for an error, 2 for wrong usage and 3 when authentication is needed. A token is never
printed.
"""

import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path

import requests

from stardelt import api, apply, bundle, login, mcp, runs, shortcuts, skill, statements

OK, ERROR, USAGE, AUTH = 0, 1, 2, 3


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(message)


def _parser():
    parser = _Parser(
        prog="stardelt",
        description="A thin client for Nova's public API.",
        epilog="resources (stardelt RESOURCE VERB [VALUES...], same options as `api`):\n"
        + shortcuts.summary(api.operations()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", help="Nova's base URL (default: $STARDELT_NOVA_URL)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("whoami", help="who Nova thinks you are (JSON)")
    signin = commands.add_parser("login", help="sign in once with a code approved in a browser (device flow)")
    signin.add_argument(
        "--issuer", help="your identity provider's realm address (default: $STARDELT_ISSUER, else asked from Nova's address)"
    )
    signin.add_argument("--client-id", help=f"the public client to sign in with (default: {login.DEFAULT_CLIENT_ID})")
    commands.add_parser("logout", help="forget the stored sign-in")
    sql = commands.add_parser("sql", help="run one statement as you")
    sql.add_argument("statement", nargs="?", help="the SQL text; or use -f")
    sql.add_argument("-f", "--file", help="read the SQL text from this file, or from standard input when it is -")
    sql.add_argument("--format", choices=["table", "json", "csv"], default="table")
    sql.add_argument(
        "--max-rows", type=int, default=0, help="print at most N rows (0: all Nova returned)"
    )
    sql.add_argument(
        "--long",
        action="store_true",
        help="run it as a statement: no request stays open, up to 100000 rows, Ctrl-C cancels it in the engine",
    )
    trigger = commands.add_parser("run", help="start a pipeline run now; --wait waits for its end")
    trigger.add_argument("namespace")
    trigger.add_argument("name")
    trigger.add_argument("--wait", action="store_true", help="wait until the run ends; exit 0 only when it succeeded")
    trigger.add_argument("--timeout", type=int, default=0, help="with --wait: give up after N seconds (0: no limit); the run is not stopped")
    trigger.add_argument("--interval", type=int, default=5, help="with --wait: seconds between two looks (default 5)")
    call = commands.add_parser("api", help="call any public operation by its operationId")
    call.add_argument("operation", nargs="?", help="an operationId (see --list)")
    call.add_argument("--list", action="store_true", dest="list_operations", help="list the operations")
    call.add_argument("-p", "--path", action="append", default=[], metavar="NAME=VALUE")
    call.add_argument("-q", "--query", action="append", default=[], metavar="NAME=VALUE")
    call.add_argument("--body", help="the request body, as JSON; @FILE reads it from a file, @- from standard input")
    call.add_argument(
        "--format", choices=["json", "table"], default="json", help="json (default) or a table when the answer is a list"
    )
    deploy = commands.add_parser("apply", help="create or update a pipeline from a JSON or YAML file")
    deploy.add_argument("-f", "--file", required=True, help="a pipeline as JSON or YAML, a directory of such files, or - for stdin")
    deploy.add_argument("--dry-run", action="store_true", help="say what would happen; write nothing")
    export = commands.add_parser("export", help="write a pipeline as a file that `apply` accepts (YAML by default)")
    export.add_argument("namespace")
    export.add_argument("name")
    export.add_argument("--format", choices=["yaml", "json"], default="yaml", help="yaml (default) or json")
    server = commands.add_parser("mcp", help="a local MCP server over the public API")
    server_commands = server.add_subparsers(dest="mcp_command", required=True)
    serve = server_commands.add_parser("serve", help="serve the read-only tools on stdio, for an MCP client")
    serve.add_argument(
        "--allow", action="append", default=[], metavar="OPERATION_ID", help="also offer this operation (e.g. query_run)"
    )
    serve.add_argument("--no-sql", action="store_true", help="leave out the guarded SQL tools sql_read and sql_explain")
    server_commands.add_parser("config", help="print how to register the server in an MCP client")
    package = commands.add_parser("bundle", help="work with a bundle: a directory in Git that describes what a team publishes")
    package_commands = package.add_subparsers(dest="bundle_command", required=True)
    scaffold = package_commands.add_parser("init", help="write a small bundle that passes validate")
    scaffold.add_argument("directory", nargs="?", default=".", help="where to write it (default: the current directory)")
    scaffold.add_argument("--name", required=True, help="the bundle's name (lower-case letters, digits, dashes)")
    scaffold.add_argument("--group", required=True, help="the group that owns the bundle's pipelines")
    check = package_commands.add_parser("validate", help="check a bundle offline (no host, no sign-in)")
    check.add_argument("directory", nargs="?", default=".", help="the bundle directory (default: the current one)")
    check.add_argument("-t", "--target", help="check this target only")
    advice = commands.add_parser("skill", help="print the agent skill (SKILL.md); with --write DIR install it")
    advice.add_argument("--write", metavar="DIR", help="write DIR/stardelt/SKILL.md, for example .claude/skills")
    return parser


def _pairs(items):
    pairs = {}
    for item in items:
        name, found, value = item.partition("=")
        if not found or not name:
            raise ValueError(f"{item!r} is not NAME=VALUE")
        pairs[name] = value
    return pairs


def _rows_of(answer):
    """The list of objects inside an answer: the answer itself, or its one list-of-objects field."""
    if isinstance(answer, dict):
        lists = [v for v in answer.values() if isinstance(v, list) and v and all(isinstance(i, dict) for i in v)]
        answer = lists[0] if len(lists) == 1 else None
    if isinstance(answer, list) and answer and all(isinstance(i, dict) for i in answer):
        return answer
    return None


def _cell(value):
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= 40 else text[:39] + "…"


def _answer_table(rows, max_columns=8):
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    columns = columns[:max_columns]
    return _table(columns, [[_cell(row.get(c)) for c in columns] for row in rows])


def _list_operations():
    ops = api.operations()
    width = max(len(i) for i in ops)
    return "".join(f"{i.ljust(width)}  {o['method']:<6} {o['path']}\n" for i, o in sorted(ops.items()))


def _table(columns, rows):
    cells = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(columns, widths)).rstrip()]
    lines += ["  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip() for r in cells]
    return "\n".join(lines) + "\n"


def _render(result, fmt, max_rows):
    columns = [c["name"] for c in result.columns]
    rows = result.rows[:max_rows] if max_rows > 0 else result.rows
    if fmt == "json":
        return json.dumps([dict(zip(columns, r)) for r in rows], default=str, ensure_ascii=False) + "\n"
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)
        return out.getvalue()
    return _table(columns, rows)


def _login(args, environ, stderr, session):
    http = session or requests.Session()
    host = (args.host or environ.get("STARDELT_NOVA_URL", "")).strip()
    issuer = (args.issuer or environ.get("STARDELT_ISSUER", "")).strip()
    client_id = args.client_id or environ.get("STARDELT_CLIENT_ID", "")
    if not issuer and not host:
        stderr.write(
            "stardelt: login needs --issuer (or STARDELT_ISSUER), or Nova's address (--host or STARDELT_NOVA_URL) "
            "to ask it for the identity provider\n"
        )
        return USAGE
    reaching = host
    try:
        if not issuer:
            issuer, advertised = login.nova_sign_in(http, host)
            client_id = client_id or advertised or ""
        reaching = issuer
        say = lambda line: stderr.write(f"stardelt: {line}\n")  # noqa: E731
        creds = login.device_login(http, issuer, client_id or login.DEFAULT_CLIENT_ID, say)
        if host:
            creds["host"] = host
        path = login.save(environ, creds)
    except login.LoginError as err:
        stderr.write(f"stardelt: {err}\n")
        return AUTH
    except requests.RequestException:
        stderr.write(f"stardelt: cannot reach {reaching}\n")
        return ERROR
    stderr.write(f"stardelt: signed in; the sign-in is kept in {path}\n")
    return OK


def main(argv=None, environ=None, stdout=None, stderr=None, session=None, stdin=None):
    environ = os.environ if environ is None else environ
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    stdin = sys.stdin if stdin is None else stdin
    try:
        args = _parser().parse_args(shortcuts.expand(sys.argv[1:] if argv is None else argv, api.operations()))
    except (_UsageError, ValueError) as err:
        stderr.write(f"stardelt: {err}\n")
        return USAGE
    except SystemExit as exit_:  # --help prints itself and exits 0
        return exit_.code if isinstance(exit_.code, int) else OK

    if args.command == "api" and args.list_operations:
        stdout.write(_list_operations())
        return OK
    if args.command == "api" and not args.operation:
        stderr.write("stardelt: api needs an operationId (or --list)\n")
        return USAGE

    if args.command == "mcp" and args.mcp_command == "config":
        stdout.write(mcp.config_text((args.host or environ.get("STARDELT_NOVA_URL", "")).strip()))
        return OK

    if args.command == "bundle" and args.bundle_command == "init":
        try:
            written = bundle.init(args.directory, args.name, args.group)
        except ValueError as err:
            stderr.write(f"stardelt: {err}\n")
            return USAGE
        stderr.write(f"stardelt: wrote {', '.join(written)} in {args.directory}; now: stardelt bundle validate {args.directory}\n")
        return OK

    if args.command == "bundle":
        problems, warnings, resolved = bundle.validate(args.directory, args.target)
        for warning in warnings:
            stderr.write(f"stardelt: warning: {warning}\n")
        for where, message in problems:
            stderr.write(f"stardelt: {where}: {message}\n")
        if problems:
            return USAGE
        for name, (mode, pipelines) in resolved.items():
            stdout.write(f"{name} ({mode}): {', '.join(pipelines)}\n")
        return OK

    if args.command == "skill":
        if args.write is None:
            stdout.write(skill.text())
            return OK
        try:
            stderr.write(f"stardelt: wrote {skill.write(args.write)}\n")
        except OSError as err:
            stderr.write(f"stardelt: {err}\n")
            return ERROR
        return OK

    if args.command == "login":
        return _login(args, environ, stderr, session)
    if args.command == "logout":
        stderr.write("stardelt: signed out\n" if login.forget(environ) else "stardelt: there was no stored sign-in\n")
        return OK

    body = None
    if args.command == "api" and args.body is not None:
        try:
            raw = args.body
            if args.body.startswith("@"):
                raw = stdin.read() if args.body == "@-" else Path(args.body[1:]).read_text(encoding="utf-8")
            body = json.loads(raw)
        except OSError as err:
            stderr.write(f"stardelt: {err}\n")
            return USAGE
        except ValueError as err:
            stderr.write(f"stardelt: the body is not JSON: {err}\n")
            return USAGE

    specs = None
    if args.command == "apply":
        try:
            specs = [apply.load(stdin.read())] if args.file == "-" else apply.load_path(args.file)
        except (OSError, apply.ApplyError) as err:
            stderr.write(f"stardelt: {err}\n")
            return USAGE

    text = None
    if args.command == "sql":
        if (args.statement is None) == (args.file is None):
            stderr.write("stardelt: give the statement or -f FILE, not both and not neither\n")
            return USAGE
        try:
            text = args.statement
            if args.file is not None:
                text = stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
        except OSError as err:
            stderr.write(f"stardelt: {err}\n")
            return USAGE
        if not text.strip():
            stderr.write("stardelt: the statement is empty\n")
            return USAGE

    stored = login.load(environ)
    host = (args.host or environ.get("STARDELT_NOVA_URL", "") or (stored or {}).get("host") or "").strip()
    if not host:
        stderr.write("stardelt: no host: pass --host or set STARDELT_NOVA_URL\n")
        return USAGE
    http = session or requests.Session()
    token = environ.get("STARDELT_TOKEN", "").strip()
    if not token:
        if stored is None:
            stderr.write("stardelt: authentication needed: run `stardelt login` or set STARDELT_TOKEN\n")
            return AUTH
        token = lambda: login.fresh_token(environ, http)  # noqa: E731
    nova = api.Client(base_url=host, token=token, session=http)

    try:
        if args.command == "whoami":
            stdout.write(json.dumps(nova.me(), indent=2, ensure_ascii=False) + "\n")
            return OK
        if args.command == "apply":
            for spec in specs:
                stdout.write(json.dumps(apply.apply(nova, spec, args.dry_run), ensure_ascii=False) + "\n")
            return OK
        if args.command == "export":
            text, note = apply.export(nova, args.namespace, args.name, args.format)
            stdout.write(text)
            if note:
                stderr.write(f"stardelt: note: {note}\n")
            return OK
        if args.command == "run":
            run, tasks = runs.start(nova, args.namespace, args.name, args.wait, args.timeout or None, args.interval)
            stdout.write(json.dumps(dict(run, tasks=tasks) if args.wait else run, indent=2, ensure_ascii=False) + "\n")
            if args.wait and run["state"] != "success":
                failed = [t["taskId"] for t in tasks if t["state"] == "failed"]
                more = f"; failed tasks: {', '.join(failed)}" if failed else ""
                stderr.write(f"stardelt: run {run['runId']} ended {run['state']}{more}\n")
                return ERROR
            return OK
        if args.command == "mcp":
            allowed = list(mcp.DEFAULT_TOOLS) + [n for n in args.allow if n not in mcp.DEFAULT_TOOLS]
            log = lambda name, failed: stderr.write(f"stardelt-mcp: {name} {'error' if failed else 'ok'}\n")  # noqa: E731
            mcp.serve(mcp.Server(nova, allowed, log=log, sql=not args.no_sql), stdin, stdout)
            return OK
        if args.command == "api":
            answer = nova.call(
                args.operation,
                path=_pairs(args.path),
                query=_pairs(args.query),
                body=body,
            )
            rows = _rows_of(answer) if args.format == "table" else None
            if rows is not None:
                stdout.write(_answer_table(rows))
            elif answer is not None:
                stdout.write(json.dumps(answer, indent=2, ensure_ascii=False) + "\n")
            return OK
        result = statements.run(nova, text) if args.long else nova.query(text)
        stdout.write(_render(result, args.format, args.max_rows))
        if result.truncated:
            stderr.write(f"stardelt: Nova cut the result at {len(result.rows)} rows\n")
        return OK
    except login.LoginError as err:
        stderr.write(f"stardelt: {err}\n")
        return AUTH
    except (statements.StatementError, runs.RunError) as err:
        stderr.write(f"stardelt: {err}\n")
        return ERROR
    except KeyboardInterrupt:
        stderr.write("stardelt: interrupted\n")
        return ERROR
    except api.StardeltApiError as err:
        if err.status == 401:
            stderr.write(f"stardelt: authentication needed: {err}\n")
            return AUTH
        stderr.write(f"stardelt: {err}\n")
        return ERROR
    except requests.exceptions.JSONDecodeError:
        stderr.write("stardelt: Nova's answer is not JSON\n")
        return ERROR
    except requests.RequestException:
        stderr.write(f"stardelt: cannot reach {host}\n")
        return ERROR
    except ValueError as err:  # an unknown operation, a bad NAME=VALUE or a body that is not JSON
        stderr.write(f"stardelt: {err}\n")
        return USAGE
