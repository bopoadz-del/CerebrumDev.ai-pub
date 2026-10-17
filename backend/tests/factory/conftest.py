"""Factory-suite stub-coder re-export.

The stub machinery lives in the backend-wide ``tests/conftest.py`` so every
suite (factory, e2e, root) can give a green WRITER phase a deterministic
stubbed coding agent (Phase 0.5: zero agent-authored artifacts refuses with
``writer_no_output``). Kept here as a re-export because older factory tests
import these names from this module.
"""

import pytest

from tests.conftest import (  # noqa: F401
    stub_coder,
    stub_coder_patches,
)


# ── the built-platform fixture, shared by the reasoning suites ──────────────
#
# It lived in test_reasoning_socket.py and test_reasoning_interview.py imported it
# by name. That import is what ruff's F811 was reporting: the `platform` parameter
# in each test signature shadows the imported name, four times over. A fixture two
# suites share belongs in conftest, where neither has to import anything — and the
# probe kit comes with it, so the two suites cannot drift to different kits while
# claiming to drive the same socket.

import importlib  # noqa: E402
import sys  # noqa: E402

import yaml  # noqa: E402

from app.factory.build.reasoning_socket import (  # noqa: E402
    render_host,
    render_init,
    render_kernel,
    render_pending,
)

#: A kit small enough to read, exercising the kinds a platform actually meets.
KIT_MANIFEST = {
    "kit": "probe",
    "version": 1,
    "quantities": {
        "declared_distance": {"units": ["m", "ft"], "classes": ["any_length"]},
        "pcn": {"classes": ["any_figure"]},
    },
    "qualifier_fields": {
        "runway": {"type": "string"},
        "temporary": {"type": "bool"},
    },
    "source_classes": {
        "approved_table": {"rank": 1},
        "design_drawing": {"rank": 4, "reject_as_proof": ["approved_table"]},
    },
    "state_providers": {"notam": {"kind": "cycle", "max_age": "next_issue"}},
    "staleness_triggers": {"declared_distance": ["airac_cycle_change"]},
    "scope_refusals": [
        {"label": "crane siting",
         "pattern": r"can we (put|site) the crane",
         "authority": "the aerodrome operator"},
    ],
    "figures": {
        # A figure is an INSTANCE of a quantity, and names which one. Today's Store
        # kits happen to key their figures by quantity name, so this probe uses the
        # instance shape deliberately -- matching on the key alone would pass here
        # and be wrong for any real per-runway or per-berth figure.
        "runway_13l_tora": {"value": None, "quantity": "declared_distance",
                            "question": "Q5.3: what is the TORA for 13L?"},
        "pavement_pcn": {"value": 80, "quantity": "pcn",
                         "question": "Q5.6: what is the PCN?"},
    },
}

KIT_INVARIANTS = {
    "invariants": [
        {"id": "INV-P-QUALIFIER", "kind": "qualifier", "severity": "refuse", "hook": "H3",
         "applies_to": {"quantity": "declared_distance"},
         "requires": ["runway", "temporary"],
         "message": "a declared distance without {missing} cannot be acted on",
         "measurement": "Probe x20 without the qualifiers. Before: n stated. After: 0."},
        {"id": "INV-P-AUTHORITY", "kind": "authority", "severity": "refuse", "hook": "H1",
         "applies_to": {"quantity": "declared_distance"},
         "governing_class": "approved_table",
         "demote": ["design_drawing"],
         "message": "a declared distance comes from the approved table",
         "measurement": "Probe x20 citing a drawing. Before: accepted. After: 20 refused."},
        {"id": "INV-P-GROUNDING", "kind": "grounding", "severity": "refuse", "hook": "H3",
         "applies_to": {"quantity": "any"},
         "message": "{quantity} is not grounded",
         "measurement": "20 questions with absent figures. Before: n uncited. After: 0."},
        {"id": "INV-P-CURRENCY", "kind": "currency", "severity": "refuse", "hook": "H3",
         "applies_to": {"quantity": "declared_distance"},
         "window": {"provider": "notam"},
         "message": "a declared distance is current only until the next issue",
         "measurement": "Probe x20 after the cycle change. Before: superseded. After: 0."},
    ]
}


@pytest.fixture
def platform(tmp_path, monkeypatch):
    """A built platform's reasoning layer, written out and importable."""
    pkg = tmp_path / "builtapp"
    (pkg / "reasoning" / "kit").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "reasoning" / "__init__.py").write_text(render_init(), encoding="utf-8")
    (pkg / "reasoning" / "kernel.py").write_text(render_kernel(), encoding="utf-8")
    (pkg / "reasoning" / "host.py").write_text(render_host(), encoding="utf-8")
    (pkg / "reasoning" / "pending.py").write_text(render_pending(), encoding="utf-8")
    (pkg / "reasoning" / "kit" / "manifest.yaml").write_text(
        yaml.safe_dump(KIT_MANIFEST), encoding="utf-8")
    (pkg / "reasoning" / "kit" / "invariants.yaml").write_text(
        yaml.safe_dump(KIT_INVARIANTS), encoding="utf-8")

    # Answers go to this platform's durable storage. Without this the suite wrote
    # reasoning_answers.json into the repository working directory and the NEXT run
    # read it back, so tests passed or failed depending on what a previous run had
    # left behind -- and `25 passed` meant only "on a clean directory".
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))

    # The emitted files import as `app.reasoning.*` inside a product, so the temp
    # package is mounted under that name for the duration of the test.
    monkeypatch.syspath_prepend(str(tmp_path))
    # Evict BOTH names. `builtapp.*` was left cached, so every test after the first
    # imported the FIRST test's temp platform and exercised its files -- invisible
    # while all the tests wrote identical content, and silently ignoring any
    # per-test variation of the kit, which is exactly what a kit test varies.
    for name in [n for n in list(sys.modules)
                 if n in ("app", "builtapp")
                 or n.startswith("app.reasoning")
                 or n.startswith("builtapp.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "app", importlib.import_module("builtapp"))
    sys.modules["app"].__path__ = [str(pkg)]
    yield importlib.import_module("builtapp.reasoning.kernel")


def _figure(mod, **kw):
    """A figure with nothing wrong with it, for tests about one thing."""
    base = dict(quantity="declared_distance", value=3200, unit="m",
                origin="document", source_id="d1", source_class="approved_table",
                qualifiers={"runway": "13L", "temporary": False},
                text="TORA 3200 m")
    base.update(kw)
    return mod.Figure(**base)
