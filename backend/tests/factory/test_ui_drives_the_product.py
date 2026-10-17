"""What a pilot serves must drive the product, and the prompt says so.

A pilot is handed to a DevOps team to deploy and test. The factory's own
default console was `<p>Generated platform UI.</p>` -- a page whose only job
was to make the ui_served_200 check go green. No writer could start above
that. FinOps (sess_065fc3eac75c4f62) also shipped a React app nothing builds:
no node step in its Dockerfile, so the real UI was dead source.

The bar is stated in the writer prompt (v8) and enforced by the
ui_end_to_end gate, so the agent knows it before writing rather than after a
rework round.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build.store_acceptance import render_ui_index
from app.factory.build.ui_e2e import _render_probe
from app.factory.build.writer_prompt import render_writer_prompt


class _Blueprint:
    product_id = "p"
    product_name = "Acme Ops"
    vertical = "ops"
    summary = "s"


class TestTheDefaultConsoleDrivesTheProduct:
    def test_it_discovers_capabilities_instead_of_hardcoding_them(self):
        html = render_ui_index("Acme Ops")
        assert "/v1/capabilities" in html
        assert '"/v1/" + cap' in html, "routes must be built from what it discovers"

    def test_it_can_create_and_list_a_record(self):
        html = render_ui_index("Acme Ops")
        assert 'method: "POST"' in html
        assert 'data-act="list"' in html

    def test_it_takes_a_token_rather_than_embedding_one(self):
        html = render_ui_index("Acme Ops")
        assert 'id="tok"' in html and "Bearer" in html
        assert "dev-local-token" not in html

    def test_it_shows_the_authority_label_an_answer_carries(self):
        html = render_ui_index("Acme Ops")
        assert "LABEL_KEYS" in html
        for key in ("label", "authority", "precedence"):
            assert f'"{key}"' in html

    def test_it_offers_only_surfaces_the_product_declares(self):
        """It used to call /v1/rag/query unconditionally, which 404s on a
        product with no RAG surface and failed that build."""
        html = render_ui_index("Acme Ops")
        assert "/openapi.json" in html
        assert '$("ask").hidden = !RAG' in html

    def test_it_is_not_the_placeholder_it_replaced(self):
        html = render_ui_index("Acme Ops")
        assert "Generated platform UI." not in html
        assert len(html) > 2000, "a console, not a banner"

    def test_it_carries_the_product_name(self):
        assert "Acme Ops" in render_ui_index("Acme Ops")


class TestThePromptStatesTheBarTheGateEnforces:
    def _prompt(self) -> str:
        return render_writer_prompt(_Blueprint(), brief="build it")

    def test_the_prompt_states_the_ui_contract(self):
        text = self._prompt()
        assert "UI (a pilot is deployed and tested" in text
        assert "at least two capabilities driven from" in text
        assert "authority label" in text

    def test_the_prompt_states_the_one_ui_rule(self):
        text = self._prompt()
        assert "The platform serves ONE UI" in text
        assert "A UI nothing builds is decoration" in text

    def test_the_gate_is_wired_into_the_writer_contract(self):
        import inspect

        from app.factory.build import gates

        src = inspect.getsource(gates.gate_writer_contract)
        assert "gate_ui_end_to_end(ctx)" in src
        assert "if not ui_live.ok" in src


class TestTheGateJudgesWhatIsServed:
    def _probe_findings(self, tmp_path: Path, html: str, openapi: dict | None = None) -> dict:
        """Run the gate's own probe body against a fake product tree."""
        import subprocess
        import sys

        (tmp_path / "app" / "static").mkdir(parents=True)
        (tmp_path / "app" / "static" / "index.html").write_text(html, encoding="utf-8")
        (tmp_path / "app" / "actions").mkdir(parents=True, exist_ok=True)
        for cap in ("alpha", "beta"):
            (tmp_path / "app" / "actions" / f"{cap}.py").write_text("", encoding="utf-8")
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "\n"
            '@app.get(\"/v1/capabilities\")\n'
            "def caps():\n"
            '    return {\"items\": [\"alpha\", \"beta\"]}\n',
            encoding="utf-8",
        )
        if openapi is not None:
            (tmp_path / "openapi.json").write_text(json.dumps(openapi), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-c", _render_probe()],
            cwd=tmp_path, capture_output=True, text=True,
        )
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("UI_PROBE=")]
        assert line, proc.stdout + proc.stderr
        return json.loads(line[-1].split("=", 1)[1])

    def test_a_banner_is_refused(self, tmp_path):
        out = self._probe_findings(
            tmp_path, "<html><body><h1>Platform</h1><p>Generated platform UI.</p></body></html>"
        )
        assert any("drives 0 of 2" in f for f in out["findings"]), out

    def test_a_console_that_discovers_capabilities_passes(self, tmp_path):
        html = (
            "<html><body><script>"
            "fetch('/v1/capabilities');"
            'const u = "/v1/" + cap;'
            "</script></body></html>"
        )
        out = self._probe_findings(tmp_path, html)
        assert out["findings"] == [], out
        assert sorted(out["ui_caps"]) == ["alpha", "beta"]

    def test_an_optional_surface_the_product_lacks_is_not_a_broken_route(self, tmp_path):
        html = (
            "<html><body><script>"
            "fetch('/v1/capabilities'); fetch('/v1/rag/query');"
            'const u = "/v1/" + cap;'
            "</script></body></html>"
        )
        out = self._probe_findings(
            tmp_path, html, openapi={"paths": {"/v1/capabilities": {}, "/v1/alpha": {}}}
        )
        assert not any("rag" in f for f in out["findings"]), out

    def test_no_served_page_at_all_is_refused(self, tmp_path):
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text("app = None\n", encoding="utf-8")
        import subprocess, sys

        proc = subprocess.run(
            [sys.executable, "-c", _render_probe()], cwd=tmp_path, capture_output=True, text=True
        )
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("UI_PROBE=")]
        out = json.loads(line[-1].split("=", 1)[1])
        assert any("serves no UI" in f for f in out["findings"]), out


