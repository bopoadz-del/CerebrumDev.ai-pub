#!/usr/bin/env bash
# Configure Kimi Code CLI credentials (writes config.toml — NOT in-app chat LLM).
#
# Kimi Code CLI reads provider keys only from config.toml
# (see https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files).
# Shell exports / backend/.env KIMI_* are NOT used by the CLI for auth.
# Default model is Moonshot kimi-k3 (https://platform.moonshot.ai/docs/guide/start-using-kimi-api).
# Managed kimi-code/k3 / k3 404s on api.moonshot.ai without TTY /login.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${1:-}"
BASE_URL="${KIMI_CODE_BASE_URL:-https://api.moonshot.ai/v1}"
# Moonshot Open Platform id. Override with KIMI_CODE_MODEL / KIMI_CODE_MODEL_ID.
MODEL="${KIMI_CODE_MODEL:-kimi-k3}"
MODEL_ID="${KIMI_CODE_MODEL_ID:-}"

if [[ -z "$KEY" ]]; then
  echo "Usage: $0 '<KIMI_CODE_API_KEY>'" >&2
  exit 1
fi

# Optional project-local note (does NOT authenticate the CLI; docs only)
ENV_FILE="$ROOT/backend/.env"
touch "$ENV_FILE"
# Do NOT strip KIMI_API_KEY / CEREBRUM_LLM_* / LLM_PROVIDER — those are in-app LLM settings.
if grep -q '^KIMI_CODE_API_KEY=' "$ENV_FILE" 2>/dev/null; then
  grep -vE '^KIMI_CODE_(API_KEY|BASE_URL|MODEL|MODEL_ID)=' "$ENV_FILE" > "${ENV_FILE}.tmp" || true
  mv "${ENV_FILE}.tmp" "$ENV_FILE"
fi
cat >> "$ENV_FILE" <<EOF
# Reference only — Kimi Code CLI auth is in config.toml (below), not this file.
KIMI_CODE_API_KEY=${KEY}
KIMI_CODE_BASE_URL=${BASE_URL}
KIMI_CODE_MODEL=${MODEL}
KIMI_CODE_MODEL_ID=${MODEL_ID}
EOF

# CLI credentials live in ~/.kimi-code/config.toml (or $KIMI_CODE_HOME/config.toml)
CODE_HOME="${KIMI_CODE_HOME:-${HOME}/.kimi-code}"
mkdir -p "$CODE_HOME"
CONFIG_TOML="${CODE_HOME}/config.toml"

python3 - "$CONFIG_TOML" "$KEY" "$BASE_URL" "$MODEL" "$MODEL_ID" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
base_url = sys.argv[3]
raw_alias = sys.argv[4] or "kimi-k3"
raw_id = sys.argv[5] if len(sys.argv) > 5 else ""

MANAGED = {
    "kimi-code/k3": ("kimi-k3", "kimi-k3"),
    "k3": ("kimi-k3", "kimi-k3"),
    "kimi-code/kimi-for-coding": ("kimi-k2.7-code", "kimi-k2.7-code"),
    "kimi-for-coding": ("kimi-k2.7-code", "kimi-k2.7-code"),
    "kimi-code/kimi-for-coding-highspeed": (
        "kimi-k2.7-code-highspeed",
        "kimi-k2.7-code-highspeed",
    ),
    "kimi-for-coding-highspeed": (
        "kimi-k2.7-code-highspeed",
        "kimi-k2.7-code-highspeed",
    ),
}

def moonshot_alias(name: str) -> str:
    mapped = MANAGED.get(name)
    return mapped[0] if mapped else name

def moonshot_id(name: str) -> str:
    mapped = MANAGED.get(name)
    if mapped:
        return mapped[1]
    return name.rsplit("/", 1)[-1] if name else "kimi-k3"

alias = moonshot_alias(raw_alias)
model_id = moonshot_id(raw_id) if raw_id else moonshot_id(alias)
tail = model_id.rsplit("/", 1)[-1].lower()
ctx = 1048576 if tail in {"k3", "kimi-k3"} else 262144
text = path.read_text(encoding="utf-8") if path.exists() else ""

block = f'''[providers.kimi]
type = "kimi"
api_key = "{key}"
base_url = "{base_url}"
'''

# Replace existing [providers.kimi] section if present; else append.
pattern = re.compile(
    r"(?ms)^\[providers\.kimi\][^\[]*(?=^\[|\Z)",
)
if pattern.search(text):
    text = pattern.sub(block.rstrip() + "\n\n", text)
else:
    if text and not text.endswith("\n"):
        text += "\n"
    text = text + ("\n" if text else "") + block

current_match = re.search(
    r'(?m)^\s*default_model\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S+))',
    text,
)
current = (
    (current_match.group(1) or current_match.group(2) or current_match.group(3) or "").strip()
    if current_match
    else ""
)
broken = current in MANAGED or (current.rsplit("/", 1)[-1] if current else "") in {
    "k3",
    "kimi-for-coding",
    "kimi-for-coding-highspeed",
}
if not current or current != alias or broken:
    text = re.sub(r"(?m)^\s*default_model\s*=.*\n?", "", text)
    if current and current != alias:
        escaped = re.escape(current)
        text = re.sub(
            rf'(?ms)^\[models\.(?:"{escaped}"|\'{escaped}\'|{escaped})\][^\[]*(?=^\[|\Z)',
            "",
            text,
        )
    body = text.lstrip("\n")
    text = f'default_model = "{alias}"\n' + ("\n" + body if body else "")

escaped_alias = re.escape(alias)
table_re = re.compile(
    rf'(?ms)^\[models\.(?:"{escaped_alias}"|\'{escaped_alias}\'|{escaped_alias})\][^\[]*(?=^\[|\Z)'
)
table = (
    f'[models."{alias}"]\n'
    'provider = "kimi"\n'
    f'model = "{model_id}"\n'
    f"max_context_size = {ctx}\n"
)
if table_re.search(text):
    text = table_re.sub(table.rstrip() + "\n\n", text)
else:
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    text += table

path.write_text(text, encoding="utf-8")
print(f"Wrote Kimi Code provider credentials + default_model={alias} model={model_id} to {path}")
print("In-app LLM env (LLM_PROVIDER / CEREBRUM_LLM_* / KIMI_API_KEY) left unchanged.")
PY
