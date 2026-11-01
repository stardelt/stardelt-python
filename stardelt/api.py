"""A small client for Nova's public API (the contract in stardelt-nova ``openapi/nova.json``).

    import stardelt.api

    nova = stardelt.api.Client()          # STARDELT_NOVA_URL and the notebook's token
    nova.me()                             # who the platform thinks you are
    nova.query("select 1 as x").to_pandas()

The token is sent as a bearer token. Every error answer from Nova is the envelope
``{"error": "<sentence>"}``; it becomes a ``StardeltApiError`` whose message is that
one sentence plus the HTTP status. A token never appears in a message or a repr.
"""

import functools
import json
import os
from importlib import resources
from urllib.parse import quote

import requests

DEFAULT_TIMEOUT = 60


@functools.lru_cache(maxsize=1)
def operations():
    """The public operations of Nova's contract: ``operationId`` to method, path and parameters.

    Read from ``operations.json``, which ``scripts/sync_contract.py`` refreshes from the contract.
    """
    text = resources.files("stardelt").joinpath("operations.json").read_text()
    return json.loads(text)["operations"]


class StardeltApiError(RuntimeError):
    """Nova answered with an error status. ``status`` is the HTTP status."""

    def __init__(self, status, message):
        super().__init__(f"{message} (HTTP {status})")
        self.status = status
        self.message = message


class QueryResult:
    """The answer of ``POST /api/query``: columns, rows and whether the rows are a prefix."""

    def __init__(self, payload):
        self.columns = payload["columns"]
        self.rows = payload["rows"]
        self.row_count = payload["row_count"]
        self.query_id = payload.get("query_id")
        self.truncated = bool(payload.get("truncated", False))

    def to_pandas(self):
        import pandas as pd

        return pd.DataFrame(self.rows, columns=[c["name"] for c in self.columns])

    def __repr__(self):
        return f"QueryResult(rows={self.row_count}, truncated={self.truncated})"


class Verbs:
    """The verbs of one resource as methods: ``nova.ops.pipelines.get(namespace="etl", name="daily")``.

    Keyword arguments are matched by name against the operation's path and query parameters (see
    ``operations()``); ``body=`` is the JSON body. A resource with a single unnamed operation
    (``nova.ops.health()``) is called directly.
    """

    def __init__(self, client, resource, verbs):
        self._client = client
        self._resource = resource
        self._verbs = verbs

    def __dir__(self):
        return sorted(verb for verb in self._verbs if verb)

    def __repr__(self):
        return f"<{self._resource}: {', '.join(dir(self))}>"

    def __getattr__(self, verb):
        if verb.startswith("_") or verb not in self._verbs:
            raise AttributeError(f"{self._resource} has no verb {verb!r}; it has: {', '.join(dir(self))}")
        return functools.partial(self._call, self._verbs[verb])

    def __call__(self, **arguments):
        if "" not in self._verbs:
            raise TypeError(f"call a verb: {self._resource}.<verb>(...); the verbs are: {', '.join(dir(self))}")
        return self._call(self._verbs[""], **arguments)

    def _call(self, operation_id, body=None, **arguments):
        op = operations()[operation_id]
        path = {name: arguments.pop(name) for name in op["path_params"] if name in arguments}
        query = {name: arguments.pop(name) for name in op["query_params"] if name in arguments}
        if arguments:
            accepted = op["path_params"] + op["query_params"] + (["body"] if op["body"] else [])
            raise TypeError(f"{operation_id} takes {accepted}, not {sorted(arguments)}")
        return self._client.call(operation_id, path=path, query=query, body=body)


class Resources:
    """``nova.ops``: one attribute per resource of the contract (``pipelines``, ``table``, ``query``, ...)."""

    def __init__(self, client):
        self._client = client
        self._resources = {}
        for operation_id in operations():
            resource, _, verb = operation_id.partition("_")
            self._resources.setdefault(resource, {})[verb] = operation_id

    def __dir__(self):
        return sorted(self._resources)

    def __repr__(self):
        return f"<resources: {', '.join(dir(self))}>"

    def __getattr__(self, resource):
        if resource.startswith("_") or resource not in self._resources:
            raise AttributeError(f"no resource {resource!r}; the resources are: {', '.join(dir(self))}")
        return Verbs(self._client, resource, self._resources[resource])


def _query_value(value):
    """A query value as Nova reads it. Python's ``True`` goes out as ``True``, which a boolean parameter refuses (400)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


class Client:
    """Talks to one Nova. ``token`` is a string or a function returning one (called per request)."""

    def __init__(self, base_url=None, token=None, session=None, timeout=DEFAULT_TIMEOUT):
        base = (base_url or os.environ.get("STARDELT_NOVA_URL", "")).strip().rstrip("/")
        if not base:
            raise ValueError("base_url is not set and STARDELT_NOVA_URL is empty")
        self.base_url = base
        self._token = token
        self._session = session or requests.Session()
        self._timeout = timeout

    def __repr__(self):
        return f"Client(base_url={self.base_url!r})"

    @property
    def ops(self):
        """Every public operation as ``nova.ops.<resource>.<verb>(...)``; ``dir(nova.ops)`` lists the resources."""
        return Resources(self)

    def _bearer(self):
        token = self._token
        if token is None:
            import stardelt

            token = stardelt._token
        return token() if callable(token) else token

    def _request(self, method, path, **kwargs):
        response = self._session.request(
            method,
            self.base_url + path,
            headers={"Authorization": f"Bearer {self._bearer()}"},
            timeout=self._timeout,
            **kwargs,
        )
        if response.status_code >= 400:
            try:
                message = response.json()["error"]
            except Exception:
                message = (response.text or "").strip()[:200] or "no message"
            raise StardeltApiError(response.status_code, " ".join(str(message).split()))
        return response

    def me(self):
        """``GET /api/me``: the signed-in person, as a dict."""
        return self._request("GET", "/api/me").json()

    def query(self, sql):
        """``POST /api/query``: run one statement as the caller. Rows beyond Nova's cap are cut and ``truncated`` is true."""
        return QueryResult(self._request("POST", "/api/query", json={"sql": sql}).json())

    def statement(self, sql, max_seconds=None):
        """Like ``query``, through the statement API: no request stays open, up to 100 000 rows, cancelled when it fails.

        ``max_seconds`` cancels a statement that takes longer (default: no limit). See ``stardelt.statements``.
        """
        from stardelt import statements

        return statements.run(self, sql, max_seconds)

    def call(self, operation_id, path=None, query=None, body=None):
        """Any public operation by its ``operationId`` (see ``operations()``).

        ``path`` fills the ``{name}`` parts of the URL (each value is URL-quoted), ``query`` is
        the query string, ``body`` a JSON-able request body. Returns the decoded JSON answer,
        or ``None`` for an empty (204) one.
        """
        op = operations().get(operation_id)
        if op is None:
            raise ValueError(f"unknown operation {operation_id!r}; stardelt.api.operations() lists them")
        path = dict(path or {})
        missing = [n for n in op["path_params"] if n not in path]
        extra = [n for n in path if n not in op["path_params"]]
        if missing or extra:
            raise ValueError(
                f"{operation_id} takes path parameters {op['path_params']}: missing {missing}, unexpected {extra}"
            )
        url_path = op["path"]
        for name, value in path.items():
            url_path = url_path.replace("{" + name + "}", quote(str(value), safe=""))
        kwargs = {}
        if query:
            kwargs["params"] = {name: _query_value(value) for name, value in query.items()}
        if body is not None:
            kwargs["json"] = body
        response = self._request(op["method"], url_path, **kwargs)
        return None if response.status_code == 204 else response.json()
