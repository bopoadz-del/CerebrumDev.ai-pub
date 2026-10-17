"""Pick the build branches a gate change is replayed on (owner rule, 2026-10-08).

Two kinds of row, one matrix:

* the newest N CERTIFIED build branches (below), which must still certify;
* every KNOWN-BAD fixture in known_bad_fixtures.json (``expect: fail``), which
  must still fail on its expected check -- a gate that certifies one, or fails
  it for another reason, has lost the check the fixture exists to prove.

A gate change is replayed, before merge, on the newest N build branches this
gate CERTIFIED -- N from .github/gate_replay.json (``certified_branches``,
default 3; ``GATE_REPLAY_N`` overrides it for one run).

Certified is read from the gate's own record, never from a name: per build
branch, its newest completed run of the Store gate workflow concluded
``success`` AND the ``store-gate`` commit status it wrote on that sha is
``success``. The replay checks out that exact sha, so the result it is
compared with is the one the gate recorded.

    python3 replay_select.py --repo OWNER/REPO --out selection.json

prints ``matrix=<json>`` to $GITHUB_OUTPUT when set.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

CONFIG = Path(__file__).resolve().parents[1] / "gate_replay.json"
KNOWN_BAD = Path(__file__).resolve().parent / "known_bad_fixtures.json"
KNOWN_BAD_LABEL_PREFIX = "known-bad-"
_SHA = re.compile(r"[0-9a-f]{40}")
CONFIG_KEY = "certified_branches"
DEFAULT_N = 3
ENV_N = "GATE_REPLAY_N"
GATE_WORKFLOW = "store-gate.yml"
STATUS_CONTEXT = "store-gate"
BRANCH_PREFIX = "build/"
API = "https://api.github.com"
#: How far back the gate's run history is read (100 runs per page).
MAX_PAGES = 10


def configured_n(env: Mapping[str, str] | None = None, config: Path = CONFIG) -> int:
    """N: the env override when it is a positive integer, else the config, else 3."""
    env = os.environ if env is None else env
    for raw in (env.get(ENV_N), _config_n(config)):
        try:
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return DEFAULT_N


def _config_n(config: Path) -> Any:
    try:
        return json.loads(config.read_text(encoding="utf-8")).get(CONFIG_KEY)
    except (OSError, ValueError, AttributeError):
        return None


def label(branch: str) -> str:
    """An artifact-safe name for a branch (artifact and job names)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", branch.split("/", 1)[-1]).strip("-") or "build"


def certified(
    runs: Iterable[Mapping[str, Any]],
    status_of: Callable[[str], str],
    exists: Callable[[str], bool],
    n: int,
    revoked_runs: set | None = None,
) -> list[dict[str, Any]]:
    """The newest ``n`` certified build branches, newest first.

    ``runs`` are the gate workflow's runs, newest first. Per branch only its
    newest COMPLETED run counts: a branch whose latest verdict is red is not
    certified, whatever it scored before. A run in ``revoked_runs``
    (revoked_certifications.json) certified nothing; a newer run on the same
    branch is a new verdict and counts."""
    revoked_runs = set(revoked_runs or ())
    seen: set = set()
    out: list[dict[str, Any]] = []
    for run in runs:
        if str(run.get("status") or "") != "completed":
            continue
        branch = str(run.get("head_branch") or "")
        if not branch.startswith(BRANCH_PREFIX) or branch in seen:
            continue
        seen.add(branch)
        if str(run.get("conclusion") or "") != "success":
            continue
        if run.get("id") in revoked_runs:
            continue
        sha = str(run.get("head_sha") or "")
        if not sha or status_of(sha) != "success" or not exists(branch):
            continue
        out.append(
            {
                "branch": branch,
                "sha": sha,
                "run_id": run.get("id"),
                "label": label(branch),
                "certified_at": run.get("created_at"),
            }
        )
        if len(out) >= n:
            break
    return out


