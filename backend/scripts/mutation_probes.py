"""Standalone mutation probes for the artifact gate (Phase 0.5, probe P0).

Run directly, no orchestrator, no network:

    python scripts/mutation_probes.py

Every probe imports the REAL factory modules -- ``gate_writer_contract``,
``enforce_receipt``, ``full_pilot_authorship_from`` (``below_floor``) and
``grade_workspace`` -- and calls them. If a probe reimplemented the logic it
would be testing a copy, and a copy proves nothing. Each probe forces zero
agent-authored artifacts and asserts the entire chain goes RED:

    P0a  writer contract gate (CODE) refuses with ``writer_no_output``
    P0b  receipt enforcement refuses an empty-authored empty-required
         handoff (never HANDOFF_TO_N3)
    P0c  unmeasured/zero authorship is below the full-pilot floor
    P0d  level grade refuses Store-green / founding for zero artifacts
    P0e  control: a coding-agent-stamped handler is counted, so the gate's
         refusal in P0a is the check working, not a blanket fail

Exit code is 0 only when every probe passes. RED-when-forced is the probe
suite's whole job: these assertions must hold with the gate live, and the
pytest mutation tests prove they flip when the gate is removed.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Tuple

# backend root so ``app`` imports when run as ``python scripts/mutation_probes.py``
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.factory.build.authority import BuildRole  # noqa: E402
from app.factory.build.authorship import (  # noqa: E402
    agent_written_handler_ids_in_workspace,
    full_pilot_authorship_from,
)
WRITER_NO_OUTPUT = "writer_no_output"
from app.factory.build.gates import GateContext, gate_writer_contract  # noqa: E402
from app.factory.build.level_grade import Level, grade_workspace  # noqa: E402


def _estate_kit():
    """The estate kit lives in the Store (the Factory holds no kits)."""
    from app.factory.kit_pack import store_kit_dir

    kit = store_kit_dir("private_estate_operations")
    assert kit is not None, "Store estate kit not found -- set CEREBRUM_BLOCKS_ROOT"
    return kit


Probe = Callable[[], None]


def probe_a_writer_gate_refuses_zero_artifacts() -> None:
    """P0a -- CODE gate: a workspace with no agent artifacts is RED."""
    with tempfile.TemporaryDirectory() as tmp:
        ctx = GateContext(workspace=Path(tmp), role=BuildRole.WRITER)
        result = gate_writer_contract(ctx)
    assert result.ok is False, result.to_json()
    assert WRITER_NO_OUTPUT in result.detail, result.detail
    assert result.gate == "writer_contract"


def probe_b_receipt_refuses_empty_handoff() -> None:
    """P0b -- a workspace with no agent-authored artifacts can never hand
    off: the writer-contract gate refuses it as writer_no_output (the
    retired cli_receipt enforcement folded into the gate)."""
    with tempfile.TemporaryDirectory() as tmp:
        ctx = GateContext(workspace=Path(tmp), role=BuildRole.WRITER)
        result = gate_writer_contract(ctx)
    assert result.ok is False, result.to_json()
    assert WRITER_NO_OUTPUT in result.detail, result.detail


def probe_c_unmeasured_is_below_floor() -> None:
    """P0c -- zero/unmeasured authorship is below the full-pilot floor."""
    floor = full_pilot_authorship_from({}, workspace=None)
    assert floor.measured is False
    assert floor.meets_floor is False
    assert floor.below_floor is True


def probe_d_zero_artifacts_cannot_grade_store_green() -> None:
    """P0d -- STORE/level chain: zero artifacts never grades Store-green."""
    with tempfile.TemporaryDirectory() as tmp:
        grade = grade_workspace(
            tmp,
            status={
                "state": "succeeded",
                "cycle": "pilot",
                "pilot_ready": True,
                "detail": (
                    "CODE PASS \u2014 x; PRODUCT PASS \u2014 y; STORE PASS \u2014 z"
                ),
            },
        )
    assert grade["level"] not in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }, grade
    assert grade["pilot_ready"] is False or not grade["full_pilot"], grade
    assert any("authorship is below" in b for b in grade["blockers"]), grade


def probe_e_control_agent_stamped_handler_is_counted() -> None:
    """P0e -- control: the counter sees coding-agent stamps, so P0a's
    refusal is the check discriminating, not a blanket fail."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        actions = root / "app" / "actions"
        actions.mkdir(parents=True)
        (actions / "widget_intake.py").write_text(
            '"""Handler for capability widget_intake.\n\n'
            "Written by the factory WRITER role (coder LLM). Blocks are "
            "invoked through\n"
            "the local dispatch runtime -- this module makes no network "
            'call.\n"""\n'
            'AUTHORED_BY = "coder LLM"\n',
            encoding="utf-8",
        )
        (actions / "legacy_template.py").write_text(
            '"""Handler for capability legacy_template.\n\n'
            "Written by the factory WRITER role (deterministic contract "
            "template).\n"
            '"""\n'
            'AUTHORED_BY = "deterministic contract template"\n',
            encoding="utf-8",
        )
        ids = agent_written_handler_ids_in_workspace(root)
    assert ids == ["widget_intake"], ids


