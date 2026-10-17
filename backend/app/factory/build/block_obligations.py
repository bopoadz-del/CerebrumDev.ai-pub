"""What a block needs before a capability may declare it.

F11 says a capability must invoke every block it declares. That rule is only
honest if the capability *can* invoke the block successfully -- and the
factory had no check that it could. Two capabilities of the four in session
sess_6400b6c shipped, booted, and failed on exactly that gap:

    daily_log_management  document_engine: No input files provided
                          (pdf/docx/xlsx). Pass file_path as pdf_path, ...
    crew_dashboard        team: Team access denied

Neither is a plumbing bug. In both, the planner assigned a block whose
precondition the capability's own schema could not meet, and nothing noticed
until the generated platform ran.

Both preconditions are the same shape, and both are satisfiable:

* **Schema obligation** -- the block needs a value only the caller can
  supply, so the capability's model spec must carry a field for it.
  ``document_engine`` needs a document; a daily log with no file field can
  never give it one.

* **Resource obligation** -- the block needs an id that only the block can
  mint, so the capability must create the resource first and carry the
  RETURNED id into the later call. Measured directly against the vendored
  blocks:

      create_team {"user_id": "u7", "name": "T7", "slug": "t7"}
        -> {"team_id": "team_4f473e37589a69bb", ..., "owner": "u7"}
      get_team_context {"user_id": "u7"}
        -> {"error": "Team access denied"}
      get_team_context {"user_id": "u7", "team_id": "team_4f473e37589a69bb"}
        -> {"role": "owner", "permissions": ["*"], ...}

  The live handler passed ``team_id = payload["primary_crew"]`` -- a crew
  name, not a team id -- and never created the team. The block was working.

  ``storage`` behaves the same way and is worth stating because it fails
  quietly rather than loudly: ``store`` MINTS its own ``file_id`` and ignores
  the caller's, so ``retrieve`` by the caller's id answers ``file_not_found``
  while ``retrieve`` by the returned id answers with the content. A
  capability that does not carry the returned id loses the file and is told
  it stored one.

The contract, from here: **if you assign it, you feed it.** Checked
statically, before any handler is authored, with the missing field named --
not discovered by running the platform.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.factory.store_kits import block_manifests

#: The Store's ``requires_inputs`` types, as a model spec spells them.
_SPEC_TYPES = {"string": "str", "text": "str", "number": "float", "boolean": "bool"}


def schema_obligations(store_root: Any = None) -> Dict[str, Dict[str, Any]]:
    """Blocks that need a value only the CALLER can supply.

    Read from each block's signed ``requires_inputs``: an entry naming
    ``satisfied_by`` is an obligation. ``any_of`` are the field names that
    satisfy it; ``add`` is what the model spec gets when it carries none of
    them. No block is named here.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for bid, manifest in sorted(block_manifests(store_root).items()):
        for need in manifest.get("requires_inputs") or []:
            alts = need.get("satisfied_by") if isinstance(need, dict) else None
            if not (isinstance(alts, list) and alts and need.get("name")):
                continue
            declared = str(need.get("type") or "string")
            out[bid] = {
                "any_of": [str(a) for a in alts],
                "add": {
                    "name": str(need["name"]),
                    "type": _SPEC_TYPES.get(declared, declared),
                    "required": bool(need.get("required", False)),
                },
                "why": str(need.get("why") or ""),
            }
            break
    return out


#: The keys of a resource obligation, as the Store's ``preconditions[]``
#: entry declares them (``ensure`` present = the block mints the resource).
_ENSURE_KEYS = ("scope", "ensure", "ensure_input", "carry", "into", "why")


