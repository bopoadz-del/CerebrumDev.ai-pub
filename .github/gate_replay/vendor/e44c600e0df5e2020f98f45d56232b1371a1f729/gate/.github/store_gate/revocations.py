"""Revoked Store-gate certifications, as data.

A certification the gate later proves false (its own run judged the wrong
tree) is recorded in ``revoked_certifications.json`` -- branch, sha, run id,
reason, evidence. Nothing here names a branch: the file is the only list.

* ``replay_select`` skips a revoked run when choosing certified builds.
* ``python3 revocations.py apply --repo OWNER/REPO`` withdraws the
  ``store-gate`` commit status the revoked run wrote -- the record the Factory
  reads before it presents an export as certified -- but only while that run is
  still the branch's newest completed gate run. A re-gated branch (a newer run,
  dispatched the normal way) carries its own verdict and is never overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

RECORD = Path(__file__).resolve().parent / "revoked_certifications.json"
REQUIRED = ("branch", "sha", "run_id", "reason", "evidence")
STATUS_CONTEXT = "store-gate"
DESCRIPTION_PREFIX = "certification revoked: "
#: GitHub truncates commit status descriptions at 140 characters.
DESCRIPTION_MAX = 140
API = "https://api.github.com"


def problems(row: Mapping[str, Any]) -> list[str]:
    out = [f"missing {k}" for k in REQUIRED if not str(row.get(k) or "").strip()]
    try:
        int(row.get("run_id"))
    except (TypeError, ValueError):
        out.append("run_id is not an integer")
    return out


def load(path: Path = RECORD) -> list[dict[str, Any]]:
    """The revoked certifications; a malformed entry refuses the whole file."""
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8")).get("revoked") or []
    except FileNotFoundError:
        return []
    for row in rows:
        bad = problems(row)
        if bad:
            raise ValueError(f"revoked_certifications: {row.get('branch')!r}: {', '.join(bad)}")
    return [dict(row, run_id=int(row["run_id"])) for row in rows]


def revoked_run_ids(rows: Iterable[Mapping[str, Any]] | None = None) -> set[int]:
    return {int(r["run_id"]) for r in (load() if rows is None else rows)}


def description(row: Mapping[str, Any]) -> str:
    return (DESCRIPTION_PREFIX + str(row["reason"]).strip())[:DESCRIPTION_MAX]


def apply(
    rows: Iterable[Mapping[str, Any]],
    *,
    newest_run_id: Callable[[str], Any],
    write_status: Callable[[str, str, str, str], Any],
) -> dict[str, list[str]]:
    withdrawn: list[str] = []
    superseded: list[str] = []
    for row in rows:
        if newest_run_id(row["branch"]) != int(row["run_id"]):
            superseded.append(row["branch"])
            continue
        evidence = str(row["evidence"]).split()[0]
        url = evidence if evidence.startswith("https://") else ""
        write_status(row["sha"], "failure", description(row), url)
        withdrawn.append(row["branch"])
    return {"withdrawn": withdrawn, "superseded": superseded}


class GitHub:
    def __init__(self, repo: str, token: str):
        self.repo, self.token = repo, token

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        req = urllib.request.Request(
            f"{API}/repos/{self.repo}{path}",
            method=method,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "cerebrum-gate-revocations",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8") or "null")

    def newest_run_id(self, branch: str) -> Any:
        body = self._call(
            "GET",
            f"/actions/workflows/store-gate.yml/runs?branch={quote(branch, safe='')}&status=completed&per_page=1",
        ) or {}
        runs = body.get("workflow_runs") or []
        return int(runs[0]["id"]) if runs else None

    def write_status(self, sha: str, state: str, desc: str, url: str) -> None:
        body = {"state": state, "context": STATUS_CONTEXT, "description": desc}
        if url:
            body["target_url"] = url
        self._call("POST", f"/statuses/{sha}", body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["apply", "check"])
    parser.add_argument("--repo", default="")
    args = parser.parse_args(argv)
    rows = load()
    if args.command == "check":
        print(f"{len(rows)} revoked certification(s), record valid")
        return 0
    gh = GitHub(args.repo, os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "")
    out = apply(rows, newest_run_id=gh.newest_run_id, write_status=gh.write_status)
    for branch in out["withdrawn"]:
        print(f"withdrawn: store-gate status on {branch}")
    for branch in out["superseded"]:
        print(f"superseded (re-gated since): {branch}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
