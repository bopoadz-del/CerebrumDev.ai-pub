"""Who may set a blueprint field, and what the canonical copy writer refuses.

Every blueprint field declares who may set it. A customer session carries only
the fields someone inside a session may set; a field only an operator may set
is dropped wherever a blueprint enters a session, and the one place that acts
on such a field -- the canonical copy writer -- refuses any run and any target
that is not provably its own.

Functions under test, by name:
``field_setters`` and ``customer_settable`` (app.factory.blueprint);
``canonical_copy_dir`` (app.factory.paths);
``clone_canonical``, ``_canonical_owner``, ``is_operator_run``,
``_maybe_clone_canonical`` and ``_run`` (app.factory.build_jobs);
``generate_product`` (app.factory.product_architect);
``approve_blueprint``, ``accept_golden`` and ``draft_product``
(app.routers.session_product).
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

import app.core.session_store as session_store
from app.core.auth import DEV_LOCAL_TENANT
from app.factory import build_jobs, product_architect
from app.factory.blueprint import (
    OPERATOR_ONLY_FIELDS,
    SESSION_SETTERS,
    BlueprintError,
    ProductBlueprint,
    SetBy,
    customer_settable,
    field_setters,
    load_blueprint,
)
from app.factory.build_jobs import (
    CANONICAL_MARKER,
    CANONICAL_NOT_CREATED_HERE,
    CANONICAL_OPERATOR_ONLY,
    CANONICAL_OTHER_OWNER,
    CANONICAL_OVERLAPS_SOURCE,
    CANONICAL_UNSAFE_NAME,
    CanonicalRefused,
    clone_canonical,
)
from app.factory.paths import UnsafeOutputDir, canonical_copy_dir
from app.routers import session_product

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints" / "examples" / "runner_smoke.yaml"
SHIPPED_BLUEPRINTS = sorted((ROOT / "blueprints").rglob("*.yaml"))

#: A value for every operator-only field. Collection fails when a field is
#: declared operator-only and this table is not extended, so the tests below
#: always cover all of them.
OPERATOR_VALUES = {
    "canonical_output": "SharedCopy",
    "serves_verticals": ["field_desk"],
}
assert set(OPERATOR_VALUES) == set(OPERATOR_ONLY_FIELDS), (
    "OPERATOR_VALUES must name exactly the operator-only blueprint fields"
)


def _blueprint(**over) -> dict:
    raw = {
        "schema_version": "product_blueprint.v1",
        "product_id": "field-desk",
        "product_name": "Field Desk",
        "vertical": "field_desk",
        "summary": "A desk for field work",
        "capabilities": [{"id": "work_orders", "description": "Track work orders"}],
    }
    raw.update(over)
    return raw


def _bp(product_id: str, canonical_output: str) -> ProductBlueprint:
    return ProductBlueprint.model_validate(
        _blueprint(product_id=product_id, canonical_output=canonical_output)
    )


def _default(field: str):
    return ProductBlueprint.model_fields[field].get_default(call_default_factory=True)


def _tree(root: Path, body: str, extra: str = "") -> Path:
    """A finished build tree: one file whose text tells the builds apart."""
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / "main.py").write_text(body, encoding="utf-8")
    if extra:
        (root / extra).write_text(body, encoding="utf-8")
    return root


def _main(root: Path) -> str:
    return (root / "app" / "main.py").read_text(encoding="utf-8")


def _link_dir(link: Path, target: Path) -> None:
    """Make ``link`` a directory link to ``target``: a symlink, or on Windows
    without the symlink privilege a junction, which resolves the same way."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, AttributeError, OSError):
            pytest.skip(reason="this platform cannot create a directory link here")


@pytest.fixture()
def outputs_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "factory_outputs"
    root.mkdir()
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(root))
    return root


# --- every field says who may set it ---------------------------------------


class _Marked(BaseModel):
    chosen: Annotated[str, SetBy.USER]
    drafted: Annotated[int, SetBy.ARCHITECT] = 0


class _MarkedOperator(BaseModel):
    kept_out: Annotated[str, SetBy.OPERATOR] = ""


class _Unmarked(BaseModel):
    chosen: Annotated[str, SetBy.USER]
    nobody_said: int = 0


class _MarkedTwice(BaseModel):
    both: Annotated[str, SetBy.USER, SetBy.OPERATOR] = ""