def resource_obligations(store_root: Any = None) -> Dict[str, Dict[str, Any]]:
    """Blocks that need an id only the BLOCK can mint.

    Read from each block's signed ``preconditions``: an entry naming
    ``ensure`` is an obligation. The capability must call ``ensure`` first
    and carry the returned ``carry`` key into every action in ``into``.
    ``scope`` says WHERE the ensure step belongs, and it is a real
    distinction rather than a label:

    * ``platform`` -- the resource exists once for the whole platform and its
      ensure inputs are platform constants, so it can and should run at
      STARTUP, before any capability (owner's ruling R1c).
    * ``per_record`` -- the resource is minted per record and its ensure
      inputs ARE the caller's data. A boot-time step would have to invent a
      record, which is F18. It stays the handler's job, and the WRITER
      contract probe names it when the handler gets it wrong.

    Getting this backwards would be worse than leaving it alone: a startup
    step that fabricates a record is exactly the class of defect the factory
    refuses. No block is named here.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for bid, manifest in sorted(block_manifests(store_root).items()):
        for pre in manifest.get("preconditions") or []:
            if not (isinstance(pre, dict) and pre.get("ensure")):
                continue
            rule: Dict[str, Any] = {"resource": str(pre.get("kind") or bid)}
            for key in _ENSURE_KEYS:
                value = pre.get(key)
                rule[key] = list(value) if isinstance(value, list) else value
            out[bid] = rule
            break
    return out


class BlockObligationError(ValueError):
    """A capability declares a block its schema cannot feed."""


#: Factory record envelope. The deterministic fallback spec always carried
#: ``reference``; LLM veterinary-care specs (sess_66a387b5c9b0495c) omitted
#: it, so PRODUCT ``_constraint_guard`` / Store contracts refused
#: schema-built payloads for pet_records_management, appointment_scheduling,
#: automated_reminders, and veterinarian_availability_tracking.
ENVELOPE_REFERENCE_FIELD: Dict[str, Any] = {
    "name": "reference",
    "type": "str",
    "required": True,
}

#: Fallback ``status`` vocabulary. LLM appointment / availability specs
#: (sess_5dfb4a3, sess_1fd1d54c) declared scheduled/completed/cancelled —
#: or a bare str that sampled as ``sample`` — while the route
#: ``_constraint_guard`` / coder handler refused anything outside this set.
ENVELOPE_STATUS_VALUES: tuple[str, ...] = ("open", "in_progress", "closed")

ENVELOPE_STATUS_FIELD: Dict[str, Any] = {
    "name": "status",
    "type": "str",
    "required": True,
    "allowed_values": list(ENVELOPE_STATUS_VALUES),
}


def is_envelope_status_field(name: Optional[str]) -> bool:
    return str(name or "").strip().lower() == "status"


def is_envelope_status_vocab(values: Optional[Sequence[Any]]) -> bool:
    return list(values or []) == list(ENVELOPE_STATUS_VALUES)


def normalize_envelope_status_field(field: Dict[str, Any]) -> bool:
    """Rewrite a ``status`` column to the factory envelope vocabulary.

    Returns True when ``allowed_values`` changed. Does not add a status
    column that the spec never declared — only agrees the existing one
    with the guard PRODUCT actually runs.
    """
    if not is_envelope_status_field(field.get("name")):
        return False
    if is_envelope_status_vocab(field.get("allowed_values")):
        return False
    field["allowed_values"] = list(ENVELOPE_STATUS_VALUES)
    field.setdefault("type", "str")
    return True


def ensure_record_envelope(
    spec: Optional[Dict[str, Any]],
) -> tuple:
    """Ensure the factory envelope ``reference`` + ``status``.

    Returns ``(spec, added_names)``. Idempotent. Adds ``reference`` when
    missing. Adds ``status`` when missing (live sess_f1fe691 clinic_dashboard
    refused schema-sample with ``Missing required field: status`` — #312
    only rewrote an existing status vocabulary). When a ``status`` column
    already exists, its allowed_values become ``open|in_progress|closed``
    so schema samples and ``_constraint_guard`` cannot disagree.
    """
    if not spec:
        return spec, []
    raw_fields = [dict(f) for f in (spec.get("fields") or []) if isinstance(f, dict)]
    # Credentials are ENV configuration, never request payload fields. A
    # connector's settings reached a spec as fields and the generated route
    # answered ``HTTP 422: Missing required field: <credential>`` on every
    # POST. Every spec passes through here -- WRITER's routes and models,
    # TESTER's samples -- so this is the one place to drop them. An ALL_CAPS
    # name is a constant or configuration key by convention and never a
    # record column; the miners additionally exclude anything the source
    # reads from the environment (block_inputs.settings_names).
    fields = [f for f in raw_fields if not str(f.get("name") or "").isupper()]
    settings_dropped = len(fields) != len(raw_fields)
    have = {str(f.get("name")) for f in fields if f.get("name")}
    added: List[str] = []
    status_changed = settings_dropped
    if "reference" not in have:
        fields.append(dict(ENVELOPE_REFERENCE_FIELD))
        added.append("reference")
    if "status" not in have:
        fields.append(dict(ENVELOPE_STATUS_FIELD))
        added.append("status")
    for field in fields:
        if normalize_envelope_status_field(field):
            status_changed = True
    if not added and not status_changed:
        return spec, []
    out = dict(spec)
    out["fields"] = fields
    if added:
        notes = list(out.get("envelope_fields") or [])
        notes.extend(added)
        out["envelope_fields"] = notes
    return out, added


def schema_obligations_for(block_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    rules = schema_obligations()
    return {b: rules[b] for b in block_ids or () if b in rules}


def resource_obligations_for(block_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    rules = resource_obligations()
    return {b: rules[b] for b in block_ids or () if b in rules}


def _field_names(spec: Optional[Dict[str, Any]]) -> List[str]:
    return [
        str(f.get("name"))
        for f in ((spec or {}).get("fields") or [])
        if isinstance(f, dict) and f.get("name")
    ]


def augment_model_spec(
    spec: Optional[Dict[str, Any]], block_ids: Sequence[str]
) -> Optional[Dict[str, Any]]:
    """Add the field an assigned block needs, when the spec has none of them.

    Runs after the coder has designed the entity, so the agent still owns the
    design; this only closes an obligation the design left open. Appended
    after the width cap on purpose -- an obligated field is not optional
    padding, and dropping it is what shipped the broken capability.
    """
    if not spec:
        return spec
    needed = schema_obligations_for(block_ids)
    if not needed:
        return spec
    fields = list(spec.get("fields") or [])
    have = {str(f.get("name")) for f in fields if isinstance(f, dict)}
    added: List[str] = []
    for block_id, rule in sorted(needed.items()):
        if have & set(rule["any_of"]):
            continue
        new_field = dict(rule["add"])
        new_field.setdefault("required", False)
        fields.append(new_field)
        have.add(str(new_field["name"]))
        added.append("%s (for %s)" % (new_field["name"], block_id))
    if not added:
        return spec
    out = dict(spec)
    out["fields"] = fields
    out["obligated_fields"] = list(spec.get("obligated_fields") or []) + added
    return out


def audit_capability(
    capability_id: str,
    block_ids: Sequence[str],
    spec: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Findings for one capability. Empty means the assignment is feedable."""
    findings: List[str] = []
    have = set(_field_names(spec))
    for block_id, rule in sorted(schema_obligations_for(block_ids).items()):
        if not (have & set(rule["any_of"])):
            findings.append(
                "%s declares %s but its schema carries none of %s -- %s"
                % (capability_id, block_id, ", ".join(rule["any_of"]), rule["why"])
            )
    return findings


