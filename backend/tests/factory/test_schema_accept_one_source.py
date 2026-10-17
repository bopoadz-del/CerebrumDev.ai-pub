"""The WRITER gate's schema-accept probe posts what TESTER posts.

Live 2026-10-07 (9de69276 vineyard repro): the build stopped on
"no capability accepted its own schema" (SAME_FAILURE_TWICE). The probe built
its payloads from a sampler of its own (``_value``/``_payload``) while TESTER
posted the Factory spec sample through the shared builder (payload_helpers,
over the product's live declared models). Two samplers, two verdicts -- and
the rework item said only "baseline POST returned HTTP 404", nothing the
writer could converge on. One builder now; the product's own refusal text
travels with every miss.
"""

from __future__ import annotations

import sys

import pytest

from app.factory.build.declared_specs import specs_from_product_models
from app.factory.build.roles_handlers import _sample_payload
from app.factory.build.payload_helpers import base_samples
from app.factory.build.writer_behaviour import _render_probe
from tests.factory.test_writer_behaviour_gate import _GUARDED, _run_gate, _write_workspace

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 9), reason="probe uses modern typing in the fixture"
)

# A vocabulary the route enforces and states AS DATA (rejection_contract
# headers) but the model's CONSTRAINTS do not list -- the shape the shared
# builder corrects and a sampler of the probe's own could never satisfy.
_ROUTE_VOCABULARY = """    if payload.get("name") != "Ada":
        import json as _j
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail="name must be one of: Ada",
            headers={
                "X-Rejected-Field": _j.dumps("name"),
                "X-Rejection-Reason": "not_allowed",
                "X-Allowed-Values": _j.dumps(["Ada"]),
            },
        )
""" + _GUARDED

_ALWAYS_REFUSES = """    from fastapi import HTTPException
    raise HTTPException(status_code=422, detail="a tank must be registered before a reading")
"""


def test_the_probe_has_no_sampler_of_its_own():
    rendered = _render_probe()
    assert "def _value(" not in rendered and "def _payload(cls)" not in rendered
    assert "def _sample_payload_for(" in rendered and "def _post_accepting(" in rendered


def test_the_gate_posts_testers_base_sample(tmp_path):
    """Run the probe's payload source INSIDE the product, as the gate does:
    the base it computes off the live models is TESTER's base exactly, and
    what it posts carries every field of it."""
    import json
    import subprocess

    from app.factory.build.payload_helpers import PROBE_PAYLOAD_SLOT, render_probe_payload

    _write_workspace(tmp_path, _GUARDED)
    specs = specs_from_product_models(tmp_path)
    expected = {cap: _sample_payload(spec) for cap, spec in specs.items()}
    assert expected and base_samples(tmp_path) == expected

    source = render_probe_payload(PROBE_PAYLOAD_SLOT)
    assert source in _render_probe()
    script = tmp_path / "dump_probe_payload.py"
    script.write_text(
        "import json, sys\nsys.path.insert(0, '.')\n"
        + source
        + "\nfrom app.models import MODELS\n"
        "base = {c: _sample_payload(s) for c, s in _declared_specs(MODELS).items()}\n"
        "posted = {c: _payload_for(c) for c in MODELS}\n"
        "print(json.dumps({'base': base, 'posted': posted}))\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(script)], cwd=str(tmp_path), capture_output=True, text=True, timeout=180
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["base"] == expected
    for cap, base in expected.items():
        assert base.items() <= out["posted"][cap].items(), (cap, out["posted"][cap])


def test_a_vocabulary_the_route_states_as_data_is_accepted(tmp_path):
    """The vineyard-stop shape: every capability refused the probe's payload.
    With the shared builder the stated rejection is corrected and accepted."""
    _write_workspace(tmp_path, _ROUTE_VOCABULARY)
    result = _run_gate(tmp_path)
    joined = " ".join(result.findings) + " " + result.detail
    assert "no capability accepted its own schema" not in joined, joined
    assert "baseline POST returned HTTP 422" not in joined, joined
    assert result.ok is True, result


def test_a_refusal_carries_the_products_own_text(tmp_path):
    _write_workspace(tmp_path, _ALWAYS_REFUSES)
    result = _run_gate(tmp_path)
    assert result.ok is False
    joined = " ".join(result.findings)
    assert "a tank must be registered before a reading" in joined, joined
