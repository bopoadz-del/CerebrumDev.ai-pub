"""Image self-sufficiency: does the image the product's Dockerfile builds carry
what the app loads at runtime?

Live 2026-10-08 (cycle 2 smoke B, build/plt_5ac16f50c9384536): the writer's
Dockerfile copied only app/ and alembic/, so the image had no vendor/ -- the
tree app/dispatch.py loads every capability's block from -- and every
capability answered BlockNotVendored. A gate that judged the checkout certified
it 22/22. The writer's own pass must catch that shape first; the gate second.

The live Factory has no Docker, so the image is EMULATED: the build context
(the product checkout, minus .dockerignore) is laid out exactly as the final
stage's COPY/ADD instructions place it, under the final WORKDIR, in a temp
directory. Then, in a subprocess rooted there with an empty environment --
the same import the product's own CI runs (``env -i python -c "import
app.main"``) -- the app is imported and one block from the product's
blocks.lock.json is loaded -- every one it locks -- through the product's own loader. A failure that
names a path inside the emulated image is reported as ``image missing
<path>``.

Generic: nothing here knows a product, a block or a directory name. What is
copied comes from the Dockerfile, what is ignored from .dockerignore, which
blocks to load from blocks.lock.json (data), and the loader is the one every
Factory-rendered product carries (``app.dispatch.load_block``).

Not judgeable -- and never a failure -- when there is no Dockerfile, no locked
block, a COPY the emulation cannot follow (``--from`` another stage), or an
import that fails on a third-party package the Factory's interpreter lacks:
those are not questions about the image's contents.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple

from app.factory.build.deploy_time_settings import SNIPPET as _DEPLOY_TIME_SNIPPET

DOCKERFILE = "Dockerfile"
DOCKERIGNORE = ".dockerignore"
LOCKFILE = "blocks.lock.json"
#: The loader every Factory-rendered product carries (app/dispatch.py).
LOADER_MODULE = "app.dispatch"
LOADER_FUNCTION = "load_block"
APP_MODULE = "app.main"
PROBE_TIMEOUT_S = 120
#: What an interpreter needs from the OS just to start (Windows: without
#: SYSTEMROOT the socket layer cannot initialise, WinError 10106). Platform
#: plumbing only -- the probe never sees the Factory's own configuration.
_OS_STARTUP_VARS = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")

#: The probe reports structurally (one JSON line): which modules could not be
#: imported (``ModuleNotFoundError.name``), and each failure's traceback, whose
#: file paths are compared with the emulated image -- never error wording.
PROBE_RESULT = "IMAGE_PROBE "

#: The environment the Store gate runs the image with (cerebrum-builds
#: store-gate.yml ``docker run -e``). The probe boots the image the way the
#: gate does: a product that correctly refuses to start without its deploy
#: token is not an image that cannot load the app (live cycle 4 fintech:
#: "ConfigError: PLATFORM_TOKEN is required" judged as a missing image path).
GATE_RUN_ENV = {"PLATFORM_TOKEN": "dev-local-token"}

# Settings the operator supplies at deploy time are stood in for exactly as
# the Factory's harness and acceptance script do (deploy_time_settings, one
# source) -- BEFORE the audit hook, so the stand-in parsing app/*.py is never
# mistaken for the loader opening a block.
_PROBE = _DEPLOY_TIME_SNIPPET + f"""
_stand_in_for_deploy_time_settings(".")
import importlib, json, sys, traceback
result = {{"modules": [], "traces": [], "loaded": 0, "opened": {{}}}}
_opened = []
def _audit(event, args):
    if event == "open" and args and isinstance(args[0], str):
        _opened.append(args[0])
sys.addaudithook(_audit)
def note(exc):
    if isinstance(exc, ModuleNotFoundError) and exc.name:
        result["modules"].append(exc.name)
    result["traces"].append(traceback.format_exc())
