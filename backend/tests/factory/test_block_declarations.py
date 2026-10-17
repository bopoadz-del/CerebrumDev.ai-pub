"""Every former block-id dispatch site decides from what the block DECLARES.

Owner ruling: a site branches on the block's signed manifest (capability_class,
preconditions, requires_inputs, factory_attach), never on its id. Each test
runs against a fixture Store of invented ids and proves both directions: a
block that declares the fact gets the behaviour whatever it is called, and a
block that carries a real Store id but declares nothing gets none of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from app.factory.build.block_inputs import prepare_block_input, render_block_inputs_module
from app.factory.build.block_obligations import resource_obligations, schema_obligations
from app.factory.build.network_posture import apply_p1_cloned_block
from app.factory.build.offline_adapters import package_imports_own_parsers
from app.factory.build.workflow_accept import (
    event_bus_workflow_capability_ids,
    grounded_event_bus_handler_body,
    handler_constructs_event_bus_step,
    needs_grounded_event_bus_handler,
)
from app.factory.dual_registry import not_cleared_block_ids


def _store(root: Path, manifests: Dict[str, Dict[str, Any]]) -> Path:
    for bid, manifest in manifests.items():
        d = root / "block_registry" / bid
        d.mkdir(parents=True)
        (d / "block.json").write_text(json.dumps({"id": bid, **manifest}), encoding="utf-8")
    return root


ENSURE = {
    "kind": "team", "ref": "crew_id", "scope": "platform", "ensure": "open_crew",
    "ensure_input": ["user_id"], "carry": "crew_id", "into": ["crew_context"],
    "why": "open_crew mints crew_id; crew_context refuses without it",
}

#: Invented ids carry the declarations; real Store ids carry none.
FIXTURE = {
    "zq_bus": {"capability_class": "events"},
    "zq_conductor": {"capability_class": "orchestration"},
    "zq_crier": {"capability_class": "messaging"},
    "zq_crew": {"capability_class": "membership", "preconditions": [ENSURE]},
    "zq_reader": {
        "capability_class": "document_extraction",
        "requires_inputs": [{
            "name": "scan_path", "type": "string", "required": False,
            "satisfied_by": ["scan_path", "text"], "why": "parses a document",
        }],
    },
    "zq_eye": {"capability_class": "vision_capture"},
    "zq_talk": {"factory_attach": {"cleared": False, "reason": "serves its own surface"}},
    "event_bus": {},
    "workflow": {},
    "notification": {},
    "team": {},
    "document_engine": {},
    "capture": {},
    "chat": {},
}


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    root = _store(tmp_path / "store", FIXTURE)
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(root))
    return root


# -- block_inputs: input shaping follows capability_class ----------------------


def test_a_declared_events_block_is_shaped_whatever_its_id():
    out = prepare_block_input("zq_bus", {"reminder_type": "due", "pet": "x"})
    assert out["topic"] == "due"
    assert out["block"] == out["tool"] == "zq_bus"


def test_a_real_id_that_declares_nothing_is_passed_through():
    data = {"reminder_type": "due"}
    assert prepare_block_input("event_bus", data) == data
    assert prepare_block_input("workflow", data) == data


def test_a_declared_orchestrator_builds_steps_for_its_peers():
    out = prepare_block_input(
        "zq_conductor", {"reminder_type": "due"}, roster=["zq_conductor", "zq_bus"]
    )
    assert [s["block"] for s in out["steps"]] == ["zq_bus"]
    assert out["steps"][0]["input"]["tool"] == "zq_bus"


def test_a_declared_messenger_targets_a_peer_never_itself():
    out = prepare_block_input(
        "zq_crier", {"channel": "mcp"}, roster=["zq_crier", "zq_bus"]
    )
    assert out["block"] == "zq_bus"


def test_the_build_map_wins_over_the_store():
    """A build passes the classes its vendored manifests declare."""
    out = prepare_block_input(
        "event_bus", {"reminder_type": "due"}, capability_classes={"event_bus": "events"}
    )
    assert out["topic"] == "due"
    assert prepare_block_input("zq_bus", {"a": "b"}, capability_classes={}) == {"a": "b"}


def test_the_rendered_product_module_dispatches_on_the_baked_classes():
    src = render_block_inputs_module({}, {"zq_bus": "events"})
    ns: Dict[str, Any] = {"__name__": "rendered_block_inputs"}
    exec(compile(src, "block_inputs.py", "exec"), ns)  # noqa: S102 -- our own render
    shaped = ns["prepare_block_input"]("zq_bus", {"reminder_type": "due"})
    assert shaped["topic"] == "due" and shaped["tool"] == "zq_bus"
    assert ns["prepare_block_input"]("event_bus", {"reminder_type": "due"}) == {
        "reminder_type": "due"
    }


# -- workflow_accept: the orchestrated-events contract -------------------------


class _Cap:
    def __init__(self, cid, bids):
        self.capability_id = cid
        self.block_ids = bids


def test_the_events_contract_binds_by_declared_class():
    inventory = [
        _Cap("declared", ["zq_conductor", "zq_bus"]),
        _Cap("named_only", ["workflow", "event_bus"]),
    ]
    assert event_bus_workflow_capability_ids(inventory) == ["declared"]
    assert needs_grounded_event_bus_handler("x", ["zq_conductor", "zq_bus"])
    assert not needs_grounded_event_bus_handler("x", ["workflow", "event_bus"])


def test_the_grounded_body_names_the_declared_blocks():
    body = grounded_event_bus_handler_body("bookings", ["zq_conductor", "zq_bus", "zq_crew"])
    assert "'zq_bus'" in body and "'zq_conductor'" in body
    assert "'event_bus'" not in body and "'workflow'" not in body


def test_a_handler_naming_the_declared_events_block_is_seen():
    src = 'steps = [{"block": "zq_bus", "input": payload}]\n'
    assert handler_constructs_event_bus_step(src)
    assert not handler_constructs_event_bus_step(src.replace("zq_bus", "event_bus"))


# -- block_obligations: preconditions / requires_inputs ------------------------


def test_resource_obligations_are_the_declared_ensures():
    rules = resource_obligations()
    assert set(rules) == {"zq_crew"}
    assert rules["zq_crew"]["carry"] == "crew_id"
    assert rules["zq_crew"]["scope"] == "platform"


def test_schema_obligations_are_the_declared_satisfied_by():
    rules = schema_obligations()
    assert set(rules) == {"zq_reader"}
    assert rules["zq_reader"]["any_of"] == ["scan_path", "text"]
    assert rules["zq_reader"]["add"] == {"name": "scan_path", "type": "str", "required": False}


# -- dual_registry: factory_attach --------------------------------------------


def test_refusals_are_the_declared_ones():
    refused = not_cleared_block_ids()
    assert refused == {"zq_talk": "serves its own surface"}


# -- network_posture: the offline capture adapter -------------------------------


class _Workspace:
    def __init__(self, root: Path):
        self.root = root

    def exists(self, rel) -> bool:
        return (self.root / rel).is_file()

    def read_text(self, rel) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def write_text(self, rel, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _vendor(ws_root: Path, bid: str, manifest: Dict[str, Any]) -> _Workspace:
    d = ws_root / "vendor" / "blocks" / bid
    d.mkdir(parents=True)
    (d / "block.json").write_text(json.dumps({"id": bid, **manifest}), encoding="utf-8")
    return _Workspace(ws_root)


def test_the_offline_adapter_follows_vision_capture(tmp_path):
    ws = _vendor(tmp_path / "a", "zq_eye", {"capability_class": "vision_capture", "inputs": []})
    assert apply_p1_cloned_block(ws, "zq_eye")
    assert (tmp_path / "a" / "vendor" / "blocks" / "zq_eye" / "block.py").is_file()

    bare = _vendor(tmp_path / "b", "capture", {"inputs": []})
    assert not apply_p1_cloned_block(bare, "capture")
    assert not (tmp_path / "b" / "vendor" / "blocks" / "capture" / "block.py").exists()


# -- roles_handlers: the parsers stub follows the package's own imports --------


def test_a_package_that_imports_its_own_parsers_needs_them():
    assert package_imports_own_parsers("from .parsers import parse\n", "zq_docs")
    assert package_imports_own_parsers("x = 1\n", "zq_docs", "import vendor.zq_docs.parsers\n")
    assert not package_imports_own_parsers("x = 1\n", "document_engine")