@pytest.mark.parametrize(
    "model, expected",
    [
        (_Marked, {"chosen": SetBy.USER, "drafted": SetBy.ARCHITECT}),
        (_MarkedOperator, {"kept_out": SetBy.OPERATOR}),
    ],
)
def test_field_setters_reads_each_fields_own_declaration(model, expected):
    assert field_setters(model) == expected


@pytest.mark.parametrize("model", [_Unmarked, _MarkedTwice])
def test_a_field_without_exactly_one_setter_is_refused(model):
    with pytest.raises(BlueprintError):
        field_setters(model)


@pytest.mark.parametrize("model", [ProductBlueprint, _Marked])
def test_every_field_of_the_model_has_a_setter(model):
    assert set(field_setters(model)) == set(model.model_fields)


@pytest.mark.parametrize("field", sorted(ProductBlueprint.model_fields))
def test_operator_only_is_exactly_the_fields_no_session_setter_may_set(field):
    inside_a_session = field_setters(ProductBlueprint)[field] in SESSION_SETTERS
    assert (field in OPERATOR_ONLY_FIELDS) is not inside_a_session


# --- the customer-settable subset -------------------------------------------


@pytest.mark.parametrize("as_model", [False, True])
@pytest.mark.parametrize("field", sorted(OPERATOR_VALUES))
def test_customer_settable_drops_an_operator_only_field_and_keeps_the_rest(field, as_model):
    plain = ProductBlueprint.model_validate(_blueprint()).model_dump(mode="json")
    carrying = ProductBlueprint.model_validate(_blueprint(**{field: OPERATOR_VALUES[field]}))
    given = carrying if as_model else carrying.model_dump(mode="json")

    kept = customer_settable(given)

    assert field not in kept
    assert kept == {k: v for k, v in plain.items() if k not in OPERATOR_ONLY_FIELDS}


@pytest.mark.parametrize("name", ["Field Desk", "Yard Ops"])
def test_customer_settable_changes_nothing_a_session_may_carry(name):
    posted = _blueprint(product_name=name, delivery_format="github_repo")
    assert customer_settable(posted) == posted


@pytest.mark.parametrize("unknown", ["not_a_field", "output_dir"])
def test_customer_settable_leaves_an_unknown_key_for_the_model_to_refuse(unknown):
    kept = customer_settable(_blueprint(**{unknown: "x"}))
    assert kept[unknown] == "x"
    with pytest.raises(ValidationError):
        ProductBlueprint.model_validate(kept)


@pytest.mark.parametrize("field", sorted(OPERATOR_VALUES))
def test_an_operators_own_blueprint_file_keeps_its_operator_only_field(field, tmp_path):
    """The operator path: a blueprint file read by the CLI is not a session."""
    path = tmp_path / "operator.yaml"
    path.write_text(
        yaml.safe_dump(_blueprint(**{field: OPERATOR_VALUES[field]})), encoding="utf-8"
    )
    assert getattr(load_blueprint(path), field) == OPERATOR_VALUES[field]


@pytest.mark.parametrize("path", SHIPPED_BLUEPRINTS, ids=lambda p: p.stem)
def test_no_shipped_blueprint_reaches_a_session_with_an_operator_only_field(path):
    kept = customer_settable(load_blueprint(path))
    assert not (set(kept) & set(OPERATOR_ONLY_FIELDS))
    # What is left is still a whole, valid blueprint.
    assert ProductBlueprint.model_validate(kept).product_id == kept["product_id"]


# --- where a canonical copy may live ----------------------------------------


@pytest.mark.parametrize("name", ["SharedCopy", "copy-2.v1", "a"])
def test_canonical_copy_dir_accepts_one_plain_name_under_the_root(name, outputs_root):
    assert canonical_copy_dir(name) == outputs_root.resolve() / name


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "../elsewhere", "nested/inside", "nested\\inside", ".hidden",
     "trailing.", "/absolute", "has space"],
)
def test_canonical_copy_dir_refuses_a_name_that_is_not_one_plain_segment(name, outputs_root):
    with pytest.raises(UnsafeOutputDir):
        canonical_copy_dir(name)


