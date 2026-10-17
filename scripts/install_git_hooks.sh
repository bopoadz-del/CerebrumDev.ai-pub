#!/bin/sh
# Point this clone at the versioned hooks (.githooks/): the hardwiring gate
# then blocks every local commit, as the CI step blocks every PR.
set -e
git -C "$(git rev-parse --show-toplevel)" config core.hooksPath .githooks
echo "hooks: core.hooksPath=.githooks (pre-commit runs scripts/scan_hardwiring.py)"
