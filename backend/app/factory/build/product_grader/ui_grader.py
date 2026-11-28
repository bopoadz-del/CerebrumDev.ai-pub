"""F-8b -- Layer-1 UI grader (Playwright, headless).

The UI script is GENERATED from the product's own blueprint: the screens it
declares (``ui_modules``) and the routes its capabilities imply
(``/v1/<capability_id>``). Fixtures (a PDF and an XLSX) are written from the
blueprint's own summary and capability text -- nothing canned.

Steps (ORDERS_1730Z Layer 1): app loads; login/tenant bind; chat-box "+"
attach a real PDF and XLSX; parsed status appears; a cited answer with a
number renders; an invalid request is answered honestly with 422/503 (never
a 200); every declared route returns 200 and renders with no console errors.

Every step calls the product the way the grading boot runs it: with the
bearer that boot sets (``image_sufficiency.GATE_RUN_ENV``, the one source),
on the API requests and on every request the page makes to the product's
own origin. A request the page makes to any other origin carries no bearer.
A product that needs no token ignores the header and is graded the same.

Which of those steps run is read from the customer brief's ``documents``
field (``customer_brief``): whether the customer uploads documents, and
which file types. No other text is consulted. A kind with no file type is
the product surface and always runs. A document step whose type the field
does not list is not run and is reported not applicable — never passed,
never a failure. A value that is not a customer brief does not choose steps
by guessing words.

No base URL, no Playwright, or no report = the UI run did not happen = FAIL.
A skipped UI run is never a pass.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.factory.build.authority import capability_route
from app.factory.build.image_sufficiency import GATE_RUN_ENV, GATE_TOKEN_VAR

RUN_TIMEOUT_S = 600
#: Playwright is told to stop its own steps this long before a run's cap, so
#: it always writes its report: a run that ran out of time is read, never lost.
REPORT_MARGIN_S = 60

#: The PATH a UI run sees. One value, read by the grade and by the toolchain
#: check, so the check proves exactly the path the grade will take.
RUN_PATH = "/usr/bin:/bin:/usr/local/bin"
TOOLCHAIN_TIMEOUT_S = 120
#: How much of a failed tool's output a recorded reason keeps.
OUTPUT_TAIL_CHARS = 400

#: The fixed step kinds. Their TARGETS come from the blueprint. Whether a
#: document kind runs is the customer brief's documents field.
STEP_KINDS = ("load", "bind", "attach_pdf", "attach_xlsx", "parsed", "cited_answer", "honest_error")

#: Not a pass and not a failure: the documents field does not include this step.
NOT_APPLICABLE = "not_applicable"

#: Returned when a value was given and it is not a customer brief. The UI
#: grade does not guess which steps apply.
BRIEF_UNREADABLE = "brief unreadable"

_UNSET: Any = object()

#: Document step kind -> the file type id the step attaches. ``cited_answer``
#: runs whenever the brief says the customer uploads documents, whichever
#: types were named. The ids are the step targets' own suffixes.
_KIND_FILE_TYPE = {
    "attach_pdf": "pdf",
    "parsed": "pdf",
    "attach_xlsx": "xlsx",
}

#: The step kinds that form one chain: each needs the page the step before it
#: left (attach a file, see it parsed, ask about it). Only these run in
#: order with a stop at the first failure. Every other step navigates by
#: itself, so it runs whatever happened to the steps before it.
CHAIN_KINDS = tuple(_KIND_FILE_TYPE) + ("cited_answer",)

#: The step kinds that act on the page as they find it (they do not navigate).
#: Each opens the product's front page first when its page is still blank, so
#: none of them depends on an earlier step having loaded it.
ON_PAGE_KINDS = ("bind",) + CHAIN_KINDS

#: A chain step that Playwright skipped because an earlier chain step failed.
#: It did not run: it is not a pass and it is not a failure of its own.
NOT_RUN = "not_run"

#: A step Playwright recorded no result for. Only the run's time limit explains
#: that; in a run that ended in time the step did not pass.
NO_RESULT = "no_result"

#: What blocked a step the run's own time limit stopped before it finished.
TIME_LIMIT = "the UI run's time limit"



@dataclass(frozen=True)
class UiStep:
    name: str
    kind: str
    target: str


@dataclass
class UiGrade:
    steps: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    not_applicable: List[Dict[str, Any]] = field(default_factory=list)
    brief_note: str = ""

    @property
    def ran(self) -> bool:
        return bool(self.steps)

    @property
    def not_run(self) -> List[Dict[str, Any]]:
        """Steps that did not run: an earlier step of their chain failed, or
        the run's own time limit stopped it first. Each row names what blocked
        it."""
        return [s for s in self.steps if s.get("status") == NOT_RUN]

    @property
    def failing(self) -> List[str]:
        """Steps that ran and did not pass, plus run-level errors. A step
        that did not run is not listed here: nothing was observed about it."""
        return [
            s["name"] for s in self.steps
            if not s.get("passed") and s.get("status") != NOT_RUN
        ] + list(self.errors)

    @property
    def ok(self) -> bool:
        return self.ran and not self.failing and not self.not_run

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "ok": self.ok, "ran": self.ran, "failing": self.failing, "steps": self.steps,
        }
        if self.not_run:
            out["not_run"] = [
                {"name": s["name"], "blocked_by": s.get("blocked_by", "")} for s in self.not_run
            ]
        if self.not_applicable:
            out["not_applicable"] = list(self.not_applicable)
        if self.brief_note:
            out["brief_note"] = self.brief_note
        return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(text).lower()).strip("_")


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dict(dump())
        except TypeError:
            pass
    data = getattr(value, "__dict__", None)
    return dict(data) if isinstance(data, dict) else {}


def brief_for_steps(
    blueprint: Any,
    *,
    supplied: Any = None,
    compiled: Any = None,
) -> Optional[str]:
    """The customer brief the UI steps are derived from, or None.

    Only a complete customer brief counts. ``blueprint`` is not a brief.
    The Factory contract is not a brief. None means no complete customer
    brief was given (callers then keep the full step list).
    """
    del blueprint, compiled
    from app.factory.build.customer_brief import stage_quote

    return stage_quote(supplied)


def _unreadable_block() -> UiGrade:
    """A value was given and it is not a customer brief. No step ran."""
    return UiGrade([], [BRIEF_UNREADABLE], brief_note=BRIEF_UNREADABLE)


def _candidate_steps(blueprint: Mapping[str, Any]) -> List[UiStep]:
    caps = [str(c.get("id")) for c in blueprint.get("capabilities") or [] if c.get("id")]
    first = caps[0] if caps else ""
    steps = [
        UiStep("load:/", "load", "/"),
        UiStep("bind:tenant", "bind", str(blueprint.get("product_id") or "")),
        UiStep("attach:pdf", "attach_pdf", "fixture.pdf"),
        UiStep("attach:xlsx", "attach_xlsx", "fixture.xlsx"),
        UiStep("parsed:status", "parsed", "fixture.pdf"),
        UiStep("answer:cited", "cited_answer", first),
        UiStep(f"honest_error:{capability_route(first)}", "honest_error", capability_route(first)),
    ]
    for mod in blueprint.get("ui_modules") or []:
        steps.append(UiStep(f"screen:{_slug(mod)}", "screen", f"/#/{_slug(mod)}"))
    for cid in caps:
        steps.append(UiStep(f"route:{capability_route(cid)}", "route", capability_route(cid)))
    return steps


def _applicable_document_kinds(brief: Any) -> Optional[set]:
    """None: no customer brief was given, so every document step applies.
    A set: the document kinds the brief's documents field turns on.
    Raises ValueError when a value was given and it is not a customer brief.
    """
    if brief is _UNSET or brief is None:
        return None
    from app.factory.build.customer_brief import read_brief

    record, _problems = read_brief(brief)
    if record is None:
        raise ValueError(BRIEF_UNREADABLE)
    if not record.uploads:
        return set()
    kinds = {"cited_answer"}
    types = set(record.file_types)
    for kind, type_id in _KIND_FILE_TYPE.items():
        if type_id in types:
            kinds.add(kind)
    return kinds


def _split_plan(
    blueprint: Mapping[str, Any], brief: Any
) -> Tuple[List[UiStep], List[UiStep], str]:
    """``(run, not_applicable, block)``.

    No brief: every candidate step runs. A customer brief runs a document
    step only when its documents field says so. Anything else that was
    passed in is not a brief: the grade blocks and does not guess.
    """
    try:
        allowed = _applicable_document_kinds(brief)
    except ValueError:
        return [], [], BRIEF_UNREADABLE
    run: List[UiStep] = []
    skip: List[UiStep] = []
    for step in _candidate_steps(blueprint):
        document_kind = step.kind in _KIND_FILE_TYPE or step.kind == "cited_answer"
        if allowed is not None and document_kind:
            (run if step.kind in allowed else skip).append(step)
        else:
            run.append(step)
    return run, skip, ""


def ui_plan(blueprint: Mapping[str, Any], brief: Any = _UNSET) -> List[UiStep]:
    """The steps that will be run. An unreadable value runs none."""
    run, _skip, _block = _split_plan(blueprint, brief)
    return run


def _not_applicable_row(step: UiStep) -> Dict[str, Any]:
    return {
        "name": step.name,
        "kind": step.kind,
        "target": step.target,
        "applicable": False,
        "status": NOT_APPLICABLE,
    }


def _question(blueprint: Mapping[str, Any], brief: Any = _UNSET) -> str:
    """The cited-answer prompt. Empty when that step does not apply, so a
    brief with no documents is not asked about an attached document."""
    _run, _skip, _note = _split_plan(blueprint, brief)
    if not any(step.kind == "cited_answer" for step in _run):
        return ""
    caps = blueprint.get("capabilities") or [{}]
    first = caps[0] if caps else {}
    if not isinstance(first, Mapping):
        first = _as_mapping(first)
    return (
        "What does the attached document say about "
        f"{str(first.get('description') or '').split('.')[0][:120]}?"
    )


def _pdf_bytes(text: str) -> bytes:
    safe = re.sub(r"[()\\]", " ", text)[:400]
    stream = f"BT /F1 11 Tf 40 760 Td ({safe}) Tj ET".encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def write_fixtures(blueprint: Mapping[str, Any], dest: Path, brief: Any = _UNSET) -> Dict[str, Path]:
    """PDF and spreadsheet fixtures, written only when a document step runs.
    A brief that cannot be read still writes them (every step is kept)."""
    dest.mkdir(parents=True, exist_ok=True)
    if not any(step.kind in ("attach_pdf", "attach_xlsx") for step in ui_plan(blueprint, brief)):
        return {}
    caps = blueprint.get("capabilities") or []
    pdf = dest / "fixture.pdf"
    pdf.write_bytes(_pdf_bytes(f"{blueprint.get('product_name', '')}: {blueprint.get('summary', '')}"))
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = _slug(blueprint.get("product_id") or "product")[:31] or "product"
    ws.append(["capability", "description", "quantity"])
    for i, c in enumerate(caps, 1):
        ws.append([str(c.get("id")), str(c.get("description") or "")[:200], i * 3])
    xlsx = dest / "fixture.xlsx"
    wb.save(xlsx)
    return {"pdf": pdf, "xlsx": xlsx}


#: Every target is resolved against BASE_URL (the rendered product). A
#: missing BASE_URL throws -- it never falls back to a relative URL.
#: The run's bearer reaches the spec through this variable, never baked into
#: the spec file. Unset or empty: the steps send no Authorization header.
BEARER_ENV = "GRADE_BEARER"

BASE_JS = (
    "const BASE = process.env.BASE_URL;\n"
    "if (!BASE) throw new Error('BASE_URL is not set: UI run cannot happen');\n"
    "const url = (t: string) => new URL(t, BASE).toString();\n"
    f"const BEARER = process.env.{BEARER_ENV} || '';\n"
    "const AUTH: Record<string, string> = BEARER ? { Authorization: 'Bearer ' + BEARER } : {};\n"
    # The bearer is sent to an address only when that address is the
    # product's own origin (BASE), ONE HOP AT A TIME: no request made here
    # follows a redirect by itself, because a followed redirect carries the
    # header on to wherever it leads.
    "const OWN = new URL(BASE).origin;\n"
    "const signed = (u: string, h: Record<string, string> = {}) => (new URL(u).origin === OWN ? { ...h, ...AUTH } : h);\n"
    # The page's own requests. A request to the product is made with the
    # bearer and its answer handed to the page as it came; when that answer
    # is a redirect the browser asks the new address itself, and that
    # request comes through here again only if it is the product's. A font,
    # script or call to another origin goes out as the page sent it.
    "const signIn = async (p: any) => { if (!BEARER) return;\n"
    "  await p.route((u: URL) => u.origin === OWN, async (route: any) => {\n"
    "    const response = await route.fetch({ maxRedirects: 0, timeout: 0, headers: signed(route.request().url(), route.request().headers()) });\n"
    "    await route.fulfill({ response });\n"
    "  }); };\n"
    # The API steps' requests. A redirect is followed here, hop by hop, each
    # hop signed only if it is the product's: 307 and 308 repeat the request,
    # any other redirect is followed with a GET, as a browser does.
    "const HOPS = 10;\n"
    "const call = async (request: any, method: string, target: string, data?: string, headers: Record<string, string> = {}) => {\n"
    "  let u = url(target); let r: any;\n"
    "  for (let hop = 0; hop <= HOPS; hop++) {\n"
    "    r = await request.fetch(u, { method, data, headers: signed(u, headers), maxRedirects: 0 });\n"
    "    const to = r.headers()['location'];\n"
    "    if (r.status() < 300 || r.status() > 399 || !to) return r;\n"
    "    u = new URL(to, u).toString();\n"
    "    if (r.status() !== 307 && r.status() !== 308) { method = 'GET'; data = undefined; headers = {}; }\n"
    "  }\n"
    "  return r;\n"
    "};\n"
)


def grading_bearer(env: Optional[Mapping[str, str]] = None) -> str:
    """The bearer the grading boot runs the product with: the gate run
    environment's token (``env`` defaults to ``GATE_RUN_ENV``). Empty when
    that environment sets none."""
    source = GATE_RUN_ENV if env is None else env
    return str(source.get(GATE_TOKEN_VAR) or "").strip()

#: An invalid request must be answered honestly. A 200 is never honest.
HONEST_ERROR_STATUSES = (422, 503)

#: Per-kind Playwright body. Kept as data so every kind's assertion is
#: inspectable (and testable) on its own.
#: A page step asks for the address and reads the status of the answer. A
#: move inside the document already loaded (only the ``#fragment`` differs,
#: which is how every screen after the first page is reached) makes no
#: request, and the browser returns no response for it: the address is then
#: loaded again by itself, so the status read is the one that address gets.
_PAGE_OK = (
    "let r = await page.goto(url(s.target));\n"
    "if (r === null) r = await page.reload();\n"
    "expect(r?.status()).toBe(200);\n"
    "await page.waitForLoadState('networkidle'); expect(errs).toEqual([]);"
)
KIND_JS: Dict[str, str] = {
    "load": _PAGE_OK,
    "screen": _PAGE_OK,
    "route": "const r = await call(request, 'GET', s.target); expect(r.status()).toBe(200);",
    "bind": "await expect(page.getByText(new RegExp(s.target.replace(/[-_]/g, '.'), 'i')).first()).toBeVisible();",
    "attach_pdf": (
        "await page.getByRole('button', { name: '+' }).first().click();\n"
        "await page.locator('input[type=file]').first().setInputFiles(process.env.GRADE_FIXTURES + '/' + s.target);"
    ),
    "parsed": "await expect(page.getByText(/parsed/i).first()).toBeVisible({ timeout: 60000 });",
    "cited_answer": (
        "const box = page.getByRole('textbox').first(); await box.fill(QUESTION); await box.press('Enter');\n"
        "await expect(page.getByText(/\\[\\d+\\]|source|citation/i).first()).toBeVisible({ timeout: 90000 });\n"
        "await expect(page.getByText(/\\d/).last()).toBeVisible();"
    ),
    "honest_error": (
        "const r = await call(request, 'POST', s.target, '\"not-an-object\"', { 'content-type': 'application/json' });\n"
        f"expect({json.dumps(list(HONEST_ERROR_STATUSES))}).toContain(r.status());"
    ),
}
KIND_JS["attach_xlsx"] = KIND_JS["attach_pdf"]


def render_spec(blueprint: Mapping[str, Any], brief: Any = _UNSET) -> str:
    """The Playwright test file. One ``test`` per plan step, titled by name.
    A step whose kind has no body is a hard error, never an empty pass.
    Steps the brief does not contain are absent: they are not run."""
    plan = ui_plan(blueprint, brief)
    missing = sorted({s.kind for s in plan} - set(KIND_JS))
    if missing:
        raise ValueError(f"no UI assertion for step kinds: {missing}")
    branches = "".join(
        f"    {'if' if i == 0 else '} else if'} (s.kind === {json.dumps(k)}) {{\n      {body.replace(chr(10), chr(10) + '      ')}\n"
        for i, (k, body) in enumerate(KIND_JS.items())
    )
    return (
        "import { test, expect } from '@playwright/test';\n"
        f"const PLAN = {json.dumps([s.__dict__ for s in plan])};\n"
        f"const CHAIN = {json.dumps(list(CHAIN_KINDS))};\n"
        f"const ON_PAGE = {json.dumps(list(ON_PAGE_KINDS))};\n"
        f"const QUESTION = {json.dumps(_question(blueprint, brief))};\n"
        + BASE_JS +
        "const errs: string[] = [];\n"
        "let page: any;\n"
        "const open = async (browser: any) => { const p = await browser.newPage(); await signIn(p);\n"
        "  p.on('console', (m: any) => { if (m.type() === 'error') errs.push(m.text()); });\n"
        "  p.on('pageerror', (e: any) => errs.push(String(e))); return p; };\n"
        "const run = async (s: any, request: any) => {\n"
        "    if (ON_PAGE.includes(s.kind) && !/^https?:/.test(page.url())) { await page.goto(url('/')); }\n"
        "    errs.length = 0;\n"
        + branches +
        "    } else { throw new Error('no assertion for ' + s.kind); }\n"
        "};\n"
        # Steps that navigate by themselves: one failing does not stop the
        # others, so every one of them is observed.
        "test.describe('steps', () => {\n"
        "  test.beforeAll(async ({ browser }) => { page = await open(browser); });\n"
        "  for (const s of PLAN.filter((s: any) => !CHAIN.includes(s.kind))) {\n"
        "    test(s.name, async ({ request }) => { await run(s, request); });\n"
        "  }\n"
        "});\n"
        # The document chain: each step needs the page the one before left,
        # so it stops at its first failure. The steps after that are skipped
        # by Playwright and read back as not run, never as failures.
        "test.describe('document chain', () => {\n"
        "  test.describe.configure({ mode: 'serial' });\n"
        "  test.beforeAll(async ({ browser }) => { page = await open(browser); });\n"
        "  for (const s of PLAN.filter((s: any) => CHAIN.includes(s.kind))) {\n"
        "    test(s.name, async ({ request }) => { await run(s, request); });\n"
        "  }\n"
        "});\n"
    )


def step_passed(results: List[Mapping[str, Any]]) -> bool:
    """A step passes only if it produced results and EVERY result passed."""
    return bool(results) and all(r.get("status") == "passed" for r in results)


def report_outcomes(report: Mapping[str, Any]) -> Dict[str, bool]:
    """Every test title in a Playwright JSON report -> whether it passed.
    A report that is not a JSON object has no outcomes."""
    seen: Dict[str, bool] = {}
    if not isinstance(report, Mapping):
        return seen

    def walk(suite: Mapping[str, Any]) -> None:
        for spec in suite.get("specs") or []:
            results = [r for t in spec.get("tests") or [] for r in t.get("results") or []]
            seen[str(spec.get("title"))] = step_passed(results)
        for child in suite.get("suites") or []:
            walk(child)

    for s in report.get("suites") or []:
        walk(s)
    return seen


def report_statuses(report: Mapping[str, Any]) -> Dict[str, str]:
    """Every test title in a Playwright JSON report -> ``passed``, ``failed``,
    ``skipped`` or ``no_result``. Skipped means every result Playwright
    recorded for the test is ``skipped``; no result means it recorded none."""
    seen: Dict[str, str] = {}
    if not isinstance(report, Mapping):
        return seen

    def walk(suite: Mapping[str, Any]) -> None:
        for spec in suite.get("specs") or []:
            results = [r for t in spec.get("tests") or [] for r in t.get("results") or []]
            if step_passed(results):
                status = "passed"
            elif not results:
                status = NO_RESULT
            elif all(r.get("status") == "skipped" for r in results):
                status = "skipped"
            else:
                status = "failed"
            seen[str(spec.get("title"))] = status
        for child in suite.get("suites") or []:
            walk(child)

    for s in report.get("suites") or []:
        walk(s)
    return seen


def run_limit_ms(timeout_s: int) -> int:
    """The time Playwright gives its own steps in a run capped at
    ``timeout_s``: the cap less the margin it needs to write its report."""
    return max(1, int(timeout_s) - REPORT_MARGIN_S) * 1000


def run_was_cut(report: Any, limit_ms: int) -> bool:
    """Did the run's time limit stop it? Playwright's report says how long the
    run took; a run that lasted its whole limit was stopped by it."""
    stats = report.get("stats") if isinstance(report, Mapping) else None
    duration = stats.get("duration") if isinstance(stats, Mapping) else None
    return isinstance(duration, (int, float)) and duration >= limit_ms


def parse_report(
    report: Mapping[str, Any], blueprint: Mapping[str, Any], brief: Any = _UNSET,
    *, limit_ms: Optional[int] = None,
) -> UiGrade:
    """Playwright JSON reporter -> UiGrade. A planned step absent from the
    report is a failed step (a skipped step is not a passed one). A chain
    step skipped after an earlier chain step failed is not run: it is quoted
    with the step that blocked it and is never a failure of its own. A step the
    brief does not contain is not applicable: it was not run, it is not
    passed, and it is not a failure. A run its time limit stopped leaves the
    steps it did not finish not run, blocked by that limit: a step that ran
    and failed before it is still a failure. A brief that cannot be read is a
    block: no step is graded."""
    run, skip, block = _split_plan(blueprint, brief)
    if block:
        return _unreadable_block()
    seen = report_statuses(report)
    cut = run_was_cut(report, run_limit_ms(RUN_TIMEOUT_S) if limit_ms is None else limit_ms)
    steps: List[Dict[str, Any]] = []
    # The chain step that failed first: the chain steps Playwright skipped
    # after it did not run because of it.
    blocker = ""
    for st in run:
        status = seen.get(st.name, "failed")
        if status == NO_RESULT:
            status = "skipped" if cut else "failed"
        row: Dict[str, Any] = {
            "name": st.name, "kind": st.kind, "target": st.target, "passed": status == "passed",
        }
        chain = st.kind in CHAIN_KINDS
        if chain and status == "skipped" and blocker:
            row["status"] = NOT_RUN
            row["blocked_by"] = blocker
        elif cut and status == "skipped":
            row["status"] = NOT_RUN
            row["blocked_by"] = TIME_LIMIT
        elif chain and status != "passed" and not blocker:
            blocker = st.name
        steps.append(row)
    errors = [] if seen else ["playwright report has no results"]
    return UiGrade(steps, errors, not_applicable=[_not_applicable_row(st) for st in skip])


def _tail(text: Any) -> str:
    return " ".join(str(text or "").split())[-OUTPUT_TAIL_CHARS:]


def _playwright_run(
    run: Any, spec: Path, *, base_url: str, out: Path, fixtures: Path, timeout: int, bearer: str = "",
) -> Any:
    """The one way a UI spec is run: the grade and the toolchain check both
    come through here, with the same command and the same environment. A
    non-empty ``bearer`` is handed to the spec as ``BEARER_ENV``."""
    env = {"PATH": RUN_PATH, "BASE_URL": base_url,
           "PLAYWRIGHT_JSON_OUTPUT_NAME": str(out), "GRADE_FIXTURES": str(fixtures)}
    if bearer:
        env[BEARER_ENV] = bearer
    return run(
        ["npx", "playwright", "test", str(spec), f"--global-timeout={run_limit_ms(timeout)}",
         "--reporter=json"],
        cwd=str(spec.parent),
        env=env,
        timeout=timeout,
        capture_output=True,
        text=True,
    )


def _report_text(out: Path, proc: Any) -> str:
    # Playwright's JSON reporter may print to stdout rather than honour the
    # PLAYWRIGHT_JSON_OUTPUT_NAME env; read the file first, then the captured
    # stdout, so a reporter that ignores the env still yields a report.
    if out.is_file():
        return out.read_text("utf-8")
    return getattr(proc, "stdout", "") or ""


def _exit_detail(proc: Any, secret: str = "") -> str:
    return f"exit {getattr(proc, 'returncode', None)}; stderr: {_tail(redact(getattr(proc, 'stderr', ''), secret))}"


#: What stands in for the run's bearer wherever the run wrote it down.
REDACTED = "[grading bearer]"


def redact(text: Any, secret: str) -> str:
    """``text`` with every copy of ``secret`` replaced by :data:`REDACTED`;
    unchanged when ``secret`` is empty."""
    text = str(text or "")
    return text.replace(secret, REDACTED) if secret else text


class ScrubIncomplete(OSError):
    """Files under the work dir that could be neither read and rewritten nor
    removed, so they may still hold the run's bearer. ``left`` names them,
    relative to the work dir."""

    def __init__(self, left: List[str]) -> None:
        super().__init__("the run's bearer could not be scrubbed from: " + ", ".join(left))
        self.left = list(left)


def scrub_files(root: Path, secret: str) -> List[str]:
    """Playwright's call log lists a failed request's headers, so the run's
    bearer can land in anything the run writes (the JSON report, an error
    context). Rewrite every file under ``root`` that holds ``secret`` with
    it redacted; the paths dealt with, relative to ``root``. Nothing is
    rewritten for an empty ``secret``.

    Every file is dealt with by itself, so one that fails never leaves the
    others unscrubbed. A file that cannot be read or rewritten is removed
    (nothing the grade reads is on disk: it was read before this ran). One
    that cannot be removed either is named in :class:`ScrubIncomplete`,
    raised after every other file has been dealt with."""
    if not secret:
        return []
    needle = secret.encode("utf-8")
    done, left = [], []
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        try:
            data = path.read_bytes()
            if needle not in data:
                continue
            path.write_bytes(data.replace(needle, REDACTED.encode("utf-8")))
        except OSError:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                left.append(rel)
                continue
        done.append(rel)
    if left:
        raise ScrubIncomplete(left)
    return done


#: How a run that passed its time cap is ended. Where the system has process
#: groups (POSIX) the run is started as its own group and the whole group is
#: killed: the launcher, the Playwright runner it started and every browser.
#: Where it has none, the process that was started is killed.
END_RUN: Any = (
    (lambda proc: os.killpg(proc.pid, signal.SIGKILL)) if hasattr(os, "killpg") else (lambda proc: proc.kill())
)


def run_tree(cmd: Any, *, timeout: Any = None, **kw: Any) -> Any:
    """``subprocess.run`` whose time cap ends EVERY process the run started.

    ``subprocess.run`` ends only the process it started; a Playwright runner
    that process had started would live on and write its report after the
    grade had scrubbed the work dir. Here the run is started as its own
    session, and when the cap passes or the wait is interrupted it is ended
    by :data:`END_RUN`. Same arguments and same result as
    ``subprocess.run``."""
    pipes = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE} if kw.pop("capture_output", False) else {}
    with subprocess.Popen(cmd, start_new_session=True, **pipes, **kw) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except BaseException:
            END_RUN(proc)
            proc.communicate()
            raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


#: The toolchain probe: one test that needs node, Playwright and a browser
#: that can launch and render -- nothing about any product.
TOOLCHAIN_TEST = "toolchain renders a page"
TOOLCHAIN_SPEC = (
    "import { test, expect } from '@playwright/test';\n"
    f"test({json.dumps(TOOLCHAIN_TEST)}, async ({{ page }}) => {{\n"
    "  await page.setContent('<p id=\"t\">rendered</p>');\n"
    "  await expect(page.locator('#t')).toHaveText('rendered');\n"
    "});\n"
)


@dataclass(frozen=True)
class ToolchainStatus:
    ok: bool
    detail: str


def toolchain_check(*, run: Any = run_tree, which: Any = shutil.which) -> ToolchainStatus:
    """Can the UI grade run HERE? Runs a one-test spec through the exact
    command and environment ``grade_ui`` uses; ok only if that test passed.
    Used at grade time and, in CI, inside the built runtime image as the app
    user -- one function, so the two can never disagree."""
    if not which("npx", path=RUN_PATH):
        return ToolchainStatus(False, f"npx is not on PATH {RUN_PATH}")
    with tempfile.TemporaryDirectory(prefix="ui_toolchain_") as tmp:
        wd = Path(tmp)
        spec = wd / "toolchain.spec.ts"
        spec.write_text(TOOLCHAIN_SPEC, encoding="utf-8")
        out = wd / "report.json"
        try:
            proc = _playwright_run(run, spec, base_url="about:blank", out=out, fixtures=wd,
                                   timeout=TOOLCHAIN_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 -- a tool that cannot start is the finding
            return ToolchainStatus(False, f"playwright could not start: {type(exc).__name__}: {_tail(exc)}")
        text = _report_text(out, proc)
        try:
            outcomes = report_outcomes(json.loads(text)) if text.strip() else {}
        except ValueError:
            outcomes = {}
    if outcomes.get(TOOLCHAIN_TEST):
        return ToolchainStatus(True, "playwright rendered a page")
    if not outcomes:
        return ToolchainStatus(False, f"playwright gave no report: {_exit_detail(proc)}")
    return ToolchainStatus(False, f"playwright could not render a page: {_exit_detail(proc)}")


def grade_ui(
    blueprint: Mapping[str, Any],
    workdir: Path | str,
    *,
    base_url: Optional[str],
    run: Any = run_tree,
    brief: Any = _UNSET,
) -> UiGrade:
    """Run the plan's steps against ``base_url``. Every step carries the
    grading boot's bearer (``grading_bearer``, the one source: no caller
    hands this function a token). The report is read before the files the
    run wrote are scrubbed of the bearer, so the scrub can never change what
    is graded; the scrub runs however the run ended."""
    _run, _skip, block = _split_plan(blueprint, brief)
    if block:
        return _unreadable_block()
    if not base_url:
        return UiGrade([], ["UI run did not happen: no base URL for the rendered product"])
    if run is run_tree:
        toolchain = toolchain_check()
        if not toolchain.ok:
            return UiGrade([], [f"UI run did not happen: UI toolchain unavailable: {toolchain.detail}"])
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    fx = write_fixtures(blueprint, wd / "fixtures", brief)
    (wd / "grade_ui.spec.ts").write_text(render_spec(blueprint, brief), encoding="utf-8")
    out = wd / "report.json"
    fixtures = fx["pdf"].parent if fx.get("pdf") else wd / "fixtures"
    token = grading_bearer()
    try:
        proc = _playwright_run(run, wd / "grade_ui.spec.ts", base_url=base_url, out=out,
                               fixtures=fixtures, timeout=RUN_TIMEOUT_S, bearer=token)
        # Read as the run wrote it: the grade is made from this text, never
        # from a file the scrub below has rewritten.
        report_text = _report_text(out, proc)
    except Exception as exc:  # noqa: BLE001 -- a run that could not start is a FAIL, recorded
        return UiGrade([], [f"UI run did not happen: {type(exc).__name__}"])
    finally:
        # Whatever ended the run (a report, a timeout, a crash, an
        # interrupt), the files it wrote are scrubbed before anything else
        # reads the work dir.
        scrub_files(wd, token)
    if not report_text.strip():
        return UiGrade([], [f"UI run did not happen: no playwright report: {_exit_detail(proc, token)}"])
    return parse_report(json.loads(report_text), blueprint, brief)
