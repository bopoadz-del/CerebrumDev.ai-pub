"""Every Factory check judges only what a declaration states.

The class that kept stopping builds: a checker asserted a product property no
brief, floor line, live model or Factory-stamped module ever declared -- a
status code written only into one test, a list key guessed from a word list,
a capability measured with another model's field, an entity assumed from the
capability id. Each site now reads the declared contract (rejection_contract,
the live models, the declared-entity resolver, the floor/brief lines), and
these tests hold both halves: a product that legitimately differs on an
undeclared property passes, and one that violates a declared contract still
fails.

The harness tests run the RENDERED Store-gate harness in a subprocess against
an invented product package, the way test_cross_tenant_declared_entity does.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from app.factory.build import (
    negative_floor,
    payload_helpers,
    placeholder_connectors,
    product_gate,
    rejection_contract,
    roles_handlers,
)
from app.factory.build.rejection_contract import (
    ACCEPT_STATUSES,
    AUTH_REFUSAL_STATUS,
    CROSS_TENANT_READ_STATUS,
    LISTED_RECORDS_SRC,
    REFUSAL_STATUSES,
    VALIDATION_REFUSAL_STATUS,
    refusal_statement,
)
from app.factory.build.store_acceptance import render_acceptance_script

FLOOR = Path(__file__).resolve().parents[2] / "app" / "factory" / "acceptance_floor.v2.json"


def _floor_line(check_id: str) -> dict:
    data = json.loads(FLOOR.read_text(encoding="utf-8"))
    return next(c for c in data["checks"] if c["id"] == check_id)


# --- the floor states every status the checks judge --------------------------


def test_every_judged_status_is_stated_in_its_floor_line():
    assert str(AUTH_REFUSAL_STATUS) in _floor_line("no_token_401")["brief_render"]
    for check in ("missing_field_422", "enum_422"):
        assert str(VALIDATION_REFUSAL_STATUS) in _floor_line(check)["brief_render"]
    assert str(CROSS_TENANT_READ_STATUS) in _floor_line("cross_tenant_404")["brief_render"]


def test_the_negative_floor_line_declares_what_a_refusal_is():
    line = _floor_line("negative_floor")
    statement = refusal_statement()
    assert statement in line["brief_render"]
    assert statement in line["requirement_text"]
    for code in REFUSAL_STATUSES:
        assert str(code) in statement


def test_the_emitted_counter_cases_judge_by_the_declared_refusal():
    src = negative_floor.render_negative_tests({}, {})
    assert "REFUSED = %r" % (tuple(REFUSAL_STATUSES),) in src
    assert "ACCEPTED = %r" % (tuple(ACCEPT_STATUSES),) in src
    assert "CROSS_TENANT_READ = %r" % (CROSS_TENANT_READ_STATUS,) in src
    compile(src, "test_negative_floor_cases.py", "exec")


# --- regression guard: emitters carry no status/key literal of their own ------

_STATUS_LITERAL = re.compile(r"status_code\s*(==|!=|in|not in)\s*\(?\s*\d{3}")
_KEY_LITERAL = re.compile(
    r"""(\.get\(|\[)\s*['"](%s)['"]\s*[)\]]"""
    % "|".join(
        re.escape(k)
        for k in (
            rejection_contract.OK_KEY,
            rejection_contract.ERROR_KEY,
            rejection_contract.RECORD_ID_KEY,
            rejection_contract.STORED_RECORD_KEY,
        )
    )
)
# "never a 500" is declared by the negative_floor line (refusal_statement).
_DECLARED_SERVER_ERROR = re.compile(r"status_code\s*!=\s*500\b")


def _test_emitter_strings(module) -> list:
    """String constants inside the functions (and module constants) that
    write a product TEST file -- recognised by the test functions they emit,
    never by a list of names."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    out = []
    for scope in ast.walk(tree):
        if not isinstance(scope, ast.FunctionDef):
            continue
        consts = [
            n.value for n in ast.walk(scope)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        if any("def test_" in c for c in consts):
            out.extend(consts)
    # Module-level source templates the emitters render into test files.
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and "def _" in node.value.value
            and "client" in node.value.value
        ):
            out.append(node.value.value)
    return out


def test_emitted_suites_carry_no_status_or_contract_key_literal():
    offenders = []
    for module in (roles_handlers, negative_floor, payload_helpers, placeholder_connectors):
        for text in _test_emitter_strings(module):
            for hit in _STATUS_LITERAL.finditer(text):
                if not _DECLARED_SERVER_ERROR.match(text, hit.start()):
                    offenders.append((module.__name__, text.strip()[:120]))
            if _KEY_LITERAL.search(text):
                offenders.append((module.__name__, text.strip()[:120]))
    assert not offenders, offenders


def test_no_reader_guesses_a_list_key():
    # A word list of candidate list keys asserted a shape nobody declared.
    for src in (product_gate.ROUND_TRIP_PROBE, *_test_emitter_strings(roles_handlers)):
        assert '"records", "results"' not in src
    assert LISTED_RECORDS_SRC.strip() in product_gate.ROUND_TRIP_PROBE


# --- the list reader reads by shape -------------------------------------------


def _listed():
    ns: dict = {}
    exec(LISTED_RECORDS_SRC, ns)
    return ns["_listed"]


def test_a_list_under_any_key_is_read():
    listed = _listed()
    row = {"reference": "T-1"}
    # A key no word list ever named -- the old reader answered [] here.
    assert listed({"tanks": [row], "total": 1}) == [row]
    assert listed([row]) == [row]


def test_a_refusal_and_an_empty_answer_list_nothing():
    listed = _listed()
    assert listed({rejection_contract.OK_KEY: False, "tanks": [{"a": 1}]}) == []
    assert listed({"tanks": [], "total": 0}) == []
    assert listed({"ids": [1, 2]}) == []  # ids are not records


# --- the counter-case reads the id by the declared create contract -----------


def test_the_cross_tenant_counter_case_reads_the_declared_stored_id():
    spec = {"fields": [{"name": "reference", "type": "str", "required": True}]}
    src = negative_floor.render_negative_tests({"tank_log": spec}, {"tank_log": {"reference": "T"}})
    line = next(s for s in src.splitlines() if s.strip().startswith("rid = "))
    expr = line.strip()[len("rid = "):] + "".join(
        s.strip() for s in src.splitlines()[src.splitlines().index(line) + 1:][:1]
    )
    declared = {
        rejection_contract.OK_KEY: True,
        rejection_contract.STORED_RECORD_KEY: {rejection_contract.RECORD_ID_KEY: 9},
    }
    assert eval(expr, {"record": declared}) == 9


# --- the Store-gate harness measures what each capability declares -----------

DRIVER = textwrap.dedent(
    '''
    import importlib.util, json, sys
    from pathlib import Path

    root = Path(sys.argv[1])
    check = sys.argv[2]
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("acc", root / "scripts" / "acceptance.py")
    acc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acc)

    from app.models import MODELS
    calls = []


    class Resp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body
            self.headers = {}
            self.text = json.dumps(body)

        def json(self):
            return self._body


    class Http:
        """Each capability validates ITS OWN model: a required field missing
        or a vocabulary value outside its list is 422; an unknown field is
        ignored (as a real route ignores what it never declared)."""

        def request(self, method, path, json=None, headers=None, **_kw):
            calls.append([method, path, json])
            cap = path.strip("/").split("/")[1]
            body = json or {}
            if method == "post" and cap in MODELS:
                cons = getattr(MODELS[cap], "CONSTRAINTS", {}) or {}
                for field, rules in cons.items():
                    if rules.get("required") and body.get(field) in (None, ""):
                        return Resp(422, {"detail": "missing " + field})
                    allowed = rules.get("allowed_values")
                    if allowed and field in body and body[field] not in allowed:
                        return Resp(422, {"detail": field + " not allowed"})
                return Resp(200, {"ok": True, "stored": {"id": 1, **body}})
            return Resp(200, {"ok": True})


    status, detail = getattr(acc, check)(Http())
    print(json.dumps({"status": status, "detail": detail, "calls": calls}))
    '''
)

# The first capability declares NO vocabulary; the second declares one. The
# old harness measured the first capability with the second model's enum
# field -- a field the first route never declared and ignores.
MODELS = textwrap.dedent(
    '''
    from dataclasses import dataclass

    @dataclass
    class Note:
        body: str = ""
        FIELDS = ["body"]
        CONSTRAINTS = {}

    @dataclass
    class Tank:
        reference: str = ""
        status: str = ""
        FIELDS = ["reference", "status"]
        CONSTRAINTS = {"reference": {"required": True},
                       "status": {"allowed_values": ["open", "closed"], "required": True}}

    MODELS = {"a_notes": Note, "b_tanks": Tank}
    '''
)


def _product(tmp_path: Path, models_src: str = MODELS) -> Path:
    root = tmp_path / "product"
    (root / "app").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "models.py").write_text(models_src, encoding="utf-8")
    (root / "app" / "placeholders.py").write_text("PLACEHOLDER_CONNECTORS = {}\n", encoding="utf-8")
    (root / "app" / "jobs.py").write_text(
        "CAPABILITIES = [{'id': 'a_notes'}, {'id': 'b_tanks'}]\n", encoding="utf-8"
    )
    (root / "scripts" / "acceptance.py").write_text(render_acceptance_script(), encoding="utf-8")
    (root / "driver.py").write_text(DRIVER, encoding="utf-8")
    return root


def _run(root: Path, check: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(root / "driver.py"), str(root), check],
        capture_output=True, text=True, timeout=120, cwd=str(root),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_enum_422_is_measured_on_the_capability_that_declares_the_vocabulary(tmp_path):
    result = _run(_product(tmp_path), "check_enum_422")
    assert result["status"] == "PASS", result
    posted = [c for c in result["calls"] if c[0] == "post"]
    assert [c[1] for c in posted] == ["/v1/b_tanks"], posted
    # The shared builder filled the declared required field, so the ONLY
    # thing wrong with the payload is the measured value.
    assert posted[0][2]["reference"]


def test_missing_field_422_is_measured_on_the_capability_that_declares_one(tmp_path):
    result = _run(_product(tmp_path), "check_missing_field_422")
    assert result["status"] == "PASS", result
    assert [c[1] for c in result["calls"] if c[0] == "post"] == ["/v1/b_tanks"]


def test_a_route_that_accepts_an_invalid_value_still_fails_enum_422(tmp_path):
    lax = MODELS.replace('"allowed_values": ["open", "closed"], ', "")
    assert lax != MODELS
    # The model still DECLARES the vocabulary to the harness, but the fake
    # route below validates from a copy without it -- a product that breaks
    # its own declared contract.
    root = _product(tmp_path)
    (root / "app" / "lax.py").write_text(lax, encoding="utf-8")
    driver = (root / "driver.py").read_text(encoding="utf-8").replace(
        "from app.models import MODELS", "from app.lax import MODELS"
    )
    (root / "driver.py").write_text(driver, encoding="utf-8")
    result = _run(root, "check_enum_422")
    assert result["status"] == "FAIL", result
    assert str(VALIDATION_REFUSAL_STATUS) in result["detail"]


def test_the_rag_plant_posts_the_declared_ingest_fields():
    from app.factory.build import rag_surface, writer_phases

    fields = writer_phases.RAG_INGEST_TEXT_FIELDS
    harness = render_acceptance_script()
    assert "RAG_INGEST_TEXT_FIELDS = %r" % (tuple(fields),) in harness
    assert '"text": marker, "content": marker' not in harness
    # The Factory's keep-path module accepts every declared field.
    tree = ast.parse(rag_surface._RAG_ROUTES_PY)
    ingest = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "RagIngestRequest"
    )
    declared = {
        n.target.id for n in ingest.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }
    assert set(fields) <= declared


def test_the_writer_is_told_the_rag_ingest_fields():
    import inspect

    from app.factory.build import writer_phases

    src = inspect.getsource(writer_phases)
    assert '"/".join(RAG_INGEST_TEXT_FIELDS)' in src


# --- the durability probe persists only what a capability declares ----------


def test_the_durability_probe_reads_the_declared_entity():
    from app.factory.build import pilot_durability

    probe = pilot_durability._render_durability_probe()
    assert "def _declared_entity" in probe
    assert "ENTITIES.get(cap_id, cap_id)" not in probe
    assert "_ACCEPT_STATUSES = %r" % (tuple(ACCEPT_STATUSES),) in probe
    compile(probe, "durability_probe", "exec")