@pytest.mark.parametrize("target", ["../elsewhere", "sessions/another"])
def test_canonical_copy_dir_refuses_a_name_that_resolves_somewhere_else(target, outputs_root):
    """A plain name that is a link to another directory is not that name's
    own directory: the RESOLVED path decides."""
    elsewhere = outputs_root / target
    elsewhere.mkdir(parents=True)
    _link_dir(outputs_root / "LinkedCopy", elsewhere.resolve())
    with pytest.raises(UnsafeOutputDir):
        canonical_copy_dir("LinkedCopy")


# --- the writer: what it copies, what it refuses ----------------------------


@pytest.mark.parametrize(
    "name, product", [("SharedCopy", "field-desk"), ("other.copy", "yard-ops")]
)
def test_an_operator_run_writes_the_canonical_copy(name, product, outputs_root, tmp_path):
    dest = clone_canonical(_bp(product, name), _tree(tmp_path / "build", "v1"), operator_run=True)

    assert dest == outputs_root.resolve() / name
    assert _main(dest) == "v1"
    assert json.loads((dest / CANONICAL_MARKER).read_text(encoding="utf-8")) == {
        "product_id": product
    }


@pytest.mark.parametrize(
    "name, product", [("SharedCopy", "field-desk"), ("other.copy", "yard-ops")]
)
def test_an_operator_run_replaces_the_copy_it_created(name, product, outputs_root, tmp_path):
    blueprint = _bp(product, name)
    clone_canonical(
        blueprint, _tree(tmp_path / "first", "v1", extra="stale.txt"), operator_run=True
    )

    dest = clone_canonical(blueprint, _tree(tmp_path / "second", "v2"), operator_run=True)

    assert _main(dest) == "v2"
    assert not (dest / "stale.txt").exists()
    assert build_jobs._canonical_owner(dest) == product


@pytest.mark.parametrize("carried", ["field-desk", "yard-ops"])
def test_a_marker_inside_the_source_tree_never_names_the_owner(carried, outputs_root, tmp_path):
    """The copy's owner is the blueprint that was built, not a file the built
    tree happens to contain."""
    src = _tree(tmp_path / "build", "v1")
    (src / CANONICAL_MARKER).write_text(json.dumps({"product_id": carried}), encoding="utf-8")

    dest = clone_canonical(_bp("the-builder", "SharedCopy"), src, operator_run=True)

    assert build_jobs._canonical_owner(dest) == "the-builder"


@pytest.mark.parametrize(
    "marker", [None, "not json", json.dumps({"something": "else"}), json.dumps(["a", "list"])]
)
def test_a_directory_the_writer_did_not_create_is_never_replaced(marker, outputs_root, tmp_path):
    held = outputs_root / "Workspaces"
    _tree(held / "another", "theirs")
    if marker is not None:
        (held / CANONICAL_MARKER).write_text(marker, encoding="utf-8")

    with pytest.raises(CanonicalRefused) as refused:
        clone_canonical(
            _bp("field-desk", held.name), _tree(tmp_path / "build", "mine"), operator_run=True
        )

    assert refused.value.reason == CANONICAL_NOT_CREATED_HERE
    assert _main(held / "another") == "theirs"


@pytest.mark.parametrize("held_name", ["Workspaces", "another-product"])
def test_a_link_to_a_directory_the_writer_did_not_create_is_never_followed(
    held_name, outputs_root, tmp_path
):
    """A plain name that links to a sibling directory resolves to that
    sibling, and the sibling is not a copy this writer created."""
    held = _tree(outputs_root / held_name, "theirs")
    _link_dir(outputs_root / "LinkedCopy", held.resolve())

    with pytest.raises(CanonicalRefused) as refused:
        clone_canonical(
            _bp("field-desk", "LinkedCopy"), _tree(tmp_path / "build", "mine"), operator_run=True
        )

    assert refused.value.reason == CANONICAL_NOT_CREATED_HERE
    assert _main(held) == "theirs"


@pytest.mark.parametrize(
    "first_owner, second_owner", [("field-desk", "yard-ops"), ("yard-ops", "field-desk")]
)
def test_a_copy_created_for_another_product_is_never_replaced(
    first_owner, second_owner, outputs_root, tmp_path
):
    clone_canonical(
        _bp(first_owner, "SharedCopy"), _tree(tmp_path / "first", "theirs"), operator_run=True
    )

    with pytest.raises(CanonicalRefused) as refused:
        clone_canonical(
            _bp(second_owner, "SharedCopy"), _tree(tmp_path / "second", "mine"), operator_run=True
        )

    assert refused.value.reason == CANONICAL_OTHER_OWNER
    assert _main(outputs_root / "SharedCopy") == "theirs"