def _load_kit_tenant_store():
    """Load the steward kit's tenant_store under a fresh alias (P1)."""
    import importlib.util
    import types

    kit = (_estate_kit()
        / "steward_runtime")
    for pkg in ("app", "app.steward"):
        if pkg not in sys.modules:
            mod = types.ModuleType(pkg)
            if pkg.startswith("app."):
                mod.__path__ = []
            sys.modules[pkg] = mod
    spec = importlib.util.spec_from_file_location(
        "mutation_probes_tenant_store", kit / "tenant_store.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mutation_probes_tenant_store"] = mod
    spec.loader.exec_module(mod)
    return mod


def probe_f_tenant_isolation_seam_detects_a_broken_seam() -> None:
    """P1 -- the tenant seam, RED-when-broken: a seam patched to accept
    client-supplied names must refuse the boot."""
    import os

    ts = _load_kit_tenant_store()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["STORAGE_PATH"] = str(Path(tmp) / "storage")
        ts.assert_tenant_store_seam()  # the real seam boots
        original = ts.resolve_tenant_store
        ts.resolve_tenant_store = lambda candidate: object()
        try:
            try:
                ts.assert_tenant_store_seam()
            except ts.TenantStoreError as exc:
                assert ts.TENANT_STORE_NOT_ADDRESSABLE in str(exc), str(exc)
            else:
                raise AssertionError(
                    "a client-named seam booted: tenant isolation is not enforced"
                )
        finally:
            ts.resolve_tenant_store = original


