"""``stardelt``: a thin command-line client for Nova's public API (shells, CI, agents).

    export STARDELT_NOVA_URL=https://nova.example.com
    export STARDELT_TOKEN=...             # a bearer token; `stardelt login` comes later
    stardelt whoami
    stardelt sql "select 1 as x" --format json

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

import requests

from stardelt import api

OK, ERROR, USAGE, AUTH = 0, 1, 2, 3


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(message)


def _parser():
    parser = _Parser(prog="stardelt", description="A thin client for Nova's public API.")
    parser.add_argument("--host", help="Nova's base URL (default: $STARDELT_NOVA_URL)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("whoami", help="who Nova thinks you are (JSON)")
    sql = commands.add_parser("sql", help="run one statement as you")
    sql.add_argument("statement")
    sql.add_argument("--format", choices=["table", "json", "csv"], default="table")
    sql.add_argument(
        "--max-rows", type=int, default=0, help="print at most N rows (0: all Nova returned)"
    )
    call = commands.add_parser("api", help="call any public operation by its operationId")
    call.add_argument("operation", nargs="?", help="an operationId (see --list)")
    call.add_argument("--list", action="store_true", dest="list_operations", help="list the operations")
    call.add_argument("-p", "--path", action="append", default=[], metavar="NAME=VALUE")
    call.add_argument("-q", "--query", action="append", default=[], metavar="NAME=VALUE")
    call.add_argument("--body", help="the request body, as JSON")
    return parser


def _pairs(items):
    pairs = {}
    for item in items:
        name, found, value = item.partition("=")
        if not found or not name:
            raise ValueError(f"{item!r} is not NAME=VALUE")
        pairs[name] = value
    return pairs


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


def main(argv=None, environ=None, stdout=None, stderr=None, session=None):
    environ = os.environ if environ is None else environ
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        args = _parser().parse_args(argv)
    except _UsageError as err:
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

    host = (args.host or environ.get("STARDELT_NOVA_URL", "")).strip()
    if not host:
        stderr.write("stardelt: no host: pass --host or set STARDELT_NOVA_URL\n")
        return USAGE
    token = environ.get("STARDELT_TOKEN", "").strip()
    if not token:
        stderr.write("stardelt: authentication needed: set STARDELT_TOKEN to a bearer token\n")
        return AUTH
    nova = api.Client(base_url=host, token=token, session=session)

    try:
        if args.command == "whoami":
            stdout.write(json.dumps(nova.me(), indent=2, ensure_ascii=False) + "\n")
            return OK
        if args.command == "api":
            answer = nova.call(
                args.operation,
                path=_pairs(args.path),
                query=_pairs(args.query),
                body=json.loads(args.body) if args.body is not None else None,
            )
            if answer is not None:
                stdout.write(json.dumps(answer, indent=2, ensure_ascii=False) + "\n")
            return OK
        result = nova.query(args.statement)
        stdout.write(_render(result, args.format, args.max_rows))
        if result.truncated:
            stderr.write(f"stardelt: Nova cut the result at {len(result.rows)} rows\n")
        return OK
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
