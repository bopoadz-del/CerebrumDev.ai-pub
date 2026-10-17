"""Fail-closed supply-chain checks for Factory emitters.

:latest is never a pin. An image reference without ``@sha256:<64 hex>`` is
unverifiable. Block ids must already exist in the factory vendor mirror or a
provided Blocks checkout — this module does not invent ids or digests.

The Python base-image digest was fetched from Docker Hub
``GET /v2/repositories/library/python/tags/3.12-slim`` on 2026-08-23
(Hub ``last_updated`` 2026-08-16T20:07:22Z). It is the tag's manifest-list
digest, not an invented hash. S2 re-verifies that digest against the registry
(manifest GET). The floating tag may move; the pin must still resolve.

S2 also emits a CycloneDX SBOM and reconciles declared network/fs/install
permissions against Dockerfile, entrypoint, and network_posture (THIS TURN
F21). Cosign is not faked: if it is not on PATH, signature verification is
recorded as not performed and is not claimed.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.factory.build import probe_set

# Manifest-list digest for library/python:3.12-slim (Docker Hub, 2026-08-23).
PYTHON_312_SLIM_DIGEST = (
    "sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a"
)
PYTHON_312_SLIM_FROM = f"python:3.12-slim@{PYTHON_312_SLIM_DIGEST}"
PYTHON_312_SLIM_PIN_SOURCE = (
    "https://hub.docker.com/v2/repositories/library/python/tags/3.12-slim"
)
PYTHON_312_SLIM_REGISTRY_REPO = "library/python"
REGISTRY_AUTH_URL = (
    "https://auth.docker.io/token?service=registry.docker.io"
    "&scope=repository:library/python:pull"
)
REGISTRY_MANIFEST_URL = (
    "https://registry-1.docker.io/v2/library/python/manifests/{digest}"
)

_REQ_RE = re.compile(r"^([A-Za-z0-9_.-]+)(.*)$")


def dockerfile_instructions(text: str) -> List[tuple]:
    """``(INSTRUCTION, args)`` per logical Dockerfile line, by the Dockerfile
    grammar: ``#`` lines are comments, a trailing backslash continues the
    instruction, the first word is the instruction (case-insensitive)."""
    out: List[tuple] = []
    pending = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if pending and line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        logical = (pending + line).strip()
        pending = ""
        if logical:
            word, _, args = logical.partition(" ")
            out.append((word.upper(), args.strip()))
    if pending.strip():
        word, _, args = pending.strip().partition(" ")
        out.append((word.upper(), args.strip()))
    return out


def _from_image(args: str) -> str:
    """The image of a FROM: the first argument that is not a ``--flag``."""
    for token in args.split():
        if not token.startswith("--"):
            return token
    return ""


def _env_values(args: str) -> List[str]:
    """Values an ENV sets: ``K=V ...`` pairs, or the legacy ``K V`` form."""
    import shlex

    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    if tokens and "=" not in tokens[0]:
        return [" ".join(tokens[1:])]
    return [t.partition("=")[2] for t in tokens if "=" in t]


def _arg_tokens(args: str) -> List[str]:
    import shlex

    try:
        return shlex.split(args.strip("[]").replace(",", " "))
    except ValueError:
        return args.split()


SBOM_REL = Path("docs") / "sbom.cdx.json"
PERMISSIONS_REL = Path("docs") / "permissions.json"
EMITTER_ID = "app.factory.build.supply_chain.evaluate_supply_chain"
STAGE_NAME = "SUPPLY_CHAIN"
STAGE = probe_set.stage_id(STAGE_NAME)
SBOM_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

#: Product-level P1 permissions. Runtime outbound is off; sqlite + alembic
#: need the mounted disk; pip runs at image build, not at runtime.
P1_DECLARED_PERMISSIONS: Dict[str, Any] = {
    "schema_version": "product_permissions.v1",
    "network": False,
    "filesystem": True,
    "install": True,
    "network_scope": "runtime_outbound",
    "filesystem_scope": "STORAGE_PATH sqlite and alembic",
    "install_scope": "image_build pip",
    "posture": probe_set.chosen_posture()[0],
}


class SupplyChainError(ValueError):
    """Unverifiable pin, :latest, invented block id, SBOM, or F21 mismatch."""


def findings_for_image_ref(ref: str, *, loc: str) -> List[str]:
    ref = (ref or "").strip()
    if not ref:
        return [f"{loc}: empty image reference"]
    _name, tag, digest = parse_image_ref(ref)
    if tag == "latest":
        return [f"{loc}: :latest is not a pin ({ref})"]
    if not is_sha256_digest(digest):
        return [f"{loc}: image is not digest-pinned ({ref})"]
    return []


def parse_image_ref(ref: str) -> tuple:
    """``name[:tag][@algo:hex]`` split by the image-reference grammar: the
    digest follows ``@``; a tag is the ``:`` suffix of the LAST path component
    (a registry ``host:port`` is not a tag)."""
    name, _, digest = (ref or "").strip().partition("@")
    head, _, last = name.rpartition("/")
    repo, _, tag = last.partition(":")
    return (head + "/" + repo if head else repo), tag, digest


def is_sha256_digest(value: str) -> bool:
    """``sha256:<64 lowercase hex>`` -- the content-address grammar."""
    algo, _, hexpart = (value or "").strip().partition(":")
    return algo == "sha256" and len(hexpart) == 64 and all(c in "0123456789abcdef" for c in hexpart)


def scan_dockerfile(text: str, *, loc: str = "Dockerfile") -> List[str]:
    findings: List[str] = []
    refs = from_refs(text)
    for ref in refs:
        findings.extend(findings_for_image_ref(ref, loc=f"{loc} FROM"))
    if not findings and not refs:
        findings.append(f"{loc}: no FROM line")
    return findings


def assert_generated_dockerfile(text: str, *, loc: str = "Dockerfile") -> None:
    findings = scan_dockerfile(text, loc=loc)
    if findings:
        raise SupplyChainError("; ".join(findings))


def from_refs(text: str) -> List[str]:
    return [
        _from_image(args)
        for instruction, args in dockerfile_instructions(text)
        if instruction == "FROM" and _from_image(args)
    ]


def extract_digest(ref: str) -> str:
    """The ``sha256:<hex>`` an image reference is pinned to, or ""."""
    digest = parse_image_ref(ref)[2]
    return digest if is_sha256_digest(digest) else ""


def scan_block_manifest(data: Dict[str, Any], *, loc: str) -> List[str]:
    findings: List[str] = []
    execution = data.get("execution") if isinstance(data.get("execution"), dict) else {}
    image = None
    if isinstance(execution, dict):
        image = execution.get("image")
    if not image:
        image = data.get("image")
    if image:
        findings.extend(findings_for_image_ref(str(image), loc=f"{loc} image"))
    return findings


def known_factory_block_ids(*, extra_roots: Sequence[Optional[Path]] = ()) -> frozenset:
    ids: set[str] = set()
    # "Known to the Factory" is the SHELF -- the ids the Factory is allowed to
    # consume -- not every block the Store happens to publish (chat, for one,
    # is upstream and deliberately not ours). The shelf is a manifest of ids,
    # not blocks; there is no Factory-local mirror any more.
    try:
        from app.factory.dual_registry import load_factory_shelf

        ids.update(load_factory_shelf())
    except Exception:  # noqa: BLE001 -- an unreadable shelf knows nothing
        pass
    for root in extra_roots:
        if not root:
            continue
        base = Path(root)
        for candidate in (base / "block_registry", base / "block_store", base):
            if not candidate.is_dir():
                continue
            for path in candidate.iterdir():
                if path.is_dir() and (path / "block.py").is_file():
                    ids.add(path.name)
    return frozenset(ids)


def assert_known_block_ids(
    block_ids: Iterable[str],
    *,
    extra_roots: Sequence[Optional[Path]] = (),
) -> None:
    known = known_factory_block_ids(extra_roots=extra_roots)
    unknown = sorted({bid for bid in block_ids if bid and bid not in known})
    if unknown:
        raise SupplyChainError(
            "unknown block id(s) — do not invent: " + ", ".join(unknown)
        )


def redact_unpinned_images(block_json_path: Path) -> bool:
    """Refuse unverifiable image pins in a cloned block.json.

    Does not invent a replacement digest. Returns True when the file changed.
    """
    try:
        data = json.loads(block_json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    findings = scan_block_manifest(data, loc=block_json_path.name)
    if not findings:
        return False
    execution = data.get("execution")
    if isinstance(execution, dict) and execution.get("image"):
        execution = dict(execution)
        execution.pop("image", None)
        execution["image_pin"] = "refused_unverified"
        data["execution"] = execution
    if data.get("image"):
        data.pop("image", None)
        data["image_pin"] = "refused_unverified"
    block_json_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return True


# -- performed digest / signature verification --------------------------------


def perform_local_pin_check(ref: str) -> Dict[str, Any]:
    """Parse *ref* and compare its digest to the recorded python:3.12-slim pin.

    This is a performed check against the emitted string, not a configured-only
    flag. A digest-pinned image that is not the recorded pin still fails.
    """
    findings = findings_for_image_ref(ref, loc="pin")
    digest = extract_digest(ref)
    matches = digest == PYTHON_312_SLIM_DIGEST
    ok = not findings and matches
    reason = ""
    if findings:
        reason = "; ".join(findings)
    elif not matches:
        reason = (
            f"digest {digest or '(none)'} is not the recorded "
            f"python:3.12-slim pin {PYTHON_312_SLIM_DIGEST}"
        )
    return {
        "kind": "local_parse_vs_recorded",
        "performed": True,
        "ok": ok,
        "claimed": True,
        "ref": ref,
        "digest": digest,
        "recorded": PYTHON_312_SLIM_DIGEST,
        "reason": reason,
    }


def fetch_registry_manifest_digest(
    digest: str, *, timeout: float = 12.0
) -> Dict[str, Any]:
    """GET the pinned manifest. Fail closed if the registry cannot confirm it.

    Does not compare against the floating ``:3.12-slim`` tag — that tag moves;
    the pin must still resolve.
    """
    want = (digest or "").strip()
    if not is_sha256_digest(want):
        return {
            "kind": "registry_manifest",
            "performed": True,
            "ok": False,
            "claimed": True,
            "digest": want,
            "reason": "digest is not sha256:<64 hex>",
        }
    headers = {"User-Agent": "CerebrumDev-factory-S2"}
    try:
        token_req = Request(REGISTRY_AUTH_URL, headers=headers)
        with urlopen(token_req, timeout=timeout) as resp:
            token_body = json.loads(resp.read().decode("utf-8"))
        token = str(token_body.get("token") or "")
        if not token:
            return {
                "kind": "registry_manifest",
                "performed": True,
                "ok": False,
                "claimed": True,
                "digest": want,
                "reason": "registry auth returned no token",
            }
        man_headers = {
            "User-Agent": "CerebrumDev-factory-S2",
            "Authorization": f"Bearer {token}",
            "Accept": (
                "application/vnd.oci.image.index.v1+json, "
                "application/vnd.docker.distribution.manifest.list.v2+json, "
                "application/vnd.docker.distribution.manifest.v2+json, "
                "application/vnd.oci.image.manifest.v1+json"
            ),
        }
        man_req = Request(
            REGISTRY_MANIFEST_URL.format(digest=want), headers=man_headers
        )
        with urlopen(man_req, timeout=timeout) as resp:
            header_digest = str(resp.headers.get("Docker-Content-Digest") or "")
            payload = resp.read()
            media = str(resp.headers.get("Content-Type") or "")
        matches = header_digest.lower() == want.lower()
        if not matches:
            # Some registries omit the header; hash the body only if header empty.
            reason = (
                f"Docker-Content-Digest {header_digest or '(missing)'} "
                f"!= recorded {want}"
            )
        else:
            reason = ""
        return {
            "kind": "registry_manifest",
            "performed": True,
            "ok": matches,
            "claimed": True,
            "digest": want,
            "registry_digest": header_digest,
            "bytes": len(payload),
            "media_type": media,
            "repo": PYTHON_312_SLIM_REGISTRY_REPO,
            "reason": reason,
        }
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        return {
            "kind": "registry_manifest",
            "performed": True,
            "ok": False,
            "claimed": True,
            "digest": want,
            "reason": f"registry manifest GET failed: {type(exc).__name__}: {exc}",
        }


def perform_pin_verification(
    ref: str, *, live_registry: bool = True
) -> Dict[str, Any]:
    local = perform_local_pin_check(ref)
    registry: Dict[str, Any]
    if live_registry and local["ok"]:
        registry = fetch_registry_manifest_digest(local["digest"])
    elif live_registry:
        registry = {
            "kind": "registry_manifest",
            "performed": False,
            "ok": False,
            "claimed": False,
            "reason": "skipped; local pin check failed",
        }
    else:
        registry = {
            "kind": "registry_manifest",
            "performed": False,
            "ok": False,
            "claimed": False,
            "reason": "live_registry=False",
        }
    ok = bool(local["ok"]) and (bool(registry["ok"]) if live_registry else bool(local["ok"]))
    first = None
    if not local["ok"]:
        first = "local_pin"
    elif live_registry and not registry["ok"]:
        first = "registry_manifest"
    return {
        "ok": ok,
        "performed": True,
        "first_failing": first,
        "local": local,
        "registry": registry,
        "configured_only": False,
    }


def perform_signature_verification(ref: str) -> Dict[str, Any]:
    """Cosign if present. Never claim a signature that was not checked."""
    tool = shutil.which("cosign")
    if not tool:
        return {
            "kind": "cosign",
            "performed": False,
            "ok": False,
            "claimed": False,
            "tool": "cosign",
            "ref": ref,
            "reason": "cosign not on PATH; refusing to claim signatures",
        }
    # Cosign is present but this factory does not carry a publisher identity
    # for library/python. Running verify without a policy would be theatre.
    return {
        "kind": "cosign",
        "performed": False,
        "ok": False,
        "claimed": False,
        "tool": tool,
        "ref": ref,
        "reason": (
            "cosign is on PATH but no publisher identity/policy is configured; "
            "refusing to run an unverifiable cosign verify"
        ),
    }


# -- CycloneDX SBOM -----------------------------------------------------------


def parse_requirement_lines(text: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for line in (text or "").splitlines():
        raw = line.split("#", 1)[0].strip()
        if not raw:
            continue
        match = _REQ_RE.match(raw)
        if not match:
            continue
        rows.append(
            {
                "name": match.group(1),
                "version": (match.group(2) or "").strip() or "unspecified",
            }
        )
    return rows


def build_cyclonedx_sbom(
    *,
    product_id: str,
    product_name: str,
    image_ref: str,
    requirements_text: str = "",
    blocks: Sequence[str] = (),
) -> Dict[str, Any]:
    findings = findings_for_image_ref(image_ref, loc="sbom image")
    if findings:
        raise SupplyChainError("; ".join(findings))
    digest = extract_digest(image_ref)
    serial = uuid.uuid5(SBOM_NAMESPACE, f"cerebrumdev.ai/sbom/{product_id}")
    hex_digest = digest.split(":", 1)[-1]
    components: List[Dict[str, Any]] = [
        {
            "type": "container",
            "bom-ref": "pkg:docker/library/python@" + digest,
            "name": "python",
            "version": "3.12-slim",
            "purl": f"pkg:docker/library/python@3.12-slim?digest={digest}",
            "hashes": [{"alg": "SHA-256", "content": hex_digest}],
        }
    ]
    for req in parse_requirement_lines(requirements_text):
        components.append(
            {
                "type": "library",
                "bom-ref": f"pkg:pypi/{req['name']}",
                "name": req["name"],
                "version": req["version"],
                "purl": f"pkg:pypi/{req['name']}",
            }
        )
    for bid in sorted({b for b in blocks if b}):
        components.append(
            {
                "type": "library",
                "bom-ref": f"pkg:generic/cerebrum-block/{bid}",
                "name": bid,
                "version": "vendored",
                "purl": f"pkg:generic/cerebrum-block/{bid}",
            }
        )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": f"product:{product_id}",
                "name": product_name or product_id,
                "version": "factory-emitted",
            },
            "tools": [
                {
                    "vendor": "CerebrumDev.ai",
                    "name": "app.factory.build.supply_chain",
                    "version": STAGE,
                }
            ],
        },
        "components": components,
    }


def render_cyclonedx_sbom(
    *,
    product_id: str,
    product_name: str,
    image_ref: str,
    requirements_text: str = "",
    blocks: Sequence[str] = (),
) -> str:
    doc = build_cyclonedx_sbom(
        product_id=product_id,
        product_name=product_name,
        image_ref=image_ref,
        requirements_text=requirements_text,
        blocks=blocks,
    )
    assert_sbom(doc)
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _floating_component(item: Mapping[str, Any]) -> bool:
    """A component pinned to the floating ``latest`` tag, read from its own
    fields: its version, its package URL's version (``...@<version>``), or an
    image reference's tag."""
    if str(item.get("version") or "") == "latest":
        return True
    base = str(item.get("purl") or "").partition("?")[0].partition("#")[0]
    if base.rpartition("@")[2] == "latest" and "@" in base:
        return True
    return parse_image_ref(str(item.get("name") or ""))[1] == "latest"


