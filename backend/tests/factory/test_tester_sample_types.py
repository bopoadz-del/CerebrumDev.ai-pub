"""TESTER sample emission must not KeyError on WRITER field types.

Live sess_f48f60b / CEREBRUMDEV-BACKEND-S: ``run_tester`` built
``tests/test_models.py`` with a four-key map ``str/int/float/bool`` and
crashed the build thread on ``type: datetime`` (``scheduled_at``). Unknown
types must be RoleError / RUN_FAILED, never an uncaught KeyError.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.roles import RoleContext, RoleError, run_tester
from app.factory.build.roles_handlers import (
    _assert_fields_sampleable,
    _model_roundtrip_literal,
    _resolve_known_field_type,
    run_tester as run_tester_impl,
)
from app.factory.build.workspace import RoleWorkspace


class _Cap:
    def __init__(self, cid, block_ids=()):
        self.capability_id = cid
        self.block_ids = tuple(block_ids)
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "Lettings Probe"
    product_id = "residential-lettings"
    vertical = "residential_lettings"


def _ctx(tmp_path: Path, specs: dict, cid: str = "viewing_booking"):
    ws = RoleWorkspace(BuildRole.TESTER, tmp_path / "build")
    return RoleContext(
        role=BuildRole.TESTER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(_Cap(cid)),
        state={"model_specs": specs, "vendored_blocks": ()},
    )


def test_known_writer_types_resolve_including_datetime_and_uuid():
    assert _resolve_known_field_type("datetime") == "datetime"
    assert _resolve_known_field_type("timestamp") == "datetime"
    assert _resolve_known_field_type("date") == "date"
    assert _resolve_known_field_type("uuid") == "uuid"
    assert _resolve_known_field_type("email") == "email"
    assert _resolve_known_field_type("text") == "str"
    assert _resolve_known_field_type("real") == "float"
    assert _resolve_known_field_type("not_a_real_type") is None


def test_model_roundtrip_literal_covers_datetime_and_uuid():
    """Mutation killed: restoring the four-key map KeyErrors on datetime."""
    dt = _model_roundtrip_literal(
        {"name": "scheduled_at", "required": False, "type": "datetime"}
    )
    assert "2026-09-03T10:00:00" in dt
    uuid_lit = _model_roundtrip_literal({"name": "listing_uid", "type": "uuid"})
    assert "00000000-0000-4000-8000-000000000001" in uuid_lit
    email_lit = _model_roundtrip_literal({"name": "contact", "type": "email"})
    assert "@" in email_lit


def test_unknown_field_type_is_role_error_not_keyerror():
    with pytest.raises(RoleError, match="not_a_real_type"):
        _model_roundtrip_literal({"name": "widget", "type": "not_a_real_type"})
    with pytest.raises(RoleError, match="not_a_real_type"):
        _assert_fields_sampleable(
            {
                "viewing_booking": {
                    "entity": "viewing",
                    "fields": [{"name": "widget", "type": "not_a_real_type"}],
                }
            }
        )


def test_tester_datetime_and_uuid_fields_do_not_crash(tmp_path):
    """sess_f48f60b: scheduled_at type=datetime must emit a suite, not crash."""
    specs = {
        "viewing_booking": {
            "entity": "viewing",
            "fields": [
                {"name": "scheduled_at", "required": False, "type": "datetime"},
                {"name": "listing_uid", "type": "uuid"},
                {"name": "reference", "type": "str"},
            ],
        }
    }
    result = run_tester(_ctx(tmp_path, specs))
    assert result.ok
    models = (tmp_path / "build" / "tests" / "test_models.py").read_text(
        encoding="utf-8"
    )
    assert "2026-09-03T10:00:00" in models
    assert "00000000-0000-4000-8000-000000000001" in models


def test_tester_unknown_field_type_raises_role_error(tmp_path):
    specs = {
        "viewing_booking": {
            "entity": "viewing",
            "fields": [{"name": "widget", "type": "not_a_real_type"}],
        }
    }
    with pytest.raises(RoleError, match="not_a_real_type") as exc:
        run_tester(_ctx(tmp_path, specs))
    assert "KeyError" not in type(exc.value).__name__
    assert "TESTER cannot sample" in str(exc.value)


def test_mutation_tester_model_suite_does_not_use_four_type_map():
    """Mutation killed: the sess_f48f60b dictcomp that KeyError'd on datetime."""
    src = inspect.getsource(run_tester_impl)
    assert "_model_roundtrip_literal" in src
    assert '''{"str": "'x'", "int": "1", "float": "1.5", "bool": "True"}''' not in src


# ── spellings of types TESTER already knows ────────────────────────────────
#
# Found by sweeping TESTER for the defect that refused two live clones in one
# day (block_obligations.DISTRIBUTIONS vs 'bcrypt'): a hand-kept table judged
# against open-ended input, where a miss refuses the whole build. TESTER has
# exactly one such table, and it is judged against whatever the writer chose
# to call a field -- so a miss costs a build the writer has already paid for.


@pytest.mark.parametrize(
    "spelling, canonical",
    [
        ("decimal", "float"), ("Decimal", "float"), ("decimal.Decimal", "float"),
        ("NUMERIC(10, 2)", "float"), ("money", "float"), ("double", "float"),
        ("bigint", "int"), ("smallint", "int"), ("long", "int"),
        ("VARCHAR(255)", "str"), ("char", "str"),
        # Optional[X] was always unwrapped; the newer spelling was not.
        ("str | None", "str"), ("None | int", "int"), ("datetime | None", "datetime"),
        ("Optional[decimal]", "float"),
    ],
)
def test_other_spellings_of_known_scalars_resolve(spelling, canonical):
    from app.factory.build.roles_handlers import _resolve_known_field_type

    assert _resolve_known_field_type(spelling) == canonical


def test_a_spec_using_those_spellings_no_longer_refuses_the_build():
    from app.factory.build.roles_handlers import _assert_fields_sampleable

    _assert_fields_sampleable(
        {
            "invoice": {
                "fields": [
                    {"name": "total", "type": "decimal"},
                    {"name": "reference", "type": "VARCHAR(64)"},
                    {"name": "paid_at", "type": "datetime | None"},
                ]
            }
        }
    )  # must not raise


@pytest.mark.parametrize("structural", ["list", "dict", "json", "list[str]", "str | int"])
def test_structural_and_ambiguous_types_still_refuse(structural):
    """The guard on the two tests above: widening the table must not have
    become "accept anything". The model emitter stores every field as a
    scalar, so sampling a list would send a payload the generated model
    cannot hold -- and ``str | int`` has no single right sample."""
    from app.factory.build.roles import RoleError
    from app.factory.build.roles_handlers import (
        _assert_fields_sampleable,
        _resolve_known_field_type,
    )

    assert _resolve_known_field_type(structural) is None
    with pytest.raises(RoleError, match="cannot sample"):
        _assert_fields_sampleable({"c": {"fields": [{"name": "f", "type": structural}]}})
