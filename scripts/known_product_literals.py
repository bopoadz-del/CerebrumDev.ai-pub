#!/usr/bin/env python3
"""Load, at CI time, every name that belongs to a product.

Nothing is listed here. The set is read from:

* the Store checkout (``CEREBRUM_BLOCKS_ROOT``): declared verticals and
  domain packs;
* the repo's golden blueprints;
* local build workspaces, when a sessions root is configured;
* every ``build/*`` branch of the cerebrum-builds repo: its MANIFEST product
  id and its product-dna blueprint (product id, name, vertical, capabilities).
  Branch names themselves carry session ids, which the hardwiring gate already
  refuses by shape.

The assembly rules (block ids excluded, multi-word only) live in
``backend/app/factory/build/product_literals.py`` so the brief lint and this
gate read one definition.

    python scripts/known_product_literals.py            # print the set
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import FrozenSet, Iterator, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.factory.build import product_literals as pl  # noqa: E402

BUILDS_REPO = os.environ.get("CEREBRUM_BUILDS_REPO", "bopoadz-del/cerebrum-builds")
_FILES = ("MANIFEST.json", "product-dna/product_blueprint.yaml", "product-dna/capability_resolution.json")


def _token() -> str:
    for key in ("CEREBRUM_BUILDS_TOKEN", "CROSS_REPO_PAT", "GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _get(url: str, token: str) -> Optional[bytes]:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError):
        return None


def build_branches(token: str) -> List[str]:
    out: List[str] = []
    page = 1
    while True:
        raw = _get(f"https://api.github.com/repos/{BUILDS_REPO}/branches?per_page=100&page={page}", token)
        if raw is None:
            raise RuntimeError(f"cannot list {BUILDS_REPO} branches (token missing or no access)")
        rows = json.loads(raw)
        out += [r["name"] for r in rows if str(r.get("name", "")).startswith("build/")]
        if len(rows) < 100:
            return out
        page += 1


def from_builds_repo(token: str) -> Iterator[str]:
    """Names recorded in every cerebrum-builds build branch."""
    import tempfile

    for branch in build_branches(token):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            for rel in _FILES:
                raw = _get(f"https://api.github.com/repos/{BUILDS_REPO}/contents/{rel}?ref={branch}", token)
                if not raw:
                    continue
                body = json.loads(raw).get("content")
                if not body:
                    continue
                dest = build / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(base64.b64decode(body))
            yield from pl.from_build_dir(build)


def load(*, require_remote: bool = False) -> FrozenSet[str]:
    store = os.environ.get("CEREBRUM_BLOCKS_ROOT")
    sessions = os.environ.get("FACTORY_SESSIONS_ROOT")
    extra: List[str] = []
    token = _token()
    # The builds-repo set is ADDITIVE: without a token the gate still runs on
    # the Store registry, the blueprints and local ledgers, and says so.
    try:
        extra = list(from_builds_repo(token))
    except RuntimeError as exc:
        if require_remote:
            raise
        print(f"known_product_literals: builds branches skipped ({exc})", file=sys.stderr)
    return pl.known_product_literals(
        store_root=Path(store) if store else None,
        sessions_root=Path(sessions) if sessions else None,
        blueprints_root=ROOT / "blueprints",
        extra=extra,
    )


def main() -> int:
    known = load()
    for name in sorted(known):
        print(name)
    print(f"\n{len(known)} known product literal(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
