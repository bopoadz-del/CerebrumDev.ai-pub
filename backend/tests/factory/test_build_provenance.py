"""A delivered pilot can say which Factory and which Store made it.

Every export shipped ``factory_commit`` and ``blocks_commit`` as "unknown".
``converge`` read them out of ``ctx.state`` and nothing in the build pipeline
ever put them there, so the two fields that answer "which code made this?"
answered "no idea" -- in a product whose whole pitch is that its answers are
traceable.
"""

from __future__ import annotations

import json

from app.factory.build.authority import BuildRole
from app.factory.build.build_provenance import (
    UNKNOWN,
    missing_provenance,
    receipt_hash,
    resolve_blocks_commit,
    resolve_factory_commit,
    resolve_provenance,
)
from app.factory.build.gates import GateContext, gate_provenance_complete


class _Ctx:
    def __init__(self, state=None, blocks_root=None):
        self.state = dict(state or {})
        self.blocks_root = blocks_root


class TestResolvingRatherThanDefaulting:
    def test_explicit_state_wins(self):
        ctx = _Ctx({"factory_commit": "f1", "blocks_commit": "b2"})

        assert resolve_provenance(ctx) == {
            "factory_commit": "f1",
            "blocks_commit": "b2",
        }

    def test_the_factory_commit_resolves_without_being_told(self):
        """This repo is a git checkout, and production sets RENDER_GIT_COMMIT,
        which ``git_head`` already falls back to."""
        assert resolve_factory_commit(_Ctx()) != UNKNOWN

    def test_the_store_commit_falls_back_to_the_lock(self):
        """The lock ships inside the image and records the sha the CLONER
        verified against, so it answers even with no Store checkout on disk.
        """
        assert resolve_blocks_commit(_Ctx(blocks_root=None)) != UNKNOWN

    def test_a_literal_unknown_in_state_is_not_an_answer(self):
        """The bug being fixed wrote the string "unknown"; carrying it
        forward would launder the same hole through a different path."""
        ctx = _Ctx({"factory_commit": "unknown", "blocks_commit": "  "})

        resolved = resolve_provenance(ctx)

        assert resolved["factory_commit"] != "unknown"
        assert resolved["blocks_commit"] != ""

    def test_the_writer_receipt_rides_along_when_there_is_one(self):
        assert "writer_receipt" not in resolve_provenance(_Ctx())
        assert resolve_provenance(_Ctx({"writer_receipt": "sha256:ab"}))[
            "writer_receipt"
        ] == "sha256:ab"


class TestTheReceiptIdentifiesOneRun:
    def test_the_same_receipt_hashes_the_same(self):
        class _R:
            def to_dict(self):
                return {"status": "completed", "tools": ["a", "b"]}

        assert receipt_hash(_R()) == receipt_hash(_R())
        assert receipt_hash(_R()).startswith("sha256:")

    def test_a_different_run_hashes_differently(self):
        assert receipt_hash({"status": "completed"}) != receipt_hash(
            {"status": "failed"}
        )

    def test_an_unhashable_receipt_yields_no_claim(self):
        assert receipt_hash(object()) == ""


class TestTheGateJudgesTheArtifact:
    def _workspace(self, tmp_path, payload):
        d = tmp_path / "docs" / "provenance"
        d.mkdir(parents=True)
        if payload is not None:
            (d / "provenance.json").write_text(json.dumps(payload), encoding="utf-8")
        return GateContext(workspace=tmp_path, role=BuildRole.STORE_MANAGER)

    def test_unknown_values_are_refused(self, tmp_path):
        ctx = self._workspace(
            tmp_path, {"factory_commit": "unknown", "blocks_commit": "unknown"}
        )

        result = gate_provenance_complete(ctx)

        assert not result.ok
        assert result.reason == "provenance_unknown"
        assert "factory_commit" in result.detail

    def test_one_unknown_is_still_a_refusal(self, tmp_path):
        ctx = self._workspace(
            tmp_path, {"factory_commit": "a1b2", "blocks_commit": "unknown"}
        )

        result = gate_provenance_complete(ctx)

        assert not result.ok
        assert result.findings == ["blocks_commit"]

    def test_a_missing_document_is_refused_by_its_own_name(self, tmp_path):
        result = gate_provenance_complete(self._workspace(tmp_path, None))

        assert not result.ok
        assert result.reason == "provenance_missing"

    def test_resolved_values_pass_and_are_reported(self, tmp_path):
        ctx = self._workspace(
            tmp_path, {"factory_commit": "a1b2", "blocks_commit": "c3d4"}
        )

        result = gate_provenance_complete(ctx)

        assert result.ok
        assert result.payload["blocks_commit"] == "c3d4"


def test_missing_provenance_treats_a_non_mapping_as_answering_nothing():
    assert missing_provenance(None) == ["factory_commit", "blocks_commit"]
