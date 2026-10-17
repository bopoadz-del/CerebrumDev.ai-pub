"""The universal reasoning socket the Factory emits into every platform it builds.

One kernel, byte-identical in every product. The KIT is data the kernel loads, so
a hotel build and an airport build get the same socket and differ only in the
vocabulary vendored beside it. Nothing in the kernel knows what a runway or a
slab is -- that is the whole point, and it is why there is one file here rather
than one per vertical.

Emitted into a product:

    app/reasoning/kernel.py        the socket. Identical everywhere.
    app/reasoning/host.py          the four functions the PRODUCT fills in
    app/reasoning/pending.py       the unanswered interview questions
    app/reasoning/routes.py        where an answer arrives after the build
    app/reasoning/kit/manifest.yaml     vendored from the Store
    app/reasoning/kit/invariants.yaml   vendored from the Store
    app/reasoning/kit/questions.yaml    the domain owner's own question sheet,
                                        where the Store kit carries one
    app/reasoning/kit/design_basis.yaml the kit's OWN figure register, where it
                                        has one. Six Store kits do, and ONE of
                                        those is already answered -- so a platform
                                        built on it arrives holding real figures
                                        rather than an empty interview.

Five hook points, from the portable spec's routing map:

    H0 pre-retrieval   refuse without searching, and assert nothing was searched
    H1 ranking         lift the governing source class
    H2 tool-time       catch impossible values where they are born
    H3 answer-time     grounding, qualifier, provenance, currency, derivation
    H4 export-time     re-run H3 on the file before it leaves

FAIL CLOSED, twice over:

  * a kit that does not load disables itself and REFUSES every statement. It
    never degrades to "no invariants", because a kit with a typo becoming a kit
    with no gates is the failure this whole layer exists to prevent.
  * a host function the product has not implemented REFUSES. The emitted
    ``host.py`` raises ``HostNotWired`` rather than returning something empty,
    so a platform that forgot to wire retrieval cannot quietly answer ungated.

UNANSWERED FIGURES ARE NOT STUBS. A kit ships with every figure value null and
each null carrying the interview question that fills it. When the build interview
does not get an answer, the figure stays null and the platform REFUSES any
question that needs it, naming the interview question the operator must go and
answer. A stub would be a fake value that can ship silently and be mistaken for
real; a refusal cannot. An answer is stored with who gave it, when, and from
which document -- otherwise the interview has only moved the invented figure from
the model to the operator.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: Where the socket lives inside a built product.
KERNEL_PATH = "app/reasoning/kernel.py"
HOST_PATH = "app/reasoning/host.py"
PENDING_PATH = "app/reasoning/pending.py"
ROUTES_PATH = "app/reasoning/routes.py"
INIT_PATH = "app/reasoning/__init__.py"
KIT_MANIFEST = "app/reasoning/kit/manifest.yaml"
KIT_INVARIANTS = "app/reasoning/kit/invariants.yaml"
#: The domain owner's own question sheet. Optional: two Store kits have no sheet
#: yet, and a platform built on one of those runs on the kit's derived questions
#: and SAYS SO rather than reporting an un-interviewed domain as ready.
KIT_QUESTIONS = "app/reasoning/kit/questions.yaml"
#: The kit's OWN figure register, where it has one: domain figure names, the
#: qualifiers each is meaningless without, and its own declared scope. Six Store
#: kits have one and one of those is filled in, so a platform built on it starts
#: with real answers rather than an empty interview.
KIT_DESIGN_BASIS = "app/reasoning/kit/design_basis.yaml"


def render_init() -> str:
    return '''"""The reasoning layer: a universal kernel plus one vendored kit.

The kernel is identical in every platform the Factory builds. The kit beside it
is the vocabulary for this platform's vertical. See kernel.py.
"""

from app.reasoning.kernel import (  # noqa: F401
    Figure,
    Finding,
    Outcome,
    ReasoningKernel,
    kernel,
)
'''


def render_kernel() -> str:
    """The socket. Identical in every product -- do not specialise it per build."""
    return '''"""The reasoning kernel. Identical in every platform; the kit is the difference.

A platform answers with FIGURES. A figure is usable only if it is grounded,
complete, dimensioned, authorised, its own, current, in scope and
self-consistent. Each of those is one invariant kind, declared in the kit beside
this file and evaluated here.

FAIL CLOSED. A kit that does not load refuses every statement. A host function
the product has not wired refuses. Neither ever returns "fine".
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

logger = logging.getLogger(__name__)

KIT_DIR = pathlib.Path(__file__).resolve().parent / "kit"

H0_PRE_RETRIEVAL = "H0"
H1_RANKING = "H1"
H2_TOOL_TIME = "H2"
H3_ANSWER_TIME = "H3"
H4_EXPORT_TIME = "H4"
ALL_HOOKS = (H0_PRE_RETRIEVAL, H1_RANKING, H2_TOOL_TIME, H3_ANSWER_TIME, H4_EXPORT_TIME)

HOOKS_BY_KIND = {
    "scope": (H0_PRE_RETRIEVAL,),
    "authority": (H1_RANKING,),
    "unit_discipline": (H2_TOOL_TIME, H3_ANSWER_TIME),
    "band": (H2_TOOL_TIME,),
    "grounding": (H3_ANSWER_TIME,),
    "qualifier": (H3_ANSWER_TIME,),
    "provenance": (H3_ANSWER_TIME,),
    "currency": (H3_ANSWER_TIME,),
    "derivation": (H3_ANSWER_TIME,),
}

#: Hard cap on figure x invariant evaluations per hook. An unbounded set in
#: exactly this position consumed 2 GB and killed a live instance twice in one
#: day. A skipped check is NOT a pass and Outcome.incomplete says so.
BUDGET = 2000


class KitDisabled(RuntimeError):
    """The kit did not load. Every statement is refused."""


class HostNotWired(RuntimeError):
    """A host function this platform must implement is missing."""


@dataclass
class Figure:
    quantity: str
    value: Any = None
    unit: Optional[str] = None
    origin: str = "model"          # tool | document | model | operator
    source_id: Optional[str] = None
    source_class: Optional[str] = None
    revision: Optional[str] = None
    qualifiers: Dict[str, Any] = field(default_factory=dict)
    claim_class: Optional[str] = None
    asked_about: Dict[str, Any] = field(default_factory=dict)
    derivations: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    bounds: Dict[str, Any] = field(default_factory=dict)
    span: Optional[Dict[str, int]] = None
    text: str = ""

    def known(self, name: str) -> bool:
        """A key present with None counts as NOT known: having a field for it is
        not knowing it."""
        return self.qualifiers.get(name) is not None

    def missing(self, names: Sequence[str]) -> List[str]:
        return [n for n in names if not self.known(n)]

    @property
    def is_operators_own(self) -> bool:
        return self.origin == "operator"


@dataclass
class Finding:
    invariant: str
    kind: str
    severity: str
    message: str
    quantity: str = ""
    missing: List[str] = field(default_factory=list)
    span: Optional[Dict[str, int]] = None
    softened_from: Optional[str] = None

    @property
    def stops(self) -> bool:
        return self.severity == "refuse"

    def as_dict(self) -> Dict[str, Any]:
        out = {"invariant": self.invariant, "kind": self.kind,
               "severity": self.severity, "message": self.message}
        if self.quantity:
            out["quantity"] = self.quantity
        if self.missing:
            out["missing"] = list(self.missing)
        if self.span:
            out["span"] = dict(self.span)
        if self.softened_from:
            out["softened_from"] = self.softened_from
        return out