@pytest.mark.parametrize("name", ["../elsewhere", "nested/inside", ".hidden"])
def test_a_name_outside_the_canonical_root_is_refused_and_nothing_is_touched(
    name, outputs_root, tmp_path
):
    elsewhere = _tree(outputs_root.parent / "elsewhere", "theirs")

    with pytest.raises(CanonicalRefused) as refused:
        clone_canonical(
            _bp("field-desk", name), _tree(tmp_path / "build", "mine"), operator_run=True
        )

    assert refused.value.reason == CANONICAL_UNSAFE_NAME
    assert _main(elsewhere) == "theirs"
    assert [p.name for p in outputs_root.iterdir()] == []


@pytest.mark.parametrize("source", ["SharedCopy", "SharedCopy/build", "."])
def test_the_copy_never_replaces_the_tree_it_is_copying(source, outputs_root):
    """The target is the source, holds it, or sits inside it: refused before
    anything is removed."""
    src = _tree(outputs_root / source, "v1")

    with pytest.raises(CanonicalRefused) as refused:
        clone_canonical(_bp("field-desk", "SharedCopy"), src, operator_run=True)

    assert refused.value.reason == CANONICAL_OVERLAPS_SOURCE
    assert _main(src) == "v1"


@pytest.mark.parametrize("already_there", [False, True])
def test_a_customer_run_never_writes_or_replaces_a_canonical_copy(
    already_there, outputs_root, tmp_path
):
    blueprint = _bp("field-desk", "SharedCopy")
    dest = outputs_root / "SharedCopy"
    if already_there:
        clone_canonical(blueprint, _tree(tmp_path / "first", "v1"), operator_run=True)

    with pytest.raises(CanonicalRefused) as refused:
        clone_canonical(blueprint, _tree(tmp_path / "second", "v2"), operator_run=False)

    assert refused.value.reason == CANONICAL_OPERATOR_ONLY
    assert dest.exists() is already_there
    assert not already_there or _main(dest) == "v1"


@pytest.mark.parametrize("operator_run", [True, False])
def test_a_blueprint_that_names_no_canonical_copy_copies_nothing(
    operator_run, outputs_root, tmp_path
):
    blueprint = ProductBlueprint.model_validate(_blueprint())

    copied = clone_canonical(blueprint, _tree(tmp_path / "build", "v1"), operator_run=operator_run)

    assert copied is None
    assert [p.name for p in outputs_root.iterdir()] == []


@pytest.mark.parametrize("operator_run", [True, False])
def test_maybe_clone_canonical_says_what_it_did_and_never_raises(
    operator_run, outputs_root, tmp_path, caplog
):
    with caplog.at_level(logging.INFO, logger=build_jobs.logger.name):
        build_jobs._maybe_clone_canonical(
            _bp("field-desk", "SharedCopy"),
            _tree(tmp_path / "build", "v1"),
            operator_run=operator_run,
        )

    assert (outputs_root / "SharedCopy").exists() is operator_run
    assert ("cloned canonical copy" in caplog.text) is operator_run
    assert (CANONICAL_OPERATOR_ONLY in caplog.text) is not operator_run


@pytest.mark.parametrize("operator_run", [True, False])
def test_maybe_clone_canonical_is_silent_when_no_copy_is_asked_for(
    operator_run, outputs_root, tmp_path, caplog
):
    with caplog.at_level(logging.INFO, logger=build_jobs.logger.name):
        build_jobs._maybe_clone_canonical(
            ProductBlueprint.model_validate(_blueprint()),
            _tree(tmp_path / "build", "v1"),
            operator_run=operator_run,
        )

    assert caplog.text == ""
    assert [p.name for p in outputs_root.iterdir()] == []


# --- the two build doors ------------------------------------------------------

#: Where a build's workspace is: an operator's own directory, or a session's.
OPERATOR_WORKSPACE = "out"
SESSION_WORKSPACE = "sessions/sess_one/out"


