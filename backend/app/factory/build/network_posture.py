"""Factory network posture — exactly one, applied at emission time.

S7: .env.example and tests/conftest.py already claim offline. Capture's
vendored block.json defaulted llm_provider to deepseek (cloud) while
declaring permissions.network: false. That is permissions-vs-behaviour
(S1 F22; the S7 brief called it F21).

Chosen: P1 (offline strict).
Rejected: P2 local Ollama, P3 cloud allowlist. See REJECTED_ALTERNATIVES.

Do not invent a chat block. U11/ui_schema_builder stays unused because
Cerebrum-Blocks is not cloned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from app.factory.build import probe_set

# Exactly one, chosen in the probe set (it refuses a second). The id, the
# reason and the rejected alternatives are data there, never literals here.
NETWORK_POSTURE, _CHOSEN = probe_set.chosen_posture()
POSTURE_ID = NETWORK_POSTURE
NETWORK_POSTURE_REASON: str = _CHOSEN["reason"]

REJECTED_ALTERNATIVES: Dict[str, str] = probe_set.rejected_postures()

P1_SOCKET_BLOCKER_MARKERS = (
    "offline suite: outbound connection",
    '_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}',
)

POSTURE_DOC = Path("docs") / "network_posture.json"

DELIVERY_ARTIFACTS: Tuple[str, ...] = (
    "Dockerfile",
    "README.md",
    ".env.example",
    "deploy/contract.json",
    "app/main.py",
    "requirements.txt",
    "docs/network_posture.json",
    "docs/build_provenance.json",
)

#: Hosts that are this machine. P1 permits loopback (the product talks to
#: itself, and its test suite blocks every other socket).
_LOOPBACK = ("localhost",)


def outbound_url(value: str) -> str:
    """``value`` when it is a URL aimed OFF this machine, else "".

    Decided by the URL's own structure: a scheme and a host that is neither
    loopback by name nor a loopback / unspecified IP address.
    """
    import ipaddress
    from urllib.parse import urlsplit

    text = str(value or "").strip().strip("'\"")
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if not parts.scheme or not host:
        return ""
    # RFC 6761: "localhost" and every name under the .localhost TLD loop
    # back -- decided by the host name's last DNS label.
    if host.rstrip(".").split(".")[-1] in _LOOPBACK:
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return text
    return "" if (address.is_loopback or address.is_unspecified) else text


def _setting_values(text: str, rel: str) -> List[Tuple[str, str]]:
    """(name, value) for every setting a file DECLARES -- ``KEY=value`` lines
    of an env file, ``ENV`` / ``ARG`` instructions of a Dockerfile. Comments
    are documentation, not settings."""
    out: List[Tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if rel == "Dockerfile":
            head, _, rest = line.partition(" ")
            if head.upper() not in ("ENV", "ARG"):
                continue
            line = rest.strip()
        name, sep, value = line.partition("=")
        if sep and name.strip():
            out.append((name.strip(), value.strip()))
    return out


def _code_urls(source: str) -> List[str]:
    """Outbound URLs written as string constants in CODE (the syntax tree, so
    a comment or docstring that names a URL is not a call)."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                docstrings.add(id(value))
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings and outbound_url(node.value)
    ]

DECLARED_GENERATOR_POSTURE_EXCEPTIONS: Tuple[str, ...] = (
    "ProductGenerator._write_env_example documents CEREBRUM_API_URL "
    "(template-path handlers POST to the store; S6 leftover)",
    "ProductGenerator._write_runtime_packaging may emit estate Postgres / FastEmbed",
)

P1_ENV_EXAMPLE = """# Copy to .env and fill in. Never commit real values.
ENV=production

# Where the sqlite file lives. Mount a volume at this path to persist.
STORAGE_PATH=./data

# Deploy identity for /health and scripts/rollback.sh (optional).
# APP_REVISION=rev-n
# APP_MARK=baseline

# P1: offline strict. Local/scripted OCR only. No LLM provider. No Ollama.
# No store URL. No store key. Vendored blocks, in-process dispatch.
# tests/conftest.py refuses non-loopback sockets. That blocker is unchanged.

# Capability write routes require this bearer token (HTTP 401 without it).
PLATFORM_TOKEN=set-at-deploy
"""

