"""The Universal Kernel gold (docs/UNIVERSAL_KERNEL.md) is ratified and immutable —
same doctrine as the Product Delivery Standard: ratify a new version, never edit
in place. The Wave 1 trust-spine pack must render through the delivery standard
fail-closed, proving the gold is fire-ready for Kimi Code.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.factory import delivery_standard

ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "docs" / "UNIVERSAL_KERNEL.md"
GOLD_SHA256 = "787ccf9671df0d3d3d5548d9cd37bdb86ba5ad1e3154d72d2ddb73c1e6ae596f"

WAVE1_PLATFORM = {
    "product_repository": "bopoadz-del/Cerebrum-Blocks",
    "default_branch": "main",
    "working_branch": "feat/universal-kernel-wave1-trust-spine",
    "pull_request": "none — wave not started; one PR per capability",
    "head_sha": "7995abe7e90a718fbb0b49641bf32ad2bebe3bad",
    "capability_repository": "bopoadz-del/Cerebrum-Blocks (self — kernel kits land in block_store/kits/universal_kernel/)",
    "factory_repository": "bopoadz-del/CerebrumDev.ai",
    "reference_repositories": "The Fork (golden donor — read-only extraction source); bopoadz-del/CerebrumDev.ai (factory-hardened donors: accounts, rate limiting, runtime/dna.py, cerebrum_product_kernel/); bopoadz-del/Projecttwo (second validation product)",
    "deployment_target": "OTHER — kernel kits are versioned libraries consumed via the Store, not deployed services",
    "production_url": "not applicable — kits are consumed by products, not deployed",
    "test_result": "unknown — wave not started; certification tests ship with each kit",
    "deployment_state": "not applicable — Store library, no service deployment"
}

WAVE1_PACK = {
    "platform_name": "Cerebrum Universal Kernel — Wave 1 (Trust Spine)",
    "domain": "Platform infrastructure — identity, authorization, isolation, audit and provenance services",
    "product_type": "Certified kernel kit library (block_store/kits/universal_kernel/)",
    "target_users": "The Cerebrum Factory (generator) and every future Cerebrum product codebase",
    "mission": "Extract, harden and certify the six trust-spine capabilities — identity, authorization_policy, scope_guard, rate_limit_guard, audit_evidence and provenance_verification — as versioned kernel kits in Cerebrum-Blocks under block_store/kits/universal_kernel/, each shipping code + tests + a hash-pinned kernel_manifest.json and proven against neutral fixtures, so that no Cerebrum product ever rebuilds authentication, authorization, tenant isolation, rate limiting, audit evidence or provenance verification again.",
    "domain_purpose": "Provide the trust spine every Cerebrum product stands on: a user can authenticate, be authorized through the full tenant → project → role → permission → action chain, be isolated from other tenants, be rate-limited against abuse, have every significant action recorded as chained audit evidence, and have kit integrity verified before anything else runs.",
    "primary_users": [
        "The Factory generator (consumes kits when generating products)",
        "Product codebases (import kits as pinned, versioned dependencies)",
        "Kernel auditors (verify certification evidence)"
    ],
    "required_roles": [
        "kernel_maintainer",
        "factory_generator",
        "product_consumer",
        "auditor"
    ],
    "required_product_modules": [
        "identity — register/login/logout/current-user, verify-email and password-reset via single-purpose hashed tokens (cdt_/cdk_/cdv_/cdr_ family), PBKDF2 hashing, dual sqlite/PostgreSQL store behind one contract (factory-hardened, merged with Fork JWT mechanics)",
        "authorization_policy — server-authoritative user → tenant → project → role → permission → action → approval-requirement chain (Fork RBAC extended beyond admin/user)",
        "scope_guard — tenant/project isolation with the 404-not-403 doctrine: cross-scope access never leaks existence",
        "rate_limit_guard — sliding-window 429 protection for the authentication surface and sensitive endpoints",
        "audit_evidence — DB-backed audit records with per-record digests, hash chaining and exportable evidence bundles (Fork JSONL graduated to real evidence)",
        "provenance_verification — checksum-manifest verification before anything else runs; hash-pinned kit manifests (factory runtime/dna.py + cerebrum_product_kernel/provenance.py pattern)"
    ],
    "core_business_workflows": [
        "Extraction: inspect donor (Fork or factory module) → neutralize domain concepts → implement generic kit → tests on neutral fixtures → kernel_manifest.json with sha256 → certification PR → merge-when-green",
        "Certification: every kit PR proves the five-point contract (code, tests, manifest, fail-closed behavior, product-pin readiness) against fixtures containing no Fork or FinanceOps data",
        "Consumption: a generated product pins kit versions in its DNA manifest and verifies them before boot"
    ],
    "authoritative_calculations": [
        "None — acceptance is behavioral, not numerical",
        "The sole numeric authority is sha256: kit manifests and pinned hashes must match byte-for-byte"
    ],
    "domain_rules": [
        "Extract generic, leave domain behind — construction material stays in The Fork, finance in FinanceOps; a line naming a domain concept does not cross",
        "One capability = one PR = merge-when-green; no batch extractions",
        "Neutralization is a rewrite, not a rename",
        "Kit tests must pass against neutral fixtures only",
        "The Fork is read-only; extraction never writes to the donor",
        "Blocks flow STORE → PRODUCT only"
    ],
    "high_impact_actions": [
        "Certifying a kit version (writing its hash-pinned kernel_manifest.json)",
        "Merging a kit into the Store default branch",
        "Changing an already-certified kit (requires a new version, never an in-place edit)",
        "Factory generator consumption of a kit into a product template"
    ],
    "prohibited_autonomous_actions": [
        "Writing domain-specific material into a kernel kit",
        "Modifying The Fork in any way",
        "Editing a certified kit in place or mutating a pinned manifest",
        "Silent mocks or unlabeled fallbacks in kit code",
        "Batching multiple capabilities into one PR"
    ],
    "data_sources": [
        "The Fork (read-only donor: JWT mechanics, RBAC seed, JSONL audit seed)",
        "CerebrumDev.ai factory modules (accounts.py, accounts_store, auth rate limiting, runtime/dna.py, cerebrum_product_kernel/provenance.py + audit.py)",
        "Neutral synthetic fixtures only — no Fork or FinanceOps data in tests"
    ],
    "required_connectors": [
        "GitHub (source inspection and the PR flow)",
        "Store manifest registry (kernel_manifest.json per kit under block_store/kits/universal_kernel/)"
    ],
    "required_exports": [
        "kernel_manifest.json per certified kit: {name, version, sha256, dependencies, provenance, acceptance_test}",
        "Certification report per kit PR: test results, provenance, hash evidence"
    ],
    "security_regulatory_rules": [
        "404-not-403: cross-tenant access never leaks existence",
        "Tokens and credentials hashed at rest; no secrets in kit code or fixtures",
        "Fail closed on missing config or integrity mismatch; honesty labels on any fallback",
        "Kit integrity verified before any kit code executes"
    ],
    "demo_data_requirements": [
        "Neutral synthetic users, tenants, projects and roles with no resemblance to Fork or FinanceOps data",
        "Fixtures clearly labeled as synthetic certification fixtures"
    ],
    "domain_acceptance_conditions": [
        "Each of the six kits passes the five-point certification contract: code, tests, hash-pinned manifest, fail-closed behavior, product-pin readiness",
        "The trust chain runs end-to-end on a neutral harness: register → login → role → cross-tenant access returns 404 → auth abuse returns 429 → significant action produces a hash-chained audit record → tampered manifest halts boot",
        "Kit test suites are green in CI on the Store repo",
        "Static scan finds zero domain terms (construction/finance) in kit code"
    ]
}


def test_universal_kernel_gold_is_immutable():
    digest = hashlib.sha256(GOLD_PATH.read_bytes()).hexdigest()
    assert digest == GOLD_SHA256, (
        "docs/UNIVERSAL_KERNEL.md changed — ratify a new version of the gold, "
        "never edit it in place"
    )


def test_gold_pins_the_one_hour_standard():
    text = GOLD_PATH.read_text(encoding="utf-8")
    assert "The One-Hour Standard" in text
    assert "24 capabilities" in text
    assert "no developer should rebuild" in text


def test_wave1_trust_spine_pack_renders_complete_brief():
    brief = delivery_standard.render(WAVE1_PLATFORM, WAVE1_PACK)
    assert "trust-spine" in brief
    for marker in delivery_standard._slot_markers():
        assert marker not in brief


def test_wave1_pack_fails_closed_when_capability_list_is_empty():
    broken = dict(WAVE1_PACK, required_product_modules=[])
    with pytest.raises(ValueError, match="required_product_modules"):
        delivery_standard.render(WAVE1_PLATFORM, broken)