def assert_feedable(
    capability_id: str,
    block_ids: Sequence[str],
    spec: Optional[Dict[str, Any]] = None,
) -> None:
    """Fail before codegen, naming the missing field.

    Deliberately raises rather than warning: a capability that cannot feed a
    block it declares fails F11 at the WRITER gate, and finding that out by
    running the platform costs the whole build.
    """
    findings = audit_capability(capability_id, block_ids, spec)
    if findings:
        raise BlockObligationError(
            "block obligation unmet before codegen: " + "; ".join(findings)
        )


def platform_obligations_for(block_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Only the preconditions that can honestly be met at startup."""
    return {
        b: rule
        for b, rule in resource_obligations_for(block_ids).items()
        if rule.get("scope") == "platform"
    }


def render_preconditions_module(
    block_ids: Sequence[str], product_name: str = "platform"
) -> str:
    """Emit ``app/preconditions.py`` for this build.

    OWNER'S RULING R1c, 2026-09-01: preconditions become GENERATED STARTUP
    CODE -- an ensure step per declared precondition, before any capability
    runs.

    Until now the obligation reached the coder as prose in its prompt
    (``describe_resource_obligations``), and every handler had to remember it
    independently. On residential-lettings three of four handlers did not:
    ``maintenance_issue_tracking`` used the correct calling convention and
    still answered ``team: Team access denied``, because nothing had created
    the team. A rule that has to be re-obeyed in every handler is a rule that
    will be missed in one.

    Only ``scope: platform`` obligations are emitted here. A ``per_record``
    one would have to invent the caller's data to run at boot, which is F18.

    The module never raises on failure: a platform that cannot reach a block
    at boot must still start and report the fact, rather than refusing to
    serve anything. The failure is recorded and readable.
    """
    rules = platform_obligations_for(block_ids)
    header = [
        '"""Platform preconditions, run once at startup.',
        "A block that mints its own id needs that id created BEFORE any",
        "capability calls it. Generated from the factory's resource",
        "obligations (R1c) rather than left to each handler to remember.",
        "",
        "``resource_id(block_id)`` returns the id the ensure step received, or",
        "None when the step has not run or did not succeed.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import logging",
        "from typing import Any, Dict, Optional",
        "",
        "_LOG = logging.getLogger(__name__)",
        "",
        "#: block_id -> the id its ensure action returned.",
        "RESOURCE_IDS: Dict[str, str] = {}",
        "#: block_id -> why its ensure step did not produce an id.",
        "RESOURCE_ERRORS: Dict[str, str] = {}",
        "",
        "PRECONDITIONS: Dict[str, Dict[str, Any]] = " + repr(
            {
                b: {
                    "ensure": r["ensure"],
                    "carry": r["carry"],
                    "input": _platform_ensure_input(r, product_name),
                    "into": list(r.get("into") or []),
                }
                for b, r in sorted(rules.items())
            }
        ),
        "",
        "",
        "def resource_id(block_id: str) -> Optional[str]:",
        '    """The id ``ensure_all`` obtained for this block, if any."""',
        "    return RESOURCE_IDS.get(block_id)",
        "",
        "",
        "def ensure_all() -> Dict[str, Any]:",
        '    """Run every platform precondition. Idempotent; never raises."""',
        "    from app.dispatch import execute",
        "",
        "    for block_id, rule in PRECONDITIONS.items():",
        "        if RESOURCE_IDS.get(block_id):",
        "            continue",
        "        try:",
        "            result = execute(",
        "                block_id, dict(rule[\"input\"]), action=rule[\"ensure\"]",
        "            )",
        "        except Exception as exc:  # noqa: BLE001 - boot must not die",
        "            RESOURCE_ERRORS[block_id] = \"%s: %s\" % (",
        "                type(exc).__name__, exc,",
        "            )",
        "            _LOG.warning(",
        "                \"precondition %s %s raised: %s\",",
        "                block_id, rule[\"ensure\"], exc,",
        "            )",
        "            continue",
        "        got = None",
        "        if isinstance(result, dict):",
        "            got = result.get(rule[\"carry\"])",
        "            if got is None and isinstance(result.get(\"result\"), dict):",
        "                got = result[\"result\"].get(rule[\"carry\"])",
        "        if got:",
        "            RESOURCE_IDS[block_id] = str(got)",
        "            RESOURCE_ERRORS.pop(block_id, None)",
        "        else:",
        "            RESOURCE_ERRORS[block_id] = str(",
        "                (result or {}).get(\"error\") if isinstance(result, dict)",
        "                else result",
        "            )[:200]",
        "            _LOG.warning(",
        "                \"precondition %s %s returned no %s: %s\",",
        "                block_id, rule[\"ensure\"], rule[\"carry\"],",
        "                RESOURCE_ERRORS[block_id],",
        "            )",
        "    return {\"ids\": dict(RESOURCE_IDS), \"errors\": dict(RESOURCE_ERRORS)}",
        "",
    ]
    return "\n".join(header)


