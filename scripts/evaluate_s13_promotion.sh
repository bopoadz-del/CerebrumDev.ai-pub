#!/usr/bin/env bash
# Re-run S13 evaluate_promotion and write build/stages/S13_promotion.json twins.
#
# Intended invocation (same as #206):
#   FACTORY_PILOT_WORKSPACE=build/pilot_workspace ./scripts/evaluate_s13_promotion.sh
#
# Without FACTORY_PILOT_WORKSPACE, U7 fail-closes: stage documents alone
# cannot flip PILOT_READY. This script does not invent a workspace, does
# not write HARVEST_AUTHORIZED.json, and does not stamp PILOT_READY.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -z "${FACTORY_PILOT_WORKSPACE:-}" && -d "$ROOT/build/pilot_workspace" ]]; then
  export FACTORY_PILOT_WORKSPACE="$ROOT/build/pilot_workspace"
  echo "FACTORY_PILOT_WORKSPACE defaulted to in-tree performed workspace: $FACTORY_PILOT_WORKSPACE" >&2
fi

if [[ -x "$ROOT/backend/venv/bin/python" ]]; then
  PY="$ROOT/backend/venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"

# Exit 2 when PILOT_READY is false is the emitter's fail-closed code, not a
# runner crash. Capture it so the twins are still written.
set +e
"$PY" -m app.factory.build.promotion "$@"
status=$?
set -e

primary="$ROOT/build/stages/S13_promotion.json"
reread="$ROOT/build/stages/S13_promotion.reread.json"
"$PY" - "$primary" "$reread" <<'PY'
import json, sys
from pathlib import Path

primary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
reread = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
primary_sha = (primary.get("provenance") or {}).get("git_sha")
reread_sha = reread.get("git_sha")
disagreements = list(reread.get("disagreements") or [])
if primary.get("PILOT_READY") != reread.get("PILOT_READY"):
    disagreements.append("PILOT_READY")
if primary.get("verdict") != reread.get("verdict"):
    disagreements.append("verdict")
if primary_sha != reread_sha:
    disagreements.append("git_sha")
print(json.dumps({
    "primary": sys.argv[1],
    "reread": sys.argv[2],
    "PILOT_READY": primary.get("PILOT_READY"),
    "verdict": primary.get("verdict"),
    "first_failing_criterion": primary.get("first_failing_criterion"),
    "git_sha_primary": primary_sha,
    "git_sha_reread": reread_sha,
    "disagreements": disagreements,
    "reread_matches": not disagreements,
    "harvest_verdict": (primary.get("harvest") or {}).get("verdict"),
}, indent=2))
PY

exit "$status"
