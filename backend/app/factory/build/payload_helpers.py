"""The ONE request-payload builder every Factory-emitted suite uses.

Two emitted suites post to a capability route: ``tests/test_routes.py`` (the
route answers) and ``tests/test_placeholder_contract.py`` (a capability
calling a declared placeholder connector answers the typed 503 refusal). The
route checks the token (401), then the payload (422), then -- for a declared
placeholder -- refuses with 503. A contract test whose payload the route
refuses at 422 never reaches the refusal it is the authority on (live
2026-10-05: "Missing required field: member_id").

Both suites therefore build and correct their payloads with these helpers,
rendered from this module (product tests are not a package, so the source is
emitted into each file rather than imported):

* the Factory's spec sample is the base;
* every field the PRODUCT'S OWN model (``app.models.MODELS``) declares
  required and the base lacks is filled from that model -- its declared
  vocabulary, else its default when that is a real value, else the neutral
  sample of the default's type (``SAMPLE_VALUES``, the same table the
  Factory's spec sampler uses);
* a rejection the product states as data (``rejection_contract``) is
  corrected in the named field and retried, each (field, value) once.

Nothing here knows a capability, a field or a vocabulary by name.
"""

from __future__ import annotations

from typing import List

from app.factory.build.rejection_contract import (
    ACCEPT_STATUSES,
    ALLOWED_VALUES_HEADER,
    ALLOWED_VALUES_KEY,
    MISSING_REQUIRED,
    NOT_ALLOWED,
    OK_KEY,
    REJECTED_FIELD_HEADER,
    REJECTED_FIELD_KEY,
    REJECTION_REASON_HEADER,
    REJECTION_REASON_KEY,
)
from app.factory.build.findings import render_capability_recorder
from app.factory.build.roles_constants import _SAMPLE_VALUES