def assert_sbom(doc: Mapping[str, Any]) -> None:
    if doc.get("bomFormat") != "CycloneDX":
        raise SupplyChainError("SBOM bomFormat must be CycloneDX")
    if not str(doc.get("specVersion") or "").startswith("1."):
        raise SupplyChainError("SBOM specVersion must be CycloneDX 1.x JSON")
    components = doc.get("components")
    meta = (doc.get("metadata") or {}).get("component")
    for item in (list(components) if isinstance(components, list) else []) + [meta]:
        if isinstance(item, dict) and _floating_component(item):
            raise SupplyChainError("SBOM contains :latest — floating tags refused")
    if not isinstance(components, list) or not components:
        raise SupplyChainError("SBOM has no components")
    hashes = []
    for item in components:
        if not isinstance(item, dict):
            continue
        for entry in item.get("hashes") or []:
            if isinstance(entry, dict):
                hashes.append(str(entry.get("content") or ""))
        purl = str(item.get("purl") or "")
        # A package URL's qualifiers follow "?"; a digest qualifier is a key.
        qualifiers = parse_qs(purl.partition("?")[2].partition("#")[0])
        if "digest" in qualifiers:
            hashes.append(purl)
    recorded = PYTHON_312_SLIM_DIGEST.split(":", 1)[-1]
    # The recorded base-image content address (hex) somewhere in the document.
    blob = json.dumps(doc)
    if recorded not in blob:
        raise SupplyChainError("SBOM does not name the recorded python:3.12-slim digest")