P1_CAPTURE_ADAPTER = '''"""P1 capture adapter. Factory CLONER emission.

Local OCR when tesseract is on PATH; otherwise scripted extraction from
provided text. No cloud LLM. No Ollama. No outbound HTTP.

Replaces the Store shim in the product workspace so a delivered platform
cannot inherit cloud-LLM defaults while block.json says network:false.
The Factory vendor-mirror Store-shim snapshot is unchanged. Blocks was not written.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

POSTURE = "P1"


def _local_ocr(path: Path) -> str:
    exe = shutil.which("tesseract")
    if not exe or not path.is_file():
        return ""
    try:
        proc = subprocess.run(
            [exe, str(path), "stdout", "-l", "eng"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip()


def _scripted_structure(raw: str) -> Dict[str, Any]:
    emails = re.findall(r"[\\w.+-]+@[\\w-]+\\.[\\w.-]+", raw)
    urls = re.findall(r"https?://\\S+", raw)
    numbers = re.findall(r"\\b\\d+(?:\\.\\d+)?\\b", raw)
    summary = raw[:240] + ("…" if len(raw) > 240 else "")
    return {
        "entities": {
            "emails": emails,
            "urls": urls,
            "numbers": numbers[:20],
        },
        "tags": ["p1", "scripted"],
        "summary": summary,
        "clean_text": " ".join(raw.split()),
    }


def _extract_text(data: Dict[str, Any]) -> tuple[str, str]:
    for key in ("raw_text", "text", "content", "body"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), "scripted"
    for key in ("path", "file", "image", "input"):
        value = data.get(key)
        if isinstance(value, (str, Path)) and Path(value).is_file():
            ocr = _local_ocr(Path(value))
            if ocr:
                return ocr, "tesseract"
            return "", "tesseract_unavailable"
    return "", "scripted"


def run(**kwargs: Any) -> Dict[str, Any]:
    data = kwargs.get("input", kwargs)
    if not isinstance(data, dict):
        data = {"input": data}
    raw, engine = _extract_text(data)
    structured = _scripted_structure(raw)
    digest = hashlib.sha256((raw or "empty").encode("utf-8")).hexdigest()[:16]
    return {
        "posture": POSTURE,
        "capture_id": digest,
        "raw_text": raw,
        "ocr_engine": engine,
        "llm_provider": "none",
        **structured,
    }
'''


class PostureError(ValueError):
    """Artifacts disagree with the chosen network posture."""


def declaration() -> Dict[str, Any]:
    return {
        "schema_version": "network_posture.v1",
        "posture": NETWORK_POSTURE,
        "reason": NETWORK_POSTURE_REASON,
        "rejected": REJECTED_ALTERNATIVES,
        "socket_blocker": "unchanged_non_loopback_refuse",
    }


def declaration_json() -> str:
    return json.dumps(declaration(), indent=2, sort_keys=True) + "\n"


def banner() -> str:
    return f"{NETWORK_POSTURE}: {NETWORK_POSTURE_REASON}"


def readme_section() -> str:
    return (
        "\n"
        "## Network posture\n"
        "\n"
        f"`{NETWORK_POSTURE}` — {NETWORK_POSTURE_REASON}\n"
        "\n"
        "This value is factory-stamped. A coding-agent README cannot change it.\n"
    )