def _platform_ensure_input(
    rule: Dict[str, Any], product_name: str
) -> Dict[str, str]:
    """Platform-constant values for a platform-scoped ensure step.

    Every value here is about the PLATFORM, never about a domain record --
    that is what makes the step honest rather than fabricated. A field the
    factory has no platform-level value for would mean the obligation is
    mis-scoped, so it is named rather than filled with a guess.
    """
    slug = "".join(
        c if c.isalnum() else "-" for c in (product_name or "platform").lower()
    ).strip("-") or "platform"
    known = {
        "user_id": "system",
        "name": "%s system" % (product_name or "platform"),
        "slug": "%s-system" % slug,
        "owner": "system",
        "description": "Created at startup by the platform's precondition step.",
    }
    out: Dict[str, str] = {}
    for field in rule.get("ensure_input") or []:
        if field not in known:
            raise BlockObligationError(
                "resource obligation for %r is scoped 'platform' but its "
                "ensure input %r has no platform-level value -- it is domain "
                "data, so the obligation is per_record" % (rule.get("resource"), field)
            )
        out[field] = known[field]
    return out


def describe_resource_obligations(block_ids: Sequence[str]) -> str:
    """The rule the coder must follow, as prose for the handler prompt."""
    rules = resource_obligations_for(block_ids)
    if not rules:
        return ""
    out = [
        "Resource preconditions. These blocks mint their own id and refuse "
        "the action without it -- never pass a domain value from the payload "
        "as that id:",
    ]
    for block_id, rule in sorted(rules.items()):
        if rule.get("scope") == "platform":
            # The startup step already created it (R1c). Telling the handler
            # to create it again is how you get two teams and a race.
            out.append(
                "- %s: the platform ALREADY created this at startup. Read the "
                "id with `from app.preconditions import resource_id` ->"
                " `resource_id(%r)` and pass it as %s into %s. Do NOT call %s "
                "yourself. %s"
                % (
                    block_id,
                    block_id,
                    rule["carry"],
                    ", ".join(rule["into"]),
                    rule["ensure"],
                    rule["why"],
                )
            )
            continue
        out.append(
            "- %s: call %s (input: %s) FIRST, read %s off its result, and "
            "pass that %s into %s. %s"
            % (
                block_id,
                rule["ensure"],
                ", ".join(rule["ensure_input"]),
                rule["carry"],
                rule["carry"],
                ", ".join(rule["into"]),
                rule["why"],
            )
        )
    return "\n".join(out)


