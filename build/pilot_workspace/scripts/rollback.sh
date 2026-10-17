#!/bin/sh
# Roll the running identity back to a prior revision.
# Does not wipe STORAGE_PATH/platform.db — persisted rows stay.
# Render equivalent: Dashboard rollback to the previous deploy
# (same disk). Losing that disk is still a SPOF; this script
# cannot invent a replica.
set -eu
TARGET="${1:?usage: rollback.sh <revision>}"
STORAGE="${STORAGE_PATH:?STORAGE_PATH required}"
mkdir -p "$STORAGE"
printf '%s\n' "$TARGET" > "$STORAGE/deploy_revision"
printf '%s\n' "{\"event\":\"rollback.performed\",\"revision\":\"$TARGET\",\"storage\":\"$STORAGE\"}"
