"""The acceptance floor: one spec, two consumers.

The Store gate graded every build against thirteen checks that the coder was
never told. Neither the writer prompt nor the C-BRIEF named a single one of
them -- measured, not assumed: ``grep`` for ``no_token_401`` across both
returned zero. So the coder discovered the floor by failing it, one rework
round per check, and every one of those rounds is a full writer pass billed
to the customer.

Putting the rules in the brief alone would just move the trust back to the
author, which is the thing the gate exists to remove. So the floor lives in
``acceptance_floor.json`` and both sides read it:

* the writer prompt renders ``requirement`` verbatim, so the agent builds
  toward the floor on the first pass;
* the Store gate takes its checklist from the same ids.

Neither may hold its own copy. ``test_acceptance_floor_is_one_source`` fails
if the two consumers ever disagree, so drift is a red build rather than a
silent regression to the state this module was written to end.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Tuple

logger = logging.getLogger(__name__)

FLOOR_REL = "acceptance_floor.v2.json"
SCHEMA = "acceptance_floor.v2"

#: The gate follows the prompt. Every check is either UNIVERSAL (each one in
#: the floor file carries ``universal: true`` — every platform owes it, always
#: enforced) or CONDITIONAL (``applies_when: "<signal>"`` — enforced only when
#: the brief declares that subject). A conditional check whose signal the brief
#: never raised is advisory (reported, scored SKIP, never a veto), so a build is
#: never rejected over a capability it was never asked to have — e.g. a RAG
#: round-trip on a platform whose brief never asked for retrieval. The split
#: lives in the floor file, as data, so a check can move between the two sets
#: without touching code.

#: The read a Store block declares when it retrieves: it reads the database at
#: vector scope. A brief retrieves when any capability binds a block whose own
#: block.json declares this read (store_kits.blocks_declaring_read).
RETRIEVAL_READ = ("database", "vector")


def is_production_grade(blueprint: Any) -> bool:
    """True when the floor applies in full: the user chose the production
    level, or declared no level at all -- the production-only checks (the
    security scan) are never lowered by omission. Read from the typed
    build level's bar (app.factory.build.build_level), never from words."""
    from app.factory.build.build_level import bar_for

    if blueprint is None:
        return True
    bar = bar_for(blueprint)
    return bar is None or bar.full_floor


def _all_signals() -> frozenset:
    """Every applies_when value the floor references."""
    return frozenset(
        str(c["applies_when"]) for c in checks() if c.get("applies_when")
    )


def binds_retrieving_block(block_ids: Any, store_root: Any = None) -> bool:
    """True when any of ``block_ids`` is a Store block whose own signed
    block.json declares the retrieval read -- decided by what the Store's
    manifests say, never by the words of a brief or the name of a block."""
    bound = {str(b).strip() for b in (block_ids or ()) if str(b).strip()}
    if not bound:
        return False
    from app.factory.store_kits import blocks_declaring_read

    return bool(bound & blocks_declaring_read(*RETRIEVAL_READ, store_root=store_root))


def _binds_a_retrieving_block(blueprint: Any) -> bool:
    """True when a capability binds a retrieving Store block."""
    return binds_retrieving_block(
        b
        for cap in (getattr(blueprint, "capabilities", None) or [])
        for b in (getattr(cap, "block_ids", None) or [])
    )


def brief_signals(blueprint: Any) -> frozenset:
    """Which conditional subjects THIS brief declared.

    ``None`` means no brief is in hand (a standalone re-render): assume the
    strictest reading — every signal present — so nothing is silently skipped.
    """
    if blueprint is None:
        return _all_signals()
    sigs = set()
    if is_production_grade(blueprint):
        sigs.add("production")
    if _binds_a_retrieving_block(blueprint):
        sigs.add("retrieval")
    if getattr(blueprint, "connectors", None):
        sigs.add("connectors")
    return frozenset(sigs)


