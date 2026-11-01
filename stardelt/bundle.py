"""Bundles (design/pipeline-publishing.md, section 7.1): a directory in Git with ``bundle.yaml`` that describes what a
team publishes, and the offline check ``stardelt bundle validate``.

The check needs no host and no sign-in. It is deliberately *less* than the server's plan (the server knows whether a
principal is ready or a notebook version exists); it catches what a pull request can be refused for before anything runs.
"""

import difflib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

import yaml

from stardelt import api, apply, runs

KEYS = {"bundle", "variables", "include", "files", "requires", "targets"}
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
GROUP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
VARIABLE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
CONNECTION = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
USE = re.compile(r"\$\{var\.([A-Za-z0-9_]+)\}")
NOTEBOOK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}\.ipynb$")
MODES = ("development", "production")
DRIFT = ("warn", "block")
MAX_FILES = 100
MAX_BYTES = 5 * 1024 * 1024


def _linked(root, path):
    """True when ``path`` or a directory between it and ``root`` is a symbolic link."""
    return any(p.is_symlink() for p in [path, *path.parents] if p != root and root in p.parents)


def _files(root, patterns, what, problems):
    """The regular files inside ``root`` that ``patterns`` match, as sorted relative POSIX paths."""
    found = set()
    if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) and p for p in patterns):
        problems.append((what, "must be a list of file patterns"))
        return []
    for pattern in patterns:
        if pattern.startswith("/") or ".." in Path(pattern).parts:
            problems.append((what, f"{pattern}: a pattern must stay inside the bundle (no absolute path, no ..)"))
            continue
        matched = 0
        for path in sorted(root.glob(pattern)):
            relative = path.relative_to(root)
            if ".git" in relative.parts:
                problems.append((str(relative), "a path inside .git is not allowed"))
            elif _linked(root, path):
                problems.append((str(relative), "a symbolic link is not allowed"))
            elif path.is_file():
                if path.stat().st_size > MAX_BYTES:
                    problems.append((str(relative), f"larger than {MAX_BYTES // (1024 * 1024)} MiB"))
                else:
                    found.add(relative.as_posix())
                    matched += 1
        if not matched:
            problems.append((what, f"{pattern}: matches no file"))
    return sorted(found)


def _substitute(text, values, problems, where):
    def one(match):
        name = match.group(1)
        if name not in values:
            problems.append((where, f"${{var.{name}}} has no value"))
            return match.group(0)
        return str(values[name])

    return USE.sub(one, text)


