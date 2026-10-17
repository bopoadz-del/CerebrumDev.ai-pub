#!/usr/bin/env python3
"""Does this run of the release cycle start a cycle? Once per READY deploy.

Order 1736Z. Every push to master deploys (deploy-aws) and every completed
deploy used to start a release cycle (post-deploy-smoke, workflow_run), so
landing a batch of N PRs started N cycles on the live box. The rule:

  * a deploy from a PUSH starts a cycle only when its commit declares it
    ready -- ``[ready-for-cycle]`` in the commit message. For a PR merge,
    put it in the PR title: GitHub's merge commit message carries the title.
    (For a squash merge, keep it in the squash message.)
  * a DISPATCHED deploy (Actions -> deploy-aws -> Run workflow) starts a
    cycle only when dispatched with ``cycle: true``; its run name then
    carries ``[cycle]``. The tip's commit message does not count.
  * a dispatched cycle (Actions -> Post-deploy smoke -> Run workflow) always
    runs: the owner asked for it.

A deploy that is not declared ready still deploys; it starts nothing. The
workflow's concurrency group mirrors this rule so a skipped run never takes
the release-cycle slot (and so never displaces a cycle waiting for it).

Reads the event from the environment, prints ``run=true|false`` and
``reason=...`` for $GITHUB_OUTPUT. Never fails: a cycle that is not
declared is a notice, not an error.
"""

from __future__ import annotations

import os
import sys
from typing import Tuple

#: In the merged commit's message (PR title): this deploy is the one to cycle.
READY_MARKER = "[ready-for-cycle]"
#: In a dispatched deploy's run name (deploy-aws ``cycle: true``).
DISPATCH_MARKER = "[cycle]"


def starts_cycle(
    *, event_name: str, deploy_event: str, deploy_title: str, commit_message: str
) -> Tuple[bool, str]:
    """(run, reason) for one run of the cycle workflow. Pure."""
    if event_name == "workflow_dispatch":
        return True, "cycle dispatched by hand"
    if event_name != "workflow_run":
        return False, f"event {event_name!r} never starts a cycle"
    if deploy_event == "workflow_dispatch":
        if DISPATCH_MARKER in (deploy_title or ""):
            return True, f"deploy dispatched with cycle=true ({DISPATCH_MARKER})"
        return False, "deploy dispatched without cycle=true: not declared ready"
    if READY_MARKER in (commit_message or ""):
        return True, f"deployed commit declares {READY_MARKER}"
    return False, f"deployed commit not declared ready (no {READY_MARKER} in its message)"


def main() -> int:
    run, reason = starts_cycle(
        event_name=os.getenv("EVENT_NAME", ""),
        deploy_event=os.getenv("DEPLOY_EVENT", ""),
        deploy_title=os.getenv("DEPLOY_TITLE", ""),
        commit_message=os.getenv("COMMIT_MESSAGE", ""),
    )
    if not run:
        print(f"::notice::no release cycle for this deploy: {reason}", file=sys.stderr)
    print(f"run={'true' if run else 'false'}")
    print(f"reason={reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
