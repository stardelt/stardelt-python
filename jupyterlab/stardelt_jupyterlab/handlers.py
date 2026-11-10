"""Server side of the notebook **Publish** button (nova#520).

The browser never holds a token. The frontend asks these two handlers what the
publish dialog should offer, then asks for the publish itself; each handler
calls the same ``stardelt.publish`` a notebook cell calls, inside the pod where
the token of the signed-in person already lives. Nothing here reads, copies or
returns the token: only the groups the dialog lists, and the stored version
Nova answers with.

SPDX-License-Identifier: MIT
"""

import asyncio
import os
import subprocess

from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import url_path_join
from tornado import web

# The line the dialog shows when the caller is in exactly one group: a schedule
# of this notebook runs as that group's principal, not as the person who saved.
PIPELINE_PRINCIPAL = "pipeline-{}"


def resolve(root, path):
    """The absolute path of ``path``, refused unless it is a notebook under ``root``.

    ``root`` is the single-user server's root directory; ``path`` is the
    notebook path the frontend sends, relative to it. Both sides are resolved
    through symlinks first, so ``..`` cannot climb out of the workspace.
    """
    file = os.path.realpath(os.path.join(root, path))
    if not file.startswith(os.path.realpath(root) + os.sep) or not file.endswith(".ipynb"):
        raise web.HTTPError(400, "not a notebook inside the workspace root")
    return file


def _uncommitted(file):
    """True when git lists the notebook as changed; false when git is absent or fails."""
    try:
        result = subprocess.run(
            ["git", "-C", os.path.dirname(file), "status", "--porcelain", "--", file],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


class _StardeltPublishHandler(APIHandler):
    """Shared reading of the workspace root and the lazily imported library."""

    def _publish_names(self):
        """The library's ``publish`` and group helpers, or ``None`` when it is absent.

        ``stardelt.publish`` is the function, not the module (``stardelt/
        __init__.py`` binds it over the submodule), so the names are imported
        from the module path, as ``stardelt/cli.py`` does.
        """
        try:
            import stardelt
            from stardelt.publish import _groups, publish
        except Exception:
            return None
        return stardelt, _groups, publish

    def _not_installed(self):
        self.set_status(503)
        self.finish({"error": "the stardelt library is not installed in this image"})


class PublishInfoHandler(_StardeltPublishHandler):
    """What the publish dialog needs for one notebook: the groups, the name, the state."""

    @web.authenticated
    async def get(self):
        root = self.settings["stardelt_root_dir"]
        file = resolve(root, self.get_query_argument("path", ""))
        names = self._publish_names()
        if names is None:
            self._not_installed()
            return
        stardelt, _groups, _publish = names
        try:
            groups = _groups(stardelt._token())
        except (ValueError, RuntimeError) as err:
            # Same shape as PublishHandler.post(): the library's one-line
            # sentence (for example "TRINO_TOKEN is not set …") instead of
            # Tornado's generic 500 "Unhandled error".
            self.set_status(400)
            self.finish({"error": str(err)})
            return
        # A schedule runs as one principal; with none or several groups there is
        # no single one to name, and the dialog leaves the line out.
        run_as = PIPELINE_PRINCIPAL.format(groups[0]) if len(groups) == 1 else ""
        # git status runs in a thread too: the workspace may be a slow disk.
        uncommitted = await asyncio.to_thread(_uncommitted, file)
        self.finish(
            {
                "groups": groups,
                "name": os.path.basename(file),
                "uncommitted": uncommitted,
                "runAs": run_as,
            }
        )


class PublishHandler(_StardeltPublishHandler):
    """Publish one notebook: the same call a cell makes, signed in the same way."""

    @web.authenticated
    async def post(self):
        names = self._publish_names()
        if names is None:
            self._not_installed()
            return
        _stardelt, _groups, publish = names
        root = self.settings["stardelt_root_dir"]
        body = self.get_json_body() or {}
        path = body.get("path", "")
        if not isinstance(path, str):
            raise web.HTTPError(400, "path must be a string")
        file = resolve(root, path)
        group = body.get("group") or None
        name = body.get("name") or None
        try:
            # publish() speaks to Nova with a blocking requests call; keep the
            # server's event loop free while it waits.
            result = await asyncio.to_thread(publish, file, group=group, name=name)
        except (ValueError, RuntimeError) as err:
            self.set_status(400)
            self.finish({"error": str(err)})
            return
        self.finish(result)


def handlers(base_url):
    """The two routes, joined onto the server's base URL."""
    return [
        (url_path_join(base_url, "stardelt", "publish-info"), PublishInfoHandler),
        (url_path_join(base_url, "stardelt", "publish"), PublishHandler),
    ]