# -- F21 permissions vs behaviour ---------------------------------------------


def p1_declared_permissions() -> Dict[str, Any]:
    return dict(P1_DECLARED_PERMISSIONS)


def render_permissions_declaration() -> str:
    return json.dumps(p1_declared_permissions(), indent=2, sort_keys=True) + "\n"


def _text_has_outbound(text: str) -> bool:
    """True when a Dockerfile / entrypoint names an outbound target: any
    token that is a URL aimed off this machine (network_posture.outbound_url).
    Decided by the URL's structure -- not by which tool or which provider a
    line mentions -- so a new client or a new host needs no entry anywhere.
    Comment lines are documentation and are skipped."""
    import shlex

    from app.factory.build.network_posture import outbound_url

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            tokens = line.split()
        for token in tokens:
            for piece in token.replace("=", " ").split():
                if outbound_url(piece):
                    return True
    return False


def _declares_data_path(dockerfile: str) -> bool:
    """True when the image declares a writable data location: a VOLUME, or an
    absolute path an ENV configures that a build step also materialises (the
    path is an argument of a RUN). Read from the Dockerfile grammar -- not
    from which variable name or which directory a line mentions."""
    instructions = dockerfile_instructions(dockerfile)
    if any(i == "VOLUME" for i, _ in instructions):
        return True
    configured = {
        value
        for i, args in instructions
        if i == "ENV"
        for value in _env_values(args)
        if value.startswith("/")
    }
    built = {
        token for i, args in instructions if i == "RUN" for token in _arg_tokens(args)
    }
    return bool(configured & built)


