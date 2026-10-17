"""The NO HARDWIRING tripwire recognises the shape of a by-name fix, in code
only, and the baseline it grandfathers can only shrink.

Owner's rule, 2026-10-02: a failing case is never fixed by name. The gate
must catch every form of doing so without itself knowing any case name --
these tests feed it invented names it has never seen.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "scan_hardwiring.py"


def _load():
    spec = importlib.util.spec_from_file_location("scan_hardwiring", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def gate(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "BASELINE", tmp_path / "scripts" / "hardwiring_baseline.json")
    # The real set comes from the Store and every build; tests inject one.
    monkeypatch.setattr(mod, "load_known_literals", lambda: frozenset({"zorblat_intake", "quillon fleet"}))
    # Likewise the release cycle's rotation pool: invented blueprint names.
    monkeypatch.setattr(
        mod, "load_pool_literals",
        lambda: frozenset({"repro_zorblat_ledger", "zorblat ledger desk", "zorblat_payments"}),
    )
    # And the Store's block ids (block_name_dispatch): invented ids, so a
    # clean runner without a Cerebrum-Blocks checkout exercises every form.
    monkeypatch.setattr(mod, "load_block_ids", lambda root=None: frozenset({"zorblat_parser"}))
    (tmp_path / "scripts").mkdir()
    (tmp_path / "pkg").mkdir()
    return mod


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


CODE_WITH_EVERY_FORM = '''
"""Module docstring citing sess_deadbeef01 and R18 -- exempt, it is prose."""
# a comment citing LIVE_SOMEPRODUCT_ROSTER and sess_cafebabe02 -- exempt

LIVE_ZORBLAT_REUSE_ROSTER = ("alpha", "beta")
_ZORBLAT_TEXT_NEEDLES = ("needle",)
RAG_ZORBLAT_VALUE_RESCUE = True
RAG_ZORBLAT_EXTRA_K = 3


def _rescue_zorblat_chunks(pool):
    """Docstring naming probe Q7 -- exempt."""
    if pool.get("session") == "sess_0123456789ab":
        return pool
    return [p for p in pool if p.get("probe") != "Q7"]
'''


ALL_FORMS = ("session_id", "live_snapshot", "rescue_fn", "needle_list", "rescue_knob", "probe_id")


def test_every_form_is_caught_in_code_and_exempt_in_prose(gate, tmp_path):
    _write(tmp_path, "pkg/mod.py", CODE_WITH_EVERY_FORM)
    hits = gate.scan_file(tmp_path / "pkg" / "mod.py", ALL_FORMS)
    forms = {(form, token) for _line, form, token in hits}
    assert ("live_snapshot", "LIVE_ZORBLAT_REUSE_ROSTER") in forms
    assert ("needle_list", "_ZORBLAT_TEXT_NEEDLES") in forms
    assert ("rescue_knob", "RAG_ZORBLAT_VALUE_RESCUE") in forms
    assert ("rescue_knob", "RAG_ZORBLAT_EXTRA_K") in forms
    assert ("rescue_fn", "_rescue_zorblat_chunks") in forms
    assert ("session_id", "sess_0123456789ab") in forms
    assert ("probe_id", "Q7") in forms
    # prose is exempt: the docstring's and the comment's citations are absent
    assert ("session_id", "sess_deadbeef01") not in forms
    assert ("session_id", "sess_cafebabe02") not in forms
    assert ("live_snapshot", "LIVE_SOMEPRODUCT_ROSTER") not in forms
    assert ("probe_id", "R18") not in forms


def test_probe_id_is_enforced_and_exact_literal_only(gate, tmp_path):
    """A letter+digits literal is a probe only when it IS the whole string --
    the shape a comparison or a photographed set uses. A message that cites a
    code is prose, and a NAME is never a probe. The form is enforced by
    default: probe ids live in the probe set, so code never spells one."""
    _write(tmp_path, "pkg/mod.py", "G5 = 5\nscore = G5 + 1\nlabel = 'round G5'\nprobe = 'G5'\n")
    default_hits = gate.scan_file(tmp_path / "pkg" / "mod.py")
    assert [(f, t) for _l, f, t in default_hits] == [("probe_id", "G5")]  # the exact literal only
    assert gate.main(["--root", "pkg"]) == 1
    _write(tmp_path, "pkg/mod.py", "G5 = 5\nscore = G5 + 1\nlabel = 'round G5'\n")
    assert gate.main(["--root", "pkg"]) == 0


def test_a_byte_order_mark_does_not_turn_docstrings_into_code(gate, tmp_path):
    """Live: data_lifecycle.py carries a BOM; ast.parse refused it, the
    docstring spans came back empty, and a citation inside a docstring was
    counted as a form in code."""
    p = tmp_path / "pkg" / "bom.py"
    p.write_bytes(
        b"\xef\xbb\xbf" + b'"""Module docstring citing sess_deadbeef01."""\n\n'
        b'def f():\n    """Cites sess_cafebabe02 too."""\n    return 1\n'
    )
    assert gate.scan_file(p) == []


def test_tests_directories_are_not_shipped_code(gate, tmp_path):
    _write(tmp_path, "pkg/tests/test_x.py", "LIVE_ZORBLAT_ROSTER = 1\n")
    _write(tmp_path, "pkg/ok.py", "x = 1\n")
    assert gate.scan(("pkg",)) == {}


def test_gate_passes_on_a_clean_tree_and_rejects_a_new_form(gate, tmp_path, capsys):
    _write(tmp_path, "pkg/ok.py", "x = 1\n")
    assert gate.main(["--root", "pkg"]) == 0
    _write(tmp_path, "pkg/bad.py", "def _rescue_zorblat(x):\n    return x\n")
    assert gate.main(["--root", "pkg"]) == 1
    err = capsys.readouterr().err
    assert "REJECTED" in err and "_rescue_zorblat" in err and "fix the mechanism" in err


def test_grandfathered_forms_pass_and_one_more_of_the_same_token_is_rejected(gate, tmp_path):
    _write(tmp_path, "pkg/old.py", "LIVE_ZORBLAT_ROSTER = 1\n")
    assert gate.main(["--root", "pkg", "--write-baseline"]) == 0
    assert gate.main(["--root", "pkg"]) == 0
    # the same token used a second time is a new form
    _write(tmp_path, "pkg/old.py", "LIVE_ZORBLAT_ROSTER = 1\ny = LIVE_ZORBLAT_ROSTER\n")
    assert gate.main(["--root", "pkg"]) == 1


def test_the_baseline_may_only_shrink(gate, tmp_path, capsys):
    _write(tmp_path, "pkg/old.py", "LIVE_ZORBLAT_ROSTER = 1\n")
    assert gate.main(["--root", "pkg", "--write-baseline"]) == 0
    _write(tmp_path, "pkg/new.py", "def _rescue_zorblat(x):\n    return x\n")
    assert gate.main(["--root", "pkg", "--write-baseline"]) == 1
    assert "may only shrink" in capsys.readouterr().err
    # a deletion batch shrinks it
    (tmp_path / "pkg" / "new.py").unlink()
    (tmp_path / "pkg" / "old.py").write_text("x = 1\n", encoding="utf-8")
    assert gate.main(["--root", "pkg", "--write-baseline"]) == 0
    data = json.loads((tmp_path / "scripts" / "hardwiring_baseline.json").read_text(encoding="utf-8"))
    assert data["total"] == 0


def test_the_real_baseline_is_consistent_with_the_real_tree():
    """The committed baseline must grandfather exactly what is there: the CI
    step must be green on the commit that introduces it."""
    mod = _load()
    assert mod.main([]) == 0


def test_a_string_equal_to_a_known_product_name_is_refused(gate, tmp_path, capsys):
    _write(tmp_path, "pkg/ok.py", "x = 1\n")
    assert gate.main(["--root", "pkg"]) == 0
    _write(tmp_path, "pkg/bad.py", 'CAP = "zorblat_intake"\n')
    assert gate.main(["--root", "pkg"]) == 1
    err = capsys.readouterr().err
    assert "product_literal" in err and "zorblat_intake" in err
    assert "pkg/bad.py:1:" in err  # file:line, so the author can find it


def test_product_names_in_prose_or_docstrings_are_not_literals(gate, tmp_path):
    _write(
        tmp_path,
        "pkg/prose.py",
        '"""Built after the zorblat_intake incident."""\n'
        "# zorblat_intake was the failing capability\n"
        'MSG = "the zorblat_intake route answered 404"\n',
    )
    assert gate.main(["--root", "pkg"]) == 0


def test_a_new_rule_grandfathers_once_then_only_shrinks(gate, tmp_path):
    _write(tmp_path, "pkg/old.py", 'A = "zorblat_intake"\n')
    (tmp_path / "scripts" / "hardwiring_baseline.json").write_text(
        json.dumps({"forms": ["session_id"], "total": 0, "files": {}}), encoding="utf-8"
    )
    assert gate.main(["--root", "pkg", "--write-baseline"]) == 0
    _write(tmp_path, "pkg/new.py", 'B = "quillon fleet"\n')
    assert gate.main(["--root", "pkg", "--write-baseline"]) == 1


def test_the_gate_itself_names_no_case():
    """The tripwire must not be a needle list in disguise: its own source
    carries no session id, probe id, product roster or rescue name."""
    mod = _load()
    hits = mod.scan_file(SCRIPT)
    assert hits == [], hits


# --- word_list: classification by vocabulary ---------------------------------


def test_inline_word_lists_and_regex_joins_are_refused(gate, tmp_path):
    _write(tmp_path, "pkg/a.py",
           "def f(t: str):\n    return [k for k in ('alpha', 'beta') if k in t]\n")
    assert gate.main(["--root", "pkg"]) == 1
    _write(tmp_path, "pkg/a.py",
           "import re\nW = ['alpha', 'beta']\nR = re.compile('|'.join(W))\n")
    assert gate.main(["--root", "pkg"]) == 1


# --- the default gate: every injected by-name decision is REJECTED -----------

_INJECTIONS = {
    "phrase_literal": (
        "phrase_match",
        "def route(text: str) -> str:\n"
        "    if 'zorblat refund' in text.lower():\n"
        "        return 'refund'\n"
        "    return 'other'\n",
    ),
    "compiled_regex": (
        "phrase_match",
        "import re\n"
        "_ASK = re.compile(r'\\bplease\\s+quillon\\b', re.IGNORECASE)\n\n\n"
        "def asks(text: str) -> bool:\n"
        "    return bool(_ASK.search(text))\n",
    ),
    "word_list": (
        "word_list",
        "def asks(text: str) -> bool:\n"
        "    return any(w in text for w in ('zorblat', 'quillon'))\n",
    ),
    "probe_id": (
        "probe_id",
        "def skip(case: dict) -> bool:\n"
        "    return case.get('id') == 'K7'\n",
    ),
}


@pytest.mark.parametrize("name", sorted(_INJECTIONS))
def test_the_default_gate_rejects_each_injection_then_is_green(gate, tmp_path, capsys, name):
    """phrase_match is ENFORCED by default at baseline 0, beside word_list and
    probe_id: no --form flag is needed for any of them to be refused."""
    form, source = _INJECTIONS[name]
    assert form in gate.DEFAULT_FORMS
    _write(tmp_path, "pkg/ok.py", "x = 1\n")
    assert gate.main(["--root", "pkg"]) == 0
    _write(tmp_path, "pkg/bad.py", source)
    assert gate.main(["--root", "pkg"]) == 1
    err = capsys.readouterr().err
    assert "REJECTED" in err and form in err and "pkg/bad.py:" in err
    (tmp_path / "pkg" / "bad.py").unlink()
    assert gate.main(["--root", "pkg"]) == 0


# --- blueprint_name: the release cycle's rotation pool -----------------------


@pytest.mark.parametrize(
    "source",
    [
        'def route(bp):\n    if bp.vertical == "zorblat_payments":\n        return 1\n    return 0\n',
        'SPECIAL = {"repro_zorblat_ledger": 2}\n',
        'TITLE = "Zorblat Ledger Desk"\n',
    ],
)
def test_a_literal_naming_a_pool_blueprint_is_refused(gate, tmp_path, capsys, source):
    """Owner, 2026-10-08: nothing in the Factory may branch on which rotation
    blueprint is running; a fix that mentions one is rejected. The id, the
    display name and the vertical each count, whatever their case."""
    assert "blueprint_name" in gate.DEFAULT_FORMS
    _write(tmp_path, "pkg/ok.py", "x = 1\n")
    assert gate.main(["--root", "pkg"]) == 0
    _write(tmp_path, "pkg/bad.py", source)
    assert gate.main(["--root", "pkg"]) == 1
    err = capsys.readouterr().err
    assert "REJECTED" in err and "blueprint_name" in err and "pkg/bad.py:" in err


def test_a_pool_blueprint_in_prose_is_not_a_literal(gate, tmp_path):
    _write(
        tmp_path,
        "pkg/prose.py",
        '"""Found by the repro_zorblat_ledger rotation build."""\n'
        "# zorblat_payments exercised the money contract\n"
        'MSG = "the repro_zorblat_ledger build failed its gate"\n',
    )
    assert gate.main(["--root", "pkg"]) == 0


def test_the_forbidden_names_are_read_from_the_pool_files():
    """Adding a blueprint to the pool extends the gate: every pool
    blueprint's id, name and vertical is in the set, nothing else is listed."""
    mod = _load()
    pool_mod_spec = importlib.util.spec_from_file_location("repro_pool", mod.POOL_SCRIPT)
    pool_mod = importlib.util.module_from_spec(pool_mod_spec)
    pool_mod_spec.loader.exec_module(pool_mod)
    pool = pool_mod.load_pool()
    names = mod.load_pool_literals()
    expected = {str(bp[f]).lower() for bp in pool for f in ("id", "name", "vertical")}
    assert names == expected
    assert len(pool) >= 8


def test_an_unreadable_pool_fails_the_gate_closed(tmp_path, monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(mod, "POOL_SCRIPT", tmp_path / "missing_repro_pool.py")
    with pytest.raises(mod.NoReproPool):
        mod.load_pool_literals()
    monkeypatch.setattr(mod, "load_known_literals", lambda: frozenset())
    monkeypatch.setattr(mod, "load_block_ids", lambda: frozenset({"some_block"}))
    assert mod.main(["--root", "backend/app/main.py"]) == 1
    assert "REJECTED" in capsys.readouterr().err


def test_the_real_tree_names_no_pool_blueprint():
    mod = _load()
    found = mod.scan(mod.DEFAULT_ROOTS, ("blueprint_name",))
    assert found == {}, found


def test_the_committed_baseline_grandfathers_nothing():
    """phrase_match was flipped at 0: every form, every root, no file entries."""
    data = json.loads((SCRIPT.parent / "hardwiring_baseline.json").read_text(encoding="utf-8"))
    mod = _load()
    assert data["files"] == {} and data["total"] == 0
    assert tuple(data["forms"]) == mod.DEFAULT_FORMS
    assert tuple(data["roots"]) == mod.DEFAULT_ROOTS