# -- dependency obligation ------------------------------------------------
#
# The third precondition class, found while proving the first two. It is the
# same contract, not a new one: a vendored block cannot run without the
# distributions its source imports, and nothing declared them.
#
#     document_engine   import yaml            (app/blocks/document_engine.py:144)
#     storage           import aiofiles        (module level)
#     web, search       import httpx, bs4      (module level)
#     database          import psycopg2        (function level)
#
# The generated requirements.txt is derived from ``app/`` only, so every one
# of these was missing. A module-level one means the block cannot import at
# all; a function-level one means the block imports, reports healthy, and
# dies on the single action that needs it -- which is precisely how
# ``document_engine`` answered "PyYAML not installed" against a real fixture
# after the schema obligation above had been satisfied.
#
# This is F10 in the vendored lane: an import satisfied by accident rather
# than by declaration. The requirements.txt header already states that rule
# for the factory's own runtime modules; it was simply never applied to the
# source the CLONER copies in.
#
# Derived by AST from the text actually written into the workspace -- not a
# hand-kept list, which would drift the first time a block gained an import.
# An import this table cannot name a distribution for RAISES. Guessing a
# PyPI name is how you ship a Dockerfile that fails at ``pip install``.

import ast as _ast
import re as _re
import sys as _sys

_STDLIB = frozenset(_sys.stdlib_module_names) | {"__future__"}

#: Modules that ship with the platform itself, or that are the platform.
#:
#: The last three are the STORE's own local roots, not PyPI distributions:
#: ``block`` is the sibling block.py that block_registry/run.py imports,
#: ``block_store`` is the Store's top-level kit package, ``level`` is a local
#: certifier package. They must never reach DISTRIBUTIONS -- ``block`` and
#: ``level`` are real, unrelated projects on PyPI, so mapping them would make a
#: customer's Dockerfile ``pip install`` strangers. Every one is a lazy import;
#: what they cost is one feature, which is not a requirements.txt matter.
_LOCAL_ROOTS = frozenset(
    {"app", "vendor", "kits", "tests", "scripts", "alembic",
     "block", "block_store", "level"}
)