def validate(directory, target=None):
    """Check the bundle in ``directory`` (and only ``target`` when given).

    Returns ``(problems, warnings, resolved)``: ``problems`` and ``warnings`` are lists (``(where, message)`` and
    sentences); ``resolved`` maps each checked target to ``(mode, [pipeline names])``.
    """
    root = Path(directory)
    problems, warnings, resolved = [], [], {}
    manifest_path = root / "bundle.yaml"
    if not manifest_path.is_file():
        return [(str(manifest_path), "no bundle.yaml here")], warnings, resolved
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as err:
        return [("bundle.yaml", f"cannot read it: {str(err).splitlines()[0]}")], warnings, resolved
    if not isinstance(manifest, dict):
        return [("bundle.yaml", "must be a mapping")], warnings, resolved
    for key in sorted(set(manifest) - KEYS):
        problems.append(("bundle.yaml", f"unknown key {key!r} (known: {', '.join(sorted(KEYS))})"))

    head = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    name, owner = head.get("name"), head.get("ownerGroup")
    if not (isinstance(name, str) and NAME.match(name)):
        problems.append(("bundle.name", "must be 3 to 40 characters: lower-case letters, digits and dashes"))
    if not (isinstance(owner, str) and GROUP.match(owner)):
        problems.append(("bundle.ownerGroup", "must be a group name"))

    variables = manifest.get("variables") or {}
    if not isinstance(variables, dict) or not all(VARIABLE.match(str(v)) and isinstance(d, (dict, type(None))) for v, d in variables.items()):
        problems.append(("variables", "must map names (lower-case, digits, underscore) to {default, description}"))
        variables = {}
    requires = manifest.get("requires") or {}
    connections = requires.get("connections", []) if isinstance(requires, dict) else None
    if not isinstance(connections, list) or not all(isinstance(c, str) and CONNECTION.match(c) for c in connections):
        problems.append(("requires.connections", "must be a list of connection names"))

    files = _files(root, manifest.get("files", []), "files", problems) if "files" in manifest else []
    if len(files) > MAX_FILES:
        problems.append(("files", f"more than {MAX_FILES} files"))
    sources = _files(root, manifest.get("include"), "include", problems) if "include" in manifest else []
    if "include" not in manifest:
        problems.append(("include", "is required: the pipeline files of the bundle"))

    targets = manifest.get("targets")
    if not isinstance(targets, dict) or not targets:
        problems.append(("targets", "must name at least one target"))
        return problems, warnings, resolved
    if target is not None and target not in targets:
        problems.append(("targets", f"no target named {target!r} (targets: {', '.join(targets)})"))
        return problems, warnings, resolved

    used = set()
    for tname, spec in targets.items():
        if target is not None and tname != target:
            continue
        where = f"targets.{tname}"
        spec = spec if isinstance(spec, dict) else {}
        mode = spec.get("mode")
        if mode not in MODES:
            problems.append((where, f"mode must be one of {', '.join(MODES)}"))
        if spec.get("drift", "warn") not in DRIFT:
            problems.append((where, f"drift must be one of {', '.join(DRIFT)}"))
        tvars = spec.get("variables") or {}
        for v in tvars:
            if v not in variables:
                problems.append((where, f"variable {v!r} is not declared under variables"))
        run_as = spec.get("runAs")
        if mode == "production":
            if not (isinstance(run_as, str) and isinstance(owner, str) and re.match(rf"^pipeline-{re.escape(owner)}(-[A-Za-z0-9._-]+)?$", run_as)):
                problems.append((where, f"a production target needs runAs: pipeline-{owner or '<ownerGroup>'} (or that name with a suffix)"))
        values = {v: (d or {}).get("default") for v, d in variables.items() if (d or {}).get("default") is not None}
        values.update(tvars)
        names = []
        for relative in sources:
            text = (root / relative).read_text(encoding="utf-8")
            used.update(USE.findall(text))
            text = _substitute(text, values, problems, f"{relative} ({tname})")
            try:
                doc = apply.load(text)
            except apply.ApplyError as err:
                problems.append((f"{relative} ({tname})", str(err)))
                continue
            names.append(doc["name"])
            schedule = doc.get("schedule")
            if "schedule" in doc and not (
                isinstance(schedule, dict)
                and all(isinstance(schedule.get(key), str) and schedule[key].strip() for key in ("cron", "timezone"))
            ):
                problems.append(
                    (f"{relative} ({tname})", "schedule needs both cron and timezone (an IANA zone such as Europe/Berlin)")
                )
            if doc.get("ownerGroup", owner) != owner:
                problems.append((f"{relative} ({tname})", f"ownerGroup {doc['ownerGroup']!r} is not the bundle's group {owner!r}"))
            given = [k for k in ("sql", "sqlFile", "notebook") if k in doc]
            if len(given) != 1:
                problems.append((f"{relative} ({tname})", "give exactly one of sql, sqlFile or notebook"))
            if "sqlFile" in doc and doc["sqlFile"] not in files:
                problems.append((f"{relative} ({tname})", f"sqlFile {doc['sqlFile']!r} is not among the bundle's files"))
            notebook = doc.get("notebook")
            if isinstance(notebook, dict) and "file" in notebook and notebook["file"] not in files:
                problems.append((f"{relative} ({tname})", f"notebook file {notebook['file']!r} is not among the bundle's files"))
            if isinstance(notebook, dict) and "file" in notebook:
                if not NOTEBOOK_NAME.match(PurePosixPath(str(notebook["file"])).name):
                    problems.append(
                        (f"{relative} ({tname})", f"notebook file {notebook['file']!r}: the file name must end in .ipynb, start with a letter or digit and use only letters, digits, '.', '_', '-' and spaces (at most 128 characters)")
                    )
                if {"name", "objectVersion"} & set(notebook):
                    problems.append((f"{relative} ({tname})", "a notebook that names a file cannot also name a version: remove name and objectVersion"))
        for dup in sorted({n for n in names if names.count(n) > 1}):
            problems.append((where, f"two pipeline files define {dup!r}"))
        resolved[tname] = (mode, names)
    for v in sorted(set(variables) - used):
        warnings.append(f"variable {v!r} is declared but no pipeline file uses it")
    return problems, warnings, resolved