def apply_p1_capture_manifest(data: Mapping[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(data))
    out.setdefault("permissions", {})["network"] = False
    desc = str(out.get("description") or "")
    if NETWORK_POSTURE not in desc:
        out["description"] = (
            f"{NETWORK_POSTURE}: local/scripted OCR and scripted structure. "
            "Cloud LLM keys and Ollama are unused. " + desc
        )
    for inp in out.get("inputs") or []:
        if not isinstance(inp, dict):
            continue
        name = inp.get("name")
        if name == "llm_provider":
            inp["default"] = "none"
        elif name == "ocr_engine":
            inp["default"] = "tesseract"
        elif name in {
            "ollama_base_url",
            "ollama_model",
            "deepseek_api_key",
            "openrouter_api_key",
            "anthropic_api_key",
            "vector_db_url",
            "deepseek_model",
            "openrouter_model",
            "anthropic_model",
        }:
            inp["default"] = ""
        elif name == "store_captures":
            inp["default"] = False
    return out


def apply_p1_cloned_block(workspace: Any, bid: str) -> bool:
    """Swap in the offline P1 adapter for a block that DECLARES itself a
    vision capture (``capability_class``), whatever its id. A block that
    declares no such class is left as vendored (and CLONER then fails
    closed if its shim needs a runtime nobody can vendor). A vendored copy
    that predates the field is read through the pinned Store's manifest."""
    from app.factory.store_kits import VISION_CAPTURE, block_capability_class, capability_class

    dest = Path("vendor") / "blocks" / bid
    meta = dest / "block.json"
    data: Dict[str, Any] = {}
    if workspace.exists(meta):
        loaded = json.loads(workspace.read_text(meta))
        data = loaded if isinstance(loaded, dict) else {}
    if (capability_class(data) or block_capability_class(bid)) != VISION_CAPTURE:
        return False
    workspace.write_text(dest / "block.py", P1_CAPTURE_ADAPTER)
    if data:
        workspace.write_text(
            meta,
            json.dumps(apply_p1_capture_manifest(data), indent=2, sort_keys=True)
            + "\n",
        )
    return True


def _posture_file(root: Path, rel: str, fallback: Path | None) -> Path | None:
    candidate = Path(root) / rel
    if candidate.is_file():
        return candidate
    if fallback is not None:
        alt = Path(fallback) / rel
        if alt.is_file():
            return alt
    return None


def assert_workspace_posture(root: Path, fallback: Path | None = None) -> None:
    """Fail closed if a RoleRunner tree does not declare exactly P1.

    *fallback* is the committed destination when WRITER is staged: a rework
    pass does not rewrite README, so the check must see the previous stamp.
    """
    base = Path(root)
    findings: List[str] = []
    missing = [rel for rel in DELIVERY_ARTIFACTS if _posture_file(base, rel, fallback) is None]
    if missing:
        raise PostureError("missing posture artifacts: " + ", ".join(missing))

    doc_path = _posture_file(base, str(POSTURE_DOC), fallback)
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    if doc.get("posture") != NETWORK_POSTURE:
        findings.append(
            f"{POSTURE_DOC}: posture {doc.get('posture')!r} != {NETWORK_POSTURE!r}"
        )
    if NETWORK_POSTURE_REASON not in str(doc.get("reason") or ""):
        findings.append(f"{POSTURE_DOC}: reason does not match NETWORK_POSTURE_REASON")

    for rel in DELIVERY_ARTIFACTS:
        text = _posture_file(base, rel, fallback).read_text(encoding="utf-8")
        if NETWORK_POSTURE not in text:
            findings.append(f"{rel}: does not name {NETWORK_POSTURE}")
        # P1 is "no outbound target". Read structurally from each artifact,
        # never by searching for the names of particular services. README is
        # prose and is not searched: what the product DOES is held by its own
        # socket-blocking test suite (P1_SOCKET_BLOCKER_MARKERS).
        if rel == "deploy/contract.json":
            try:
                contract = json.loads(text)
            except ValueError:
                contract = None
            if not isinstance(contract, dict):
                findings.append(f"{rel}: not a JSON object")
            else:
                if contract.get("datastores"):
                    findings.append(
                        f"{rel}: P1 forbids datastores {contract['datastores']!r}"
                    )
                for name, value in sorted((contract.get("environment") or {}).items()):
                    if outbound_url(str(value)):
                        findings.append(f"{rel}: P1 forbids outbound {name}")
        if rel in {".env.example", "Dockerfile"}:
            for name, value in _setting_values(text, rel):
                if outbound_url(value):
                    findings.append(f"{rel}: P1 forbids outbound setting {name}")
        if rel == "app/main.py":
            for url in _code_urls(text):
                findings.append(f"{rel}: P1 forbids outbound URL {url}")
    if findings:
        raise PostureError("; ".join(findings))


def scan_disagreements(texts: Iterable[Tuple[str, str]]) -> List[str]:
    other = set(REJECTED_ALTERNATIVES) - {NETWORK_POSTURE}
    hits: List[str] = []
    for loc, text in texts:
        for token in other:
            if f'NETWORK_POSTURE = "{token}"' in text or f'"posture": "{token}"' in text:
                hits.append(f"{loc}: declares {token}")
    return hits