try:
    importlib.import_module({APP_MODULE!r})
    load = getattr(importlib.import_module({LOADER_MODULE!r}), {LOADER_FUNCTION!r})
except Exception as exc:
    note(exc)
else:
    for block_id in json.loads(sys.argv[1]):
        try:
            load(block_id)
        except Exception as exc:
            note(exc)
        else:
            result["loaded"] += 1
            result["opened"][block_id] = True
result["files"] = sorted(set(_opened))
print({PROBE_RESULT!r} + json.dumps(result))
"""


@dataclass
class Verdict:
    ok: bool
    judged: bool
    detail: str
    missing: List[str] = field(default_factory=list)
    #: Locked blocks the loader took from somewhere other than their locked
    #: path (a silent local fallback), one finding each.
    misloaded: List[str] = field(default_factory=list)
    #: Per missing image path, what the build context says about it (the
    #: context file it would come from, and the .dockerignore rule that
    #: excludes it, if one does) -- so the rework names the line to change.
    causes: Dict[str, str] = field(default_factory=dict)


# -- .dockerignore -------------------------------------------------------------------


def read_dockerignore(root: Path) -> List[Tuple[bool, str]]:
    """``(negated, pattern)`` per rule, in order (Docker's own semantics: the
    last matching rule decides, ``!`` re-includes)."""
    path = root / DOCKERIGNORE
    rules: List[Tuple[bool, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rules
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:].strip() if negated else line
        pattern = pattern.strip("/")
        if pattern.startswith("./"):
            pattern = pattern[2:]
        if pattern:
            rules.append((negated, pattern))
    return rules


def _matches(rel: str, pattern: str) -> bool:
    if "**" in pattern:
        regex = "^" + re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*")
        regex = regex.replace(r"\*", "[^/]*").replace(r"\?", "[^/]") + "$"
        return re.match(regex, rel) is not None
    return fnmatch.fnmatchcase(rel, pattern)


def ignored(rel: str, rules: Sequence[Tuple[bool, str]]) -> bool:
    """Whether ``rel`` (context-relative, posix) is excluded. A rule that
    matches a parent directory excludes everything under it."""
    parts = PurePosixPath(rel).parts
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    verdict = False
    for negated, pattern in rules:
        if any(_matches(p, pattern) for p in prefixes):
            verdict = not negated
    return verdict


# -- the emulated image ---------------------------------------------------------------


def _final_stage(instructions: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
    last = 0
    for i, (word, _args) in enumerate(instructions):
        if word == "FROM":
            last = i
    return list(instructions[last:])


def _copy_args(args: str) -> Tuple[List[str], Optional[str], bool]:
    """``(sources, dest, followable)`` of a COPY/ADD."""
    text = args.strip()
    if text.startswith("["):
        try:
            tokens = [str(t) for t in json.loads(text)]
        except ValueError:
            return [], None, False
    else:
        try:
            tokens = shlex.split(text)
        except ValueError:
            tokens = text.split()
    followable = True
    plain: List[str] = []
    for token in tokens:
        if token.startswith("--"):
            if token.startswith("--from"):
                followable = False
            continue
        plain.append(token)
    if len(plain) < 2:
        return [], None, False
    return plain[:-1], plain[-1], followable


def _context_files(root: Path, rules: Sequence[Tuple[bool, str]]) -> List[str]:
    out: List[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(root).parts:
            continue
        rel = path.relative_to(root).as_posix()
        if rel in (DOCKERFILE,) or not ignored(rel, rules):
            out.append(rel)
    return out


def emulate_image(root: Path, image: Path) -> Tuple[str, List[str]]:
    """Lay out the final stage's COPY/ADD of ``root`` under ``image``.

    Returns ``(workdir, unfollowable)``: the final WORKDIR (an image path) and
    the COPY lines the emulation could not follow."""
    from app.factory.build.supply_chain import dockerfile_instructions

    text = (root / DOCKERFILE).read_text(encoding="utf-8")
    rules = read_dockerignore(root)
    files = _context_files(root, rules)
    workdir = "/"
    unfollowable: List[str] = []
    for word, args in _final_stage(dockerfile_instructions(text)):
        if word == "WORKDIR":
            target = args.strip().strip('"')
            workdir = str(PurePosixPath(workdir) / target) if not target.startswith("/") else target
            (image / workdir.lstrip("/")).mkdir(parents=True, exist_ok=True)
            continue
        if word not in ("COPY", "ADD"):
            continue
        sources, dest, followable = _copy_args(args)
        if not followable or dest is None:
            unfollowable.append(f"{word} {args}")
            continue
        dest_abs = dest if dest.startswith("/") else str(PurePosixPath(workdir) / dest)
        # Docker copies INTO the destination when it ends in "/", names "." /
        # "..", already exists as a directory, or receives several sources.
        dest_is_dir = (
            dest.endswith("/") or dest in (".", "..") or len(sources) > 1
            or (image / dest_abs.lstrip("/")).is_dir()
        )
        for src in sources:
            src_norm = src.strip("/") if src not in (".", "./") else ""
            if src_norm.startswith("./"):
                src_norm = src_norm[2:]
            matched = [
                f for f in files
                if src_norm == "" or f == src_norm or f.startswith(src_norm + "/")
                or (any(c in src_norm for c in "*?[") and (
                    fnmatch.fnmatchcase(f, src_norm)
                    or any(fnmatch.fnmatchcase(p, src_norm) for p in _parents(f))
                ))
            ]
            for rel in matched:
                if src_norm == "":
                    sub = rel
                elif rel == src_norm and not (root / src_norm).is_dir():
                    sub = PurePosixPath(rel).name if dest_is_dir else ""
                else:
                    base = src_norm if not any(c in src_norm for c in "*?[") else _glob_base(rel, src_norm)
                    sub = rel[len(base):].lstrip("/") if rel.startswith(base) else PurePosixPath(rel).name
                target = PurePosixPath(dest_abs) / sub if sub else PurePosixPath(dest_abs)
                out = image / str(target).lstrip("/")
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / rel, out)
    return workdir, unfollowable


def _parents(rel: str) -> List[str]:
    parts = PurePosixPath(rel).parts
    return ["/".join(parts[: i + 1]) for i in range(len(parts) - 1)]


def _glob_base(rel: str, pattern: str) -> str:
    """The matched prefix of a glob source: the directory the glob names."""
    for prefix in _parents(rel) + [rel]:
        if fnmatch.fnmatchcase(prefix, pattern):
            return str(PurePosixPath(prefix).parent) if prefix == rel else prefix
    return ""


# -- the probe ------------------------------------------------------------------------


def locked_blocks(root: Path) -> List[str]:
    """Every block the product locked (blocks.lock.json, data), in its order.
    The image must carry all of them: one block loading proves nothing about
    the others (live: smoke B loaded one block from a local fallback while its
    storage block was absent)."""
    try:
        data = json.loads((root / LOCKFILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if isinstance(blocks, dict):
        ids = list(blocks)
    elif isinstance(blocks, list):
        ids = [str(b.get("id") or "") for b in blocks if isinstance(b, dict)]
    else:
        ids = []
    return [i for i in ids if i]


def locked_paths(root: Path) -> Dict[str, str]:
    """``{block id: its locked path}`` for every lock entry that declares one."""
    try:
        data = json.loads((root / LOCKFILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if isinstance(blocks, dict):
        entries = [(k, v) for k, v in blocks.items() if isinstance(v, dict)]
    elif isinstance(blocks, list):
        entries = [(str(b.get("id") or ""), b) for b in blocks if isinstance(b, dict)]
    else:
        entries = []
    return {
        bid: str(entry["path"]).replace("\\", "/").strip("/")
        for bid, entry in entries
        if bid and isinstance(entry.get("path"), str) and entry["path"].strip("/")
    }


def runtime_paths(root: Path | str) -> List[str]:
    """The paths the app's loader reads at runtime that the product tree
    carries: every locked block's path (blocks.lock.json, data), sorted.

    The writer is told these before it writes a Dockerfile. It works in a
    staging checkout that does not show the CLONER's tree, and two writers
    (cycles 8 and 9) built images without it because nothing named it."""
    root = Path(root)
    return sorted({rel for rel in locked_paths(root).values() if (root / rel).exists()})


def locked_entries(root: Path | str) -> Dict[str, List[str]]:
    """``{block id: the module files directly under its locked path}``, read
    off the product tree (the lock and the files it points at -- data).

    What the app's loader must open for each locked block: the writer is told
    these, because the WRITER gate refuses a block whose load did not read
    under its locked path. A locked path the tree does not carry (or that
    holds no module file) is named as the path itself."""
    root = Path(root)
    out: Dict[str, List[str]] = {}
    for bid, rel in sorted(locked_paths(root).items()):
        base = root / rel
        modules = sorted(p.name for p in base.glob("*.py") if p.is_file()) if base.is_dir() else []
        out[bid] = [f"{rel}/{name}" for name in modules] or [rel]
    return out


def _misloaded(result: dict, image: Path, root: Path) -> List[str]:
    """A locked block that LOADED counts as loaded from its lock only when the
    run opened a file under its locked path (at start-up or on the call --
    a loader may load every block when it is imported); otherwise it came
    from somewhere else, a silent fallback. A block that failed to load is a
    missing path, judged apart."""
    paths = locked_paths(root)
    loaded = result.get("opened") or {}
    files = [_in_image(f, image).replace("\\", "/") for f in result.get("files") or []]
    out: List[str] = []
    for bid, rel in paths.items():
        if bid not in loaded:
            continue
        if any(f"/{rel}/" in f or f.endswith(f"/{rel}") for f in files):
            continue
        tail = f"/{bid}.py"
        source = next((f for f in files if f.endswith(tail) or f"/{bid}/" in f), "a path outside the lock")
        out.append(f"block {bid} loaded from {source}, not its locked path {rel}")
    return out


def _probe_result(out: str) -> Optional[dict]:
    for line in reversed(out.splitlines()):
        if line.startswith(PROBE_RESULT):
            try:
                return json.loads(line[len(PROBE_RESULT):])
            except ValueError:
                return None
    return None


def _missing_paths(result: dict, image: Path, root: Path) -> Tuple[List[str], bool]:
    """``(image paths the failures name, judgeable)``."""
    base = str(image).rstrip("/")
    missing: List[str] = []
    for trace in result.get("traces") or []:
        for match in re.finditer(re.escape(base) + r"(/[^\s'\")]+)", trace):
            path = match.group(1).rstrip(".,:;")
            # Traceback frames name files the image DOES carry; only an
            # absent path is a missing one.
            if (image / path.lstrip("/")).exists() or path in missing:
                continue
            missing.append(path)
    judgeable = True
    for module in result.get("modules") or []:
        top = module.split(".")[0]
        if (root / top).exists() or (root / f"{top}.py").exists():
            path = "/" + module.replace(".", "/")
            if path not in missing:
                missing.append(path)
        else:
            # A third-party package the Factory's interpreter lacks: not a
            # question about what the image carries.
            judgeable = False
    return missing, judgeable or bool(missing)


def context_cause(path: str, workdir: str, root: Path, rules: Sequence[Tuple[bool, str]]) -> str:
    """Why an image path under the final WORKDIR is absent, read off the build
    context: the context file it would be copied from, and whether
    .dockerignore excludes it or no COPY/ADD of the final stage places it."""
    base = str(PurePosixPath(workdir)).rstrip("/") + "/"
    if not path.startswith(base):
        return ""
    rel = path[len(base):]
    if not (root / rel).is_file():
        return ""
    parts = PurePosixPath(rel).parts
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    excluding = ""
    for negated, pattern in rules:
        if any(_matches(p, pattern) for p in prefixes):
            excluding = "" if negated else pattern
    # The build context is the product tree the writer's pass is merged into,
    # not the writer's staging checkout: say so, or a writer that cannot see
    # the path reads "the build context has it" as untrue (cycle 9 smoke A).
    context = (
        "the build context (the product tree your pass is merged into, which "
        "carries it even when your checkout does not)"
    )
    if excluding:
        return f"{context} has {rel}, but .dockerignore rule {excluding!r} excludes it"
    return (
        f"{context} has {rel}, but no COPY/ADD in the Dockerfile's final stage "
        f"puts {parts[0]}/ under {workdir}"
    )


def _probe_env(cwd: Path) -> dict:
    env = {k: os.environ[k] for k in _OS_STARTUP_VARS if os.environ.get(k)}
    env.update(GATE_RUN_ENV)
    env.update({"PYTHONPATH": str(cwd), "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def _in_image(text: str, image: Path) -> str:
    """``text`` with the scratch directory mapped back to image paths: a
    verdict lands in the build ledger, which must read the same every run."""
    return text.replace(str(image), "")


def check(root: Path | str, *, python: str = sys.executable, timeout_s: int = PROBE_TIMEOUT_S) -> Verdict:
    """Import the app and load every locked block inside the emulated image."""
    root = Path(root)
    if not (root / DOCKERFILE).is_file():
        return Verdict(ok=True, judged=False, detail="no Dockerfile: image not judgeable")
    blocks = locked_blocks(root)
    if not blocks:
        return Verdict(ok=True, judged=False, detail="no locked block: nothing to load")
    with tempfile.TemporaryDirectory(prefix="image-emulation-") as tmp:
        image = Path(tmp)
        try:
            workdir, unfollowable = emulate_image(root, image)
        except (OSError, ValueError) as exc:
            reason = _in_image(str(exc), image)
            return Verdict(ok=True, judged=False, detail=f"Dockerfile not emulatable: {reason}")
        if unfollowable:
            return Verdict(
                ok=True, judged=False,
                detail="COPY the emulation cannot follow: " + "; ".join(unfollowable[:3]),
            )
        cwd = image / workdir.lstrip("/")
        env = _probe_env(cwd)
        try:
            proc = subprocess.run(
                [python, "-c", _PROBE, json.dumps(blocks)],
                cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return Verdict(ok=True, judged=False, detail=f"probe timed out after {timeout_s}s")
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        result = _probe_result(proc.stdout or "")
        if result is None:
            last = next((ln for ln in reversed(out.strip().splitlines()) if ln.strip()), "")
            return Verdict(ok=False, judged=True, detail=f"image cannot run the app: {_in_image(last, image)[:300]}")
        misloaded = _misloaded(result, image, root)
        if misloaded:
            return Verdict(
                ok=False, judged=True, misloaded=misloaded, detail="; ".join(misloaded),
            )
        if not result.get("traces"):
            return Verdict(
                ok=True, judged=True,
                detail=f"image imports the app and loads all {len(blocks)} locked blocks from their locked paths",
            )
        missing, judgeable = _missing_paths(result, image, root)
        if not judgeable:
            return Verdict(ok=True, judged=False, detail="not judgeable here: a third-party package is not installed")
        if missing:
            rules = read_dockerignore(root)
            causes = {p: c for p in missing if (c := context_cause(p, workdir, root, rules))}
            return Verdict(
                ok=False, judged=True, missing=missing,
                detail="; ".join(f"image missing {p}" for p in missing),
                causes=causes,
            )
        last = (result["traces"][-1].strip().splitlines() or [""])[-1]
        return Verdict(ok=False, judged=True, detail=f"image cannot load the app: {_in_image(last, image)[:300]}")