def _slug(user):
    """A person's sign-in name as part of a pipeline name: lower-case letters, digits and single dashes."""
    return re.sub(r"[^a-z0-9]+", "-", str(user or "").lower()).strip("-")


def render(directory, target, user=None):
    """The pipelines of one target as the bodies ``pipelines_create`` takes: ``(problems, mode, specs)``.

    Everything ``validate`` checks is checked first. Then each pipeline file gets its variables, its ``sqlFile`` read
    into ``sql`` and the bundle's group as ``ownerGroup`` when it names none. A ``development`` target deploys under
    ``dev-<user>-<name>`` (two people can deploy one bundle side by side) and without a schedule (run it with
    ``stardelt run``); a ``production`` target deploys as written. A notebook that is a file of the bundle stays
    ``notebook: {file: ...}`` here: ``deploy`` publishes it and pins the version. ``specs`` is empty when there are problems.
    """
    problems, _, _ = validate(directory, target)
    if problems:
        return problems, None, []
    root = Path(directory)
    manifest = yaml.safe_load((root / "bundle.yaml").read_text(encoding="utf-8"))
    chosen = manifest["targets"][target]
    mode = chosen["mode"]
    if mode == "development" and not _slug(user):
        return [(f"targets.{target}", "a development target needs the name of the person who deploys: sign in first")], mode, []
    owner = manifest["bundle"]["ownerGroup"]
    values = {v: (d or {}).get("default") for v, d in (manifest.get("variables") or {}).items() if (d or {}).get("default") is not None}
    values.update(chosen.get("variables") or {})
    specs = []
    for relative in _files(root, manifest["include"], "include", []):
        where = f"{relative} ({target})"
        doc = apply.load(_substitute((root / relative).read_text(encoding="utf-8"), values, [], where))
        doc.setdefault("ownerGroup", owner)
        if "sqlFile" in doc:
            doc["sql"] = (root / doc.pop("sqlFile")).read_text(encoding="utf-8")
        if mode == "development":
            doc["name"] = f"dev-{_slug(user)}-{doc['name']}"
            doc.pop("schedule", None)
            if len(doc["name"]) > 40:
                problems.append((where, f"the development name {doc['name']} is longer than 40 characters"))
        specs.append(doc)
    return problems, mode, ([] if problems else specs)


def _revision(root):
    """The commit ``root`` is at (40 hex digits, with ``-dirty`` appended when files have uncommitted changes), or
    ``None`` when ``root`` is not in a Git checkout or ``git`` is not there."""
    def git(*args):
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, check=False)
        return done.stdout.strip() if done.returncode == 0 else None

    try:
        commit = git("rev-parse", "HEAD")
        if not commit or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            return None
        return commit + ("-dirty" if git("status", "--porcelain") else "")
    except (OSError, subprocess.SubprocessError):
        return None


def provenance(directory, target):
    """What a deploy tells Nova about where its pipelines came from: ``{bundle, target[, revision]}``."""
    root = Path(directory)
    name = yaml.safe_load((root / "bundle.yaml").read_text(encoding="utf-8"))["bundle"]["name"]
    revision = _revision(root)
    return {"bundle": name, "target": target, **({"revision": revision} if revision else {})}


def _deployed(nova, bundle_name, target, mode, user):
    """The listed pipelines an earlier deploy of this bundle target left on the stage.

    Only a pipeline that carries the stamp of this bundle and this target counts (a pipeline made by hand,
    or by another bundle or target, never does). In a ``development`` target only the caller's own ones, the
    names that start with ``dev-<user>-``: two people deploy one bundle side by side.
    """
    prefix = f"dev-{_slug(user)}-" if mode == "development" else ""
    return [
        pipeline
        for pipeline in nova.call("pipelines_list")["pipelines"]
        if (pipeline.get("deployment") or {}).get("bundle") == bundle_name
        and (pipeline.get("deployment") or {}).get("target") == target
        and pipeline["name"].startswith(prefix)
    ]


