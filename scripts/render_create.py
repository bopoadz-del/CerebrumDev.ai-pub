"""Create CerebrumDev.ai services on Render via the Render API.

Prefer the Blueprint in render.yaml. This script is a fallback that must stay
aligned with that Blueprint (disk, API keys, Ollama model, SPA env).

Set RENDER_API_KEY and RENDER_OWNER_ID env vars before running.
"""
import json
import os
import sys
import urllib.request
import urllib.error

RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
OWNER_ID = os.getenv("RENDER_OWNER_ID", "")
REPO = "https://github.com/bopoadz-del/CerebrumDev.ai"

if not OWNER_ID:
    print("ERROR: Set RENDER_OWNER_ID environment variable")
    sys.exit(1)

if not RENDER_API_KEY:
    print("ERROR: Set RENDER_API_KEY environment variable")
    sys.exit(1)

BACKEND_VARS = [
    {"key": "ENV", "value": "production"},
    {"key": "CEREBRUM_DEV_API_KEY", "generateValue": True},
    {"key": "CEREBRUM_API_URL", "value": "https://cerebrum-blocks-10ug.onrender.com"},
    # Must be provisioned manually to match Cerebrum-Blocks — do not generate.
    {"key": "CEREBRUM_API_KEY", "value": os.getenv("CEREBRUM_API_KEY", "")},
    {"key": "FRONTEND_URL", "value": "https://cerebrum-dev.com"},
    {"key": "CORS_ALLOW_ORIGINS", "value": "https://cerebrum-dev.com,https://www.cerebrum-dev.com,https://cerebrumdev-frontend-kkz2.onrender.com"},
    {"key": "LLM_PROVIDER", "value": "ollama"},
    {"key": "OLLAMA_URL", "value": os.getenv("OLLAMA_URL", "https://ollama.com")},
    {"key": "OLLAMA_MODEL", "value": os.getenv("OLLAMA_MODEL", "kimi-k2.7-code:cloud")},
    {"key": "OLLAMA_API_KEY", "value": os.getenv("OLLAMA_API_KEY", "")},
    {"key": "QWEN_API_KEY", "value": os.getenv("QWEN_API_KEY", "")},
    {"key": "QWEN_BASE_URL", "value": os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")},
    {"key": "QWEN_MODEL", "value": os.getenv("QWEN_MODEL", "qwen-plus")},
    {"key": "CHROMA_PERSIST_DIR", "value": os.getenv("CHROMA_PERSIST_DIR", "/app/storage/chroma")},
    {"key": "STORAGE_PATH", "value": "/app/storage"},
    {"key": "PYTHONIOENCODING", "value": "utf-8"},
]


def create_service(payload):
    req = urllib.request.Request(
        "https://api.render.com/v1/services",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}")
        print(e.read().decode())
        sys.exit(1)


backend_payload = {
    "type": "web_service",
    "name": "cerebrumdev-backend",
    "ownerId": OWNER_ID,
    "repo": REPO,
    "autoDeploy": "yes",
    "branch": "master",
    "serviceDetails": {
        "env": "docker",
        "plan": "starter",
        "region": "oregon",
        "envSpecificDetails": {"dockerfilePath": "./Dockerfile"},
        "healthCheckPath": "/health",
        "numInstances": 1,
        "disk": {
            "name": "cerebrumdev-storage",
            "mountPath": "/app/storage",
            "sizeGB": 1,
        },
    },
    "envVars": BACKEND_VARS,
}

frontend_payload = {
    "type": "web_service",
    "name": "cerebrumdev-frontend",
    "ownerId": OWNER_ID,
    "repo": REPO,
    "autoDeploy": "yes",
    "branch": "master",
    "rootDir": "frontend",
    "serviceDetails": {
        "env": "node",
        "plan": "starter",
        "region": "oregon",
        "envSpecificDetails": {
            "buildCommand": "npm install && npm run build",
            "startCommand": "node serve.mjs",
        },
        "healthCheckPath": "/",
        "numInstances": 1,
    },
    "envVars": [
        {"key": "NODE_VERSION", "value": "20"},
        {"key": "VITE_API_URL", "value": "https://api.cerebrum-dev.com"},
        # Do not bake CEREBRUM_DEV_API_KEY into a VITE_* var — the SPA uses
        # cdt_ login tokens. Dashboard is source of truth for frontend env.
    ],
}

print("Creating backend service...")
backend = create_service(backend_payload)
print(json.dumps(backend, indent=2))

print("\nCreating frontend service...")
frontend = create_service(frontend_payload)
print(json.dumps(frontend, indent=2))
print(
    "\nNext: confirm frontend VITE_API_URL in the dashboard. Do not set VITE_API_KEY."
)