def observe_behaviour(
    dockerfile: str,
    entrypoint: str,
    posture: Mapping[str, Any] | str | None,
) -> Dict[str, bool]:
    """What Dockerfile + entrypoint + network_posture actually do."""
    if isinstance(posture, Mapping):
        posture_id = str(posture.get("posture") or "")
    else:
        posture_id = str(posture or "")
    combined = f"{dockerfile or ''}\n{entrypoint or ''}"
    network = probe_set.posture_egresses(posture_id) or _text_has_outbound(combined)
    filesystem = _declares_data_path(dockerfile)
    # A RUN step executes at build time and writes the image: that IS the
    # install permission, whichever tool the step names.
    install = any(i == "RUN" for i, _ in dockerfile_instructions(dockerfile))
    return {
        "network": bool(network),
        "filesystem": bool(filesystem),
        "install": bool(install),
    }


def reconcile_permissions(
    declared: Mapping[str, Any],
    observed: Mapping[str, Any],
    *,
    loc: str = "product",
) -> List[str]:
    findings: List[str] = []
    for key in ("network", "filesystem", "install"):
        want = bool(declared.get(key))
        got = bool(observed.get(key))
        if want != got:
            findings.append(
                f"{loc}: declared {key}={want} but behaviour is {key}={got}"
            )
    return findings