@pytest.mark.parametrize(
    "workspace, tenant_bound, expected",
    [
        (OPERATOR_WORKSPACE, False, True),
        (OPERATOR_WORKSPACE, True, False),
        (SESSION_WORKSPACE, False, False),
        (SESSION_WORKSPACE, True, False),
    ],
)
def test_is_operator_run_only_with_no_tenant_and_outside_any_session(
    workspace, tenant_bound, expected, tmp_path
):
    assert build_jobs.is_operator_run(tmp_path / workspace, tenant_bound=tenant_bound) is expected


@pytest.mark.parametrize(
    "workspace, tenant_bound",
    [(OPERATOR_WORKSPACE, False), (OPERATOR_WORKSPACE, True), (SESSION_WORKSPACE, False)],
)
def test_run_honours_the_canonical_copy_only_on_an_operators_own_run(
    workspace, tenant_bound, outputs_root, tmp_path, monkeypatch
):
    """``build_jobs._run``: a build bound to a tenant store, or built in a
    session's workspace, never writes a canonical copy; an operator's own run
    still does."""
    from app.factory.build.ledger import BuildLedger
    from app.factory.build.tenant_bind import TenantStoreBinding

    blueprint = load_blueprint(SMOKE).model_copy(update={"canonical_output": "SharedCopy"})
    out = _tree(tmp_path / workspace, "v1")
    BuildLedger(build_jobs._ledger_path(out)).start_run(
        product_id=blueprint.product_id, inputs_hash="h"
    )
    store = tmp_path / "store"
    (store / "block_registry").mkdir(parents=True)

    class _FakePlanner:
        def __init__(self, root, factory_shelf=None):
            pass

        def plan(self, bp):
            return SimpleNamespace(budget_class="small")

    class _FakeRunner:
        def __init__(self, *a, **k):
            pass

        def run(self):
            from app.factory.build.runner import Outcome

            return SimpleNamespace(outcome=Outcome.SUCCESS, rework_used=0)

    monkeypatch.setattr("app.factory.planner.CapabilityPlanner", _FakePlanner)
    monkeypatch.setattr("app.factory.planner.assert_generatable", lambda p: p)
    monkeypatch.setattr("app.factory.build.runner.RoleRunner", _FakeRunner)
    tenant_store = (
        TenantStoreBinding(tenant_key="k", store_dir=tmp_path / "tenant", bound_at=0.0)
        if tenant_bound
        else None
    )

    build_jobs._run(blueprint, out, store, "code", tenant_store)

    operator_run = workspace == OPERATOR_WORKSPACE and not tenant_bound
    assert (outputs_root / "SharedCopy" / "app" / "main.py").exists() is operator_run


def _template_engine(monkeypatch, seen: dict) -> None:
    """``generate_product`` on the template engine, with the Store, the
    planner and the generator stood in: only the door itself runs."""
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "template")

    class _FakePlanner:
        def __init__(self, root=None, factory_shelf=None):
            pass

        def plan(self, bp):
            return SimpleNamespace(capabilities=[])

    class _FakeGenerator:
        def __init__(self, blueprint, *, blocks_root=None, **kw):
            seen["blocks_root"] = blocks_root
            self._blueprint = blueprint

        def generate(self, output_dir):
            _tree(Path(output_dir), "v1")
            return {
                "output_dir": str(output_dir),
                "inputs_hash": "h",
                "product_id": self._blueprint.product_id,
            }

    monkeypatch.setattr(product_architect, "CapabilityPlanner", _FakePlanner)
    monkeypatch.setattr(product_architect, "ProductGenerator", _FakeGenerator)
    monkeypatch.setattr(product_architect, "git_head", lambda root: "0" * 40)
    monkeypatch.setattr("app.factory.compliance_gate.assert_compliant", lambda *a, **k: None)
    monkeypatch.setattr("app.factory.compliance_gate.load_trust_tiers", lambda: None)


