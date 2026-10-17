"""The brief lint decides "another product" by IDENTITY, never by a name.

Live 2026-10-07 (588ab537, vineyard repro): approve answered
"BRIEF_LINT_REJECTED: brief names another product's capability / product /
vertical: Vineyard Management Platform". An earlier build had been named
that; this build was "Vineyard Management Platform for a Family Winery" and
its user wrote "a vineyard management platform" -- so a display name shared
with a prior build refused the build outright. Display names collide
legitimately and appear in the user's own words; they are never leakage
evidence. Another platform's machine identity (capability / product /
platform id) that this blueprint did not declare still is.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.product_literals import foreign_identities_in, is_identity_token
from app.factory.product_architect import plan_blueprint

SMOKE = Path(__file__).resolve().parents[3] / "blueprints/examples/runner_smoke.yaml"

#: An invented prior build's records: its display name and its identities.
PRIOR_NAME = "Zorblat Ledger Platform"
PRIOR_CAPABILITY = "zorblat_cask_ledger"
PRIOR_PLATFORM = "plt_0123456789abcdef"
KNOWN = frozenset({PRIOR_NAME, PRIOR_CAPABILITY, PRIOR_PLATFORM})


def _compiled():
    bp = load_blueprint(SMOKE)
    return compile_brief(bp, plan_blueprint(bp))


def _with_line(compiled, line: str):
    compiled.text += "\n" + line + "\n"
    compiled.emitted_lines = frozenset(compiled.emitted_lines | {" ".join(line.split()).lower()})
    return compiled


def test_a_brief_sharing_a_prior_products_display_name_is_accepted():
    # The live case: the user's words and this build's own name contain a
    # display name another build used. Not leakage.
    compiled = _with_line(_compiled(), f"Build me a {PRIOR_NAME.lower()} for a family business.")
    compiled.product_name = PRIOR_NAME + " for a Family Business"
    result = lint_brief(compiled, known_literals=KNOWN)
    assert result.ok, result.errors


def test_a_brief_carrying_another_platforms_capability_id_is_refused():
    compiled = _with_line(_compiled(), f"Reuse {PRIOR_CAPABILITY} from the other workspace.")
    errors = lint_brief(compiled, known_literals=KNOWN).errors
    assert any("another product" in e and PRIOR_CAPABILITY in e for e in errors), errors


def test_a_brief_carrying_another_platforms_id_is_refused():
    compiled = _with_line(_compiled(), f"Copy the tree of {PRIOR_PLATFORM}.")
    errors = lint_brief(compiled, known_literals=KNOWN).errors
    assert any(PRIOR_PLATFORM in e for e in errors), errors


def test_an_identity_this_blueprint_declares_is_its_own():
    compiled = _compiled()
    own = compiled.inventory[0].capability_id
    assert lint_brief(compiled, known_literals=frozenset({own, PRIOR_NAME})).ok


def test_identity_tokens_are_machine_ids_and_names_never_are():
    assert is_identity_token(PRIOR_CAPABILITY)
    assert is_identity_token(PRIOR_PLATFORM)
    assert not is_identity_token(PRIOR_NAME)
    assert not is_identity_token("platform")
    assert foreign_identities_in(PRIOR_NAME, KNOWN, set()) == []