def render_payload_helpers() -> List[str]:
    """Source lines defining ``_sample_payload_for``, ``_rejection`` and
    ``_post_accepting``. They use the module-global ``client``."""
    by_type = {k: v for k, v in _SAMPLE_VALUES.items() if k in ("str", "int", "float", "bool")}
    return [
        "import dataclasses as _dataclasses",
        "import json as _json",
        "",
        f"_SAMPLE_BY_TYPE = {by_type!r}",
        # The declared accept contract (rejection_contract): every helper
        # below judges a response by these, never a literal of its own.
        f"_ACCEPT_STATUSES = {tuple(ACCEPT_STATUSES)!r}",
        f"_OK_KEY = {OK_KEY!r}",
        "",
        "",
        "def _model_of(capability_id):",
        "    try:",
        "        from app.models import MODELS",
        "    except Exception:",
        "        return None",
        "    return MODELS.get(capability_id)",
        "",
        "",
        "def _field_sample(capability_id, field):",
        '    """A value the product\'s own model declares acceptable for ``field``."""',
        "    cls = _model_of(capability_id)",
        "    if cls is None:",
        "        return None",
        "    rules = (getattr(cls, 'CONSTRAINTS', {}) or {}).get(field) or {}",
        "    allowed = rules.get('allowed_values')",
        "    if allowed:",
        "        return allowed[0]",
        "    default = None",
        "    if _dataclasses.is_dataclass(cls):",
        "        attr = (getattr(cls, '_FIELD_PY', {}) or {}).get(field, field)",
        "        for f in _dataclasses.fields(cls):",
        "            if f.name != attr:",
        "                continue",
        "            if f.default is not _dataclasses.MISSING:",
        "                default = f.default",
        "            elif f.default_factory is not _dataclasses.MISSING:",
        "                default = f.default_factory()",
        "    low = rules.get('min')",
        "    if default not in (None, '') and not isinstance(default, bool):",
        "        if not isinstance(default, (int, float)) or default:",
        "            if low is None or not isinstance(default, (int, float)) or default >= low:",
        "                return default",
        "    sample = _SAMPLE_BY_TYPE.get(type(default).__name__, _SAMPLE_BY_TYPE['str'])",
        "    if low is not None and isinstance(sample, (int, float)) and sample < low:",
        "        sample = low",
        "    return sample",
        "",
        "",
        "def _sample_payload_for(capability_id, base):",
        '    """The Factory spec sample, completed with every field the product\'s',
        '    own model declares required and the sample lacks."""',
        "    payload = dict(base or {})",
        "    cls = _model_of(capability_id)",
        "    if cls is None:",
        "        return payload",
        "    constraints = getattr(cls, 'CONSTRAINTS', {}) or {}",
        "    for name in list(getattr(cls, 'FIELDS', []) or []):",
        "        if not (constraints.get(name) or {}).get('required'):",
        "            continue",
        "        if payload.get(name) in (None, ''):",
        "            value = _field_sample(capability_id, name)",
        "            if value not in (None, ''):",
        "                payload[name] = value",
        "    return payload",
        "",
        "",
        "def _rejection(resp):",
        '    """The rejection as data: (field, reason, allowed) or None. Read from',
        "    the structured rejection the product states -- headers on a 422,",
        "    keys on an ok:false body -- never from prose or JSON text, so no",
        '    token in a response body can leak into a payload."""',
        f"    field = resp.headers.get({REJECTED_FIELD_HEADER!r})",
        "    if field is not None:",
        "        try:",
        "            field = _json.loads(field)",
        "        except ValueError:",
        "            return None",
        f"        reason = resp.headers.get({REJECTION_REASON_HEADER!r})",
        f"        allowed = resp.headers.get({ALLOWED_VALUES_HEADER!r})",
        "        try:",
        "            allowed = _json.loads(allowed) if allowed is not None else None",
        "        except ValueError:",
        "            allowed = None",
        "        return field, reason, allowed",
        "    try:",
        "        body = resp.json()",
        "    except Exception:",
        "        return None",
        f"    if not isinstance(body, dict) or {REJECTED_FIELD_KEY!r} not in body:",
        "        return None",
        f"    return (body.get({REJECTED_FIELD_KEY!r}), body.get({REJECTION_REASON_KEY!r}),",
        f"            body.get({ALLOWED_VALUES_KEY!r}))",
        "",
        "",
        "def _post_accepting(path, payload, headers, capability_id=None):",
        '    """POST the completed payload; when the product refuses a field AS',
        "    DATA, adopt the value its own model declares and retry. Returns",
        "    (response, corrections). Each (field, value) is tried at most once,",
        "    so the loop converges or stops. A refusal that is not a field",
        '    rejection (a 503 placeholder refusal, a 401) is returned as is."""',
        "    payload = _sample_payload_for(capability_id, payload)",
        "    corrections = []",
        "    tried = set()",
        "    resp = client.post(path, json=payload, headers=headers)",
        "    for _ in range(len(payload) + 2):",
        "        failed = resp.status_code not in _ACCEPT_STATUSES",
        "        if not failed:",
        "            try:",
        '                failed = resp.json().get(_OK_KEY) is False',
        "            except Exception:",
        "                failed = False",
        "        if not failed:",
        "            break",
        "        rejection = _rejection(resp)",
        "        if rejection is None:",
        "            break",
        "        field, reason, allowed = rejection",
        "        if not isinstance(field, str) or not field:",
        "            break",
        f"        if reason == {NOT_ALLOWED!r} and isinstance(allowed, list) and allowed:",
        "            value = allowed[0]",
        f"        elif reason == {MISSING_REQUIRED!r}:",
        "            value = _field_sample(capability_id, field)",
        "            if value in (None, ''):",
        "                break",
        "        else:",
        "            break",
        "        key = (field, repr(value))",
        "        if key in tried:",
        "            break",
        "        tried.add(key)",
        "        corrections.append(",
        "            str(field) + '=' + repr(payload.get(field)) + '->' + repr(value)",
        "        )",
        "        payload[field] = value",
        "        resp = client.post(path, json=payload, headers=headers)",
        "    if _still_failed(resp):",
        "        _record_capability_failure(capability_id)",
        "    return resp, corrections",
        "",
        "",
        "def _still_failed(resp):",
        "    if resp.status_code not in _ACCEPT_STATUSES:",
        "        return True",
        "    try:",
        '        return resp.json().get(_OK_KEY) is False',
        "    except Exception:",
        "        return False",
        "",
        "",
        *render_capability_recorder(),
    ]


# -- the payload every Factory PROBE posts -----------------------------------
#
# The WRITER gate's behaviour probe and the PRODUCT round-trip probe used to
# carry a sampler each (``_ann``/``_value``/``_payload``), and TESTER a third
# (roles_handlers._sample_payload) -- three verdicts on "a payload built from
# its own schema" (live 9de69276: the writer gate stopped on "no capability
# accepted its own schema" while TESTER's payloads were accepted). Every probe
# now renders this ONE slot: TESTER's own sampler, rendered from its source,
# over the declared specs read off the live models (declared_specs) -- then
# completed and corrected by the builder above.
#
# The base is computed when the probe RUNS, never baked when it is stamped:
# a stamped self-check is Factory-owned and its bytes must not depend on the
# product's models (they do not exist when WRITER starts, and a refresh would
# otherwise report every product as running a stale Factory file).

#: The line a probe carries where its payload source is rendered in.
PROBE_PAYLOAD_SLOT = "# __PROBE_PAYLOAD__ (rendered in by payload_helpers.render_probe_payload)"


