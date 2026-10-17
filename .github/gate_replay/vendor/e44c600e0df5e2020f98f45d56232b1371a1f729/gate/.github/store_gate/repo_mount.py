"""The product's checkout, mounted read-only into the acceptance container.

``python3 repo_mount.py <harness> <checkout>`` prints the ``docker run``
arguments that put the checkout inside the image and tell the harness where.

The harness runs INSIDE the product's image, but some of its checks judge the
repository, not the running service: the floor declares their subject as a
file in the tree (``tree:<path>``) or the Factory's record. The image carries
only what the product's .dockerignore lets in, and leaving ``.github/`` out of
a served artifact is the product's call -- live 2026-10-07 (smoke B,
build/plt_9488d97f22f04dee) the gate failed ``ci_present_and_full_suite`` with
".github/workflows/ci.yml missing" on a branch whose CI was green.

The env variable's NAME is the harness's own ``REPO_ROOT_ENV`` constant (the
Factory renders it), read here from the syntax tree: the gate names nothing
itself. A harness that declares none (stamped by an older Factory) is told
nothing and reads the image as before.

The harness ITSELF is a repository artifact too. Live 2026-10-08 (cycle 2
smoke A, build/plt_28405ae4961d420b, run 37760007852): "python3: can't open
file '/app/scripts/acceptance.py'" -- the writer's Dockerfile copies only
``app/`` and the migrations, a minimal production image (its call), and the
gate assumed the Factory's harness was inside it. So the checkout is ALWAYS
mounted, and the harness runs from there:

* ``exec-path`` -- where to run it: from the mount when the harness declares
  ``RUNTIME_ROOT_ENV`` (it can be told where the image's runtime tree is), or
  when the image does not carry it; an older harness the image carries runs in
  the image as it always did.
* ``exec-env`` -- the ``RUNTIME_ROOT_ENV=<container working directory>``
  argument, so runtime checks and the served ``app.main`` judge the IMAGE.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: Where the checkout appears inside the container. The gate's own choice; the
#: harness is told it through REPO_ROOT_ENV, never assumes it.
MOUNT_POINT = "/repository"


def _harness_constant(harness: Path, name: str) -> str:
    """The harness's literal module-level string ``name``, or ''."""
    try:
        tree = ast.parse(Path(harness).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return ""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value.strip()
    return ""


def repo_root_env(harness: Path) -> str:
    """The harness's literal module-level ``REPO_ROOT_ENV``, or ''."""
    return _harness_constant(harness, "REPO_ROOT_ENV")


def runtime_root_env(harness: Path) -> str:
    """The harness's literal module-level ``RUNTIME_ROOT_ENV``, or ''."""
    return _harness_constant(harness, "RUNTIME_ROOT_ENV")


def docker_args(harness: Path, checkout: Path) -> list:
    """``docker run`` arguments: the checkout, read-only, always; the
    harness's REPO_ROOT_ENV pointing at it when the harness declares one."""
    source = str(Path(checkout).resolve())
    args = ["-v", f"{source}:{MOUNT_POINT}:ro"]
    name = repo_root_env(harness)
    if name:
        args += ["-e", f"{name}={MOUNT_POINT}"]
    return args


def exec_env(harness: Path, workdir: str) -> list:
    """``docker exec`` arguments telling a mount-run harness where the image's
    runtime tree is. Nothing when the harness cannot be told, or the
    container's working directory is unknown."""
    name = runtime_root_env(harness)
    workdir = (workdir or "").strip()
    if not name or not workdir:
        return []
    return ["-e", f"{name}={workdir}"]


#: Where an older harness is copied INTO the container: one level under the
#: working directory, so its ``ROOT = Path(__file__).parents[1]`` IS the image's
#: runtime tree. Run from the mount it judged the checkout instead -- live
#: 2026-10-08 build/plt_5ac16f50c9384536 certified 22/22 an image that carries
#: no vendor/ (its storage block unloadable) because the checkout does.
STAGED_DIR = ".store-gate-harness"


def exec_path(harness: Path, harness_rel: str, image_has_harness: bool, workdir: str = "") -> str:
    """The path ``python3`` runs inside the container.

    A harness that declares RUNTIME_ROOT_ENV runs from the mount and is told
    the runtime tree. An older one judges the tree it sits in, so it must sit
    in the image: its own copy when the image carries it, else a copy staged
    under the working directory (``needs_staging``). With no working directory
    known nothing can judge the image: the in-image path is returned and the
    run fails closed ("no score", Factory-owed) -- never the checkout."""
    if runtime_root_env(harness):
        return f"{MOUNT_POINT}/{harness_rel.lstrip('/')}"
    workdir = (workdir or "").strip().rstrip("/")
    if image_has_harness or not workdir:
        return harness_rel
    return f"{workdir}/{STAGED_DIR}/{Path(harness_rel).name}"


def needs_staging(path: str, harness_rel: str) -> bool:
    """True when ``exec_path`` chose a staged in-container copy."""
    return f"/{STAGED_DIR}/" in path and path != harness_rel


def main(argv: list) -> int:
    if len(argv) >= 4 and argv[1] == "exec-env":
        print(" ".join(exec_env(Path(argv[2]), argv[3])))
        return 0
    if len(argv) >= 4 and argv[1] == "exec-path":
        # argv[2] is the harness's path relative to the checkout -- the same
        # relative path inside the image and under the mount. argv[4] is the
        # container's working directory (where a staged copy goes).
        workdir = argv[4] if len(argv) >= 5 else ""
        path = exec_path(Path(argv[2]), argv[2], argv[3].strip() == "1", workdir)
        if path.startswith(MOUNT_POINT):
            where = "the mounted checkout (told the image's runtime tree)"
        elif needs_staging(path, argv[2]):
            where = "a copy staged inside the image's runtime tree"
        else:
            where = "the image"
        print(f"the harness runs from {where}: {path}", file=sys.stderr)
        print(path)
        return 0
    if len(argv) < 3:
        print(
            "usage: repo_mount.py <harness> <checkout> | exec-env <harness> <workdir>"
            " | exec-path <harness_rel> <image_has_harness 0|1> [workdir]",
            file=sys.stderr,
        )
        return 2
    args = docker_args(Path(argv[1]), Path(argv[2]))
    print(f"checkout mounted read-only at {MOUNT_POINT}", file=sys.stderr)
    print(" ".join(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