def _stale(nova, bundle_name, target, mode, user, keep):
    """The pipelines an earlier deploy of this bundle target left that the bundle no longer names, as ``(namespace, name)``."""
    return [(p["namespace"], p["name"]) for p in _deployed(nova, bundle_name, target, mode, user) if p["name"] not in keep]


def _lines(value):
    """A field of a pipeline document as lines: a statement as it is, anything else as YAML."""
    if value is None:
        return []
    if isinstance(value, str):
        return value.splitlines()
    return yaml.dump(value, Dumper=apply._BlockDumper, sort_keys=False, allow_unicode=True, default_flow_style=False).splitlines()


def _diffs(nova, specs, results):
    """``{name: text}``: for each ``would-update`` pipeline, a unified diff of every field Nova says would change.

    ``deployment`` (the stamp: the commit changes with every deploy) is not shown. A pipeline whose deployed document
    cannot be read gets one sentence instead of a diff.
    """
    wanted = [r for r in results if r.get("action") == "would-update" and set(r.get("changes", [])) - {"deployment"}]
    if not wanted:
        return {}
    namespaces = {p["name"]: p["namespace"] for p in nova.call("pipelines_list")["pipelines"]}
    new_documents = {spec["name"]: spec for spec in specs}
    found = {}
    for result in wanted:
        name = result["name"]
        try:
            old_document = nova.call("pipelines_spec", path={"namespace": namespaces[name], "name": name})["document"]
        except (KeyError, api.StardeltApiError) as err:
            found[name] = f"{name}: the difference is not available ({err})"
            continue
        parts = []
        for change in result["changes"]:
            if change == "deployment":
                continue
            key = "sql" if change == "statement" else change
            parts += difflib.unified_diff(
                _lines(old_document.get(key)),
                _lines(new_documents[name].get(key)),
                fromfile=f"{name} {key} (deployed)",
                tofile=f"{name} {key} (bundle)",
                lineterm="",
                n=2,
            )
        found[name] = "\n".join(parts)
    return found


def _drift_mode(directory, target):
    """What the target says to do about a pipeline someone changed after its last deploy: ``warn`` (default) or ``block``."""
    manifest = yaml.safe_load((Path(directory) / "bundle.yaml").read_text(encoding="utf-8"))
    return ((manifest.get("targets") or {}).get(target) or {}).get("drift", "warn")


def _notebook(spec):
    """The notebook of a pipeline spec when it is a file of the bundle (``notebook: {file: ...}``), else ``None``."""
    notebook = spec.get("notebook")
    return notebook if isinstance(notebook, dict) and "file" in notebook else None


def _publish(nova, directory, spec, dry_run):
    """The answer of ``notebooks_publish`` for the notebook file of ``spec``; with ``dry_run`` Nova only says what a
    publish would do (``unchanged``) and stores nothing. Nova's own sentence is the ``ApplyError`` when it refuses."""
    file = spec["notebook"]["file"]
    try:
        document = json.loads((Path(directory) / file).read_text(encoding="utf-8"))
    except ValueError as err:
        raise apply.ApplyError(f"{spec['name']}: {file} is not a notebook: {err}") from err
    try:
        return nova.call(
            "notebooks_publish",
            path={"group": spec["ownerGroup"], "name": PurePosixPath(file).name},
            query={"dryRun": True} if dry_run else None,
            body=document,
        )
    except api.StardeltApiError as err:
        raise apply.ApplyError(f"{spec['name']}: {file}: {err}") from err


def _pinned(spec, answer):
    """``spec`` with its notebook file replaced by the version ``answer`` names: ``{name, objectVersion[, params]}``."""
    notebook = spec["notebook"]
    pinned = {"name": PurePosixPath(notebook["file"]).name, "objectVersion": answer["objectVersion"]}
    if notebook.get("params"):
        pinned["params"] = notebook["params"]
    return {**spec, "notebook": pinned}


def _blocked(target, names):
    """The refusals of a ``drift: block`` target for the pipelines in ``names``."""
    return [
        (target, f"{name} was changed after its last deploy and this target says drift: block; look at the change, then pass --accept-drift to overwrite it")
        for name in sorted(names)
    ]


