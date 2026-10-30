"""A small client for Nova's public API (the contract in stardelt-nova ``openapi/nova.json``).

    import stardelt.api

    nova = stardelt.api.Client()          # STARDELT_NOVA_URL and the notebook's token
    nova.me()                             # who the platform thinks you are
    nova.query("select 1 as x").to_pandas()

The token is sent as a bearer token. Every error answer from Nova is the envelope
``{"error": "<sentence>"}``; it becomes a ``StardeltApiError`` whose message is that
one sentence plus the HTTP status. A token never appears in a message or a repr.
"""

import os

import requests

DEFAULT_TIMEOUT = 60


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