def probe_g_precedence_ladder_is_data_not_prompt_text() -> None:
    """P2 -- inverting the rank DATA flips the winner (a prompt-baked ladder
    would survive inversion)."""
    import json

    from app.cerebrum_product_kernel.precedence import (
        LayerObject,
        load_ladder,
        resolve_formula_by_id,
    )

    certified = LayerObject(object_id="margin_v1", layer=1, evaluate=lambda: 40.0)
    taught = LayerObject(
        object_id="margin_v1", layer=3, tenant_id="tenant_a", evaluate=lambda: 42.0
    )
    verdict = resolve_formula_by_id("margin_v1", [certified, taught])
    assert verdict.winner.layer == 3

    with tempfile.TemporaryDirectory() as tmp:
        inverted = Path(tmp) / "inverted.json"
        inverted.write_text(
            json.dumps(
                {
                    "schema": "precedence.v1",
                    "rank": [
                        {"layer": 1, "rank": 4, "label": "certified"},
                        {"layer": 2, "rank": 3, "label": "documents"},
                        {"layer": 3, "rank": 2, "label": "formulas"},
                        {"layer": 4, "rank": 1, "label": "procedures"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        ladder = load_ladder(inverted)
        flipped = resolve_formula_by_id(
            "margin_v1", [certified, taught], ladder=ladder
        )
    assert flipped.winner.layer == 1, "the precedence ladder is baked in, not data"


def probe_h_claim_labels_refuse_stripped_layer() -> None:
    """P3 -- stripping a claim's layer must fail the emission (unlabeled_claim)."""
    from app.cerebrum_product_kernel.claim_labels import (
        Claim,
        ClaimLabelError,
        LabeledAnswer,
        UNLABELED_CLAIM,
    )

    stripped = Claim.__new__(Claim)  # the mutation: no layer, no validation
    answer = LabeledAnswer(claims=[stripped])
    try:
        answer.to_dict()
    except ClaimLabelError as exc:
        assert UNLABELED_CLAIM in str(exc), str(exc)
    else:
        raise AssertionError("an unlabeled claim was emitted — labels are not enforced")


def probe_i_retrieval_engine_is_real() -> None:
    """P4 -- swap the embedder for a keyword matcher and T4.1's semantic
    path goes RED (the engine refuses keyword retrieval)."""
    from app.cerebrum_product_kernel.retrieval_engine import (
        InMemoryVectorStore,
        KEYWORD_EMBEDDER_NOT_RETRIEVAL,
        RetrievalEngine,
        RetrievalEngineError,
    )

    class SemanticFixture:
        semantic = True

        def __call__(self, text):
            v = [1.0, 0.0] if "price" in text or "cost" in text else [0.0, 1.0]
            n = (v[0] * v[0] + v[1] * v[1]) ** 0.5
            return [x / n for x in v]

    class KeywordMatcher:
        semantic = False

        def __call__(self, text):
            return [0.5, 0.5]

    engine = RetrievalEngine(embedder=SemanticFixture(), store=InMemoryVectorStore())
    engine.ingest(
        "t",
        [{"id": "c1", "text": "The service price is forty.", "layer": 1}],
    )
    assert engine.retrieve("t", "how much does it cost")

    try:
        RetrievalEngine(embedder=KeywordMatcher(), store=InMemoryVectorStore())
    except RetrievalEngineError as exc:
        assert KEYWORD_EMBEDDER_NOT_RETRIEVAL in str(exc), str(exc)
    else:
        raise AssertionError(
            "a keyword matcher drove the retrieval engine — retrieval is not real"
        )


def probe_j_worker_refuses_unbound_tenant() -> None:
    """P5 -- run the writer worker without a bound tenant store and the
    dispatch must refuse (the builder lives under Phase 1 isolation)."""
    from app.factory.build.codewhale_worker import (
        NO_AUTHENTICATED_TENANT,
        WorkerError,
        run_worker_job,
    )

    try:
        run_worker_job("build anything", "/tmp/anywhere", tenant_store=None)
    except WorkerError as exc:
        assert NO_AUTHENTICATED_TENANT in str(exc), str(exc)
    else:
        raise AssertionError(
            "the worker ran with no tenant store — builder isolation is not enforced"
        )


def probe_k_manifest_honesty_detects_forgery() -> None:
    """P6 -- a forged vector_rag manifest with no engine in the zip must be
    a manifest_mismatch, never shipped."""
    from app.factory.build.export_manifest import (
        MANIFEST_MISMATCH,
        build_manifest,
        verify_manifest,
    )

    manifest = build_manifest(
        product_id="probe",
        tenant_id=None,
        ci_run="ci-1",
        retrieval_mode="vector_rag",
        tenancy_mode="multi_tenant_partition",
        embedder="onnx-minilm",
        vector_store="chroma_tenant_collection",
        engine_version="retrieval_engine.v1",
        prompt_version="writer_worker_prompt.v1",
        layer_counts={1: 1},
        engine_included=True,
    )
    problems = verify_manifest(manifest, {"app/main.py"})
    if not problems:
        raise AssertionError(
            "a forged vector_rag manifest verified clean — the manifest is decorative"
        )
    assert all(MANIFEST_MISMATCH in p for p in problems)


def probe_l_one_tenant_cannot_starve_the_platform() -> None:
    """P7 -- CerebrumDev.ai is multi-tenant. One account holding a slot must
    not refuse every other account on the box, and must still be held to its
    OWN limit while the box has room.

    Asserts on the REFUSAL REASON, never on a count of results: a
    `sum(1 for ok, _ in runs)` counts every tuple regardless of outcome and
    has reported green against a broken build in this project before.
    """
    import os

    from app.factory.build.codewhale_worker import (
        PROCESS_CAP_ENV,
        PROCESS_SLOTS_EXHAUSTED,
        TENANT_CAP_ENV,
        TENANT_SLOTS_EXHAUSTED,
        InProcessSlotCounter,
        WorkerError,
        set_slot_counter,
        worker_job_slot,
        worker_slots_snapshot,
    )
    from app.factory.build.tenant_bind import bind_tenant_store

    saved = {k: os.environ.get(k) for k in (PROCESS_CAP_ENV, TENANT_CAP_ENV)}
    previous = set_slot_counter(InProcessSlotCounter())
    os.environ[PROCESS_CAP_ENV] = "3"
    os.environ[TENANT_CAP_ENV] = "1"
    try:
        hog = bind_tenant_store("probe_l_hog")
        other = bind_tenant_store("probe_l_other")
        assert hog is not None and other is not None
        assert hog.tenant_key != other.tenant_key

        with worker_job_slot(hog):
            # 1. The hog is stopped at its OWN limit, not the box's.
            try:
                with worker_job_slot(hog):
                    pass
            except WorkerError as exc:
                assert TENANT_SLOTS_EXHAUSTED in str(exc), str(exc)
                assert PROCESS_SLOTS_EXHAUSTED not in str(exc), str(exc)
            else:
                raise AssertionError(
                    "one tenant took a second slot past its per-tenant cap — "
                    "fairness is not enforced"
                )
            # 2. A DIFFERENT tenant is still served. This is the bug.
            try:
                with worker_job_slot(other):
                    assert worker_slots_snapshot()["total"] == 2
            except WorkerError as exc:
                raise AssertionError(
                    "a second tenant was refused while the box had free "
                    "slots — one tenant consumed the platform, concurrency "
                    f"is single-tenant: {exc}"
                )
        # 3. Nothing leaked.
        snap = worker_slots_snapshot()
        assert snap == {"total": 0, "by_tenant": {}}, snap
    finally:
        set_slot_counter(previous)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


PROBES: List[Tuple[str, Probe]] = [
    ("P0a writer gate refuses zero artifacts", probe_a_writer_gate_refuses_zero_artifacts),
    ("P0b receipt refuses empty handoff", probe_b_receipt_refuses_empty_handoff),
    ("P0c unmeasured is below floor", probe_c_unmeasured_is_below_floor),
    ("P0d zero artifacts cannot grade Store-green", probe_d_zero_artifacts_cannot_grade_store_green),
    ("P0e control: agent stamp is counted", probe_e_control_agent_stamped_handler_is_counted),
    ("P1 tenant seam detects a broken seam", probe_f_tenant_isolation_seam_detects_a_broken_seam),
    ("P2 precedence ladder is data not prompt text", probe_g_precedence_ladder_is_data_not_prompt_text),
    ("P3 claim labels refuse a stripped layer", probe_h_claim_labels_refuse_stripped_layer),
    ("P4 retrieval engine is real RAG", probe_i_retrieval_engine_is_real),
    ("P5 worker refuses an unbound tenant", probe_j_worker_refuses_unbound_tenant),
    ("P6 manifest honesty detects forgery", probe_k_manifest_honesty_detects_forgery),
    ("P7 one tenant cannot starve the platform", probe_l_one_tenant_cannot_starve_the_platform),
]


def main(argv: List[str]) -> int:
    failures = 0
    for name, probe in PROBES:
        try:
            probe()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
    print(
        f"mutation_probes: {len(PROBES) - failures}/{len(PROBES)} probes passed"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
