"""Rules are interpreted by the rule model's typed answer, never by their words."""

from __future__ import annotations

from app.core import rule_parser


def test_without_a_rule_model_a_rule_is_kept_verbatim_not_split_on_its_words(monkeypatch):
    monkeypatch.setattr(rule_parser, "active_provider", lambda: "")
    parsed = rule_parser.parse_rules(["If the invoice is overdue then email the client."])
    assert parsed == [
        {
            "raw": "If the invoice is overdue then email the client.",
            "trigger": "",
            "action": "",
            "code_snippet": (
                "# Rule (not interpreted -- no rule model answered): "
                "If the invoice is overdue then email the client.\n"
            ),
        }
    ]


def test_the_rule_models_typed_trigger_and_action_drive_the_snippet(monkeypatch):
    monkeypatch.setattr(rule_parser, "active_provider", lambda: "kimi")
    monkeypatch.setattr(
        rule_parser,
        "get_llm_config",
        lambda: {"base_url": "http://x", "api_key": "k", "model": "m"},
    )
    monkeypatch.setattr(
        rule_parser,
        "_call_openai_sync",
        lambda *a, **k: {
            "rules": [
                {"raw": "Overdue invoices get a reminder", "trigger": "invoice.overdue", "action": "send_reminder"},
                {"raw": "Always be polite"},
            ]
        },
    )
    parsed = rule_parser.parse_rules(["Overdue invoices get a reminder", "Always be polite"])
    assert parsed[0]["trigger"] == "invoice.overdue"
    assert "context.matches('invoice.overdue')" in parsed[0]["code_snippet"]
    # The model gave no trigger/action for the second: nothing is guessed
    # from the word "Always".
    assert parsed[1]["trigger"] == "" and parsed[1]["action"] == ""
    assert parsed[1]["code_snippet"].startswith("# Rule (not interpreted")