class TestThePromptStatesTheDeliveryLevel:
    """The bar the agent aims at, stated where the agent reads it.

    A first attempt at this made the ui_end_to_end gate stricter instead --
    promoting its two advisories to findings. That was the wrong lever. The
    gate is a detector: tightening it rejects more builds without making any
    of them better, and these gates have held since day one. What raises the
    product is what the agent is told before it writes, and what the emitted
    suite checks while it can still fix things.
    """

    def _prompt(self):
        return render_writer_prompt(_Blueprint(), brief="build it")

    def test_the_prompt_states_the_production_delivery_level(self):
        text = self._prompt()
        assert "PRODUCTION DELIVERY" in text, (
            "the agent is never told the delivery level, so it aims at "
            "'renders' instead of 'an operator can finish the job'"
        )
        assert "FULL WORKING UI" in text

    def test_the_prompt_names_the_dashboard_block_as_the_ui_contract(self):
        text = self._prompt()
        assert "dashboard" in text, "the UI block the operator surfaces come from"
        assert "ui_schema" in text, "how the surfaces are configured"

    def test_the_prompt_names_the_store_gate_as_the_judge(self):
        text = self._prompt()
        assert "Store gate" in text or "store gate" in text


class TestTheTesterChecksTheUIBeforeTheGateDoes:
    """TESTER stamps eight suites and none of them touches the UI.

    So the agent cannot see a UI problem while it can still fix one. It
    yields, ``ui_end_to_end`` rejects the build, and a whole rework round is
    spent on something a failing test would have shown in the same pass. Live:
    sess_42d244d317f042b2 lost a 10m22s writer pass to
    ``ui_end_to_end ok=False reason=ui_not_wired_end_to_end``.

    The prompt's own DEPTH section tells the agent to run
    ``python -m pytest -m "not pilot" -q`` before yielding. A UI suite in that
    run is the difference between fixing it in-pass and burning a round.

    The bar does not move. The emitted suite checks what the gate already
    treats as FATAL and nothing more -- the unbuilt frontend and the missing
    authority label stay advisory there, so they stay absent here. Making
    those fail via the emitted suite would be the gate change this replaced,
    smuggled in through the tester.
    """

    def _emitted(self):
        from app.factory.build.ui_e2e import render_ui_tests

        return render_ui_tests(
            {"book_slot": {"entity": "booking", "fields": []},
             "cancel_slot": {"entity": "booking", "fields": []}}
        )

    def test_the_tester_emits_a_ui_suite(self):
        source = self._emitted()
        assert source.strip(), "TESTER emits no UI suite at all"
        assert "def test_" in source

    def test_it_checks_the_ui_is_served_and_reaches_the_product(self):
        source = self._emitted()
        assert "app/static/index.html" in source, "it must check a UI is served"
        assert "/v1/capabilities" in source, "it must check the UI reaches the product"

    def test_it_does_not_enforce_what_the_gate_leaves_advisory(self):
        """The bar stays where the gates have had it since day one."""
        source = self._emitted().lower()
        assert "package.json" not in source, (
            "the unbuilt-frontend check is ADVISORY in ui_e2e; asserting it "
            "here raises the bar through the tester, which is the gate change "
            "this work replaced"
        )
        for label_key in ("authority", "precedence"):
            assert f'"{label_key}"' not in source, (
                "the authority label is advisory in ui_e2e; it must not be "
                "fatal here"
            )

    def test_run_tester_stamps_it(self):
        import inspect

        from app.factory.build import roles_handlers

        source = inspect.getsource(roles_handlers.run_tester)
        assert "test_ui_operator_flow.py" in source, (
            "the emitter exists but TESTER never writes it, so the agent still "
            "never runs it"
        )