@pytest.mark.parametrize(
    "workspace, tenant_identity, quota_account_id",
    [
        (OPERATOR_WORKSPACE, None, None),
        (OPERATOR_WORKSPACE, "acct-1", None),
        (OPERATOR_WORKSPACE, None, "acct-1"),
        (OPERATOR_WORKSPACE, "acct-1", "acct-1"),
        (SESSION_WORKSPACE, None, None),
    ],
)
def test_generate_product_honours_the_canonical_copy_only_on_an_operators_own_run(
    workspace, tenant_identity, quota_account_id, outputs_root, tmp_path, monkeypatch
):
    """``product_architect.generate_product``: a run that carries a tenant,
    or builds in a session's workspace, is a customer's; only a run that is
    neither is an operator's own."""
    _template_engine(monkeypatch, {})
    operator_run = (
        workspace == OPERATOR_WORKSPACE and tenant_identity is None and quota_account_id is None
    )

    result = product_architect.generate_product(
        _bp("field-desk", "SharedCopy"),
        tmp_path / workspace,
        blocks_root=tmp_path,
        tenant_identity=tenant_identity,
        quota_account_id=quota_account_id,
    )

    dest = outputs_root / "SharedCopy"
    assert dest.exists() is operator_run
    assert result.get("canonical_output") == (str(dest.resolve()) if operator_run else None)


@pytest.mark.parametrize("store", ["store-a", "store-b"])
def test_generate_product_resolves_the_store_itself_when_given_no_root(
    store, outputs_root, tmp_path, monkeypatch
):
    seen: dict = {}
    _template_engine(monkeypatch, seen)
    monkeypatch.setattr("app.factory.blocks_source.resolve_blocks_root", lambda: tmp_path / store)

    result = product_architect.generate_product(
        ProductBlueprint.model_validate(_blueprint()), tmp_path / "build"
    )

    assert seen["blocks_root"] == tmp_path / store
    assert result["product_id"] == "field-desk"


# --- the session boundaries ---------------------------------------------------


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    from app.main import app

    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    return TestClient(app)


def _new_session(**draft) -> str:
    """A fresh session owned by the dev tenant; with ``draft`` given, it
    already holds that drafted blueprint."""
    sid = f"sess_prov_{uuid.uuid4().hex[:12]}"
    session_store.create_session(sid, DEV_LOCAL_TENANT)
    if draft:
        state = session_store.get_session(sid)
        state.product_design.blueprint = ProductBlueprint.model_validate(
            _blueprint(**draft)
        ).model_dump(mode="json")
        session_store.update_session(sid, state)
    return sid


def _stored(sid: str):
    """The session as WRITTEN: the in-memory copy is evicted first, so this is
    what the route persisted and not the object it happened to hold."""
    session_store._session_store.pop(sid, None)
    state = session_store.get_session(sid)
    assert state is not None
    return state.product_design


@pytest.mark.parametrize("field", sorted(OPERATOR_VALUES))
def test_approve_never_stores_an_operator_only_field_from_the_request_body(field, client):
    sid = _new_session(product_name="Field Desk")
    posted = _blueprint(product_name="Renamed Desk", **{field: OPERATOR_VALUES[field]})

    r = client.post(
        f"/v1/sessions/{sid}/product/approve", json={"approve": True, "blueprint": posted}
    )

    assert r.status_code == 200, r.text
    assert r.json()["ignored_fields"] == [field]
    stored = _stored(sid).blueprint
    assert stored[field] == _default(field)
    # The part a session may carry is stored as posted.
    assert stored["product_name"] == "Renamed Desk"


@pytest.mark.parametrize("name", ["Renamed Desk", "Yard Ops"])
def test_approve_stores_a_client_blueprint_that_sets_nothing_operator_only(name, client):
    sid = _new_session(product_name="Field Desk")

    r = client.post(
        f"/v1/sessions/{sid}/product/approve",
        json={"approve": True, "blueprint": _blueprint(product_name=name)},
    )

    assert r.json() == {"ok": True, "blueprint_approved": True, "ignored_fields": []}
    assert _stored(sid).blueprint["product_name"] == name


@pytest.mark.parametrize(
    "bad", [{"schema_version": "product_blueprint.v0"}, {"not_a_field": 1}]
)
def test_approve_refuses_an_invalid_client_blueprint_and_keeps_the_draft(bad, client):
    sid = _new_session(product_name="Kept Desk")

    r = client.post(
        f"/v1/sessions/{sid}/product/approve",
        json={"approve": True, "blueprint": _blueprint(**bad)},
    )

    assert r.status_code == 400, r.text
    stored = _stored(sid)
    assert stored.blueprint["product_name"] == "Kept Desk"
    assert stored.blueprint_approved is False


@pytest.mark.parametrize("approve", [True, False])
def test_approve_with_no_blueprint_at_all_is_refused(approve, client):
    sid = _new_session()

    r = client.post(f"/v1/sessions/{sid}/product/approve", json={"approve": approve})

    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "no blueprint to approve"