def deploy(nova, directory, target, dry_run=False, prune=False, accept_drift=False, diff=False):
    """Plan, then (unless ``dry_run``) deploy ``target``: ``(problems, results)``.

    ``nova`` is a ``stardelt.api.Client``. Every pipeline is first checked by Nova (``pipelines_validate``, the
    ``--dry-run`` of ``apply``); when anything is refused nothing is written and the refusals are the problems.
    ``results`` are the answers of ``apply``: ``would-create`` / ``would-update`` for a plan, ``created`` /
    ``updated`` for a deploy. With ``prune`` a pipeline an earlier deploy of this bundle target stamped and the bundle no
    longer names is listed as ``would-delete`` and, in a real deploy, removed (``deleted``) after everything else
    succeeded. Every pipeline carries the bundle's name, the target and the Git commit of the bundle
    directory (``provenance``), which Nova stamps on it. There is no deployment record and nothing is removed that
    the bundle no longer names. A target with ``drift: block`` refuses, for a plan and a deploy alike, a pipeline someone changed
    after its last deploy (``modified``) unless ``accept_drift``; the default ``warn`` only reports it. With ``diff`` each
    ``would-update`` (and ``updated``) result carries a ``diff`` text: what Nova would change, field by field.
    """
    user = nova.me().get("user")
    problems, mode, specs = render(directory, target, user)
    if problems:
        return problems, []
    stamp = provenance(directory, target)
    specs = [{**spec, "provenance": stamp} for spec in specs]
    results, refused, changed_by_hand, unpublished = [], [], set(), []
    for position, spec in enumerate(specs):
        if _notebook(spec):
            try:
                answer = _publish(nova, directory, spec, dry_run=True)
            except apply.ApplyError as err:
                refused.append((target, str(err)))
                continue
            if not answer["unchanged"]:
                # There is no version to pin yet, so Nova cannot check this pipeline: a deploy publishes first.
                unpublished.append(position)
                results.append({"action": "would-publish", "name": spec["name"], "notebook": f"{spec['ownerGroup']}/{PurePosixPath(spec['notebook']['file']).name}"})
                continue
            specs[position] = spec = _pinned(spec, answer)
        try:
            results.append(apply.apply(nova, spec, dry_run=True))
        except apply.ApplyError as err:
            refused.append((target, str(err)))
        else:
            if results[-1].get("modified"):
                changed_by_hand.add(spec["name"])
    if changed_by_hand and not accept_drift and _drift_mode(directory, target) == "block":
        refused += _blocked(target, changed_by_hand)
    diffs = _diffs(nova, specs, results) if diff else {}
    results = [{**r, "diff": diffs[r["name"]]} if diffs.get(r["name"]) else r for r in results]
    stale = _stale(nova, stamp["bundle"], target, mode, user, {spec["name"] for spec in specs}) if prune else []
    results += [{"action": "would-delete", "name": name} for _, name in stale]
    if refused or dry_run:
        return refused, results
    done = []
    for position in unpublished:
        spec = specs[position]
        try:
            answer = _publish(nova, directory, spec, dry_run=False)
        except apply.ApplyError as err:
            return [(target, str(err))], done
        specs[position] = spec = _pinned(spec, answer)
        done.append({"action": "published", "name": spec["name"], "notebook": f"{spec['ownerGroup']}/{spec['notebook']['name']}", "objectVersion": answer["objectVersion"]})
        try:
            verdict = apply.apply(nova, spec, dry_run=True)
        except apply.ApplyError as err:
            return [(target, str(err))], done
        if verdict.get("modified"):
            changed_by_hand.add(spec["name"])
    if changed_by_hand and not accept_drift and _drift_mode(directory, target) == "block":
        return _blocked(target, changed_by_hand), done
    done += [
        {
            **apply.apply(nova, spec),
            **({"modified": True} if spec["name"] in changed_by_hand else {}),
            **({"diff": diffs[spec["name"]]} if diffs.get(spec["name"]) else {}),
        }
        for spec in specs
    ]
    for namespace, name in stale:
        nova.call("pipelines_delete", path={"namespace": namespace, "name": name})
        done.append({"action": "deleted", "name": name})
    return [], done


