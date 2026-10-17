#!/bin/sh
# Apply versioned migrations against the persistent disk, then serve.
# Failure here refuses boot (fail-closed). Do not start uvicorn on a
# schema that is behind head.
set -eu
cd /app
python -m alembic upgrade head
python -c "import json, os; print(json.dumps({'event': 'entrypoint.start', 'revision': os.getenv('APP_REVISION', ''), 'storage': os.getenv('STORAGE_PATH', '')}))"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