#: import name -> PyPI distribution, for every third-party import present in
#: the Store's block roster. Recorded explicitly because the import name and
#: the distribution name disagree often enough that inferring is unsafe.
DISTRIBUTIONS: Dict[str, str] = {
    "CoolProp": "CoolProp",
    "PIL": "Pillow",
    "QuantLib": "QuantLib",
    "aiofiles": "aiofiles",
    "aiohttp": "aiohttp",
    "aiosmtplib": "aiosmtplib",
    "boto3": "boto3",
    "botocore": "botocore",
    "bs4": "beautifulsoup4",
    "cryptography": "cryptography",
    "ddgs": "ddgs",
    "deep_translator": "deep-translator",
    "docx": "python-docx",
    "easyocr": "easyocr",
    "ezdxf": "ezdxf",
    "fitz": "PyMuPDF",
    "gtts": "gTTS",
    "httpx": "httpx",
    "ifcopenshell": "ifcopenshell",
    "marker": "marker-pdf",
    "mcp": "mcp",
    "model2vec": "model2vec",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "packaging": "packaging",
    "pandas": "pandas",
    "pdfplumber": "pdfplumber",
    "pint": "Pint",
    "psycopg2": "psycopg2-binary",
    "pydantic": "pydantic",
    "pypdf": "pypdf",
    # The marker block reads the PDF text layer with pypdfium2 since
    # Cerebrum-Blocks #144 (marker-pdf pinned pillow<11).
    "pypdfium2": "pypdfium2",
    # Legacy import name still used by Store document_engine (Cerebrum-Blocks
    # #105). Live sess_d1cb9d51c5354bea / CEREBRUMDEV-BACKEND-A crashed CLONER
    # because only the modern ``pypdf`` key was recorded.
    "PyPDF2": "PyPDF2",
    "pytesseract": "pytesseract",
    "sentence_transformers": "sentence-transformers",
    "shapely": "shapely",
    "sklearn": "scikit-learn",
    "speech_recognition": "SpeechRecognition",
    "stripe": "stripe",
    "sympy": "sympy",
    "ultralytics": "ultralytics",
    "yaml": "PyYAML",
    # Live: "CLONER failed: vendored source imports 'bcrypt' and the factory
    # has no PyPI distribution recorded for it". bcrypt was only the first one
    # the build tripped over -- moving the Store pin brought twelve import
    # names this table had never seen. Names are the Store's own pins where it
    # pins them. tests/factory/test_block_obligations_cover_the_store.py now
    # sweeps the pinned Store, so the next one fails in CI and not in a
    # customer's build.
    "Levenshtein": "Levenshtein",
    "RestrictedPython": "RestrictedPython",
    "asyncpg": "asyncpg",
    "bcrypt": "bcrypt",
    "manifold3d": "manifold3d",
    "mlflow": "mlflow",
    "psutil": "psutil",
    "redis": "redis",
    "requests": "requests",
    "sentry_sdk": "sentry-sdk",
    "specklepy": "specklepy",
    "trimesh": "trimesh",
    # Runtime floor packages. Present so a vendored module that imports one
    # is not reported as unknown; _render_requirements dedupes them.
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "alembic": "alembic",
    "sqlalchemy": "SQLAlchemy",
    "starlette": "starlette",
}


class _ImportScan(_ast.NodeVisitor):
    """Top-level imports vs. imports reached only by calling something."""

    def __init__(self) -> None:
        self.found: Dict[str, str] = {}
        self._depth = 0

    def _record(self, name: str, node: _ast.AST) -> None:
        top = (name or "").split(".")[0]
        if not top or top in _STDLIB or top in _LOCAL_ROOTS:
            return
        where = "lazy" if self._depth else "module"
        # A module-level import is the stronger claim; never downgrade it.
        if self.found.get(top) != "module":
            self.found[top] = where

    def visit_Import(self, node: _ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name, node)

    def visit_ImportFrom(self, node: _ast.ImportFrom) -> None:
        if node.level:
            return
        self._record(node.module or "", node)

    def visit_FunctionDef(self, node) -> None:
        self._depth += 1
        for child in _ast.iter_child_nodes(node):
            self.visit(child)
        self._depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef


#: Fallback scanner. Deliberately crude -- it only has to find the import
#: statement, and it runs on source the AST already refused.
_IMPORT_LINE_RE = _re.compile(
    r"^(?P<indent>[ \t]*)(?:import|from)[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_.]*)",
    _re.MULTILINE,
)


