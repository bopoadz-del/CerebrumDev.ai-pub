"""S1: lane-authority map. LotDesk symptoms name an owner module."""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build.authority import ROLE_CONTRACTS
from app.factory.build.domain_acceptance import inspect_lotdesk_domain
from app.factory.build.lotdesk_gate import reject_lotdesk_as_shipped
from app.factory.build.preflight import S4_EVIDENCE_FILENAME, write_evidence
from app.factory.build.roles import _coder_route_body
from app.factory.build.root_cause import (
    EMITTER_ID,
    LOTDESK_CLASS_CODES,
    REQUIRED_DEFECT_IDS,
    REQUIRED_F_IDS,
    REQUIRED_U_IDS,
    canonical_fingerprint,
    defect_owners,
    evaluate_root_cause,
    fingerprint_disagreements,
    lane_authority_map,
    lotdesk_symptom_owners,
    reread_matches,
    write_reread_twin,
)

ROOT = Path(__file__).resolve().parents[3]


def test_required_u_and_f_keys_exist():
    owners = defect_owners()
    assert tuple(f"U{i}" for i in range(1, 13)) == REQUIRED_U_IDS
    assert tuple(f"F{i}" for i in range(1, 30)) == REQUIRED_F_IDS
    assert len(REQUIRED_DEFECT_IDS) == 41
    missing = [code for code in REQUIRED_DEFECT_IDS if code not in owners]
    assert missing == []
    for code, spec in owners.items():
        assert spec["owner_module"], code
        assert spec["title"], code
        assert spec["lane"], code
        assert spec["owner_present"] is True, f"{code} -> {spec['owner_module']}"
    assert owners["F7"]["owner_module"].endswith("domain_pack.py")


def test_lane_authority_map_cites_authority_contracts():
    mapped = lane_authority_map()
    assert mapped["source"] == "backend/app/factory/build/authority.py"
    for role in ROLE_CONTRACTS:
        assert role.value in mapped["roles"]
        lanes = [glob for _root, glob in ROLE_CONTRACTS[role].write_lanes]
        assert mapped["roles"][role.value]["write_lanes"] == lanes
    assert mapped["roles"]["COLLECTOR"]["read_only"] is True
    # #705: the Factory test bootstrap is the ROOT conftest.py (TESTER's), so
    # the product's tests/conftest.py is never overwritten.
    assert mapped["roles"]["TESTER"]["write_lanes"] == ["tests/**", "conftest.py"]
    assert "vendor/**" in mapped["roles"]["CLONER"]["write_lanes"]
    assert "tests/**" not in mapped["roles"]["WRITER"]["write_lanes"]
    assert "conftest.py" not in mapped["roles"]["WRITER"]["write_lanes"]


def test_lotdesk_class_symptoms_map_to_named_owner_module():
    shipped = reject_lotdesk_as_shipped()
    domain = inspect_lotdesk_domain()
    symptoms = lotdesk_symptom_owners(shipped=shipped, domain=domain)
    assert symptoms["ok"] is True
    assert symptoms["unmapped"] == []
    assert symptoms["lotdesk"] == "fixture only; not patched"
    found = set(symptoms["codes"])
    for code in LOTDESK_CLASS_CODES:
        assert code in found, f"LotDesk inspect must still prove {code}"
    owners = defect_owners()
    by_code = {item["code"]: item for item in symptoms["mapped"]}
    for code in found:
        assert code in owners, code
        assert by_code[code]["owner_module"] == owners[code]["owner_module"]
        assert by_code[code]["owner_module"]
        assert (ROOT / by_code[code]["owner_module"]).is_file(), code


def test_root_cause_evidence_and_reread_mismatch_fails(tmp_path):
    result = evaluate_root_cause()
    assert result["verdict"] == "PASS"
    assert result["ok"] is True
    assert result["PILOT_READY"] is False
    assert result["emitter"] == EMITTER_ID
    assert result["lane_authority_map"]["source"].endswith("authority.py")
    assert result["s4_evidence"] == S4_EVIDENCE_FILENAME
    assert S4_EVIDENCE_FILENAME == "S4_ship_kernel.json"
    dest = tmp_path / "S1_root_cause.json"
    write_evidence(dest, result)
    twin_path = write_reread_twin(dest, result)
    written = json.loads(dest.read_text(encoding="utf-8"))
    twin = json.loads(twin_path.read_text(encoding="utf-8"))
    assert twin["disagreements"] == []
    assert reread_matches(written, twin) is True
    assert fingerprint_disagreements(result, evaluate_root_cause()) == []
    assert canonical_fingerprint(result) == canonical_fingerprint(evaluate_root_cause())

    tampered = dict(twin)
    tampered["disagreements"] = ["owner_modules"]
    assert reread_matches(written, tampered) is False


def test_coder_route_body_stays_none_on_s1_path():
    assert _coder_route_body(None, None, None) is None
    owners = defect_owners()
    assert owners["U5"]["owner_symbol"] == "_coder_route_body"
    assert owners["U5"]["status"] == "closed"
    assert owners["U4"]["status"] == "open"
    assert S4_EVIDENCE_FILENAME in owners["U4"]["note"]
    assert S4_EVIDENCE_FILENAME in owners["U12"]["note"]
    assert owners["U7"]["status"] == "open"