@pytest.mark.parametrize("approve", [True, False])
def test_approve_without_a_body_blueprint_keeps_the_draft_and_ignores_nothing(approve, client):
    sid = _new_session(product_name="Kept Desk")

    r = client.post(f"/v1/sessions/{sid}/product/approve", json={"approve": approve})

    assert r.json() == {"ok": True, "blueprint_approved": approve, "ignored_fields": []}
    assert _stored(sid).blueprint["product_name"] == "Kept Desk"


@pytest.mark.parametrize("field", sorted(OPERATOR_VALUES))
def test_accepting_a_golden_never_puts_its_operator_only_field_on_the_session(
    field, client, monkeypatch
):
    golden = ProductBlueprint.model_validate(
        _blueprint(
            product_id="golden-desk",
            product_name="Golden Desk",
            drafting_mode="golden",
            **{field: OPERATOR_VALUES[field]},
        )
    )
    sid = _new_session(golden_suggestion={"golden_id": "golden-desk"})
    monkeypatch.setattr(session_product, "_golden_for_draft", lambda draft, vertical: golden)

    r = client.post(f"/v1/sessions/{sid}/product/draft/accept-golden")

    assert r.status_code == 200, r.text
    stored = _stored(sid).blueprint
    assert stored[field] == _default(field)
    # The golden itself is what was accepted, and the response shows the
    # same blueprint the session now holds.
    assert stored["product_id"] == "golden-desk"
    assert r.json()["blueprint"] == stored
    assert yaml.safe_load(r.json()["yaml"])[field] == _default(field)


@pytest.mark.parametrize(
    "route, detail",
    [
        ("accept-golden", "no draft to accept a golden for"),
        ("decline-golden", "no draft to decline a golden for"),
    ],
)
def test_resolving_a_golden_offer_without_a_draft_says_there_is_no_draft(route, detail, client):
    sid = _new_session()

    r = client.post(f"/v1/sessions/{sid}/product/draft/{route}")

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == detail


@pytest.mark.parametrize("field", sorted(OPERATOR_VALUES))
def test_a_cached_draft_never_puts_an_operator_only_field_on_the_session(
    field, client, monkeypatch, outputs_root
):
    from app.factory.build.blueprint_cache import (
        brief_blueprint_key,
        factory_code_version,
        store_blueprint,
    )

    monkeypatch.setenv("FACTORY_STAGE_RESUME", "1")
    brief = f"A desk for field work, cached with {field}"
    key = brief_blueprint_key(brief, vertical="", factory_sha=factory_code_version())
    assert store_blueprint(
        key, _blueprint(product_name="Cached Desk", **{field: OPERATOR_VALUES[field]})
    )

    def _must_not_draft(*a, **k):
        raise AssertionError("the cached blueprint must be used, not a fresh draft")

    monkeypatch.setattr(session_product, "draft_blueprint_from_brief", _must_not_draft)
    sid = _new_session()

    r = client.post(f"/v1/sessions/{sid}/product/draft", json={"brief": brief})

    assert r.status_code == 200, r.text
    assert r.json()["source"] == "resumed"
    stored = _stored(sid).blueprint
    assert stored[field] == _default(field)
    assert stored["product_name"] == "Cached Desk"


@pytest.mark.parametrize("hint", ["field_desk", "yard_ops"])
def test_draft_takes_the_vertical_from_the_request(hint, client, monkeypatch, outputs_root):
    """``draft_product``: the request's vertical is the session's choice and
    is what the architect is handed."""
    monkeypatch.setenv("FACTORY_STAGE_RESUME", "0")
    seen: dict = {}

    def _draft(brief, *, vertical_hint=None):
        seen["vertical_hint"] = vertical_hint
        return ProductBlueprint.model_validate(_blueprint(vertical=vertical_hint or "none"))

    monkeypatch.setattr(session_product, "draft_blueprint_from_brief", _draft)
    sid = _new_session()

    r = client.post(
        f"/v1/sessions/{sid}/product/draft",
        json={"brief": "A desk for field work", "vertical_hint": hint},
    )

    assert r.status_code == 200, r.text
    assert seen["vertical_hint"] == hint
    assert _stored(sid).vertical == hint