def summary(nova, directory, target):
    """What a deploy of ``target`` left on the stage, one row per pipeline: ``(problems, rows)``.

    The rows are the pipelines ``destroy`` would list (stamped with this bundle's name and this target; in a
    ``development`` target only the caller's own). Each row has the ``name`` and, when Nova knows them, the
    ``revision`` (Git commit) it was deployed from, ``deployedBy``, ``deployedAt`` and ``modified`` (true when someone changed
    the pipeline after the deploy). Nothing is written.
    """
    problems, _, resolved = validate(directory, target)
    if problems:
        return problems, []
    mode = resolved[target][0]
    user = nova.me().get("user")
    if mode == "development" and not user:
        return [(target, "sign in first: a development target shows only your own pipelines")], []
    rows = []
    for pipeline in sorted(_deployed(nova, provenance(directory, target)["bundle"], target, mode, user), key=lambda p: p["name"]):
        stamp = pipeline["deployment"]
        row = {"name": pipeline["name"]}
        for key in ("revision", "deployedBy", "deployedAt", "modified"):
            if stamp.get(key) is not None:
                row[key] = stamp[key]
        rows.append(row)
    return [], rows


def run(nova, directory, target, only=None, timeout=None, interval=5):
    """Start the pipelines an earlier deploy of ``target`` left on the stage, one after the other, and wait for each:
    ``(problems, results)``.

    The pipelines are the ones ``summary`` lists (stamped with this bundle's name and this target; in a ``development``
    target only the caller's own). ``only`` names one of them, with or without the ``dev-<user>-`` prefix. Each result is
    ``{"action": "ran", "name", "runId", "state"}`` (plus ``failedTasks`` when tasks failed). The first run that does not end
    ``success`` stops the rest. ``timeout`` (seconds, default no limit) gives up waiting for a run, which keeps going.
    """
    problems, _, resolved = validate(directory, target)
    if problems:
        return problems, []
    mode = resolved[target][0]
    user = nova.me().get("user")
    if mode == "development" and not user:
        return [(target, "sign in first: a development target runs only your own pipelines")], []
    prefix = f"dev-{_slug(user)}-" if mode == "development" else ""
    found = sorted(_deployed(nova, provenance(directory, target)["bundle"], target, mode, user), key=lambda p: p["name"])
    if only is not None:
        found = [p for p in found if p["name"] in (only, prefix + only)]
        if not found:
            return [(target, f"{only}: no pipeline of this bundle target is deployed under that name; `stardelt bundle summary` lists them")], []
    if not found:
        return [(target, "nothing is deployed from this bundle target: run `stardelt bundle deploy` first")], []
    results = []
    for pipeline in found:
        try:
            ended, tasks = runs.start(nova, pipeline["namespace"], pipeline["name"], wait=True, timeout=timeout, interval=interval)
        except runs.RunError as err:
            return [(target, f"{pipeline['name']}: {err}")], results
        row = {"action": "ran", "name": pipeline["name"], "runId": ended["runId"], "state": ended["state"]}
        failed = [task["taskId"] for task in tasks if task["state"] == "failed"]
        if failed:
            row["failedTasks"] = failed
        results.append(row)
        if ended["state"] != "success":
            break
    return [], results


def destroy(nova, directory, target, confirm=False):
    """Remove everything an earlier deploy of ``target`` put on the stage: ``(problems, results)``.

    The candidates are exactly the pipelines ``--prune`` would remove if the bundle named none: the ones stamped with this
    bundle's name and this target (in a ``development`` target only the caller's own ``dev-<user>-`` ones). A pipeline made by
    hand, or stamped by another bundle or target, is never touched. Without ``confirm`` nothing is removed and the results
    are ``would-delete`` lines; with it each pipeline is deleted and the results are ``deleted`` lines. The files of the
    bundle are not touched.
    """
    problems, _, resolved = validate(directory, target)
    if problems:
        return problems, []
    mode = resolved[target][0]
    user = nova.me().get("user")
    if mode == "development" and not user:
        return [(target, "sign in first: a development target removes only your own pipelines")], []
    found = _stale(nova, provenance(directory, target)["bundle"], target, mode, user, set())
    if not confirm:
        return [], [{"action": "would-delete", "name": name} for _, name in found]
    done = []
    for namespace, name in found:
        nova.call("pipelines_delete", path={"namespace": namespace, "name": name})
        done.append({"action": "deleted", "name": name})
    return [], done


