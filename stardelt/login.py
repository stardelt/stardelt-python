"""``stardelt login``: sign in once with the device authorization grant (RFC 8628).

The person gets an address and a short code, approves it in a browser, and the tokens are kept in
``$XDG_CONFIG_HOME/stardelt/credentials.json`` (``~/.config/...`` without it; the directory is mode 0700, the
file 0600). ``STARDELT_TOKEN`` still wins when it is set, for CI. The access token is renewed with the refresh
token when less than a minute is left. A token is never printed.
"""

import json
import os
import time
from pathlib import Path

DEFAULT_CLIENT_ID = "stardelt-dev"
GRANT_DEVICE = "urn:ietf:params:oauth:grant-type:device_code"
SKEW = 60
TIMEOUT = 30


class LoginError(RuntimeError):
    """The sign-in did not work; the message says what to do next."""


def credentials_path(environ):
    """The credentials file, or ``None`` when the environment names neither XDG_CONFIG_HOME nor HOME."""
    base = environ.get("XDG_CONFIG_HOME") or (environ.get("HOME") and os.path.join(environ["HOME"], ".config"))
    return Path(base) / "stardelt" / "credentials.json" if base else None


def load(environ):
    """The stored sign-in as a dict, or ``None`` when there is none (or the file is not usable)."""
    path = credentials_path(environ)
    try:
        creds = json.loads(path.read_text()) if path else None
    except (OSError, ValueError):
        return None
    return creds if isinstance(creds, dict) and creds.get("access_token") else None


def save(environ, creds):
    path = credentials_path(environ)
    if path is None:
        raise LoginError("cannot keep the sign-in: neither XDG_CONFIG_HOME nor HOME is set")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(creds, handle, indent=2)
    os.replace(temp, path)
    return path


def forget(environ):
    """Delete the stored sign-in. ``True`` when there was one."""
    path = credentials_path(environ)
    try:
        path.unlink()
    except (OSError, AttributeError):
        return False
    return True


def _post(session, url, form):
    response = session.request("POST", url, data=form, timeout=TIMEOUT)
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


def discover(session, issuer):
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    response = session.request("GET", url, timeout=TIMEOUT)
    try:
        meta = response.json() if response.status_code == 200 else {}
    except ValueError:
        meta = {}
    if not meta.get("device_authorization_endpoint") or not meta.get("token_endpoint"):
        raise LoginError(f"{issuer} does not offer the device sign-in (no device_authorization_endpoint)")
    return meta


def _creds(issuer, client_id, token_endpoint, body, now, previous=None):
    refresh = body.get("refresh_token") or (previous or {}).get("refresh_token")
    return {
        "issuer": issuer,
        "client_id": client_id,
        "token_endpoint": token_endpoint,
        "access_token": body["access_token"],
        "refresh_token": refresh,
        "expires_at": int(now + int(body.get("expires_in", 300))),
    }


def device_login(session, issuer, client_id, say, sleep=time.sleep, clock=time.time):
    """Run the device flow and return the credentials dict. ``say`` receives the lines the person must read."""
    meta = discover(session, issuer)
    status, start = _post(session, meta["device_authorization_endpoint"], {"client_id": client_id, "scope": "openid"})
    if status != 200 or "device_code" not in start:
        raise LoginError(f"the identity provider refused the sign-in request (HTTP {status})")
    where = start.get("verification_uri_complete") or start.get("verification_uri", "")
    say(f"Open {where} in a browser and approve the code {start.get('user_code', '')}")
    interval = int(start.get("interval", 5))
    deadline = clock() + int(start.get("expires_in", 600))
    while clock() < deadline:
        sleep(interval)
        status, body = _post(
            session,
            meta["token_endpoint"],
            {"grant_type": GRANT_DEVICE, "device_code": start["device_code"], "client_id": client_id},
        )
        if status == 200 and "access_token" in body:
            return _creds(issuer, client_id, meta["token_endpoint"], body, clock())
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error == "access_denied":
            raise LoginError("the sign-in was refused in the browser")
        if error == "expired_token":
            break
        raise LoginError(f"the identity provider answered {error or f'HTTP {status}'}")
    raise LoginError("the code expired: run `stardelt login` again")


def fresh_token(environ, session, clock=time.time):
    """The stored access token, renewed first when it has less than a minute left."""
    creds = load(environ)
    if creds is None:
        raise LoginError("not signed in: run `stardelt login` or set STARDELT_TOKEN")
    if creds.get("expires_at", 0) - clock() > SKEW:
        return creds["access_token"]
    if not creds.get("refresh_token"):
        raise LoginError("the sign-in has expired: run `stardelt login` again")
    status, body = _post(
        session,
        creds["token_endpoint"],
        {"grant_type": "refresh_token", "refresh_token": creds["refresh_token"], "client_id": creds["client_id"]},
    )
    if status != 200 or "access_token" not in body:
        raise LoginError("the sign-in has ended: run `stardelt login` again")
    renewed = _creds(creds["issuer"], creds["client_id"], creds["token_endpoint"], body, clock(), creds)
    renewed["host"] = creds.get("host")
    save(environ, renewed)
    return renewed["access_token"]
