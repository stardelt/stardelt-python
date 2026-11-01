"""Resource-first commands: ``stardelt pipelines get etl daily`` is
``stardelt api pipelines_get -p namespace=etl -p name=daily``.

The words come from the contract (``operations.json``): an ``operationId`` is ``<resource>_<verb>``, and a
verb of several words joins them with a dash (``pipelines_run_get`` is ``pipelines run-get``). The values of
the path parameters follow the verb in the order of the path; options (``-q``, ``--body``, ``--format``,
``--host``) are the ones of ``stardelt api``.
"""

COMMANDS = {"whoami", "sql", "api", "mcp", "apply", "skill", "bundle"}


def _split(operation_id):
    resource, _, verb = operation_id.partition("_")
    return resource, verb.replace("_", "-")


def resources(ops):
    """Resource word to ``{verb: operation_id}``, e.g. ``{"pipelines": {"get": "pipelines_get"}}``."""
    found = {}
    for operation_id in ops:
        resource, verb = _split(operation_id)
        found.setdefault(resource, {})[verb] = operation_id
    return found


def summary(ops):
    """One line per resource, for ``--help``."""
    lines = []
    for resource, verbs in sorted(resources(ops).items()):
        words = ", ".join(sorted(v for v in verbs if v))
        lines.append(f"  {resource}: {words}" if words else f"  {resource}")
    return "\n".join(lines)


def _usage(ops, resource, verbs):
    parts = []
    for verb, operation_id in sorted(verbs.items()):
        names = [n.upper() for n in ops[operation_id]["path_params"]]
        parts.append(" ".join([verb] + names).strip())
    return f"{resource}: pick one of: " + "; ".join(parts)


def expand(argv, ops):
    """``argv`` for the ``api`` command when it starts with a resource word, else ``argv`` unchanged.

    Raises ``ValueError`` (a usage error) when the verb is missing or unknown or a path value is missing.
    """
    argv = list(argv)
    start = 2 if argv[:1] == ["--host"] else 0
    if len(argv) <= start or argv[start].startswith("-") or argv[start] in COMMANDS:
        return argv
    found = resources(ops)
    resource = argv[start]
    if resource not in found:
        return argv
    verbs = found[resource]
    rest = argv[start + 1 :]
    if "" in verbs and (not rest or rest[0].startswith("-")):
        verb, rest = "", rest
    elif rest and rest[0] in verbs:
        verb, rest = rest[0], rest[1:]
    else:
        raise ValueError(_usage(ops, resource, verbs))
    operation_id = verbs[verb]
    names = ops[operation_id]["path_params"]
    values = rest[: len(names)]
    if len(values) < len(names) or any(v.startswith("-") for v in values):
        wanted = " ".join(n.upper() for n in names)
        raise ValueError(f"{(resource + ' ' + verb).strip()} needs {wanted}")
    pairs = [x for name, value in zip(names, values) for x in ("-p", f"{name}={value}")]
    return argv[:start] + ["api", operation_id] + pairs + rest[len(names) :]
