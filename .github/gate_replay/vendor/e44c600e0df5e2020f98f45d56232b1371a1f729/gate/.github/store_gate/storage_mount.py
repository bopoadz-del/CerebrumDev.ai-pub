"""Where the gate mounts the product's persistence: where the IMAGE declares it.

``docker image inspect <image> | python3 storage_mount.py <volume>`` prints the
``docker run`` arguments for the persistence volume.

The image's own ``STORAGE_PATH`` (its Dockerfile ENV) is the declaration. A
named volume mounted over a directory the image has is initialised from that
directory -- ownership included -- so a product that created and owned its
root at build time (the floor's single_persistence_root contract) boots on a
fresh volume. Overriding the path with one the image never declared hands the
service a root-owned mount it cannot write, and the product dies at boot for
the gate's assumption, not its own defect (live 2026-10-07,
build/plt_c0b1ad1f127e43f8).

Only an image that declares no root gets the gate's default, and then the gate
says where it put it (``-e STORAGE_PATH``) so the product reads the same path.
"""

from __future__ import annotations

import json
import posixpath
import sys
from typing import Any, NamedTuple

#: The root the gate uses only for an image that declares none.
GATE_DEFAULT_ROOT = "/app/storage"
STORAGE_ENV = "STORAGE_PATH"
#: Suffixes meaning STORAGE_PATH names the database FILE (app.db accepts it).
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")


class Mount(NamedTuple):
    path: str
    declared: bool


def _config(inspect: Any) -> dict:
    if isinstance(inspect, list) and inspect and isinstance(inspect[0], dict):
        cfg = inspect[0].get("Config")
        return cfg if isinstance(cfg, dict) else {}
    return {}


def resolve(inspect: Any) -> Mount:
    """The persistence directory to mount, and whether the image declared it."""
    cfg = _config(inspect)
    value = ""
    for entry in cfg.get("Env") or ():
        name, sep, rest = str(entry).partition("=")
        if sep and name == STORAGE_ENV:
            value = rest.strip()
    if not value:
        return Mount(path=GATE_DEFAULT_ROOT, declared=False)
    workdir = str(cfg.get("WorkingDir") or "/")
    path = value if posixpath.isabs(value) else posixpath.join(workdir, value)
    path = posixpath.normpath(path)
    if path.lower().endswith(_DB_SUFFIXES):
        path = posixpath.dirname(path) or "/"
    return Mount(path=path, declared=True)


def docker_args(mount: Mount, volume: str) -> list:
    args = []
    if not mount.declared:
        args += ["-e", f"{STORAGE_ENV}={mount.path}"]
    args += ["-v", f"{volume}:{mount.path}"]
    return args


def main(argv: list) -> int:
    volume = argv[1] if len(argv) > 1 else "storegate-storage"
    try:
        inspect = json.load(sys.stdin)
    except ValueError:
        inspect = []
    mount = resolve(inspect)
    print(
        f"storage mount: {mount.path} ({'declared by the image' if mount.declared else 'gate default: the image declares no STORAGE_PATH'})",
        file=sys.stderr,
    )
    print(" ".join(docker_args(mount, volume)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