def floor_path() -> Path:
    return Path(__file__).resolve().parent.parent / FLOOR_REL


@lru_cache(maxsize=1)
def _load() -> Dict[str, Any]:
    data = json.loads(floor_path().read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(
            f"{FLOOR_REL}: expected schema {SCHEMA}, found {data.get('schema')!r}"
        )
    checks = data.get("checks") or []
    if not checks:
        raise ValueError(f"{FLOOR_REL}: the floor is empty")
    seen = set()
    for check in checks:
        cid = str(check.get("id") or "").strip()
        if not cid:
            raise ValueError(f"{FLOOR_REL}: a check has no id")
        if cid in seen:
            raise ValueError(f"{FLOOR_REL}: duplicate check id {cid!r}")
        seen.add(cid)
        for field in ("requirement_text", "brief_render", "gate_fn", "check"):
            if not str(check.get(field) or "").strip():
                raise ValueError(f"{FLOOR_REL}: {cid} has no {field}")
        is_universal = check.get("universal") is True
        is_conditional = bool(str(check.get("applies_when") or "").strip())
        is_static_advisory = check.get("advisory") is True
        if not (is_universal or is_conditional or is_static_advisory):
            raise ValueError(
                f"{FLOOR_REL}: {cid} must be universal:true, carry an "
                "applies_when signal, or be advisory:true"
            )
        subject = str(check.get("subject") or "").strip()
        if not SUBJECT_RE.match(subject):
            raise ValueError(
                f"{FLOOR_REL}: {cid} must declare subject as runtime, "
                f"factory_record, or tree:<path> (found {subject!r})"
            )
        stage = check.get("stage")
        if stage is not None and stage not in STAGES:
            raise ValueError(
                f"{FLOOR_REL}: {cid} declares stage {stage!r}; "
                f"known stages: {sorted(STAGES)}"
            )
    if not any(c.get("stage") == STAGE_IMAGE for c in checks):
        raise ValueError(f"{FLOOR_REL}: no check declares stage {STAGE_IMAGE!r}")
    return data


#: The pipeline step a check's verdict depends on, when one exists. ``image``:
#: the check is judged on the product's built image, so a ``docker build`` of
#: the product's Dockerfile that fails is THAT check's failure (the Store gate
#: scores it so, and the other checks NOT_RUN) -- never "the gate's workflow
#: failed". Declared here, rendered into the harness as IMAGE_CHECKS, read by
#: the gate: no check id is named in either.
STAGE_IMAGE = "image"
#: ``audit``: the check's verdict is the gate's own security scan of the
#: product (bandit over app/). The gate attaches the scan's findings -- test
#: id, file and line -- to THESE lines as evidence, so a red audit tells the
#: writer what to change. Rendered into the harness as AUDIT_CHECKS.
STAGE_AUDIT = "audit"
STAGES = frozenset({STAGE_IMAGE, STAGE_AUDIT})


def image_check_ids() -> Tuple[str, ...]:
    """The checks a failed image build fails (the floor's ``stage: image``)."""
    return tuple(str(c["id"]) for c in checks() if c.get("stage") == STAGE_IMAGE)


def audit_check_ids() -> Tuple[str, ...]:
    """The checks whose evidence is the gate's security scan (``stage: audit``)."""
    return tuple(str(c["id"]) for c in checks() if c.get("stage") == STAGE_AUDIT)


#: What a check judges. The owner of a failure is derived from this and from
#: who wrote the subject -- never from a list of check names. A check that
#: judges the booted product's answers is the product's; one that judges the
#: Factory's own bookkeeping is the Factory's; one that judges a file in the
#: tree belongs to whoever rendered that file (factory_rendered_paths), so a
#: broken file the Factory stamped is the Factory's failure wherever it lands.
SUBJECT_RE = __import__("re").compile(r"^(runtime|factory_record|tree:[A-Za-z0-9_./-]+)$")
SUBJECT_RUNTIME = "runtime"
SUBJECT_FACTORY_RECORD = "factory_record"

#: Owner vocabulary, shared with the TESTER gate's classifier so one word
#: means one thing across both gates.
FACTORY = "FACTORY"
PRODUCT = "PRODUCT"

_PATH_IN_DETAIL = __import__("re").compile(
    r"(?<![A-Za-z0-9_])((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:py|ya?ml|txt|json|sh|html|toml|ini|cfg))"
)


def repository_check_ids() -> Tuple[str, ...]:
    """The checks that judge the REPOSITORY, not the running image: a declared
    subject of ``tree:<path>`` (a file in the checkout) or ``factory_record``
    (the Factory's bookkeeping in the checkout). The Store gate runs the
    harness inside the product's image, which carries only what the product's
    .dockerignore lets in; these checks read the checkout instead. Derived from
    each check's declared subject -- no check id is listed."""
    out = []
    for c in checks():
        subject = str(c.get("subject") or "")
        kind, _, target = subject.partition(":")
        if subject == SUBJECT_FACTORY_RECORD or (kind == "tree" and target):
            out.append(str(c["id"]))
    return tuple(out)


def subject_of(check_id: str) -> str:
    for c in checks():
        if str(c["id"]) == check_id:
            return str(c.get("subject") or "")
    return ""


def _path_parts(path: str) -> Tuple[bool, Tuple[str, ...]]:
    """(is_absolute, the path's segments) -- ``.`` and empty segments dropped,
    read from the path's own structure rather than a list of known roots."""
    text = str(path or "").replace("\\", "/").strip()
    parts = tuple(p for p in PurePosixPath(text).parts if p not in ("/", "."))
    return text.startswith("/"), parts


def _factory_rendered(path: str, rendered: frozenset) -> bool:
    """True when ``path`` is (or names, by a slash-qualified suffix) a file the
    Factory renders. A bare basename never matches -- ``acceptance.py`` in a
    score line is not a claim about scripts/acceptance.py.

    An ABSOLUTE path is a file inside a workspace mounted somewhere; whatever
    the mount root, it names a rendered file when its trailing segments are
    that file's whole relative path."""
    absolute, parts = _path_parts(path)
    if not parts:
        return False
    rel = "/".join(parts)
    if rel in rendered:
        return True
    if absolute:
        return any(parts[-len(r.split("/")):] == tuple(r.split("/")) for r in rendered)
    return len(parts) > 1 and any(r.endswith("/" + rel) for r in rendered)


def owner_of(check_id: str, detail: str = "") -> str:
    """Who owns a failed line: PRODUCT or FACTORY, derived -- never looked up.

    1. A detail that names a file the Factory rendered is the Factory's,
       whatever the check (``no_token_literal`` tripping on the stamped
       app/tenancy.py is the stamp's defect, not the coder's).
    2. Otherwise the check's declared subject decides: ``runtime`` is the
       product's (it answered); ``factory_record`` is the Factory's; ``tree:``
       is the Factory's only when the path is a file the Factory renders --
       a directory the coder fills (app, tests, alembic) is the product's.
    """
    from app.factory.build.store_acceptance import factory_rendered_paths

    rendered = frozenset(factory_rendered_paths())
    for m in _PATH_IN_DETAIL.finditer(str(detail or "")):
        if _factory_rendered(m.group(1), rendered):
            return FACTORY
    subject = subject_of(check_id)
    if subject == SUBJECT_FACTORY_RECORD:
        return FACTORY
    kind, _, target = subject.partition(":")
    if kind == "tree" and target:
        return FACTORY if _factory_rendered(target, rendered) else PRODUCT
    return PRODUCT


def floor_version() -> int:
    """The version both consumers must be reading."""
    return int(_load().get("version") or 0)


def checks() -> Tuple[Mapping[str, Any], ...]:
    """Every check, in the gate's reporting order."""
    return tuple(_load()["checks"])


def check_ids() -> Tuple[str, ...]:
    """The gate's checklist. This is what ACCEPTANCE_CHECK_NAMES is."""
    return tuple(str(c["id"]) for c in checks())


def advisory_ids(blueprint: Any = None) -> Tuple[str, ...]:
    """Checks the gate REPORTS but does not fail a build on.

    A check is advisory when the floor demands evidence that no step of the
    pipeline yet produces. On 2026-09-26 three of the 21 were in that state:
    ``one_live_connector`` (nothing anywhere sets STORE_LIVE_CONNECTOR),
    ``backup_restore_roundtrip`` (the restore drill runs in the product's own
    tests, which the gate never executes) and ``bench_p95`` (the bench job
    prints STORE_BENCH_P95_MS inside product CI, which the gate never reads).
    Every build failed on all three by construction — a bar that cannot be
    cleared is not a bar, it is a wall, and it hid the checks that COULD fail.

    Advisory is a fact about the pipeline, not about the requirement: the
    requirement text still reaches the writer's prompt unchanged, the check
    still runs and still prints FAIL with its reason, and the line is still
    counted in the k/N score as SKIP. It just does not veto the build. The
    flag lives in the floor file, next to the check it describes, so both
    consumers read one source — the same reason the checklist itself does.
    The owner chose demotion over building the bridges (Gate 3b, 2026-09-26).

    ``blueprint`` widens this set to follow the prompt: a CONDITIONAL check whose
    ``applies_when`` signal the brief never raised is advisory for THIS build, on
    top of the statically-advisory ones. ``None`` (no brief in hand) raises every
    signal, so the set is exactly the static one — the safe, strictest default.
    """
    sigs = brief_signals(blueprint)
    out = []
    for c in checks():
        cid = str(c["id"])
        if c.get("advisory") is True:
            out.append(cid)
            continue
        applies_when = c.get("applies_when")
        if applies_when and str(applies_when) not in sigs:
            out.append(cid)  # conditional check the brief did not ask for
    return tuple(out)


def enforced_ids(blueprint: Any = None) -> Tuple[str, ...]:
    """The checks that VETO this build — the checklist minus what is advisory
    for its brief (universal checks, plus conditionals the brief asked for)."""
    adv = set(advisory_ids(blueprint))
    return tuple(cid for cid in check_ids() if cid not in adv)


def requirements() -> List[str]:
    """What the coder is told, in the same order the gate reports."""
    return [str(c["requirement_text"]).strip() for c in checks()]


def render_for_prompt(blueprint: Any = None) -> str:
    """The floor as the REQUIREMENTS block of the writer's prompt.

    Rendered verbatim from the same file the gate grades against, so the agent
    builds toward the checklist rather than guessing at it. The set follows THIS
    brief: a check that is advisory for this build (a conditional the brief did
    not ask for) is marked so, so the writer spends effort on what actually
    vetoes its build and is never failed on a requirement it was never asked for.
    """
    adv = set(advisory_ids(blueprint))
    lines = [
        f"ACCEPTANCE FLOOR (v{floor_version()} — the Store gate grades this "
        "build against exactly these, in this order; a line marked [not "
        "required by this brief] is reported but does not fail the build):",
    ]
    for check in checks():
        line = str(check["brief_render"]).strip()
        if str(check["id"]) in adv:
            line += "  [not required by this brief]"
        lines.append(line)
    return "\n".join(lines)


def floor_hash() -> str:
    """The bytes both consumers must be reading.

    P3's drift lock: the brief render test and the gate render test both pin
    this, so a change to the floor that reaches only one of them is a red
    build rather than a silent divergence.
    """
    import hashlib

    return "sha256:" + hashlib.sha256(
        floor_path().read_bytes()
    ).hexdigest()


def gate_fns() -> Tuple[str, ...]:
    """The check function each entry expects the harness to define."""
    return tuple(str(c["gate_fn"]) for c in checks())
