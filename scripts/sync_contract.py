#!/usr/bin/env python3
"""Refresh ``stardelt/operations.json`` from Nova's OpenAPI contract.

    python scripts/sync_contract.py                       # the contract on stardelt-nova's main (needs `gh`)
    python scripts/sync_contract.py --contract nova.json  # a local copy

The file lists the ``public`` operations by ``operationId``: method, path, the names of the
path and query parameters, and whether a JSON body is expected. ``stardelt.api.Client.call``
and ``stardelt api`` read it, so the SDK and the command line cover every public operation
the moment this file is refreshed. Run it again whenever the contract changes.
"""

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "stardelt" / "operations.json"


def fetch_main_contract():
    done = subprocess.run(
        ["gh", "api", "repos/stardelt/stardelt-nova/contents/openapi/nova.json", "--jq", ".content"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(base64.b64decode(done.stdout))


def operations_of(contract):
    operations = {}
    for path, item in contract["paths"].items():
        for method, op in item.items():
            if "public" not in op.get("tags", []):
                continue
            params = op.get("parameters", [])
            operations[op["operationId"]] = {
                "method": method.upper(),
                "path": path,
                "path_params": [p["name"] for p in params if p["in"] == "path"],
                "query_params": [p["name"] for p in params if p["in"] == "query"],
                "body": "requestBody" in op,
            }
    return {
        "contract_version": contract["info"]["version"],
        "operations": dict(sorted(operations.items())),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--contract", help="a local nova.json (default: stardelt-nova main, via gh)")
    parser.add_argument("--out", default=str(OUT), help="where to write (default: stardelt/operations.json)")
    args = parser.parse_args(argv)
    contract = json.loads(Path(args.contract).read_text()) if args.contract else fetch_main_contract()
    text = json.dumps(operations_of(contract), indent=2) + "\n"
    Path(args.out).write_text(text)
    print(f"wrote {args.out}: {len(json.loads(text)['operations'])} operations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