@dataclass
class Outcome:
    verdict: str
    hook: str
    findings: List[Finding] = field(default_factory=list)
    incomplete: bool = False
    skipped: int = 0
    retrieval_permitted: bool = True

    @property
    def blocked_reason(self) -> str:
        return "; ".join(f.message for f in self.findings if f.stops)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "hook": self.hook,
            "findings": [f.as_dict() for f in self.findings],
            "blocked_reason": self.blocked_reason,
            "retrieval_permitted": self.retrieval_permitted,
            "incomplete": self.incomplete,
            "skipped_checks": self.skipped,
        }


def _verdict(findings: Sequence[Finding]) -> str:
    if any(f.stops for f in findings):
        return "refused"
    if any(f.severity == "flag" for f in findings):
        return "flagged"
    return "annotated" if findings else "pass"


class ReasoningKernel:
    """Loads the kit beside this file and runs the routing map over figures."""

    def __init__(self, kit_dir: Optional[pathlib.Path] = None) -> None:
        self.kit_dir = pathlib.Path(kit_dir or KIT_DIR)
        self.disabled_reason: Optional[str] = None
        self.manifest: Dict[str, Any] = {}
        self.invariants: List[Dict[str, Any]] = []
        #: The domain owner's question sheet, or {} when the kit ships without
        #: one. Empty is NOT "nothing left to ask" -- see interview().
        self.sheet: Dict[str, Any] = {}
        #: The kit's OWN figure register (design_basis / operating_basis), or {}.
        #: This is the only source that can arrive ALREADY ANSWERED.
        self.register: Dict[str, Any] = {}
        self.register_meta: Dict[str, Any] = {}
        self._load()

    # -- loading -----------------------------------------------------------

    def _load(self) -> None:
        name = self.kit_dir.name
        switch = os.getenv(f"REASONING_KIT_OFF", "").strip().lower()
        if switch in ("1", "true", "yes", "on"):
            self.disabled_reason = "the reasoning kit is switched off by REASONING_KIT_OFF"
            logger.error("REASONING KIT DISABLED: %s", self.disabled_reason)
            return
        try:
            self.manifest = yaml.safe_load(
                (self.kit_dir / "manifest.yaml").read_text(encoding="utf-8")) or {}
            records = yaml.safe_load(
                (self.kit_dir / "invariants.yaml").read_text(encoding="utf-8")) or {}
            self.invariants = list(records.get("invariants") or [])
        except Exception as exc:  # noqa: BLE001 -- any failure disables the kit
            self.disabled_reason = f"kit did not load: {type(exc).__name__}: {exc}"
            logger.error("REASONING KIT DISABLED: %s", self.disabled_reason)
            return
        # The question sheet. Absent is fine and is reported as such; PRESENT BUT
        # BROKEN disables the kit, because a sheet that will not parse would
        # otherwise leave zero outstanding questions -- which reads as a finished
        # interview, the most misleading thing this file could report.
        sheet_path = self.kit_dir / "questions.yaml"
        if sheet_path.is_file():
            try:
                self.sheet = yaml.safe_load(sheet_path.read_text(encoding="utf-8")) or {}
                if not (self.sheet.get("questions") or []):
                    raise ValueError("the sheet declares no questions")
                if not (self.sheet.get("answer_format") or []):
                    raise ValueError(
                        "the sheet declares no answer_format, so nothing knows which "
                        "fields an answer must carry and every answer would pass bare")
            except Exception as exc:  # noqa: BLE001
                self.disabled_reason = (
                    f"the kit's question sheet did not load: {type(exc).__name__}: {exc}. "
                    f"An unreadable sheet leaves nothing outstanding, which reads as a "
                    f"completed interview")
                logger.error("REASONING KIT DISABLED: %s", self.disabled_reason)
                return

        # The kit's own figure register. Absent is fine; PRESENT BUT BROKEN
        # disables, for the same reason as the sheet -- and more sharply here,
        # because a register can arrive already answered and losing it would turn
        # a platform that holds real figures into one that refuses everything.
        register_path = self.kit_dir / "design_basis.yaml"
        if register_path.is_file():
            try:
                raw = yaml.safe_load(register_path.read_text(encoding="utf-8")) or {}
                # Named for what it IS: a data centre has a design basis, an
                # operating plant an operating basis. Enumerated, not guessed.
                block = next((n for n in ("design_basis", "operating_basis") if n in raw), None)
                if block is None:
                    raise ValueError(
                        "no figure register block (expected design_basis or operating_basis)")
                figures = raw.get(block)
                if not isinstance(figures, dict) or not figures:
                    raise ValueError(f"{block} is empty or not a mapping")
                self.register = {str(k): (v or {}) for k, v in figures.items()}
                self.register_meta = {
                    "block": block,
                    "source": str(raw.get("source") or ""),
                    "scope": str(raw.get("scope") or ""),
                    "facility": str(raw.get("facility") or ""),
                }
            except Exception as exc:  # noqa: BLE001
                self.disabled_reason = (
                    f"the kit's figure register did not load: {type(exc).__name__}: "
                    f"{exc}. A register can arrive already answered, and losing it turns "
                    f"a platform that holds real figures into one that refuses them")
                logger.error("REASONING KIT DISABLED: %s", self.disabled_reason)
                return
        if not self.invariants:
            self.disabled_reason = (
                "the kit declares no invariants; it gates nothing and is disabled "
                "rather than trusted")
            logger.error("REASONING KIT DISABLED: %s", self.disabled_reason)

    @property
    def enabled(self) -> bool:
        return self.disabled_reason is None

    def _disabled_outcome(self, hook: str) -> Outcome:
        finding = Finding(
            "KIT_DISABLED", "scope", "refuse",
            f"the reasoning kit is DISABLED and cannot gate anything: "
            f"{self.disabled_reason}. A kit that does not load refuses; it does not "
            f"pass statements through with no invariants")
        return Outcome("refused", hook, [finding], retrieval_permitted=False)

    # -- the answer store --------------------------------------------------
    #
    # Answers are THIS PLATFORM'S data and live in this platform's storage. They
    # are never written into the kit, because the kit is a signed Store block
    # shared by every customer: one client's declared distances reaching the next
    # platform built from the same kit would be the provenance failure this whole
    # layer exists to prevent, arriving signed.

    #: Env vars that say where this platform's durable storage is, in order.
    STORAGE_VARS = ("STORAGE_PATH", "DATA_DIR")

    def _answers_root(self) -> Optional[pathlib.Path]:
        for var in self.STORAGE_VARS:
            value = (os.getenv(var) or "").strip()
            if value:
                return pathlib.Path(value)
        return None

    def _answers_path(self) -> Optional[pathlib.Path]:
        """Where answers live, or None when this platform has no durable storage
        configured.

        It used to fall back to ``.``, the process working directory. That reads
        as working -- answers save, come back, and the route reports them
        recorded -- and then a restart under a different working directory, or a
        second worker started elsewhere, silently has none of them. An answer that
        is reported recorded and is not durable is the same defect as an answer
        that is reported recorded and was never written; the fallback only made it
        harder to see. Reads now return nothing (so the platform REFUSES, which is
        correct) and writes raise, naming the variable to set.
        """
        root = self._answers_root()
        return None if root is None else root / "reasoning_answers.json"

    def _read_store(self) -> Dict[str, Dict[str, Any]]:
        """The whole answer store, read fresh, in two namespaces.

        ``figures`` are keyed by the kit's quantity names; ``questions`` by the
        owner sheet's own ids (B.1, 9.5.2). Two namespaces rather than one flat
        map because a sheet id and a quantity name are different kinds of key and
        a collision between them would silently answer the wrong thing.

        Read from disk every time rather than cached: an answer posted by one
        worker must be visible to the next request on another, and a cache here
        would make a recorded answer look unrecorded.
        """
        path = self._answers_path()
        if path is None or not path.is_file():
            return {"figures": {}, "questions": {}}
        try:
            import json

            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            logger.error("reasoning answers unreadable at %s; treating as none", path)
            return {"figures": {}, "questions": {}}
        if "figures" not in data and "questions" not in data:
            # Written before the sheet existed: the whole file was figure answers.
            # Read it rather than discarding answers a platform already holds.
            return {
                "figures": {str(k): v for k, v in data.items() if isinstance(v, dict)},
                "questions": {},
            }
        return {
            "figures": {str(k): v for k, v in (data.get("figures") or {}).items()
                        if isinstance(v, dict)},
            "questions": {str(k): v for k, v in (data.get("questions") or {}).items()
                          if isinstance(v, dict)},
        }

    def answers(self) -> Dict[str, Dict[str, Any]]:
        """Figure answers this platform has been given."""
        return self._read_store()["figures"]

    def question_answers(self) -> Dict[str, Dict[str, Any]]:
        """Answers to the owner sheet's questions, keyed by sheet id."""
        return self._read_store()["questions"]

    def record_answer(self, figure: str, value: Any, *, answered_by: str,
                      answered_at: str, source: str) -> Dict[str, Any]:
        """Persist one answer, then READ IT BACK before reporting success.

        The read-back is the point. An earlier version validated the provenance,
        returned the dict and stored nothing, so the route answered ok:true for a
        no-op -- a success report over an operation that did nothing, which is the
        defect class this layer exists to catch.
        """
        if not (answered_by and answered_at and source):
            raise ValueError(
                "an interview answer needs answered_by, answered_at and source: an "
                "unattributed figure is not evidence, whoever supplied it")
        if figure not in (self.manifest.get("figures") or {}):
            raise KeyError(f"{figure} is not a figure this kit asks about")

        store = self._read_store()
        store["figures"][figure] = {
            "value": value,
            "answered_by": answered_by,
            "answered_at": answered_at,
            "source": source,
        }
        self._write_store(store)

        stored = self.answers().get(figure)
        if not stored or stored.get("value") != value:
            raise RuntimeError(
                f"{figure} did not persist: the answer is not readable back, so it "
                f"has not been recorded and must not be reported as recorded")
        return dict(stored, figure=figure)

    def _write_store(self, store: Dict[str, Dict[str, Any]]) -> None:
        """Atomic: a crash mid-write must not leave a half-written answer file
        that reads as "no answers at all"."""
        import json
        import os as _os
        import tempfile as _tempfile

        path = self._answers_path()
        if path is None:
            raise RuntimeError(
                "this platform has no durable storage configured, so an answer cannot "
                "be recorded: set " + " or ".join(self.STORAGE_VARS) + ". Writing to the "
                "working directory would report the answer as recorded and lose it on "
                "the next restart")
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = _tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with _os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(store, stream, indent=2, sort_keys=True)
            _os.replace(temporary, path)
        except BaseException:
            _os.unlink(temporary)
            raise

    # -- the owner's question sheet ----------------------------------------

    def required_answer_fields(self) -> List[str]:
        """The fields an answer must arrive with IN THIS DOMAIN, from the sheet's
        own 'Answer format:' line.

        Fit-out wants quality band and market; fire protection wants the code
        edition; dental wants adult-or-paediatric and the protocol version. The
        kernel holds no list of its own -- one definition of a complete answer per
        domain, and it is the domain owner's.
        """
        return [
            str(entry).strip().lower().replace(" ", "_")
            for entry in (self.sheet.get("answer_format") or ())
            if str(entry).strip().lower() != "value"
        ]

    def sheet_questions(self) -> List[Dict[str, Any]]:
        return [q for q in (self.sheet.get("questions") or []) if isinstance(q, dict)]

    @staticmethod
    def _gates(question: Dict[str, Any]) -> bool:
        """[GATE] gates. UNMARKED also gates: a question whose class cannot be
        read must block rather than pass. Only an explicit [GAP] does not."""
        return question.get("gate") is not False

    def interview(self) -> Dict[str, Any]:
        """What the domain owner's sheet still wants answered on this platform.

        ``questions_source`` is why this method is not just a count. A kit with no
        sheet and a kit whose sheet is fully answered both have nothing
        outstanding, and reporting them alike would call a domain nobody has
        interviewed ready to gate.
        """
        register_state = None
        if self.register:
            answered_here = self.answers()
            filled = [n for n, spec in self.register.items()
                      if (spec or {}).get("value") is not None or n in answered_here]
            register_state = {
                "block": self.register_meta.get("block"),
                "source": self.register_meta.get("source"),
                "scope": self.register_meta.get("scope"),
                "facility": self.register_meta.get("facility"),
                "figures": len(self.register),
                "answered": len(filled),
                "open": len(self.register) - len(filled),
                "open_figures": sorted(set(self.register) - set(filled)),
                # Whether anyone has actually put values in. One Store register is
                # filled; reporting it as un-interviewed calls an answered domain
                # an empty one.
                "interview_ran": bool(filled),
            }

        if not self.sheet:
            if register_state is not None:
                return {
                    "questions_source": "design_basis",
                    "sheet_supplied": False,
                    "design_basis_supplied": True,
                    "design_basis": register_state,
                    "interview_ran": register_state["interview_ran"],
                    "outstanding": register_state["open"],
                    "ready": register_state["interview_ran"] and not register_state["open"],
                    "note": (
                        f"Answered from this kit's own figure register: "
                        f"{register_state['answered']} of {register_state['figures']} "
                        f"figures. {register_state['scope']}"
                        if register_state["interview_ran"] else
                        "This kit's figure register is declared and EMPTY -- no interview "
                        "has run, every value is null. Nothing needing one of these "
                        "figures can be answered."),
                }
            return {
                "questions_source": "derived",
                "sheet_supplied": False,
                "design_basis_supplied": False,
                "ready": False,
                "note": (
                    "This kit ships without the domain owner's question sheet. Its "
                    "questions are DERIVED from its quantity names, one per quantity. "
                    "Treat readiness as unknown, not met."),
                "outstanding": len(self.pending_questions()),
            }
        answered = self.question_answers()
        questions = self.sheet_questions()
        gating = [q for q in questions if self._gates(q)]
        outstanding = [q for q in gating if str(q.get("id")) not in answered]
        gaps = [q for q in questions
                if not self._gates(q) and str(q.get("id")) not in answered]
        sections = self.sheet.get("sections") or {}
        return {
            "questions_source": ("design_basis+owner_sheet" if register_state
                                 else "owner_sheet"),
            "sheet_supplied": True,
            "design_basis_supplied": register_state is not None,
            **({"design_basis": register_state} if register_state else {}),
            "title": self.sheet.get("title") or "",
            "source_document": self.sheet.get("source_document") or "",
            "questions": len(questions),
            "gating": len(gating),
            "answered": len([q for q in questions if str(q.get("id")) in answered]),
            "outstanding": len(outstanding),
            "gaps_outstanding": len(gaps),
            "ready": not outstanding,
            "required_fields": self.required_answer_fields(),
            # Sheet order, not sorted: the owner grouped these from rates through
            # to incidents, and out of that order the interview reads as a quiz.
            "next": [
                {
                    "id": str(q.get("id")),
                    "section": str(q.get("section") or ""),
                    "section_title": str(
                        (sections.get(str(q.get("section") or "")) or {}).get("title") or ""),
                    "marked": ("GATE" if q.get("gate") is True
                               else ("GAP" if q.get("gate") is False else "unmarked")),
                    "text": str(q.get("text") or ""),
                    "covers": list(q.get("covers") or []),
                }
                for q in outstanding
            ],
        }

    def record_question_answer(self, question_id: str, answer: Any,
                               fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Persist one answer to one sheet question, then READ IT BACK.

        Refuses an id the sheet does not ask, and refuses an answer missing any
        field this domain's answer format requires -- an answer without its market
        and quality band is not an answer to "cost per m² by quality band", it is a
        number that will be cited as one.
        """
        if not self.sheet:
            raise KeyError(
                "this kit ships without a question sheet; there are no sheet questions "
                "to answer")
        known = {str(q.get("id")) for q in self.sheet_questions()}
        if question_id not in known:
            raise KeyError(f"{question_id} is not a question this kit's sheet asks")
        fields = dict(fields or {})
        missing = [f for f in self.required_answer_fields() if not fields.get(f)]
        if missing:
            raise ValueError(
                f"{question_id} needs {', '.join(missing)} — this domain's answer format "
                f"requires them, and without them the answer cannot be cited")

        store = self._read_store()
        store["questions"][question_id] = {"answer": answer, "fields": fields}
        self._write_store(store)

        stored = self.question_answers().get(question_id)
        if not stored or stored.get("answer") != answer:
            raise RuntimeError(
                f"{question_id} did not persist: the answer is not readable back, so it "
                f"has not been recorded and must not be reported as recorded")
        return dict(stored, id=question_id)

    # -- the unanswered interview -----------------------------------------

    def pending_questions(self) -> Dict[str, str]:
        """Figures the build interview has not answered, and the question for each.

        These are not stubs. A figure with no answer has no value, and any
        statement needing it is REFUSED naming the question -- so the gap is
        visible to the operator instead of shipping as a plausible number.
        """
        out: Dict[str, str] = {}
        answered = self.answers()
        for name, entry in self.register.items():
            if str(name) in answered or (entry or {}).get("value") is not None:
                continue
            # The owner's own wording wins here too. Without this the register's
            # generic "declared with no value" shadowed "[B.2] your rate per package:
            # partitions, ceilings, raised floor..." -- the register knows the figure
            # exists, the sheet knows what to ask for it.
            asked = self._sheet_wording(str(name))
            scope = self.register_meta.get("scope") or ""
            out[str(name)] = asked or (
                f"declared in this kit's figure register with no value. {scope}".strip())
        for name, entry in (self.manifest.get("figures") or {}).items():
            if str(name) in answered:
                continue
            if not isinstance(entry, dict) or entry.get("value") is None:
                question = self._sheet_wording(str(name))
                if not question and isinstance(entry, dict):
                    question = str(entry.get("question") or entry.get("interview_id") or "")
                out[str(name)] = question or "unanswered: no interview question recorded"
        return out

    def _sheet_wording(self, figure: str) -> str:
        """The owner's own wording for a figure, where the sheet names it.

        Precedence, not duplication: the derived question stays in the manifest as
        the fallback for a quantity no sheet question names, and for the two kits
        that ship without a sheet at all. Where the owner asked it themselves,
        theirs is the question -- "your rate per package: partitions, ceilings,
        raised floor..." rather than the derived "what is the rate?", which was one
        number for a whole domain.
        """
        # The sheet's `covers` names QUANTITIES; the figures block is keyed by
        # figure. In today's Store kits those are the same string, because the
        # figures were generated one per quantity -- but a figure is properly an
        # INSTANCE of a quantity, so a figure may name its own. Matching on the key
        # alone would silently fall back to the derived question for every
        # instance-named figure, which is what a per-asset figure always is.
        entry = (self.manifest.get("figures") or {}).get(figure)
        quantity = str((entry or {}).get("quantity") or figure) if isinstance(entry, dict) \
            else figure
        asks = [
            q for q in self.sheet_questions()
            if quantity in [str(c) for c in (q.get("covers") or ())] and self._gates(q)
        ]
        if not asks:
            return ""
        lead = asks[0]
        text = f"[{lead.get('id')}] {lead.get('text')}"
        if len(asks) > 1:
            also = ", ".join(str(q.get("id")) for q in asks[1:])
            text += f" (also asked by {also})"
        return text

    def figure_value(self, name: str) -> Tuple[Any, Optional[str]]:
        """``(value, refusal)``. A null figure yields a refusal, never a default.

        An answer this platform has been given wins over the kit's null, which is
        how one answer takes effect the moment it lands instead of waiting for
        some whole-kit swap.
        """
        answered = self.answers().get(name)
        if answered and answered.get("value") is not None:
            return answered["value"], None
        # The kit's own register, where it has one. An operator answer on THIS
        # platform still wins: the register is the facility it was written for, and
        # the operator is telling us about this one.
        if name in self.register:
            value = (self.register.get(name) or {}).get("value")
            if value is not None:
                return value, None
            return None, (
                f"{name} is declared in this kit's figure register with no value: "
                f"{self.register_meta.get('scope') or 'scope not stated'}. Nothing can "
                f"be stated about it until it is answered")
        entry = (self.manifest.get("figures") or {}).get(name)
        if not isinstance(entry, dict) or entry.get("value") is None:
            question = self.pending_questions().get(name, "")
            return None, (
                f"{name} has no value on this platform: {question}. The build "
                f"interview did not answer it, so nothing can be stated about it")
        return entry["value"], None

    # -- the hooks ---------------------------------------------------------

    def pre_retrieval(self, question: str) -> Outcome:
        """H0. Runs in the router BEFORE anything is retrieved.

        No document makes an operational-authority question answerable, so
        retrieving at all is wrong: it produces citations that read as though
        they authorised the decision.
        """
        if not self.enabled:
            return self._disabled_outcome(H0_PRE_RETRIEVAL)
        for refusal in (self.manifest.get("scope_refusals") or []):
            pattern = str(refusal.get("pattern") or "")
            if pattern and re.search(pattern, question or "", re.IGNORECASE):
                label = str(refusal.get("label") or "operational authority")
                who = str(refusal.get("authority") or "the named duty holder")
                finding = Finding(
                    f"SCOPE:{label}", "scope", "refuse",
                    f"refused before retrieval ({label}): this is {who}'s decision. "
                    f"No document makes it answerable, so nothing was retrieved")
                return Outcome("refused", H0_PRE_RETRIEVAL, [finding],
                               retrieval_permitted=False)
        return Outcome("pass", H0_PRE_RETRIEVAL, [], retrieval_permitted=True)

    def ranking(self, figures: Sequence[Figure]) -> Outcome:
        return self._sweep(H1_RANKING, figures)

    def tool_time(self, figures: Sequence[Figure]) -> Outcome:
        return self._sweep(H2_TOOL_TIME, figures)

    def answer_time(self, figures, state=None, events=()) -> Outcome:
        return self._sweep(H3_ANSWER_TIME, figures, state, events)

    def export_time(self, figures, state=None, events=()) -> Outcome:
        outcome = self._sweep(H3_ANSWER_TIME, figures, state, events)
        outcome.hook = H4_EXPORT_TIME
        return outcome

    # -- the sweep ---------------------------------------------------------

    def _governs(self, record: Dict[str, Any], figure: Figure) -> bool:
        raw = (record.get("applies_to") or {}).get("quantity")
        if raw is None or raw == "any":
            matched = True
        else:
            names = [raw] if isinstance(raw, str) else list(raw)
            classes = self._classes()
            matched = any(
                n == figure.quantity
                # A name the kit declares as a quantity CLASS covers every
                # quantity in it -- membership in the declared classes, not
                # a naming prefix.
                or (str(n) in classes and figure.quantity in classes[str(n)])
                for n in names)
        if not matched:
            return False
        wanted = (record.get("applies_to") or {}).get("claim_class")
        return not wanted or figure.claim_class == wanted

    def _classes(self) -> Dict[str, Tuple[str, ...]]:
        out: Dict[str, List[str]] = {}
        for name, spec in (self.manifest.get("quantities") or {}).items():
            for cls in ((spec or {}).get("classes") or ()):
                out.setdefault(str(cls), []).append(str(name))
        return {k: tuple(v) for k, v in out.items()}

    def _hooks_of(self, record: Dict[str, Any]) -> Tuple[str, ...]:
        declared = record.get("hook")
        if declared:
            return (str(declared),)
        return HOOKS_BY_KIND.get(record.get("kind", ""), ())

    def _sweep(self, hook, figures, state=None, events=()) -> Outcome:
        if not self.enabled:
            return self._disabled_outcome(hook)
        records = [r for r in self.invariants if hook in self._hooks_of(r)]
        findings: List[Finding] = []
        spent = skipped = 0
        for figure in figures:
            for record in records:
                if spent >= BUDGET:
                    skipped += 1
                    continue
                spent += 1
                if not self._governs(record, figure):
                    continue
                finding = self._one(record, figure, figures, state, events)
                if finding is not None:
                    findings.append(finding)
        if skipped:
            logger.error(
                "REASONING BUDGET EXCEEDED at %s: %d checks skipped (cap %d). "
                "A skipped check is not a pass", hook, skipped, BUDGET)
        outcome = Outcome(_verdict(findings), hook, findings)
        outcome.incomplete = bool(skipped)
        outcome.skipped = skipped
        return outcome

    def _finding(self, record, figure, message, missing=()) -> Finding:
        """Never refuse the operator's own figure: if they typed it, it is
        authoritative input rather than a claim to check."""
        headline = str(record.get("message") or "")
        listed = ", ".join(missing) if missing else ""
        text = (f"{headline} [{message}]" if headline and message else (headline or message))
        text = text.replace("{missing}", listed or "its qualifiers")
        text = text.replace("{value}", str(figure.value) if figure.value is not None
                            else (listed or "the figure"))
        severity = str(record.get("severity") or "refuse")
        softened = None
        if figure.is_operators_own and severity == "refuse":
            severity, softened = "flag", "refuse"
            text += (" -- flagged, not refused: this is the operator's own figure, "
                     "which is authoritative input rather than a claim to check")
        return Finding(str(record.get("id") or "INV"), str(record.get("kind") or ""),
                       severity, text, figure.quantity, list(missing),
                       dict(figure.span) if figure.span else None, softened)

    def _one(self, record, figure, siblings, state, events) -> Optional[Finding]:
        kind = record.get("kind")
        state = state or {}

        needed = [str(f) for f in (record.get("requires_state") or ())]
        if needed and record.get("block_if_missing_state"):
            absent = [n for n in needed if state.get(n) is None]
            if absent:
                return self._finding(record, figure,
                    "state UNKNOWN -- " + ", ".join(absent) + " unavailable. Live "
                    "state has no design-basis fallback", absent)

        if kind == "grounding":
            if figure.origin == "operator":
                return None
            if figure.origin in ("document", "tool") and figure.source_id:
                return None
            return self._finding(record, figure,
                f"{figure.quantity} is not grounded: origin is {figure.origin} with "
                f"no source_id", ["source_id"])

        if kind == "qualifier":
            missing = figure.missing([str(f) for f in (record.get("requires") or ())])
            any_of = [str(f) for f in (record.get("requires_any_of") or ())]
            if any_of and not any(figure.known(f) for f in any_of):
                missing = missing + [f"one of {', '.join(any_of)}"]
            if missing:
                return self._finding(record, figure,
                    f"a {figure.quantity} figure without {', '.join(missing)} cannot "
                    f"be acted on", missing)
            return None

        if kind == "authority":
            governing = record.get("governing") or record.get("governing_class")
            accepted = ([governing] if isinstance(governing, str) else list(governing or []))
            if record.get("fallback_class"):
                accepted.append(str(record["fallback_class"]))
            if figure.source_class is None:
                return self._finding(record, figure,
                    f"{figure.quantity} carries no source class, so it cannot be "
                    f"ranked against {', '.join(accepted)}", ["source_class"])
            if figure.source_class in accepted:
                return None
            demoted = [str(c) for c in (record.get("demote") or ())]
            if figure.source_class in demoted:
                return self._finding(record, figure,
                    f"a {figure.source_class} is demoted for {figure.quantity}: "
                    f"{' or '.join(accepted)} governs")
            ladder = self.manifest.get("source_classes") or {}
            mine = ladder.get(figure.source_class) or {}
            for name in accepted:
                theirs = ladder.get(name) or {}
                if figure.source_class in (theirs.get("reject_as_proof") or ()):
                    return self._finding(record, figure,
                        f"a {figure.source_class} is not proof of {figure.quantity}: "
                        f"{name} governs and it is explicitly rejected as proof")
                if mine.get("rank") and theirs.get("rank") and mine["rank"] > theirs["rank"]:
                    return self._finding(record, figure,
                        f"{figure.source_class} (rank {mine['rank']}) is outranked by "
                        f"{name} (rank {theirs['rank']})")
            return None

        if kind == "currency":
            provider = str((record.get("window") or {}).get("provider") or "")
            if provider and state.get(provider) is None:
                return self._finding(record, figure,
                    f"state UNKNOWN -- {provider} unavailable. Live state has no "
                    f"design-basis fallback", [provider])
            trigger = record.get("trigger_source")
            if trigger and figure.qualifiers.get(str(trigger)) is True:
                return self._finding(record, figure,
                    f"{trigger} is true -- the record predates a disturbance", [str(trigger)])
            declared = set((self.manifest.get("staleness_triggers") or {}).get(
                figure.quantity, ()) or ())
            happened = [e for e in (events or ()) if e in declared]
            if happened:
                return self._finding(record, figure,
                    f"{figure.quantity} is stale: " + ", ".join(happened)
                    + " has happened since it was recorded", happened)
            return None

        if kind == "provenance":
            forbidden = [str(f) for f in (record.get("forbid") or ())]
            named = [d for d in figure.derivations if d in forbidden]
            if named:
                return self._finding(record, figure,
                    f"forbidden derivation: {', '.join(named)}", named)
            for dimension in [str(a) for a in (record.get("across") or ())]:
                mine = figure.qualifiers.get(dimension)
                if dimension == "revision" and figure.revision is not None:
                    mine = figure.revision
                theirs = figure.asked_about.get(dimension)
                if mine is not None and theirs is not None and str(mine) != str(theirs):
                    return self._finding(record, figure,
                        f"this figure belongs to {dimension}={mine}; the question is "
                        f"about {dimension}={theirs}", [dimension])
            return None

        if kind == "derivation":
            fired = [c for c in (record.get("block_if") or ()) if c in figure.conditions]
            if fired:
                return self._finding(record, figure,
                    f"blocking condition: {', '.join(fired)}", list(fired))
            forbidden = [str(f) for f in (record.get("forbid") or ())]
            named = [d for d in figure.derivations if d in forbidden]
            if named:
                return self._finding(record, figure,
                    f"forbidden derivation: {', '.join(named)}", named)
            steps = [str(s) for s in (record.get("requires_steps") or ())]
            absent = [s for s in steps if s not in figure.steps]
            if steps and absent:
                return self._finding(record, figure,
                    "calculation shown without " + ", ".join(absent)
                    + " -- a bare answer cannot be checked", absent)
            wrong = check_arithmetic(figure.text)
            if wrong:
                shown, expected, stated = wrong
                return self._finding(record, figure,
                    f"stated working and stated result disagree: '{shown}' -- "
                    f"{expected:g}, not {stated:g}")
            for other in siblings:
                if other is figure or other.quantity != figure.quantity:
                    continue
                if (other.value is not None and figure.value is not None
                        and other.unit == figure.unit and other.value != figure.value):
                    return self._finding(record, figure,
                        f"the answer contradicts itself on {figure.quantity}: "
                        f"{figure.value} and {other.value}")
            return None

        if kind == "unit_discipline":
            legal = ((self.manifest.get("quantities") or {}).get(figure.quantity) or {})
            units = [str(u) for u in (legal.get("units") or ())]
            if units and figure.unit and figure.unit not in units:
                return self._finding(record, figure,
                    f"unit {figure.unit!r} is not legal for {figure.quantity} "
                    f"(expected one of {', '.join(units)})", ["unit"])
            one_of = [str(t) for t in (record.get("requires_one_of") or ())]
            if one_of:
                haystack = (figure.text or "").lower()
                values = {str(v).lower() for v in figure.qualifiers.values() if v is not None}
                if figure.unit:
                    values.add(str(figure.unit).lower())
                if not any(t.lower() in haystack or t.lower() in values for t in one_of):
                    return self._finding(record, figure,
                        "none of " + ", ".join(one_of) + " stated", one_of)
            forbid = [str(f) for f in (record.get("forbid") or ())]
            if ("bare_number" in forbid or "unspecified" in forbid) and not figure.unit:
                if re.search(r"(?<![\\w.])-?\\d[\\d,]*(?:\\.\\d+)?(?![\\w.%])", figure.text or ""):
                    if not _number_carries_a_suffix(figure.text or ""):
                        return self._finding(record, figure,
                            "bare number -- every figure carries its unit", ["unit"])
            missing = figure.missing([str(f) for f in (record.get("requires") or ())])
            if missing:
                return self._finding(record, figure,
                    f"{figure.quantity} without {', '.join(missing)}", missing)
            return None

        if kind == "band":
            band = record.get("band") or {}
            low, high = band.get("min"), band.get("max")
            if str(low) == "present" or str(high) == "present":
                absent = [n for n, want in (("min", low), ("max", high))
                          if str(want) == "present" and (figure.bounds or {}).get(n) is None]
                if absent:
                    return self._finding(record, figure,
                        f"{figure.quantity} is a band -- {', '.join(absent)} missing; a "
                        f"single-sided figure is not a conservative answer, it is an "
                        f"unusable one", absent)
                if band.get("same_source") and (
                        (figure.bounds or {}).get("source_id_min")
                        != (figure.bounds or {}).get("source_id_max")):
                    return self._finding(record, figure,
                        f"the two bounds of {figure.quantity} come from different "
                        f"sources", ["same_source"])
                return None
            if figure.value is None or isinstance(figure.value, bool):
                return None
            try:
                value = float(figure.value)
            except (TypeError, ValueError):
                return None
            if low is not None and value < float(low):
                return self._finding(record, figure,
                    f"{figure.quantity} = {figure.value} is below the possible minimum "
                    f"{low} -- check the unit it was computed in")
            if high is not None and value > float(high):
                return self._finding(record, figure,
                    f"{figure.quantity} = {figure.value} is above the possible maximum "
                    f"{high} -- check the unit it was computed in")
            return None

        return None


def _number_carries_a_suffix(text):
    """Does a digit run continue (after optional spaces) into a letter or a
    percent sign? A character scan of the figure's own text -- a unit attached
    to the number -- not a match against any word."""
    i, n = 0, len(text)
    while i < n:
        if text[i].isdigit():
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and (text[j].isalpha() or text[j] == "%"):
                return True
        i += 1
    return False


_EQUATION = re.compile(
    r"(-?\\d[\\d,]*(?:\\.\\d+)?)\\s*([/*x+-])\\s*(-?\\d[\\d,]*(?:\\.\\d+)?)\\s*=\\s*"
    r"(-?\\d[\\d,]*(?:\\.\\d+)?)")


def check_arithmetic(text: str, tolerance: float = 0.01):
    """Find a stated ``a op b = c`` whose result is wrong.

    Live: "L/20 = 4800/20 = 200 mm" -- rule right, inputs right, result wrong.
    The hardest defect to see by reading and trivial to catch by evaluating.
    """
    for match in _EQUATION.finditer(text or ""):
        left, op, right, stated = match.groups()
        try:
            a = float(left.replace(",", ""))
            b = float(right.replace(",", ""))
            c = float(stated.replace(",", ""))
        except ValueError:
            continue
        if op == "/":
            if b == 0:
                continue
            expected = a / b
        elif op in ("*", "x"):
            expected = a * b
        elif op == "+":
            expected = a + b
        else:
            expected = a - b
        if expected == 0:
            if abs(c) > tolerance:
                return match.group(0), expected, c
            continue
        if abs(expected - c) / abs(expected) > tolerance:
            return match.group(0), expected, c
    return None


#: One kernel per process. The kit does not change while the platform runs.
kernel = ReasoningKernel()
'''


def render_host() -> str:
    """The four functions the PRODUCT fills in. Unwired means refuse, not pass."""
    return '''"""The host contract: the four functions this platform must implement.

The kernel cannot read this platform's corpus, its SCADA or its documents, so the
platform supplies them. Until it does, each raises HostNotWired and the caller
REFUSES -- an unwired host must never look like a clean pass, because a platform
that forgot to wire retrieval would then answer ungated and look fine doing it.

Fill these in and delete the raises. Nothing else in the reasoning layer changes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.reasoning.kernel import Figure, HostNotWired, Outcome, kernel


def extract_figures(answer: str, tool_results: Any, context: Any) -> List[Figure]:
    """Every figure this answer states, with what is KNOWN about each.

    Do not infer a qualifier, a claim class or a source class from the prose.
    Inferring them is the defect the layer exists to catch: an unqualified figure
    must arrive unqualified so the kernel can refuse it.
    """
    raise HostNotWired(
        "extract_figures is not implemented: this platform cannot gate its own "
        "answers until it is")


def resolve_source(source_id: str) -> Dict[str, Any]:
    """``{class, revision, effective_date}`` for a citation."""
    raise HostNotWired("resolve_source is not implemented")


def state(provider_name: str) -> Optional[Dict[str, Any]]:
    """The named live-state record, or None when it cannot be read.

    None means UNKNOWN. Never substitute a design figure for a live state.
    """
    raise HostNotWired("state is not implemented")


def apply(outcome: Outcome) -> None:
    """Annotate, flag or refuse. Able to replace ONE figure (using the finding's
    span) or the whole answer."""
    raise HostNotWired("apply is not implemented")


def gate_question(question: str) -> Outcome:
    """H0, for the router to call BEFORE retrieval.

    Call this first and return its refusal without retrieving anything. A test
    should assert the retrieval function was never called.
    """
    return kernel.pre_retrieval(question)
'''


def render_pending(manifest: Optional[Dict[str, Any]] = None) -> str:
    """The unanswered interview questions, as the platform's own record."""
    return '''"""Figures the build interview did not answer, and the question for each.

These are NOT stubs. A stub is a fake value that ships silently and gets mistaken
for real. Here the figure has no value at all, and any statement needing it is
REFUSED naming the question the operator must go and answer.

When an answer arrives it is stored with who gave it, when, and from which
document. Without that the interview has only moved the invented figure from the
model to the operator.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.reasoning.kernel import kernel


def unanswered() -> Dict[str, str]:
    """``{figure: the interview question}`` for every figure with no value."""
    return kernel.pending_questions()


def value_of(figure: str) -> Tuple[Any, Optional[str]]:
    """``(value, refusal)``. A figure with no answer returns a refusal naming the
    question -- never a default, never a zero, never a plausible number."""
    return kernel.figure_value(figure)


def answer(figure: str, value: Any, *, answered_by: str, answered_at: str,
           source: str) -> Dict[str, Any]:
    """Record an interview answer WITH its provenance, and PERSIST it.

    All three of answered_by, answered_at and source are required. An answer
    without them is an unattributed figure, which is the thing this layer refuses
    from a model and must equally refuse from a person.

    The kernel writes it to this platform's storage and reads it back before
    returning; if it did not persist, this raises instead of reporting success.
    An earlier version validated the provenance, returned the dict and stored
    nothing, so a caller got ok:true for a no-op.
    """
    return kernel.record_answer(
        figure, value, answered_by=answered_by, answered_at=answered_at,
        source=source)


def answered() -> Dict[str, Dict[str, Any]]:
    """Every answer this platform holds, with who gave it, when and from where."""
    return kernel.answers()


def interview() -> Dict[str, Any]:
    """The domain owner's own question sheet, and what is still outstanding.

    Read ``questions_source`` before reading ``ready``. A kit that ships without a
    sheet reports ``derived`` and is never ready: nothing has interviewed that
    domain, and reporting it the same way as a completed interview would be the
    loudest untruth this module could tell.
    """
    return kernel.interview()


def answer_question(question_id: str, answer: Any, **fields: Any) -> Dict[str, Any]:
    """Answer one sheet question, with every field this domain's format requires.

    The required fields come from the sheet's own 'Answer format:' line, not from
    a list held here -- fit-out requires quality band and market, dental requires
    adult-or-paediatric and the protocol version. Persisted and read back before
    success is reported.
    """
    return kernel.record_question_answer(question_id, answer, fields)
'''


def emit(ctx: Any) -> list:
    """Write the socket into the product under build, AND vendor its kit.

    The socket without its kit is a socket that refuses everything. Emitting the
    five code files and leaving the kit directory empty was the state this was in:
    correct by the fail-closed rule, and useless, because the built platform gated
    nothing and could answer nothing either. So the kit is vendored here, from the
    Store, exactly as a block is.

    Three outcomes, all of them stated rather than silent:

      * a vertical with no kit          -> no kit vendored. The platform runs; the
                                          kernel refuses any figure. Logged as a
                                          warning, because it is a real limitation
                                          and not an error in this build.
      * a kit named, Store unreachable  -> no kit vendored, logged as an ERROR. A
                                          platform that refuses every figure
                                          because of an environment problem must
                                          not be quiet about it.
      * a kit named and found, broken   -> RAISES. Shipping a product whose gate is
                                          disabled by a typo in the Store is worse
                                          than failing the build that would ship
                                          it.
    """
    import pathlib

    written = []
    for relative, body in (
        (INIT_PATH, render_init()),
        (KERNEL_PATH, render_kernel()),
        (HOST_PATH, render_host()),
        (PENDING_PATH, render_pending()),
        (ROUTES_PATH, render_routes()),
    ):
        ctx.workspace.write_text(pathlib.Path(relative), body)
        written.append(relative)
    written.extend(vendor_kit(ctx))
    return written


def vendor_kit(ctx: Any) -> list:
    """Copy this vertical's kit out of the Store and into the product.

    Returns the paths written, which is empty when there is no kit to vendor --
    and an empty return is a platform whose reasoning layer refuses every figure,
    which the caller logs.
    """
    import logging
    import pathlib

    import yaml

    logger = logging.getLogger(__name__)

    plan = getattr(ctx, "plan", None)
    blueprint = getattr(ctx, "blueprint", None)
    resolved = getattr(plan, "__dict__", None) if plan is not None else None

    # Resolve the Store BEFORE the kit, so the kit can be resolved against what
    # the Store actually publishes. Only a build does this: resolving the root
    # can clone the Store, and ``kit_for_vertical`` is also called per message by
    # the Floor's chat leg, which must not pay for a clone to answer a question.
    root = None
    unreachable = None
    if vertical_of(blueprint, resolved):
        try:
            from app.factory.blocks_source import resolve_blocks_root

            root = resolve_blocks_root()
        except Exception as exc:  # noqa: BLE001 -- an unreachable Store is not a crash
            unreachable = exc

    if vertical_of(blueprint, resolved) and root is None:
        logger.error(
            "reasoning socket: Store unreachable (%s); no kit vendored, so this "
            "platform will refuse every figure it needs", unreachable or "no root")
        return []

    kit = kit_for_vertical(blueprint, resolved, store_root=root)
    if not kit:
        # Half a kit named for this vertical is a broken Store kit, not an
        # absent one: fail the build and name the missing file.
        vertical = vertical_of(blueprint, resolved)
        half = pathlib.Path(root) / "app" / "blocks" / vertical if root and vertical else None
        if half is not None and half.is_dir() and half.resolve().parent == (pathlib.Path(root) / "app" / "blocks").resolve():
            for name in ("manifest.yaml", "invariants.yaml"):
                if not (half / name).is_file() and any(
                        (half / other).is_file()
                        for other in ("manifest.yaml", "invariants.yaml")):
                    raise FileNotFoundError(
                        f"Store kit {half.name} is missing {name}; refusing to "
                        "build a platform on half a kit")
        logger.warning(
            "reasoning socket: no kit for this vertical. The platform will refuse "
            "every figure its rules need — that is fail-closed, not a gate.")
        return []

    if unreachable is not None:
        logger.error("reasoning socket: Store unreachable (%s); kit '%s' NOT vendored, "
                     "so this platform will refuse every figure", unreachable, kit)
        return []
    if root is None:
        logger.error(
            "reasoning socket: Store unreachable; kit '%s' NOT vendored, so this "
            "platform will refuse every figure it needs", kit)
        return []

    source = pathlib.Path(root) / "app" / "blocks" / kit
    required = {"manifest.yaml": KIT_MANIFEST, "invariants.yaml": KIT_INVARIANTS}
    #: The domain owner's question sheet. Optional -- two Store kits have none, and
    #: a platform built on one of those runs on the kit's derived questions and says
    #: so, rather than reporting an un-interviewed domain as ready.
    optional = {"questions.yaml": KIT_QUESTIONS,
                "design_basis.yaml": KIT_DESIGN_BASIS}

    for name in required:
        if not (source / name).is_file():
            raise FileNotFoundError(
                f"reasoning kit '{kit}' is missing {name} at {source}. The product "
                f"would ship with its reasoning gate disabled, which is worse than "
                f"failing this build")

    # Parse before vendoring. A kit that does not load disables the kernel, and a
    # product that refuses every figure because of a typo in the Store must not be
    # something we discover after deployment.
    manifest = yaml.safe_load((source / "manifest.yaml").read_text(encoding="utf-8")) or {}
    records = yaml.safe_load((source / "invariants.yaml").read_text(encoding="utf-8")) or {}
    if not (records.get("invariants") if isinstance(records, dict) else records):
        raise ValueError(
            f"reasoning kit '{kit}' declares no invariants; it would gate nothing and "
            f"disable the kernel in the built product")
    if str(manifest.get("kit") or "") != kit:
        raise ValueError(
            f"reasoning kit at {source} names kit '{manifest.get('kit')}', not '{kit}'. "
            f"Vendoring it would give this platform one domain's vocabulary under "
            f"another domain's name")

    paths = []
    for name, destination in list(required.items()) + list(optional.items()):
        if not (source / name).is_file():
            continue
        ctx.workspace.write_text(
            pathlib.Path(destination), (source / name).read_text(encoding="utf-8"))
        paths.append(destination)

    sheet = source / "questions.yaml"
    if sheet.is_file():
        logger.info("reasoning socket: vendored kit '%s' with the domain owner's "
                    "question sheet", kit)
    else:
        logger.warning(
            "reasoning socket: vendored kit '%s', but it has NO owner question sheet. "
            "Its questions are derived from its quantity names; the platform reports "
            "them as derived and never as an interview that has been done.", kit)
    return paths


def kit_for_vertical(blueprint: Any, resolved: Optional[Dict[str, Any]] = None,
                     store_root: Any = None) -> Optional[str]:
    """Which kit this build gets, from the blueprint's own vertical.

    Nothing is guessed from a product name: the blueprint states its vertical, and
    a vertical with no kit gets None -- which the socket reports as a refusal
    rather than as a pass.

    ``store_root``, when a caller has already resolved one, lets a vertical that
    names a Store kit outright resolve without an entry in the alias table below.
    """
    vertical = vertical_of(blueprint, resolved)
    if not vertical:
        return None
    # The Store's reasoning kits say which verticals they serve
    # (``serves_verticals`` in app/blocks/<kit>/manifest.yaml). The Factory
    # kept a copy of that list, and the copy went stale: ``stadium_venue``
    # shipped in the Store, was absent here, and every stadium build took the
    # no-kit path and refused every figure -- reported as fail-closed, the one
    # way for a missing kit to look correct. No Store root, no kit: the caller's
    # no-kit path refuses, which is honest.
    if store_root is None:
        return None
    from app.factory.store_kits import reasoning_kits, serving_kit

    return serving_kit(vertical, reasoning_kits(store_root))


def vertical_of(blueprint: Any, resolved: Optional[Dict[str, Any]] = None) -> str:
    """The blueprint's stated vertical, normalised. "" when it states none."""
    for attribute in ("vertical", "domain", "industry"):
        value = getattr(blueprint, attribute, None) or (
            (resolved or {}).get(attribute) if resolved else None)
        if value:
            return str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return ""


def render_routes() -> str:
    """The operator's way to answer the kit's questions after the build.

    The build interview cannot ask everything -- the Floor's question rounds are
    bounded, and most of these figures are operational rather than design-time.
    So the platform ships with the remainder OPEN: the kernel refuses anything
    that needs them, and these routes are where the answer arrives later, from
    the person who actually knows it.
    """
    return '''"""Reasoning-layer figures: what is still unanswered, and how to answer it.

GET  /v1/reasoning/pending     figures with no value, and the question for each
POST /v1/reasoning/answer      record one figure answer, with its provenance
GET  /v1/reasoning/interview   the domain owner's own question sheet, and what is
                               still outstanding on this platform
POST /v1/reasoning/interview   answer one sheet question, with every field this
                               domain's answer format requires

Until a figure is answered the kernel REFUSES every statement needing it and
names this question. That is not a stub: a stub is a fake value that ships
silently and gets mistaken for real, and there is none here to mistake.

An answer requires answered_by, answered_at and source. An unattributed figure is
not evidence whoever supplied it -- refusing it from a model and accepting it from
a person would just move the invented figure one seat along.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from app import auth
from app.reasoning import pending
from app.reasoning.kernel import kernel

router = APIRouter(tags=["reasoning"])


@router.get("/v1/reasoning/pending")
async def reasoning_pending(request: Request) -> Dict[str, Any]:
    """Every figure with no answer, and the question that fills it."""
    tenant = await _principal(request)
    unanswered = pending.unanswered()
    return {
        "kit_enabled": kernel.enabled,
        "kit_disabled_reason": kernel.disabled_reason,
        "unanswered_count": len(unanswered),
        "questions": [
            {"figure": name, "question": question}
            for name, question in sorted(unanswered.items())
        ],
        "note": (
            "Any answer needing one of these is refused, naming the question. "
            "Nothing is defaulted and nothing is estimated."
        ),
    }


@router.post("/v1/reasoning/answer")
async def reasoning_answer(request: Request) -> Dict[str, Any]:
    """Record one answer WITH its provenance. All three fields are required."""
    tenant = await _principal(request)
    auth.require_permission(tenant, "write")
    body = await _json(request)
    figure = str(body.get("figure") or "").strip()
    if not figure:
        raise HTTPException(status_code=422, detail="figure is required")
    if figure not in pending.unanswered():
        raise HTTPException(
            status_code=409,
            detail=f"{figure} is not an open question on this platform",
        )
    try:
        recorded = pending.answer(
            figure,
            body.get("value"),
            answered_by=str(body.get("answered_by") or ""),
            answered_at=str(body.get("answered_at") or ""),
            source=str(body.get("source") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        # It did not persist. Reporting ok here would be a success over an
        # operation that did nothing, which is what this layer refuses from
        # everyone else.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    recorded["tenant_id"] = getattr(tenant, "tenant_id", None)
    return {
        "ok": True,
        "recorded": recorded,
        "still_unanswered": len(pending.unanswered()),
    }


@router.get("/v1/reasoning/interview")
async def reasoning_interview(request: Request) -> Dict[str, Any]:
    """The domain owner's own question sheet, and what is still outstanding.

    ``questions_source`` must be read before ``ready``. ``owner_sheet`` means these
    are the owner's questions in their own words. ``derived`` means this kit ships
    without a sheet and its questions were derived from its quantity names -- then
    readiness is unknown, not met.
    """
    tenant = await _principal(request)
    state = pending.interview()
    state["kit_enabled"] = kernel.enabled
    state["kit_disabled_reason"] = kernel.disabled_reason
    return state


@router.post("/v1/reasoning/interview")
async def reasoning_answer_question(request: Request) -> Dict[str, Any]:
    """Answer one sheet question, with every field this domain's format requires.

    ``GET /v1/reasoning/interview`` lists ``required_fields`` for this domain. An
    answer short of any of them is refused rather than stored partially: a cost
    without its market and quality band is not an answer to "cost per m² by
    quality band", it is a number that will be cited as one.
    """
    tenant = await _principal(request)
    auth.require_permission(tenant, "write")
    body = await _json(request)
    question_id = str(body.get("id") or body.get("question_id") or "").strip()
    if not question_id:
        raise HTTPException(status_code=422, detail="id is required")
    fields = dict(body.get("fields") or {})
    for name in kernel.required_answer_fields():
        if name in body and name not in fields:
            fields[name] = body[name]
    try:
        recorded = pending.answer_question(question_id, body.get("answer"), **fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        # It did not persist. ok here would be a success report over an operation
        # that did nothing -- the defect class this layer exists to catch.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    state = pending.interview()
    recorded["tenant_id"] = getattr(tenant, "tenant_id", None)
    return {
        "ok": True,
        "recorded": recorded,
        "outstanding": state.get("outstanding"),
        "ready": state.get("ready"),
    }


async def _principal(request: Request):
    from app.tenancy import resolve_tenant

    return await resolve_tenant(request)


async def _json(request: Request) -> Dict[str, Any]:
    from app.auth import json_object

    return await json_object(request)
'''