MANIFEST = """bundle:
  name: {name}
  ownerGroup: {group}
variables:
  catalog:
    default: warehouse
    description: the catalog the pipelines read
include: [resources/*.yaml]
files: [sql/*.sql]
requires:
  connections: []
targets:
  dev:
    mode: development
    variables: {{catalog: warehouse}}
  prod:
    mode: production
    variables: {{catalog: warehouse}}
    runAs: pipeline-{group}
"""
PIPELINE = """name: daily
sqlFile: sql/daily.sql
params:
  catalog: "${var.catalog}"
schedule:
  cron: "0 3 * * *"
  timezone: UTC
"""
STATEMENT = "-- Replace this with your statement. ${params.catalog} is filled in from the pipeline's params.\nselect count(*) as trips from ${params.catalog}.demo.trips\n"


def init(directory, name, group):
    """Write a small bundle that passes ``validate`` into ``directory``; returns the files written.

    Raises ``ValueError`` (one sentence) for a bad name or group, or when ``bundle.yaml`` already exists.
    """
    root = Path(directory)
    if not NAME.match(name):
        raise ValueError("the name must be 3 to 40 characters: lower-case letters, digits and dashes")
    if not GROUP.match(group):
        raise ValueError("the group must be a group name")
    if (root / "bundle.yaml").exists():
        raise ValueError(f"{root / 'bundle.yaml'} already exists")
    files = {
        "bundle.yaml": MANIFEST.format(name=name, group=group),
        "resources/daily.yaml": PIPELINE,
        "sql/daily.sql": STATEMENT,
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sorted(files)


class GenerateError(ValueError):
    """The one sentence that says why ``generate`` wrote nothing."""


def _picked_up(relative, patterns):
    return isinstance(patterns, list) and any(PurePosixPath(relative).match(p) for p in patterns if isinstance(p, str))


def generate(nova, directory, namespace, name):
    """Write the pipeline ``namespace``/``name`` into the bundle in ``directory``; returns the relative paths written.

    The pipeline goes to ``resources/<name>.yaml``; a statement goes to ``sql/<name>.sql`` and is named there as
    ``sqlFile``. ``ownerGroup`` is left out because the bundle's group is the default. Raises ``GenerateError`` (one
    sentence) and writes nothing when ``bundle.yaml`` cannot be read, the pipeline is not one a file can change, belongs
    to another group than the bundle, a file would be overwritten, or the bundle's ``include`` or ``files`` would not
    pick a new file up.
    """
    root = Path(directory)
    try:
        manifest = yaml.safe_load((root / "bundle.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise GenerateError(f"{root / 'bundle.yaml'} is missing or cannot be read: write one with `stardelt bundle init`") from None
    if not isinstance(manifest, dict):
        raise GenerateError(f"{root / 'bundle.yaml'} must be a mapping")
    head = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", name):
        raise GenerateError(f"{name!r} is not a plain pipeline name")
    answer = nova.call("pipelines_spec", path={"namespace": namespace, "name": name})
    if answer["managedBy"] != "nova" or not answer["editable"]:
        raise GenerateError(f"{name} cannot be changed from a file (it is managed outside Nova, or has a shape the form cannot edit)")
    document = dict(answer["document"])
    group = document.pop("ownerGroup", None)
    if group != head.get("ownerGroup"):
        raise GenerateError(f"{name} belongs to the group {group}, the bundle to {head.get('ownerGroup')}: a bundle publishes the pipelines of one group")
    document.pop("name", None)
    statement = document.pop("sql", None)
    pipeline = {"name": name}
    files = {}
    if statement is not None:
        pipeline["sqlFile"] = f"sql/{name}.sql"
        files[pipeline["sqlFile"]] = statement if statement.endswith("\n") else statement + "\n"
    pipeline.update(document)
    files[f"resources/{name}.yaml"] = yaml.dump(
        pipeline, Dumper=apply._BlockDumper, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    for relative in sorted(files):
        key = "files" if relative.startswith("sql/") else "include"
        if not _picked_up(relative, manifest.get(key)):
            raise GenerateError(f"{relative} would not be picked up: add a pattern for it under {key} in bundle.yaml")
        if (root / relative).exists():
            raise GenerateError(f"{root / relative} already exists: generate does not overwrite it")
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sorted(files)
