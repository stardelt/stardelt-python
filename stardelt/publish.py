"""Publish a notebook to Nova, from a notebook (platform#291).

    import stardelt

    stardelt.publish("my.ipynb")                       # your only group
    stardelt.publish("my.ipynb", group="analytics")    # when you are in several

A publish is one ``PUT /api/notebooks/<group>/<name>`` to Nova, signed with the
same token the warehouse calls use (``TRINO_TOKEN``, copied into the pod by the
hub). Nova strips every cell's output and stores one immutable object version,
so a schedule can pin exactly what ran. The person, not the notebook, decides
what may be written: Nova refuses a group the token is not a member of.

Identity comes from the pod, never from an argument. The group defaults to the
person's only group from the token's ``groups`` claim; with none or several,
the call names them and asks for ``group=``. The name defaults to the file's
base name. The result is a small dict — ``name``, ``group``, ``version``,
``sha256``, ``parameters`` — and is printed for the notebook cell.

Nova is reached through its public URL (``STARDELT_NOVA_URL``): the single-user
network policy blocks in-cluster ranges. That URL's certificate is a public
one, while ``REQUESTS_CA_BUNDLE`` in the pod is the warehouse's internal CA, so
this call verifies against the public trust store (``certifi``) explicitly.
The token is never printed, returned or put in an error message.

SPDX-License-Identifier: MIT
"""

import base64
import json
import os
from urllib.parse import quote

import certifi
import requests

# Nova's own limit (`MAX_NOTEBOOK_BYTES` in backend/src/notebooks.rs): a body
# over this is refused there, so refuse it here first — nothing is sent.
MAX_PUBLISH_BYTES = 5 * 1024 * 1024

TIMEOUT_SECONDS = 60


def _one_line(text):
    lines = str(text).strip().splitlines()
    return lines[0] if lines else ""


def _redact(text, secret):
    """Remove a credential from a message before it can reach a notebook or a log."""
    return text.replace(secret, "<token>") if secret else text


def _claims(token):
    """The claims inside the JWT, without verifying it (Nova verifies)."""
    if not token or token.count(".") != 2:
        return {}
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, UnicodeDecodeError):
        return {}


def _groups(token):
    """The person's groups from the token, as plain names (no Keycloak path)."""
    claims = _claims(token)
    raw = claims.get("groups")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    groups = []
    for group in raw:
        if not isinstance(group, str):
            continue
        group = group.strip().lstrip("/")
        if group and group not in groups:
            groups.append(group)
    return groups


def _choose_group(token, group):
    """The group to publish to: the named one, or the token's only one."""
    if group is not None:
        group = str(group).strip()
        if not group:
            raise ValueError("stardelt.publish: group must not be empty.")
        return group
    groups = _groups(token)
    if len(groups) == 1:
        return groups[0]
    listed = ", ".join(groups) if groups else "none"
    if groups:
        raise ValueError(
            f"stardelt.publish: you are in more than one group ({listed}) — "
            "pass group= with the one this notebook belongs to."
        )
    raise ValueError(
        "stardelt.publish: the token carries no groups — pass group= with the "
        "one this notebook belongs to."
    )


def publish(path, group=None, name=None):
    """Publish the notebook at ``path`` to Nova, return what was stored.

    ``group`` defaults to the person's only group from the token and is
    required when the token carries none or several (``ValueError`` lists
    them). ``name`` defaults to the file's base name. The file is sent as-is:
    Nova validates it as an nbformat 4 notebook, strips outputs and returns
    the stored version. Files larger than 5 MiB are refused before sending.

    The returned dict has ``name``, ``group``, ``version`` (the immutable
    object version a schedule pins), ``sha256`` (of the stored bytes) and the
    parsed ``parameters``; it is printed too. Every failure is one line, with
    Nova's own sentence when the server answered.
    """
    from stardelt import _token  # the refreshed token sql() uses; deferred to avoid a cycle

    base = os.environ.get("STARDELT_NOVA_URL", "").strip().rstrip("/")
    if not base:
        raise RuntimeError(
            "STARDELT_NOVA_URL is not set — this notebook is not running on the platform."
        )

    token = _token()
    group = _choose_group(token, group)
    filename = os.path.basename(os.fspath(path))
    name = str(name).strip() if name is not None else filename
    if not name:
        raise ValueError("stardelt.publish: name must not be empty.")

    with open(path, "rb") as handle:
        body = handle.read()
    if len(body) > MAX_PUBLISH_BYTES:
        raise ValueError(
            f"stardelt.publish: {filename} is {len(body) / (1024 * 1024):.1f} MiB — "
            "Nova accepts at most 5 MiB; split the notebook or leave data out of it."
        )

    url = f"{base}/api/notebooks/{quote(group, safe='')}/{quote(name, safe='')}"
    try:
        response = requests.put(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=TIMEOUT_SECONDS,
            # The pod's REQUESTS_CA_BUNDLE is the warehouse's internal CA, which
            # did not sign Nova's public certificate: use the public roots.
            verify=certifi.where(),
        )
    except requests.RequestException as exc:
        message = f"Nova is not reachable ({type(exc).__name__})"
        raise RuntimeError(f"stardelt.publish failed: {_redact(_one_line(message), token)}") from None

    if response.status_code != 200:
        try:
            sentence = response.json().get("error")
        except ValueError:
            sentence = None
        sentence = _one_line(sentence) if sentence else f"HTTP {response.status_code}"
        raise RuntimeError(f"stardelt.publish failed: {_redact(sentence, token)}")

    try:
        stored = response.json()
    except ValueError:
        raise RuntimeError("stardelt.publish failed: Nova answered without a JSON body") from None

    result = {
        "name": stored.get("name", name),
        "group": stored.get("group", group),
        "version": stored.get("objectVersion", ""),
        "sha256": stored.get("sha256", ""),
        "parameters": stored.get("parameters", []),
    }
    print(result)
    return result
