"""Bundles (design/pipeline-publishing.md, section 7.1): a directory in Git with ``bundle.yaml`` that describes what a
team publishes, and the offline check ``stardelt bundle validate``.

The check needs no host and no sign-in. It is deliberately *less* than the server's plan (the server knows whether a
principal is ready or a notebook version exists); it catches what a pull request can be refused for before anything runs.
"""

import re
from pathlib import Path

import yaml

from stardelt import apply

KEYS = {"bundle", "variables", "include", "files", "requires", "targets"}
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
GROUP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
VARIABLE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
CONNECTION = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
USE = re.compile(r"\$\{var\.([A-Za-z0-9_]+)\}")
MODES = ("development", "production")
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
        for dup in sorted({n for n in names if names.count(n) > 1}):
            problems.append((where, f"two pipeline files define {dup!r}"))
        resolved[tname] = (mode, names)
    for v in sorted(set(variables) - used):
        warnings.append(f"variable {v!r} is declared but no pipeline file uses it")
    return problems, warnings, resolved
