"""Platform grounding stage: every answer-producing path routes through it.

Verdicts: grounded / flag-as-estimate / blocked. Blocked answers are null —
never a raw ungrounded fallback. Every verdict is persisted.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.core.grounding import (
    STRICT_FIGURES_ENV,
    strict_figures,
    VERDICT_BLOCKED,
    VERDICT_FLAG,
    VERDICT_GROUNDED,
    evaluate_grounding,
    persist_verdict,
    verdict_log_path,
)


def _estate_kit():
    """The estate kit lives in the Store (the Factory holds no kits)."""
    from app.factory.kit_pack import store_kit_dir

    kit = store_kit_dir("private_estate_operations")
    assert kit is not None, "Store estate kit not found -- set CEREBRUM_BLOCKS_ROOT"
    return kit



class TestEvaluateGrounding:
    def test_plain_answer_with_no_claims_is_grounded(self):
        verdict = evaluate_grounding(
            "You can refine the blueprint or approve it.",
            sources=["Blueprint pending approval."],
            query="what now?",
        )
        assert verdict["verdict"] == VERDICT_GROUNDED
        assert verdict["allowed_response"] == "You can refine the blueprint or approve it."

    def test_invented_url_is_blocked_with_null_answer(self):
        verdict = evaluate_grounding(
            "Download your build at https://cdn.example.com/build.zip",
            sources=["No product has been generated yet."],
            query="where is my download?",
        )
        assert verdict["verdict"] == VERDICT_BLOCKED
        assert verdict["allowed_response"] is None
        assert any("url" in r.lower() for r in verdict["reasons"])

    def test_url_present_in_sources_is_grounded(self):
        verdict = evaluate_grounding(
            "Docs live at https://docs.internal/guide",
            sources=["See https://docs.internal/guide for setup."],
            query="where are docs?",
        )
        assert verdict["verdict"] == VERDICT_GROUNDED

    def test_urls_are_found_by_the_url_grammar_not_a_pattern(self):
        """A URL is a token the stdlib parser gives a scheme and a host:
        markdown brackets and trailing punctuation are not part of it, any
        scheme counts, and a scheme with no host (mailto:) is not a URL."""
        from app.core.grounding import _urls

        text = (
            "See [docs](https://docs.internal/guide?x=1). Mirror: ftp://files.internal/a, "
            "or mail mailto:ops@example.com."
        )
        assert _urls(text) == ["https://docs.internal/guide?x=1", "ftp://files.internal/a"]

        grounded = evaluate_grounding(
            "Read [the guide](https://docs.internal/guide).",
            sources=["See https://docs.internal/guide for setup."],
        )
        assert grounded["verdict"] == VERDICT_GROUNDED
        invented = evaluate_grounding(
            "Fetch it from ftp://files.invalid/build.zip",
            sources=["No product has been generated yet."],
        )
        assert invented["verdict"] == VERDICT_BLOCKED

    def test_ungrounded_figure_is_flagged_with_disclosure(self):
        verdict = evaluate_grounding(
            "That typically costs 4500 USD per month.",
            sources=["Pricing is not configured for this session."],
            query="what does it cost?",
        )
        assert verdict["verdict"] == VERDICT_FLAG
        assert verdict["allowed_response"] is not None
        assert "4500" in " ".join(verdict.get("unsupported_figures", []))
        assert "estimate" in verdict["allowed_response"].lower()

    def test_figure_present_in_sources_is_grounded(self):
        verdict = evaluate_grounding(
            "Your corpus has 4500 documents.",
            sources=["Corpus size: 4500 documents."],
            query="corpus size?",
        )
        assert verdict["verdict"] == VERDICT_GROUNDED

    def test_strict_mode_blocks_ungrounded_figures(self):
        verdict = evaluate_grounding(
            "Torque the bolt to 85 Nm.",
            sources=["No torque specs retrieved."],
            query="torque?",
            strict=True,
        )
        assert verdict["verdict"] == VERDICT_BLOCKED
        assert verdict["allowed_response"] is None


class TestVerdictPersistence:
    def test_persist_verdict_appends_jsonl(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        record = persist_verdict(
            {
                "surface": "test",
                "query": "q",
                "verdict": VERDICT_GROUNDED,
            }
        )
        path = verdict_log_path()
        assert path.is_file()
        lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        assert lines[-1]["verdict"] == VERDICT_GROUNDED
        assert lines[-1]["surface"] == "test"
        assert record["recorded_at"]


class TestChatRoutesThroughGrounding:
    @pytest.mark.asyncio
    async def test_blocked_answer_never_streams_and_persists_verdict(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        from app.core.session_store import create_session
        from app.factory.floor_actions import FloorAction
        from app.routers.chat import _stream_response

        state = create_session("grounding-test-session", "user-1")
        assert state is not None

        poisoned = {
            "message": "Your build is ready at https://fake.invalid/download.zip",
            "chain": None,
            "rules": [],
        }
        with patch(
            "app.routers.chat.generate_chain_suggestion",
            new=AsyncMock(return_value=poisoned),
        ):
            events = []
            async for evt in _stream_response(
                "grounding-test-session", "where is my zip?", action=FloorAction.CHAIN
            ):
                events.append(evt)

        joined = "".join(events)
        assert "fake.invalid" not in joined, "blocked answer text must never reach the stream"
        assert "grounding" in joined
        assert '\\"verdict\\": \\"blocked\\"' in joined or '"verdict": "blocked"' in joined.replace("\\", "")

        log = verdict_log_path()
        assert log.is_file(), "verdict must be persisted to the audit store"
        entries = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
        assert entries[-1]["verdict"] == VERDICT_BLOCKED

    @pytest.mark.asyncio
    async def test_grounded_answer_streams_and_persists_verdict(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        from app.core.session_store import create_session
        from app.factory.floor_actions import FloorAction
        from app.routers.chat import _stream_response

        create_session("grounding-ok-session", "user-1")
        fine = {
            "message": "I suggest starting with the intake capability.",
            "chain": None,
            "rules": [],
        }
        with patch(
            "app.routers.chat.generate_chain_suggestion",
            new=AsyncMock(return_value=fine),
        ):
            events = []
            async for evt in _stream_response(
                "grounding-ok-session", "what first?", action=FloorAction.CHAIN
            ):
                events.append(evt)

        joined = "".join(events)
        assert "intake" in joined
        entries = [
            json.loads(l)
            for l in verdict_log_path().read_text(encoding="utf-8").splitlines()
        ]
        assert entries[-1]["verdict"] == VERDICT_GROUNDED


class TestFactoryEmitsGroundingStage:
    def test_steward_runtime_query_carries_grounding_verdict(self):
        """The emitted steward runtime must route retrieval through the stage:
        the kit source wires a grounding verdict + audit persistence."""

        kit = (_estate_kit() / "steward_runtime")
        api_src = (kit / "api.py").read_text(encoding="utf-8")
        assert "retrieval_verdict" in api_src, "steward query path has no grounding stage"
        assert "record_verdict" in api_src, "steward query path does not persist verdicts"
        grounding_src = (kit / "grounding.py").read_text(encoding="utf-8")
        assert "persist_audit_event" in grounding_src

    def test_retrieval_verdict_zero_hits_is_insufficient_with_null_answer(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "steward_grounding_under_test",
            (_estate_kit() / "steward_runtime/grounding.py"),
        )
        module = importlib.util.module_from_spec(spec)
        # The kit imports app.steward.audit_store, absent on the platform —
        # stub it so the pure verdict function is testable here.
        import sys
        import types

        sys.modules.setdefault("app.steward", types.ModuleType("app.steward"))
        fake_store = types.ModuleType("app.steward.audit_store")
        fake_store.persist_audit_event = lambda *a, **k: {}
        sys.modules["app.steward.audit_store"] = fake_store
        spec.loader.exec_module(module)

        verdict = module.retrieval_verdict([])
        assert verdict["verdict"] == "insufficient_sources"
        assert verdict["answer"] is None
        ok = module.retrieval_verdict([{"chunk_id": "c1"}])
        assert ok["verdict"] == "grounded"


class TestFigureVerificationIsByValue:
    """Figures are compared as values against the figures sources assert.

    The previous test was `figure not in corpus_string`, so any number that
    happened to be a substring of a real one verified clean. A fabricated
    "25" passed because a source somewhere said "1250".
    """

    SOURCES = ["The Q3 report shows revenue of 1250 units across 3 regions."]

    def test_a_figure_that_is_only_a_substring_is_not_grounded(self):
        result = evaluate_grounding(
            "There were 25 incidents.", sources=self.SOURCES, strict=False
        )
        assert result["verdict"] == VERDICT_FLAG
        assert result["unsupported_figures"] == ["25"]

    def test_the_same_value_spelled_differently_is_grounded(self):
        """1,250 and 1250.00 are the figure the source asserts, not new ones."""
        for spelling in ("1250", "1,250", "1250.00"):
            result = evaluate_grounding(
                f"Revenue was {spelling} units.", sources=self.SOURCES
            )
            assert result["verdict"] == VERDICT_GROUNDED, spelling

    def test_a_genuinely_invented_figure_is_still_caught(self):
        result = evaluate_grounding(
            "Revenue was 9987 units.", sources=self.SOURCES, strict=False
        )
        assert result["verdict"] == VERDICT_FLAG
        assert result["unsupported_figures"] == ["9987"]

    def test_single_digits_are_not_treated_as_claims(self):
        """"3 regions" is prose, not a reportable figure."""
        result = evaluate_grounding("There are 3 regions.", sources=self.SOURCES)
        assert result["verdict"] == VERDICT_GROUNDED


class TestStrictFiguresIsReachable:
    """`strict` shipped from the start and no caller ever passed it."""

    def test_it_defaults_to_blocking(self, monkeypatch):
        monkeypatch.delenv(STRICT_FIGURES_ENV, raising=False)
        assert strict_figures() is True

    @pytest.mark.parametrize("value", ["0", "false", "off", "no", ""])
    def test_it_can_be_turned_down_to_flagging(self, monkeypatch, value):
        monkeypatch.setenv(STRICT_FIGURES_ENV, value)
        assert strict_figures() is False

    def test_the_chat_surface_asks_for_strict(self):
        """A wrong build count is worse than a refusal on this surface."""
        import inspect

        from app.routers import chat as chat_router

        source = inspect.getsource(chat_router)
        assert "strict=strict_figures()" in source