def _tester_sampler_parts():
    """TESTER's sampler: its functions (call order) and the tables they read."""
    from app.factory.build import block_inputs, roles_handlers

    functions = (
        block_inputs.sample_channel_value,
        roles_handlers._resolve_known_field_type,
        roles_handlers._normalize_field_type,
        roles_handlers._looks_like_email_field,
        roles_handlers._temporal_sample,
        roles_handlers._sample_value,
        roles_handlers._sample_payload,
    )
    tables = {
        "STORE_NOTIFICATION_CHANNELS": block_inputs.STORE_NOTIFICATION_CHANNELS,
        "_DOMAIN_CHANNEL_SAMPLE": block_inputs._DOMAIN_CHANNEL_SAMPLE,
        "_TYPE_ALIASES": roles_handlers._TYPE_ALIASES,
        "_TEMPORAL_SAMPLES": roles_handlers._TEMPORAL_SAMPLES,
        "_SAMPLE_VALUES": roles_handlers._SAMPLE_VALUES,
    }
    return functions, tables


def render_tester_sampler() -> List[str]:
    """TESTER's ``_sample_payload`` and what it calls, rendered from their
    own source (annotations dropped: the product needs no typing names).
    Not a copy that can drift -- the Factory's functions, as they are."""
    import ast
    import inspect
    import textwrap

    class _Unannotate(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            self.generic_visit(node)
            node.returns = None
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
                if arg is not None:
                    arg.annotation = None
            return node

        def visit_AnnAssign(self, node):
            if node.value is None:
                return None
            return ast.copy_location(ast.Assign(targets=[node.target], value=node.value), node)

    functions, tables = _tester_sampler_parts()
    lines = [f"{name} = {value!r}" for name, value in tables.items()]
    for fn in functions:
        tree = _Unannotate().visit(ast.parse(textwrap.dedent(inspect.getsource(fn))))
        lines += ["", "", ast.unparse(ast.fix_missing_locations(tree))]
    return lines


def base_samples(workspace) -> dict:
    """The spec sample per capability -- exactly the base TESTER posts:
    ``roles_handlers._sample_payload`` over the product's live declared
    models (declared_specs). Empty when nothing is declared."""
    from pathlib import Path

    from app.factory.build.declared_specs import specs_from_product_models
    from app.factory.build.roles_handlers import _sample_payload

    root = Path(getattr(workspace, "workspace", workspace))
    try:
        specs = specs_from_product_models(root)
    except Exception:  # noqa: BLE001 -- no readable models: the builder completes from MODELS
        return {}
    return {
        str(cap): _sample_payload(spec)
        for cap, spec in (specs or {}).items()
        if isinstance(spec, dict)
    }


def render_probe_payload(source: str) -> str:
    """``source`` with :data:`PROBE_PAYLOAD_SLOT` replaced by the one payload
    source: the declared-spec reader, TESTER's sampler, the builder, and
    ``_payload_for(cap_id)``."""
    from app.factory.build.declared_specs import SPECS_FROM_MODELS

    lines = [
        *SPECS_FROM_MODELS.strip("\n").splitlines(),
        "",
        "",
        *render_tester_sampler(),
        "",
        "",
        *render_payload_helpers(),
        "",
        "",
        "_DECLARED_SPECS = None",
        "",
        "",
        "def _payload_for(cap_id):",
        '    """TESTER\'s base sample over the live declared models, completed',
        "    with what the product's own model requires (payload_helpers -- one",
        '    builder for every probe)."""',
        "    global _DECLARED_SPECS",
        "    if _DECLARED_SPECS is None:",
        "        try:",
        "            from app.models import MODELS as _LIVE_MODELS",
        "            _DECLARED_SPECS = _declared_specs(_LIVE_MODELS)",
        "        except Exception:",
        "            _DECLARED_SPECS = {}",
        "    spec = _DECLARED_SPECS.get(cap_id)",
        "    base = _sample_payload(spec) if isinstance(spec, dict) else {}",
        "    return _sample_payload_for(cap_id, base)",
        "",
    ]
    return source.replace(PROBE_PAYLOAD_SLOT, "\n".join(lines), 1)


def run_probe_source(ctx, source: str):
    """Run a rendered probe from a temp file OUTSIDE the workspace.

    ``python -c <probe>`` put the whole source on the command line; with the
    builder rendered in it outgrew the Windows argv limit (WinError 206). The
    loader keeps ``''`` (the workspace, the run's cwd) on sys.path exactly as
    ``-c`` did, so ``import app`` resolves the same way.
    """
    import os
    import sys
    import tempfile

    fd, path = tempfile.mkstemp(prefix="factory-probe-", suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(source)
        loader = (
            "import runpy, sys; sys.path.insert(0, ''); "
            f"runpy.run_path({path!r}, run_name='__main__')"
        )
        return ctx.run([sys.executable, "-c", loader])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
