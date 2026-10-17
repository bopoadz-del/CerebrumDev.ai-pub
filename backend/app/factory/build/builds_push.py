"""Push a Factory workspace to private cerebrum-builds (N1a).

Does not reimplement CLONER — copies whatever COLLECTOR/CLONER already
left in ``workspace`` (plus ``docs/coder_brief.md``). Live git+HTTPS
uses ``CEREBRUM_BUILDS_GITHUB_TOKEN``. Collect uses the GitHub API.

Scratch ``build/**`` branches are cut from the store's ``main`` so
``.github/workflows/store-gate.yml`` is on the pushed commit (Actions
``on.push`` reads the workflow from that commit).

TODO(N1c): secret scan of the pushed tree.
TODO(N1c): delete the scratch branch after collect.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final, List, Mapping, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

RECEIPT_NAMES = ("receipt.json", "docs/receipt.json", "docs/coder_receipt.json")

BUILDS_TOKEN_ENV: Final = "CEREBRUM_BUILDS_GITHUB_TOKEN"
BUILDS_REPO_ENV = "CEREBRUM_BUILDS_REPO"
DEFAULT_BUILDS_REPO = "bopoadz-del/cerebrum-builds"
GITHUB_API = "https://api.github.com"
# Compare 404 is usually missing head/seed after BA rename or GitHub ref lag —
# not an API outage. Retry briefly, then fail closed with a clear diagnosis.
COMPARE_404_RETRIES = 5
COMPARE_404_SLEEP_S = 2.0
GIT_NAME = "cerebrum-factory"
GIT_EMAIL = "factory@cerebrum.dev"

#: The Factory's own identifier formats. routers/sessions.py mints session
#: ids as SESSION_ID_PREFIX + hex; every build branch is BRANCH_PREFIX + the
#: session id + "-" + a tag (make_branch_name). Parsers below read these
#: shapes; nothing searches a message for words.
SESSION_ID_PREFIX = "sess_"
BRANCH_PREFIX = "build/"
#: Characters that wrap a pasted token (quotes, brackets, trailing
#: punctuation a phone keyboard adds) -- stripped from both ends.
_TOKEN_WRAPPERS = "\"'`()[]{}<>.,;:!?"
_SESSION_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


#: Files the factory keeps in a workspace for its OWN bookkeeping. They are
#: not the product and must not ship -- neither to cerebrum-builds nor in the
#: customer's zip. A delivered platform was found carrying the owner's
#: internal account id (.generation_quota_account) and the coding agent's
#: lock directory (.codewhale). build_ledger.jsonl itself stays: the receipt
#: and the authorship floor are verified against it.
FACTORY_INTERNAL_NAMES = frozenset(
    {
        ".codewhale",
        ".generation_quota_account",
        "build_ledger.jsonl.lock",
        # The Factory's own record of the build, not the product. The ledger
        # is 189 events and ~86KB of role transitions, gate verdicts and
        # agent narration on a live export. The customer gets the platform,
        # not the transcript of how it was made.
        "build_ledger.jsonl",
    }
)

#: How the platform was manufactured: the prompt the writer was given, the
#: brief it was compiled from, what the agent reported doing, and the
#: argv it was invoked with. Internal by nature -- a buyer's IT team has no
#: use for them, and they describe the Factory rather than the product.
#:
#: Exact paths, not names: ``build_provenance.json`` is distinctive, but
#: excluding by bare name is how an exclusion list starts eating a product's
#: own files later.
FACTORY_INTERNAL_PATHS = frozenset(
    {
        "product-dna/generation_manifest.json",
        "docs/writer_prompt.txt",
        "docs/coder_brief.md",
        "docs/coder_receipt.json",
        "docs/writer_argv.json",
        "docs/writer_progress.jsonl",
        "docs/writer_progress.log",
        "docs/build_provenance.json",
        # The Factory's receipt of what it stamped/vendored/declared: read
        # locally at gate ingest to own audit findings, never shipped.
        "docs/factory_receipt.json",
        # The WRITER pass's record of Factory-owned files it touched
        # (factory_owned.VIOLATIONS_REL): read by the WRITER gate, never shipped.
        "docs/writer_factory_owned.json",
    }
)

#: Never product source, at any depth -- bytecode and tool caches the
#: factory's own pytest runs leave behind.
EXPORT_SKIP_DIR_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv", ".git"}
)
EXPORT_SKIP_SUFFIXES = frozenset({".pyc", ".pyo"})
#: Top-level runtime state. Every generated platform defaults STORAGE_PATH
#: to ./data and its own .dockerignore excludes data/; the factory's TESTER
#: probes write their databases and sample uploads there. Top level only --
#: a product may well have app/data/ or frontend/src/data/ as real source.
EXPORT_SKIP_TOP_LEVEL = frozenset({"data"})


def is_exported(rel: Path) -> bool:
    """Whether a workspace-relative path ships -- in the zip AND the repo.

    One rule set for both exporters. They used to differ: the zip skipped
    bytecode and caches at any depth, while the cerebrum-builds push ignored
    only ``.git``, so build sess_065fc3eac75c4f62 (FinOps) pushed 131
    ``.pyc`` files and a data/ tree (an empty cerebrum.db plus the probes'
    sample uploads) that its zip would never have carried.
    """
    parts = Path(rel).parts
    if not parts:
        return False
    if parts[0] in EXPORT_SKIP_TOP_LEVEL:
        return False
    if any(part in EXPORT_SKIP_DIR_NAMES for part in parts):
        return False
    if any(part in FACTORY_INTERNAL_NAMES for part in parts):
        return False
    if Path(rel).as_posix() in FACTORY_INTERNAL_PATHS:
        return False
    return Path(rel).suffix not in EXPORT_SKIP_SUFFIXES


class BuildsPushError(RuntimeError):
    """Token/repo missing or push/collect failed before a usable branch tip.

    ``unreachable`` is the typed answer to "could GitHub be asked at all?":
    True for a missing token, a transport failure or an API refusal (no
    answer about the build); False when GitHub answered.
    """

    def __init__(self, message: str = "", *, unreachable: bool = False) -> None:
        super().__init__(message)
        self.unreachable = unreachable


@dataclass(frozen=True)
class BuildsRef:
    """Scratch branch on cerebrum-builds.

    ``seed_sha`` is the factory seed commit (pre-agent tip). That commit
    is cut from ``cerebrum-builds`` ``main``, so GitHub compare against
    ``main`` or this SHA has a common ancestor. ``collect_branch`` diffs
    agent commits against this SHA (not ``main``) so the factory overlay
    is not judged by the N1b path jail.
    """

    owner: str
    repo: str
    repository_url: str
    branch: str
    seed_sha: str


def builds_token(env: Mapping[str, str]) -> str:
    return str(env.get(BUILDS_TOKEN_ENV) or "").strip()


def sanitize_session_id(raw: str) -> str:
    text = _SESSION_SAFE_RE.sub("-", str(raw or "").strip())
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    return (text or "session")[:64]


def make_branch_name(session_id: str, *, suffix: Optional[str] = None) -> str:
    """One ``build/**`` branch per session run (Store gate later triggers here)."""
    sid = sanitize_session_id(session_id)
    tag = (suffix or uuid.uuid4().hex[:8]).strip() or uuid.uuid4().hex[:8]
    return f"build/{sid}-{tag}"


def parse_builds_repo(env: Mapping[str, str]) -> Tuple[str, str, str]:
    raw = str(env.get(BUILDS_REPO_ENV) or "").strip() or DEFAULT_BUILDS_REPO
    if raw.startswith("https://") or raw.startswith("http://"):
        clean = raw.rstrip("/").removesuffix(".git")
        parts = [p for p in clean.split("/") if p]
        if len(parts) < 2:
            raise BuildsPushError(
                f"{BUILDS_REPO_ENV} is not a GitHub owner/repo URL"
            )
        owner, name = parts[-2], parts[-1]
    else:
        if raw.count("/") != 1:
            raise BuildsPushError(
                f"{BUILDS_REPO_ENV} must be owner/repo or a GitHub HTTPS URL"
            )
        owner, name = (p.strip() for p in raw.split("/", 1))
        name = name.removesuffix(".git")
    if not owner or not name or "/" in name:
        raise BuildsPushError(f"{BUILDS_REPO_ENV} missing or invalid")
    return owner, name, f"https://github.com/{owner}/{name}"


def _id_part(text: str) -> bool:
    return bool(text) and all(ch.isascii() and ch.isalnum() for ch in text)


def is_build_branch(name: str) -> bool:
    """``build/<platform_id>`` -- a platform's branch of record -- or the
    legacy per-run ``build/sess_<id>[-<tag>...]`` that make_branch_name emits."""
    from app.factory.build.platform_identity import platform_id_from_branch

    if platform_id_from_branch(name):
        return True
    head = BRANCH_PREFIX + SESSION_ID_PREFIX
    if not isinstance(name, str) or not name.startswith(head):
        return False
    return all(_id_part(part) for part in name[len(head):].split("-"))


def session_token(word: str) -> Optional[Tuple[str, str]]:
    """``(session_id, "-tag" or "")`` when ``word`` is a session id or build
    branch, however it was wrapped; ``None`` otherwise. Case-insensitive:
    ids are lowercase hex and a phone may capitalise the paste."""
    low = str(word or "").strip(_TOKEN_WRAPPERS).lower()
    if low.startswith(BRANCH_PREFIX):
        low = low[len(BRANCH_PREFIX):]
    if not low.startswith(SESSION_ID_PREFIX):
        return None
    head, _, tail = low[len(SESSION_ID_PREFIX):].partition("-")
    if len(head) < 6 or not _id_part(head):
        return None
    tag = tail.split("-", 1)[0] if tail else ""
    if tail and not _id_part(tag):
        return None
    return SESSION_ID_PREFIX + head, ("-" + tag) if tag else ""


def redact_url_credentials(text: Optional[str]) -> str:
    """Replace the userinfo of every URL in ``text`` with ``<redacted>`` --
    each whitespace-separated token parsed by urllib.parse and rebuilt."""
    from itertools import groupby
    from urllib.parse import urlsplit

    out = []
    for is_space, run in groupby(str(text or ""), str.isspace):
        piece = "".join(run)
        if is_space:
            out.append(piece)
            continue
        core = piece.strip(_TOKEN_WRAPPERS)
        if core:
            try:
                parts = urlsplit(core)
            except ValueError:
                parts = None
            if parts is not None and parts.scheme and "@" in parts.netloc:
                host = parts.netloc.rsplit("@", 1)[1]
                clean = parts._replace(netloc="<redacted>@" + host).geturl()
                piece = piece.replace(core, clean, 1)
        out.append(piece)
    return "".join(out)


def _scrub(text: Optional[str], token: str = "") -> str:
    blob = redact_url_credentials(text)
    if token:
        blob = blob.replace(token, "<redacted>")
    return blob


#: GitHub refuses any single file above this size (its hard per-file limit).
GITHUB_MAX_FILE_BYTES = 100 * 1024 * 1024
#: A push GitHub rejects is retried this many times in all before the build
#: fails. A rejection can be transient (GitHub answers a bare "(failed)"); a
#: deterministic one fails identically each attempt and is then reported with
#: git's full output, never classified from its words.
PUSH_ATTEMPTS = 3
PUSH_BACKOFF_S = 5.0


def oversized_files(tree: Path, limit: int = GITHUB_MAX_FILE_BYTES) -> list:
    """Tracked-candidate files in ``tree`` GitHub would refuse, largest first.

    ``.git`` is skipped. Checked BEFORE a push so an oversized file fails the
    build with its name and size instead of an opaque remote rejection.
    """
    found = []
    for path in tree.rglob("*"):
        if ".git" in path.relative_to(tree).parts or not path.is_file() or path.is_symlink():
            continue
        size = path.stat().st_size
        if size > limit:
            found.append((path.relative_to(tree).as_posix(), size))
    return sorted(found, key=lambda item: -item[1])


def push_with_retry(
    git: Callable[..., subprocess.CompletedProcess],
    args: Sequence[str],
    *,
    cwd: Path,
    token: str,
    label: str,
    attempts: Optional[int] = None,
    backoff_s: Optional[float] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> subprocess.CompletedProcess:
    """Push ``cwd`` to the build repo; retry, then fail with git's FULL output.

    The whole stderr (every ``remote:`` line GitHub sent) is kept, scrubbed of
    the token: a tail cut at 300 characters once dropped the line that named
    the rejection (live 2026-10-07, ``(failed)`` with no reason).
    """
    attempts = PUSH_ATTEMPTS if attempts is None else attempts
    backoff_s = PUSH_BACKOFF_S if backoff_s is None else backoff_s
    sleep = time.sleep if sleep is None else sleep
    big = oversized_files(Path(cwd), GITHUB_MAX_FILE_BYTES)
    if big:
        listed = ", ".join(f"{name} ({size} bytes)" for name, size in big)
        raise BuildsPushError(
            f"{label}: file(s) above GitHub's {GITHUB_MAX_FILE_BYTES}-byte limit: {listed}"
        )
    outputs = []
    for attempt in range(1, max(1, attempts) + 1):
        result = git(list(args), cwd=cwd)
        if result.returncode == 0:
            return result
        outputs.append(
            f"attempt {attempt}: " + _scrub((result.stderr or "") + (result.stdout or ""), token).strip()
        )
        if attempt < attempts:
            sleep(backoff_s * attempt)
    raise BuildsPushError(f"{label} after {len(outputs)} attempt(s): " + " | ".join(outputs))


def _default_run_git(args: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", *list(args)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )


#: The only things a build branch takes from ``main``: the repo itself and the
#: Store gate's workflows. Everything else must come from the workspace.
_INHERITED_FROM_MAIN = frozenset({".git", ".github"})

#: The Store gate's own workflow. ``workflow_dispatch`` runs the workflow file
#: of the dispatched ref, so whatever a build branch carries here IS the gate:
#: it is always ``main``'s, never a copy a workspace happens to hold.
STORE_GATE_PATH = ".github/workflows/store-gate.yml"

#: The WHOLE gate: the workflow and the helper scripts it executes from the
#: branch's own tree. They change together on ``main``, so a branch must carry
#: them from the same ``main`` -- live 2026-10-08 (build/plt_5b8166c1a43c4622,
#: run 37764042614) a checkpoint refreshed the workflow alone, the new workflow
#: called ``repo_mount.py exec-path`` on the branch's older helper, and the gate
#: ran ``python3 ""``. A directory entry covers everything under it.
STORE_GATE_PATHS: tuple = (STORE_GATE_PATH, ".github/store_gate")


def is_gate_owned(rel: str | Path) -> bool:
    """True when ``rel`` is part of the Store gate (``main``'s, never a copy)."""
    text = Path(rel).as_posix()
    if text.startswith("./"):
        text = text[2:]
    return any(text == p or text.startswith(p.rstrip("/") + "/") for p in STORE_GATE_PATHS)


def _drop_inherited_product(tree: Path) -> None:
    """Remove whatever product ``main`` happens to carry before the overlay.

    A build branch is cut from ``main`` so it inherits the Store gate. But the
    overlay only REPLACES top-level entries the workspace has; anything else
    on ``main`` survives into the customer's branch. On 2026-09-14 a whole
    build branch was merged into cerebrum-builds ``main`` (a hospitality
    platform from another account), and from then on every new platform's
    branch carried that one's leftovers -- its kits/, its receipt.json, its
    .generation_quota_account -- and the Docker gate measured workspace +
    leftovers rather than what the zip ships. Whatever ``main`` contains,
    a branch is the workspace plus the gate, nothing else.
    """
    for item in tree.iterdir():
        if item.name in _INHERITED_FROM_MAIN:
            continue
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()


def _sync_workspace_onto_tree(src: Path, dest: Path) -> None:
    """Overlay product files onto a cloned ``main`` tree.

    Replaces or adds workspace entries. Keeps ``dest/.github/`` (Store gate)
    even when the workspace also has a ``.github/`` directory.
    """
    dest.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        raise BuildsPushError(f"workspace is not a directory: {src}")
    def _not_exported(dirpath: str, names: list) -> list:
        base = Path(dirpath).relative_to(src)
        return [n for n in names if not is_exported(base / n)]

    for item in src.iterdir():
        if not is_exported(Path(item.name)):
            continue
        if item.name == ".github":
            # MERGE, never skip and never replace. The inherited ``dest/.github/``
            # is where the Store gate's own workflow lives, so it must survive;
            # but skipping the workspace's ``.github/`` wholesale -- which this
            # did until 2026-09-26 -- threw away the product's stamped
            # ``workflows/ci.yml`` (test + pip-audit/bandit + bench) and every
            # branch inherited main's 18-line pytest-only file instead. The
            # gate then read THAT file, and ``audit_clean`` failed every fresh
            # build by construction. ``is_exported('.github/workflows/ci.yml')``
            # was already True; the allowlist never got a vote.
            for sub in item.rglob("*"):
                rel = sub.relative_to(src)
                if sub.is_dir() or not is_exported(rel):
                    continue
                if is_gate_owned(rel):
                    # The gate is main's (live 2026-10-05: a stale workspace
                    # copy replaced it, dropped the bandit step, and
                    # audit_clean read "unmeasured" -- 21/22). Its helper
                    # scripts are the gate too (STORE_GATE_PATHS).
                    continue
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sub, out)
            continue
        target = dest / item.name
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        if item.is_dir():
            shutil.copytree(item, target, ignore=_not_exported)
        else:
            shutil.copy2(item, target)


def push_workspace(
    workspace: Path,
    *,
    env: Mapping[str, str],
    session_id: str,
    run_git: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    suffix: Optional[str] = None,
    branch: Optional[str] = None,
) -> BuildsRef:
    """Commit ``workspace`` onto a ``build/**`` branch cut from ``main``.

    ``branch``: the platform's branch of record (``build/<platform_id>``),
    created here the first time it is pushed. Without it a legacy per-run
    ``build/sess_<id>-<tag>`` is named."""
    token = builds_token(env)
    if not token:
        raise BuildsPushError(
            f"{BUILDS_TOKEN_ENV} missing — fail-closed; no agent start",
            unreachable=True,
        )
    owner, name, repo_url = parse_builds_repo(env)
    if branch is not None and not is_build_branch(branch):
        raise BuildsPushError(f"not a build branch: {branch!r}")
    branch = branch or make_branch_name(session_id, suffix=suffix)
    git = run_git or _default_run_git
    tmp = Path(tempfile.mkdtemp(prefix="cerebrum-builds-"))
    try:
        auth_url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
        _require_git(
            git,
            ["clone", "--depth=1", "--branch", "main", auth_url, "."],
            cwd=tmp,
            token=token,
        )
        parent_proc = _require_git(git, ["rev-parse", "HEAD"], cwd=tmp, token=token)
        parent_sha = (parent_proc.stdout or "").strip()
        if not parent_sha:
            raise BuildsPushError("push failed: empty seed parent sha")
        _require_git(git, ["checkout", "-B", branch], cwd=tmp, token=token)
        _drop_inherited_product(tmp)
        _sync_workspace_onto_tree(Path(workspace), tmp)
        _require_git(git, ["add", "-A"], cwd=tmp, token=token)
        commit = git(
            [
                "-c",
                f"user.email={GIT_EMAIL}",
                "-c",
                f"user.name={GIT_NAME}",
                "commit",
                "--allow-empty",
                "-m",
                "factory: seed cli-pivot workspace",
            ],
            cwd=tmp,
        )
        _require_git_result(commit, "commit", token)
        sha_proc = _require_git(git, ["rev-parse", "HEAD"], cwd=tmp, token=token)
        seed_sha = (sha_proc.stdout or "").strip()
        if not seed_sha:
            raise BuildsPushError("push failed: empty seed sha")
        push_with_retry(
            git,
            ["push", "-u", "origin", f"HEAD:{branch}"],
            cwd=tmp,
            token=token,
            label="push failed before agent start",
        )
        return BuildsRef(
            owner=owner,
            repo=name,
            repository_url=repo_url,
            branch=branch,
            seed_sha=seed_sha,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _require_git(
    git: Callable[..., subprocess.CompletedProcess],
    args: Sequence[str],
    *,
    cwd: Path,
    token: str,
) -> subprocess.CompletedProcess:
    result = git(list(args), cwd=cwd)
    return _require_git_result(result, _scrub(" ".join(args), token), token)


def _require_git_result(
    result: subprocess.CompletedProcess,
    label: str,
    token: str,
) -> subprocess.CompletedProcess:
    if result.returncode != 0:
        raise BuildsPushError(
            f"push failed before agent start ({label}): "
            + _scrub(result.stderr or result.stdout, token)
        )
    return result


def github_request(
    method: str,
    path: str,
    *,
    token: str,
    opener: Callable[..., Any] = urlopen,
    timeout_s: float = 30.0,
    body: Optional[Mapping[str, Any]] = None,
) -> Tuple[int, Any]:
    url = path if path.startswith("https://") else f"{GITHUB_API}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "CerebrumFactory-N1a",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, headers=headers, method=method.upper(), data=data)
    try:
        with opener(req, timeout=timeout_s) as resp:
            raw = resp.read()
            status = int(getattr(resp, "status", 200) or 200)
    except HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        status = int(exc.code)
    except URLError as exc:
        raise BuildsPushError(f"GitHub API down: {exc.reason}", unreachable=True) from exc
    text = raw.decode("utf-8", errors="replace") if raw else ""
    if not text:
        return status, {}
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def _file_content(payload: Mapping[str, Any]) -> Optional[str]:
    content = payload.get("content")
    if not content:
        return None
    encoding = str(payload.get("encoding") or "base64").lower()
    raw = str(content).replace("\n", "")
    if encoding == "base64":
        return base64.b64decode(raw).decode("utf-8")
    return str(content)


def _decode_receipt(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        text = _file_content(payload)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text
    return payload


def fetch_commit_sha(
    owner: str,
    repo: str,
    ref: str,
    *,
    token: str,
    opener: Callable[..., Any] = urlopen,
) -> str:
    """Resolve a branch or SHA to a commit SHA via the GitHub commits API."""
    path = f"/repos/{owner}/{repo}/commits/{quote(ref, safe='')}"
    status, body = github_request("GET", path, token=token, opener=opener)
    if status >= 400 or not isinstance(body, Mapping):
        raise BuildsPushError(f"GitHub API down: commit {ref} HTTP {status}", unreachable=True)
    sha = str(body.get("sha") or "").strip()
    if not sha:
        raise BuildsPushError(f"GitHub API: empty sha for {ref}")
    return sha


def list_session_build_refs(
    owner: str,
    repo: str,
    session_id: str,
    *,
    token: str,
    opener: Callable[..., Any] = urlopen,
) -> List[Tuple[str, str]]:
    """``(branch, sha)`` pairs for ``build/<session>-*`` on cerebrum-builds."""
    sid = sanitize_session_id(session_id)
    prefix = f"heads/build/{sid}"
    path = f"/repos/{owner}/{repo}/git/matching-refs/{quote(prefix, safe='/')}"
    status, body = github_request("GET", path, token=token, opener=opener)
    if status == 404:
        return []
    if status >= 400 or not isinstance(body, list):
        raise BuildsPushError(f"GitHub API down: matching-refs HTTP {status}", unreachable=True)
    found: List[Tuple[str, str]] = []
    for item in body:
        if not isinstance(item, Mapping):
            continue
        ref = str(item.get("ref") or "").strip()
        obj = item.get("object") if isinstance(item.get("object"), Mapping) else {}
        sha = str(obj.get("sha") or "").strip()
        branch = ref.removeprefix("refs/heads/")
        if branch.startswith(f"build/{sid}") and sha:
            found.append((branch, sha))
    return found


def build_refs_of_record(
    owner: str,
    repo: str,
    *,
    platform_id: Optional[str],
    session_id: str,
    token: str,
    opener: Callable[..., Any] = urlopen,
) -> List[Tuple[str, str]]:
    """THE resolver: ``(branch, sha)`` pairs for a build's branch of record.

    A platform (``platform_id``, persisted on the session and recorded in the
    run's ledger) has exactly one branch, ``build/<platform_id>``. A run with
    no platform id -- a ledger from before platforms existed -- keeps its
    legacy ``build/<session>-*`` branches. Every reader that needs "the
    session's build branch" (N3 dispatch/collect, adopt-green, attach, the
    collect fallback) asks here, never builds a name itself.
    """
    from app.factory.build.platform_identity import branch_of_record, is_platform_id

    if is_platform_id(platform_id):
        branch = branch_of_record(str(platform_id))
        path = f"/repos/{owner}/{repo}/git/ref/{quote('heads/' + branch, safe='/')}"
        status, body = github_request("GET", path, token=token, opener=opener)
        if status < 400 and isinstance(body, Mapping):
            obj = body.get("object") if isinstance(body.get("object"), Mapping) else {}
            sha = str(obj.get("sha") or "").strip()
            if sha:
                return [(branch, sha)]
        elif status != 404:
            raise BuildsPushError(f"GitHub API down: ref {branch} HTTP {status}", unreachable=True)
        # No platform branch yet: a session approved before platforms existed
        # may still carry its legacy branch.
    if not session_id:
        return []
    return list_session_build_refs(owner, repo, session_id, token=token, opener=opener)


def fetch_receipt(
    ref: BuildsRef,
    *,
    env: Mapping[str, str],
    branch: Optional[str] = None,
    opener: Callable[..., Any] = urlopen,
) -> Any:
    token = builds_token(env)
    if not token:
        raise BuildsPushError(f"{BUILDS_TOKEN_ENV} missing")
    head = branch or ref.branch
    for name in RECEIPT_NAMES:
        path = (
            f"/repos/{ref.owner}/{ref.repo}/contents/{quote(name, safe='/')}"
            f"?ref={quote(head, safe='')}"
        )
        status, body = github_request("GET", path, token=token, opener=opener)
        if status == 404:
            continue
        if status >= 400:
            raise BuildsPushError(f"GitHub API down: contents {name} HTTP {status}", unreachable=True)
        decoded = _decode_receipt(body)
        if decoded is not None:
            return decoded
    return None


def _changed_paths(files: Sequence[Mapping[str, Any]]) -> List[str]:
    found: List[str] = []

    def _add(raw: Any) -> None:
        path = str(raw or "").replace("\\", "/").lstrip("./")
        if path and path not in found and path != "/dev/null":
            found.append(path)

    for item in files:
        _add(item.get("filename"))
        _add(item.get("previous_filename"))
    return found


def _unified_diff(files: Sequence[Mapping[str, Any]]) -> str:
    chunks: List[str] = []
    for item in files:
        name = str(item.get("filename") or "").replace("\\", "/")
        if not name:
            continue
        prev = str(item.get("previous_filename") or name).replace("\\", "/")
        patch = item.get("patch")
        chunks.append(f"diff --git a/{prev} b/{name}")
        if patch:
            chunks.append(f"--- a/{prev}")
            chunks.append(f"+++ b/{name}")
            chunks.append(str(patch).rstrip("\n"))
    return "\n".join(chunks)



def session_id_from_build_branch(branch: str) -> str:
    """``build/<session>-<8hex>`` → session id (best-effort)."""
    name = str(branch or "").strip().removeprefix("refs/heads/").removeprefix("build/")
    if not name:
        return ""
    head, sep, tail = name.rpartition("-")
    if sep and len(tail) == 8 and all(c in "0123456789abcdef" for c in tail.lower()):
        return head
    return name


def _ref_resolves(
    owner: str,
    repo: str,
    ref: str,
    *,
    token: str,
    opener: Callable[..., Any] = urlopen,
) -> bool:
    """True when GitHub commits API can resolve ``ref`` (branch or SHA)."""
    if not ref:
        return False
    path = f"/repos/{owner}/{repo}/commits/{quote(ref, safe='')}"
    status, body = github_request("GET", path, token=token, opener=opener)
    return status < 400 and isinstance(body, Mapping) and bool(str(body.get("sha") or "").strip())


def _compare_seed_head(
    ref: BuildsRef,
    head: str,
    *,
    token: str,
    opener: Callable[..., Any] = urlopen,
) -> Tuple[int, Any]:
    compare_path = (
        f"/repos/{ref.owner}/{ref.repo}/compare/"
        f"{quote(ref.seed_sha, safe='')}...{quote(head, safe='')}"
    )
    return github_request("GET", compare_path, token=token, opener=opener)


def collect_branch(
    ref: BuildsRef,
    *,
    env: Mapping[str, str],
    branch: Optional[str] = None,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = COMPARE_404_RETRIES,
    retry_sleep_s: float = COMPARE_404_SLEEP_S,
) -> Tuple[Any, List[str], str]:
    """Receipt + changed paths + unified diff vs the pre-agent seed commit.

    Compare HTTP 404 means a missing seed and/or head (BA never pushed, branch
    deleted, head renamed, or GitHub ref lag) — not a GitHub outage. Retry,
    rediscover ``build/<session>-*`` tips, fall back to the seed branch name,
    then fail closed with a clear retryable-infra diagnosis.
    """
    token = builds_token(env)
    if not token:
        raise BuildsPushError(f"{BUILDS_TOKEN_ENV} missing")
    head = branch or ref.branch
    status, body = _compare_seed_head(ref, head, token=token, opener=opener)

    if status == 404:
        attempts = max(0, int(retries))
        for _ in range(attempts):
            sleep(float(retry_sleep_s))
            status, body = _compare_seed_head(ref, head, token=token, opener=opener)
            if status != 404:
                break

    if status == 404:
        seed_ok = _ref_resolves(
            ref.owner, ref.repo, ref.seed_sha, token=token, opener=opener
        )
        head_ok = _ref_resolves(ref.owner, ref.repo, head, token=token, opener=opener)

        # Head renamed / deleted tip: try other build/<session>-* refs, then seed branch.
        candidates: List[str] = []
        if ref.branch and ref.branch != head:
            candidates.append(ref.branch)
        from app.factory.build.platform_identity import platform_id_from_branch

        pid = platform_id_from_branch(head) or platform_id_from_branch(ref.branch)
        sid = "" if pid else (
            session_id_from_build_branch(head) or session_id_from_build_branch(ref.branch)
        )
        if pid or sid:
            try:
                alts = build_refs_of_record(
                    ref.owner, ref.repo, platform_id=pid, session_id=sid,
                    token=token, opener=opener,
                )
            except BuildsPushError:
                alts = []
            for alt_branch, _sha in sorted(alts, key=lambda row: row[0]):
                if alt_branch not in candidates and alt_branch != head:
                    candidates.append(alt_branch)

        for alt in candidates:
            status, body = _compare_seed_head(ref, alt, token=token, opener=opener)
            if status == 404:
                continue
            if status < 400 and isinstance(body, Mapping):
                head = alt
                head_ok = True
                break

        if status == 404:
            seed_label = "present" if seed_ok else "missing"
            head_label = "present" if head_ok else "missing"
            seed_short = (ref.seed_sha or "")[:12] or "?"
            raise BuildsPushError(
                f"compare HTTP 404: seed={seed_label} head={head_label} "
                f"(seed={seed_short} head={head}) — retryable infra "
                f"(branch missing after BA, renamed, or not yet visible)"
            )

    if status >= 400 or not isinstance(body, Mapping):
        # Non-404 transport/API failures stay "API down".
        raise BuildsPushError(f"GitHub API down: compare HTTP {status}", unreachable=True)
    files = [f for f in (body.get("files") or []) if isinstance(f, Mapping)]
    receipt = fetch_receipt(ref, env=env, branch=head, opener=opener)
    return receipt, _changed_paths(files), _unified_diff(files)