def third_party_imports(source: str) -> Dict[str, str]:
    """``{import_name: "module"|"lazy"}`` for one Python source string.

    A file the factory cannot parse is NOT treated as importing nothing --
    that would be the accident-not-declaration failure all over again, and
    unparseable Store source is routine here rather than exceptional: the
    CLONER exists in part to vendor partial and truncated block sources and
    emit Store-unwired adapters over them. A file whose line 40 is broken
    still really imports what its line 1 says it imports.

    So: AST when it parses (which distinguishes a module-level import from
    one reached only by calling something), a line scan when it does not.
    """
    try:
        tree = _ast.parse(source or "")
    except SyntaxError:
        found: Dict[str, str] = {}
        for match in _IMPORT_LINE_RE.finditer(source or ""):
            top = match.group("name").split(".")[0]
            if not top or top in _STDLIB or top in _LOCAL_ROOTS:
                continue
            where = "lazy" if match.group("indent") else "module"
            if found.get(top) != "module":
                found[top] = where
        return found
    scan = _ImportScan()
    scan.visit(tree)
    return scan.found


def distribution_for(module: str) -> str:
    """PyPI distribution for an import name, or raise naming the module."""
    try:
        return DISTRIBUTIONS[module]
    except KeyError:
        raise BlockObligationError(
            "vendored source imports %r and the factory has no PyPI "
            "distribution recorded for it; add it to "
            "block_obligations.DISTRIBUTIONS rather than shipping a "
            "requirements.txt that omits it" % (module,)
        ) from None


def dependency_obligations(files: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """Distributions the vendored files oblige the platform to declare.

    ``{distribution: {"module": import_name, "reach": "module"|"lazy",
    "files": [...]}}``. ``reach`` is the strongest reach seen: ``module``
    means the block cannot import without it, ``lazy`` means one action dies.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for rel in sorted(files):
        for module, reach in sorted(third_party_imports(files[rel]).items()):
            dist = distribution_for(module)
            row = out.setdefault(
                dist, {"module": module, "reach": reach, "files": []}
            )
            if reach == "module":
                row["reach"] = "module"
            row["files"].append(rel)
    return out


def vendored_source_files(workspace: Any) -> Dict[str, str]:
    """``{relative path: text}`` for every Python file under ``vendor/``."""
    from pathlib import Path as _Path

    root = _Path(workspace)
    vendor = root / "vendor"
    files: Dict[str, str] = {}
    if not vendor.is_dir():
        return files
    for py in sorted(vendor.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            files[py.relative_to(root).as_posix()] = py.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
    return files


def dependency_obligations_on_disk(workspace: Any) -> Dict[str, Dict[str, Any]]:
    """What the vendored source ON DISK obliges the platform to declare.

    Live, a voice-agent platform: 23 vendored blocks, a requirements.txt of
    seven packages and none of theirs. The clean Docker image had no numpy,
    ``vector_search`` could not load, a grounding test went red and the image
    build failed -- while the same suite passed 89/89 on a machine that
    happened to have numpy installed.

    The CLONER computes these obligations and keeps them in the runner's
    in-memory state. That state "dies with the process": a build that times
    out and resumes (this one did) reaches WRITER with the key gone, and
    requirements.txt is rendered from nothing. ``vendored_blocks`` and the lock
    were already rehydrated from disk on resume; this never was.

    The vendored bytes are the truth and they are always on disk, so ask
    them. Same scan, same table, same refusal on an unnameable import.
    """
    return dependency_obligations(vendored_source_files(workspace))


def render_dependency_lines(
    obligations: Dict[str, Dict[str, Any]], already: Sequence[str] = ()
) -> str:
    """requirements.txt lines for the vendored blocks, with their reason.

    Unversioned on purpose. A floor the factory invented is a guess, and a
    wrong floor is a build that fails at ``pip install`` rather than a
    dependency that is honestly unpinned; ``blocks.lock.json`` is where the
    pinning conversation belongs.
    """
    have = {str(name).lower() for name in already}
    rows = [
        (dist, row)
        for dist, row in sorted(obligations.items())
        if dist.lower() not in have
    ]
    if not rows:
        return ""
    lines = [
        "",
        "# Vendored block dependencies. Derived by AST from the source the",
        "# CLONER copied in, not hand-kept. 'module' means the block cannot",
        "# import without it; 'action' means it imports, reports healthy, and",
        "# fails only on the feature that needs it -- the quieter defect.",
    ]
    for dist, row in rows:
        reach = "module" if row["reach"] == "module" else "action"
        where = ", ".join(sorted({f.rsplit("/", 1)[-1] for f in row["files"]})[:3])
        lines.append(f"{dist}  # {reach}: {row['module']} in {where}")
    return "\n".join(lines) + "\n"