def read_known_bad(path: Path = KNOWN_BAD) -> Mapping[str, Any]:
    """The known-bad fixture list (data). A missing file is an empty list."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"fixtures": []}


def known_bad(data: Mapping[str, Any], exists: Callable[[str], bool]) -> list[dict[str, Any]]:
    """Matrix rows for every known-bad fixture, each marked ``expect: fail``.

    A fixture the replay cannot run proves nothing, so a malformed row or a
    branch that no longer exists is an error, never a skip. A fixture never
    sits on a build/ branch: it must not be selectable as certified or
    exported as a product."""
    rows: list[dict[str, Any]] = []
    for item in data.get("fixtures") or []:
        branch = str(item.get("branch") or "").strip()
        sha = str(item.get("sha") or "").strip()
        check = str(item.get("expected_failing_check") or "").strip()
        if not branch:
            raise ValueError(f"known-bad fixture without a branch: {item}")
        if branch.startswith(BRANCH_PREFIX):
            raise ValueError(f"known-bad fixture {branch} sits on a build/ branch")
        if not _SHA.fullmatch(sha):
            raise ValueError(f"known-bad fixture {branch}: sha must be a full commit sha, got {sha!r}")
        if not check:
            raise ValueError(f"known-bad fixture {branch}: no expected_failing_check")
        if not str(item.get("reason") or "").strip():
            raise ValueError(f"known-bad fixture {branch}: no reason")
        if not exists(branch):
            raise ValueError(f"known-bad fixture branch {branch} does not exist")
        rows.append(
            {
                "branch": branch,
                "sha": sha,
                "label": KNOWN_BAD_LABEL_PREFIX + label(branch),
                "expect": "fail",
                "expected_check": check,
            }
        )
    return rows


class GitHub:
    def __init__(self, repo: str, token: str, opener: Callable[..., Any] = urllib.request.urlopen):
        self.repo = repo
        self.token = token
        self.opener = opener

    def get(self, path: str) -> Any:
        req = urllib.request.Request(
            f"{API}/repos/{self.repo}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "cerebrum-gate-replay",
            },
        )
        try:
            with self.opener(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def runs(self) -> Iterable[Mapping[str, Any]]:
        for page in range(1, MAX_PAGES + 1):
            body = self.get(f"/actions/workflows/{GATE_WORKFLOW}/runs?per_page=100&page={page}") or {}
            batch = body.get("workflow_runs") or []
            yield from batch
            if len(batch) < 100:
                return

    def status_of(self, sha: str) -> str:
        body = self.get(f"/commits/{sha}/status") or {}
        for item in body.get("statuses") or []:
            if item.get("context") == STATUS_CONTEXT:
                return str(item.get("state") or "")
        return ""

    def exists(self, branch: str) -> bool:
        return self.get(f"/branches/{quote(branch, safe='')}") is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    gh = GitHub(args.repo, token)
    n = configured_n()
    from revocations import revoked_run_ids

    picked = certified(gh.runs(), gh.status_of, gh.exists, n, revoked_runs=revoked_run_ids())
    try:
        bad = known_bad(read_known_bad(), gh.exists)
    except ValueError as exc:
        print(f"known-bad fixtures cannot be replayed: {exc}")
        return 1
    print(f"replaying the gate on {len(picked)} of the newest {n} certified build branch(es):")
    for row in picked:
        print(f"  {row['branch']} @ {row['sha'][:12]} (certified {row['certified_at']})")
    print(f"and on {len(bad)} known-bad fixture(s), each of which must still fail:")
    for row in bad:
        print(f"  {row['branch']} @ {row['sha'][:12]} (must fail {row['expected_check']})")
    if not picked:
        # A replay on nothing proves nothing: say so, never pass silently.
        print("no certified build branch found: the gate change cannot be replayed")
        return 1
    picked = picked + bad
    Path(args.out).write_text(json.dumps(picked, indent=2) + "\n", encoding="utf-8")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("matrix=" + json.dumps(picked) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