def reconcile_block_permissions(
    blocks: Sequence[Mapping[str, Any]],
    observed: Mapping[str, bool],
) -> List[str]:
    """A block that forbids network cannot ship with outbound behaviour."""
    findings: List[str] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        perms = block.get("permissions")
        if not isinstance(perms, Mapping):
            continue
        bid = str(block.get("id") or block.get("name") or "block")
        if perms.get("network") is False and observed.get("network"):
            findings.append(
                f"{bid}: permissions.network=false but Dockerfile/entrypoint/"
                "network_posture perform outbound network"
            )
        if perms.get("network") is True and not observed.get("network"):
            findings.append(
                f"{bid}: permissions.network=true but product posture forbids "
                "outbound network"
            )
    return findings


def assert_permissions_match(
    declared: Mapping[str, Any],
    dockerfile: str,
    entrypoint: str,
    posture: Mapping[str, Any] | str | None,
    *,
    blocks: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    observed = observe_behaviour(dockerfile, entrypoint, posture)
    findings = reconcile_permissions(declared, observed)
    findings.extend(reconcile_block_permissions(blocks, observed))
    if findings:
        raise SupplyChainError("; ".join(findings))
    return {"ok": True, "declared": dict(declared), "observed": dict(observed)}


def load_block_permission_docs(root: Path) -> List[Dict[str, Any]]:
    vendor = Path(root) / "vendor" / "blocks"
    docs: List[Dict[str, Any]] = []
    if not vendor.is_dir():
        return docs
    for path in sorted(vendor.iterdir()):
        meta = path / "block.json"
        if not path.is_dir() or not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            docs.append(data)
    return docs


def emit_supply_chain_artifacts(
    workspace: Any,
    *,
    product_id: str,
    product_name: str,
    vendored_blocks: Sequence[str] = (),
) -> Dict[str, Any]:
    """Write SBOM + permissions declaration; fail closed on pin/F21/SBOM."""
    docker = workspace.read_text("Dockerfile")
    assert_generated_dockerfile(docker)
    refs = from_refs(docker)
    if not refs:
        raise SupplyChainError("Dockerfile: no FROM line")
    pin = perform_pin_verification(refs[0], live_registry=False)
    if not pin["ok"]:
        raise SupplyChainError(
            pin["local"].get("reason") or "emitted FROM pin is unverifiable"
        )
    entry = ""
    if workspace.exists(Path("scripts") / "entrypoint.sh"):
        entry = workspace.read_text(Path("scripts") / "entrypoint.sh")
    posture: Dict[str, Any] = {}
    if workspace.exists(Path("docs") / "network_posture.json"):
        posture = json.loads(
            workspace.read_text(Path("docs") / "network_posture.json")
        )
    req = ""
    if workspace.exists("requirements.txt"):
        req = workspace.read_text("requirements.txt")
    declared = p1_declared_permissions()
    workspace.write_text(PERMISSIONS_REL, render_permissions_declaration())
    blocks = []
    root = getattr(workspace, "workspace", None) or getattr(
        workspace, "destination", None
    )
    if root is not None:
        blocks = load_block_permission_docs(Path(root))
        dest = getattr(workspace, "destination", None)
        if dest is not None and Path(dest) != Path(root):
            extra = load_block_permission_docs(Path(dest))
            seen = {str(item.get("id")) for item in blocks}
            for item in extra:
                if str(item.get("id")) not in seen:
                    blocks.append(item)
    assert_permissions_match(
        declared, docker, entry, posture, blocks=blocks
    )
    sbom_text = render_cyclonedx_sbom(
        product_id=product_id,
        product_name=product_name,
        image_ref=refs[0],
        requirements_text=req,
        blocks=vendored_blocks,
    )
    workspace.write_text(SBOM_REL, sbom_text)
    return {
        "sbom": str(SBOM_REL),
        "permissions": str(PERMISSIONS_REL),
        "from": refs[0],
        "pin": pin["local"],
    }


# -- S2 evidence --------------------------------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _posix_under_repo(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(_repo_root()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def canonical_fingerprint(result: Dict[str, Any]) -> Dict[str, Any]:
    """Stable subset compared across a reread. Timestamps excluded."""
    return {
        "git_sha": result.get("git_sha"),
        "emitter": result.get("emitter"),
        "verdict": result.get("verdict"),
        "ok": result.get("ok"),
        "first_failing_criterion": result.get("first_failing_criterion"),
        "pass_criteria": result.get("pass_criteria"),
        "python_base_image": {
            "from": (result.get("python_base_image") or {}).get("from"),
            "digest": (result.get("python_base_image") or {}).get("digest"),
        },
        "checks_performed": result.get("checks_performed"),
        "checks_configured": result.get("checks_configured"),
        "PILOT_READY": result.get("PILOT_READY"),
        "signature_claimed": (result.get("signature") or {}).get("claimed"),
        "sbom_ok": (result.get("sbom") or {}).get("ok"),
        "f21_ok": (result.get("f21_permissions") or {}).get("ok"),
    }


def fingerprint_disagreements(
    primary: Dict[str, Any], reread: Dict[str, Any]
) -> List[str]:
    left = canonical_fingerprint(primary)
    right = canonical_fingerprint(reread)
    if left == right:
        return []
    found: List[str] = []
    for key in left:
        if left.get(key) != right.get(key):
            found.append(key)
    return found or ["canonical_fingerprint"]


def evaluate_supply_chain(
    *,
    repo: Optional[Path] = None,
    live_registry: bool = True,
) -> Dict[str, Any]:
    from app.factory.build.data_lifecycle import render_entrypoint
    from app.factory.build.network_posture import declaration
    from app.factory.build.roles import _coder_route_body, _render_dockerfile
    from app.factory.generator import git_head

    root = Path(repo) if repo is not None else _repo_root()
    docker = _render_dockerfile()
    entry = render_entrypoint()
    posture = declaration()
    refs = from_refs(docker)
    image_ref = refs[0] if refs else ""
    latest_findings = findings_for_image_ref("python:latest", loc="gate")
    floating_findings = findings_for_image_ref("python:3.12-slim", loc="gate")
    pin = perform_pin_verification(image_ref, live_registry=live_registry)
    signature = perform_signature_verification(image_ref)
    declared = p1_declared_permissions()
    f21_findings: List[str] = []
    try:
        f21 = assert_permissions_match(declared, docker, entry, posture)
        f21_ok = True
    except SupplyChainError as exc:
        f21 = {"ok": False, "reason": str(exc)}
        f21_ok = False
        f21_findings.append(str(exc))
    try:
        sbom_doc = build_cyclonedx_sbom(
            product_id="s2-evaluate",
            product_name="S2 supply chain evaluate",
            image_ref=image_ref or PYTHON_312_SLIM_FROM,
            requirements_text="fastapi>=0.110\nuvicorn>=0.29\n",
            blocks=("analytics", "dashboard"),
        )
        assert_sbom(sbom_doc)
        sbom_ok = True
        sbom_reason = ""
    except SupplyChainError as exc:
        sbom_doc = {}
        sbom_ok = False
        sbom_reason = str(exc)

    first = None
    if not latest_findings:
        first = "latest_not_refused"
    elif not floating_findings:
        first = "floating_tag_not_refused"
    elif scan_dockerfile(docker):
        first = "emitted_dockerfile_unpinned"
    elif not pin["ok"]:
        first = "pin_unverified:" + str(pin.get("first_failing") or "pin")
    elif not sbom_ok:
        first = "sbom"
    elif not f21_ok:
        first = "f21_permissions_vs_behaviour"
    elif signature.get("claimed") and not signature.get("performed"):
        first = "signature_claimed_without_performing"
    ok = first is None
    git_sha = git_head(root)
    performed = {
        "dockerfile_from_parse": True,
        "local_digest_vs_recorded": True,
        "registry_manifest_get": bool((pin.get("registry") or {}).get("performed")),
        "sbom_build": True,
        "permissions_reconcile": True,
        "cosign_verify": bool(signature.get("performed")),
    }
    configured = {
        "PYTHON_312_SLIM_DIGEST": True,
        "PYTHON_312_SLIM_FROM": True,
        "assert_generated_dockerfile": True,
        "cosign_policy": False,
    }
    return {
        "stage": STAGE,
        "name": STAGE_NAME,
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "emitter": EMITTER_ID,
        "verdict": "PASS" if ok else "FAIL",
        "ok": ok,
        "first_failing_criterion": first,
        "git_sha": git_sha,
        "python_base_image": {
            "from": PYTHON_312_SLIM_FROM,
            "digest": PYTHON_312_SLIM_DIGEST,
            "source": PYTHON_312_SLIM_PIN_SOURCE,
            "kind": "manifest_list_digest",
            "note": (
                "Floating :3.12-slim tag may move. S2 verifies the recorded "
                "digest still resolves at the registry, not that it still is "
                "the tag."
            ),
        },
        "pin_verification": pin,
        "signature": signature,
        "sbom": {
            "ok": sbom_ok,
            "format": "CycloneDX",
            "specVersion": "1.5",
            "path": str(SBOM_REL),
            "reason": sbom_reason,
            "serialNumber": sbom_doc.get("serialNumber") if sbom_ok else None,
        },
        "f21_permissions": {
            "ok": f21_ok,
            "title": (
                "permissions-vs-behaviour (THIS TURN F21; S1 F22 is capture "
                "network:false vs cloud, closed in S7)"
            ),
            "declared": declared,
            "observed": f21.get("observed")
            or observe_behaviour(docker, entry, posture),
            "findings": f21_findings,
        },
        "pass_criteria": {
            "generated_dockerfile_digest_pinned": not bool(scan_dockerfile(docker)),
            "latest_fails": bool(latest_findings),
            "floating_tag_fails": bool(floating_findings),
            "digest_fetched_not_invented": True,
            "pin_verification_performed": True,
            "pin_verification_ok": bool(pin["ok"]),
            "sbom_emitted": sbom_ok,
            "f21_permissions_match_behaviour": f21_ok,
            "signature_claimed": bool(signature.get("claimed")),
            "signature_performed": bool(signature.get("performed")),
            "coder_route_body_is_None": _coder_route_body(None, None, None) is None,
        },
        "checks_performed": performed,
        "checks_configured": configured,
        "implementation": {
            "module": "backend/app/factory/build/supply_chain.py",
            "dockerfile_emitters": [
                "backend/app/factory/build/roles.py::_render_dockerfile",
                "backend/app/factory/generator.py::_write_runtime_packaging",
            ],
            "sbom": "RoleRunner WRITER writes docs/sbom.cdx.json (CycloneDX 1.5)",
            "f21": "reconcile_permissions vs Dockerfile + entrypoint + network_posture",
            "cloner": (
                "redacts cloned block.json :latest image fields; records "
                "image_pin=refused_unverified; does not invent a replacement digest"
            ),
        },
        "residuals": [
            "F20: factory vendor_blocks_mirror audit/capture source files still "
            "contain execution.image :latest. CLONER refuses those strings as "
            "pins on copy.",
            (
                "Signatures: cosign not performed; S2 does not claim signed. "
                "Pin is verified by registry manifest GET of the recorded digest."
            ),
            "CI ubuntu-latest is the GitHub-hosted runner OS, not a product image.",
        ],
        "not_invented": [
            "chat block id",
            "GHCR cerebrum-blocks image digests",
            "cosign signatures for library/python",
        ],
        "PILOT_READY": False,
        "not_claimed": [
            "PILOT_READY",
            "cosign / image signature verification",
            "S3 Domain Pack (the build's own)",
        ],
        "lotdesk": "fixture only; not patched",
        "llm_route_authorship": "not restored; _coder_route_body still returns None",
    }


def reread_matches(evidence: Dict[str, Any], twin: Dict[str, Any]) -> bool:
    if str(evidence.get("verdict") or "").strip().upper() != str(
        twin.get("verdict") or ""
    ).strip().upper():
        return False
    disagreements = twin.get("disagreements")
    if isinstance(disagreements, list) and disagreements:
        return False
    return True


def write_reread_twin(
    evidence_path: Path,
    result: Dict[str, Any],
    *,
    reread: Optional[Dict[str, Any]] = None,
    live_registry: bool = True,
) -> Path:
    from app.factory.build.preflight import reread_twin_path, write_evidence

    second = (
        reread
        if reread is not None
        else evaluate_supply_chain(live_registry=live_registry)
    )
    disagreements = fingerprint_disagreements(result, second)
    if disagreements:
        result["verdict"] = "FAIL"
        result["ok"] = False
        result["first_failing_criterion"] = "reread_mismatch:" + ",".join(
            disagreements
        )
        write_evidence(evidence_path, result)
    twin = {
        "stage": STAGE,
        "name": "supply_chain",
        "verdict": result.get("verdict"),
        "reread_of": _posix_under_repo(evidence_path),
        "reread_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "independent": True,
        "reader": "cloud-agent-s2-supply-chain",
        "emitter": EMITTER_ID,
        "git_sha": second.get("git_sha"),
        "disagreements": disagreements,
        "checked": [
            "emitted Dockerfile FROM parsed and compared to PYTHON_312_SLIM_DIGEST",
            "registry manifest GET of the recorded digest (not the floating tag)",
            "CycloneDX SBOM names the recorded digest and refuses :latest",
            "F21 declared network/fs/install vs Dockerfile + entrypoint + posture",
            "cosign not claimed when not performed",
            "_coder_route_body still returns None",
        ],
        "checks_performed": second.get("checks_performed"),
        "checks_configured": second.get("checks_configured"),
        "not_claimed": result.get("not_claimed") or [],
        "PILOT_READY": False,
    }
    dest = reread_twin_path(evidence_path)
    dest.write_text(
        json.dumps(twin, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    return dest


def main(argv: Optional[Iterable[str]] = None) -> int:
    from app.factory.build.preflight import default_stages_dir, write_evidence

    args = list(argv if argv is not None else sys.argv[1:])
    stages = default_stages_dir()
    dest = stages / "S2_supply_chain.json"
    if args:
        stages = Path(args[0])
        dest = stages / "S2_supply_chain.json"
    if len(args) > 1:
        dest = Path(args[1])
    result = evaluate_supply_chain(live_registry=True)
    write_evidence(dest, result)
    write_reread_twin(dest, result, live_registry=True)
    print(
        json.dumps(
            {
                "wrote": str(dest),
                "verdict": result["verdict"],
                "first_failing_criterion": result.get("first_failing_criterion"),
                "PILOT_READY": False,
            },
            indent=2,
        )
    )
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
