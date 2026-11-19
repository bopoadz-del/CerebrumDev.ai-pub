"""F-8b -- Layer-1 UI grader (Playwright, headless).

The UI script is GENERATED from the product's own blueprint: the screens it
declares (``ui_modules``) and the routes its capabilities imply
(``/v1/<capability_id>``). Fixtures (a PDF and an XLSX) are written from the
blueprint's own summary and capability text -- nothing canned.

Steps (ORDERS_1730Z Layer 1): app loads; login/tenant bind; chat-box "+"
attach a real PDF and XLSX; parsed status appears; a cited answer with a
number renders; an invalid request is answered honestly with 422/503 (never
a 200); every declared route returns 200 and renders with no console errors.

Which of those steps run is read from the brief. A step kind declares the
subject it grades (``KIND_SUBJECT``); the step runs only when the brief
contains that subject. A kind with no subject is the product surface the
blueprint declares and always runs. A step the brief does not contain is
not run and is reported not applicable — never passed, never a failure.
A brief that cannot be read keeps every step, and the grade says so.

No base URL, no Playwright, or no report = the UI run did not happen = FAIL.
A skipped UI run is never a pass.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

from app.factory.build.authority import capability_route

RUN_TIMEOUT_S = 600

#: The PATH a UI run sees. One value, read by the grade and by the toolchain
#: check, so the check proves exactly the path the grade will take.
RUN_PATH = "/usr/bin:/bin:/usr/local/bin"
TOOLCHAIN_TIMEOUT_S = 120
#: How much of a failed tool's output a recorded reason keeps.
OUTPUT_TAIL_CHARS = 400

#: The fixed step kinds. Their TARGETS come from the blueprint. Whether a
#: kind runs is the brief's (``KIND_SUBJECT``); a kind with no subject is the
#: product surface and always runs.
STEP_KINDS = ("load", "bind", "attach_pdf", "attach_xlsx", "parsed", "cited_answer", "honest_error")

#: Not a pass and not a failure: the brief does not contain this step.
NOT_APPLICABLE = "not_applicable"

#: Said on the grade when no customer brief could be read. Every step is
#: kept — a missing brief must not silently drop a check.
BRIEF_UNREADABLE_NOTE = "brief could not be read; every UI step was kept"

_UNSET: Any = object()

#: A step kind runs only when the brief contains this subject. Kinds absent
#: from the map are the product surface (load, bind, screens, routes, the
#: honest error) and always run. One subject, one rule, for every kind.
KIND_SUBJECT: Dict[str, str] = {
    "attach_pdf": "documents",
    "attach_xlsx": "documents",
    "parsed": "documents",
    "cited_answer": "documents",
}

#: Whole-token vocabulary of a subject. Membership is a contract check, the
#: same way a document suffix is: the token is the subject, never a product,
#: a client or a probe. Plurals are folded by one trailing "s".
_SUBJECT_WORDS: Dict[str, FrozenSet[str]] = {
    "documents": frozenset({
        "document",
        "spreadsheet",
        "citation",
        "cited",
        "cite",
    }),
}

#: Headings of the compiled writer brief, matched as whole lines. Customer
#: sections are the brief; the Factory contract beside them is not, so a
#: sentence the contract always carries cannot turn a step on.
_BRIEF_HEADINGS = frozenset({"USER BRIEF", "COMPILED C-BRIEF", "CAPABILITIES"})
_CUSTOMER_HEADINGS = ("USER BRIEF", "CAPABILITIES")
#: The contract's own heading line (BRIEF_TEMPLATE). A text that is this
#: contract, with no customer section, is not a brief.
_CONTRACT_HEADING = "CUT 1 — STEP 0 INVENTORY + STOP"

#: Blueprint fields that are the customer's own ask. Identity fields are not
#: among them: a product's name is not the brief.
_NARRATIVE_FIELDS = ("description", "narrative", "notes", "summary")


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
    def failing(self) -> List[str]:
        return [s["name"] for s in self.steps if not s.get("passed")] + list(self.errors)

    @property
    def ok(self) -> bool:
        return self.ran and not self.failing

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "ok": self.ok, "ran": self.ran, "failing": self.failing, "steps": self.steps,
        }
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


def _subject_vocab(subject: str) -> FrozenSet[str]:
    words = set(_SUBJECT_WORDS.get(subject, ()))
    if subject == "documents":
        # The grader's own document-suffix contract: a token shaped like a
        # file of one of those suffixes IS the subject.
        from app.factory.build.grader import _DOC_EXTENSIONS

        words |= {ext[1:].lower() for ext in _DOC_EXTENSIONS if ext.startswith(".") and len(ext) > 1}
    return frozenset(words)


def _brief_tokens(text: str) -> set:
    found: set = set()
    for raw in re.findall(r"[A-Za-z0-9_.:/-]+", text or ""):
        token = raw.strip(".:/-_").lower()
        if not token:
            continue
        found.add(token)
        marker, sep, rest = token.partition(":")
        if sep and marker == "document" and rest.strip(".:/-_"):
            found.add("document")
        suffix = Path(token).suffix.lower()
        if suffix.startswith(".") and suffix[1:].isalnum():
            found.add(suffix[1:])
        if len(token) > 3 and token.endswith("s"):
            found.add(token[:-1])
    return found


def _subject_present(text: str, subject: str) -> bool:
    vocab = _subject_vocab(subject)
    return bool(vocab) and bool(_brief_tokens(text) & vocab)


def _sections(text: str) -> Dict[str, str]:
    """Bodies under the compiled brief's own headings, keyed by heading.

    A heading counts only as a whole line equal to one of ``_BRIEF_HEADINGS``.
    """
    sections: Dict[str, List[str]] = {}
    current = ""
    for line in text.splitlines():
        name = line.strip()
        if name in _BRIEF_HEADINGS:
            current = name
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _customer_from_text(text: str) -> List[str]:
    """The customer's own words in ``text``.

    A compiled writer brief carries the Factory contract beside those words.
    The contract is the Factory's instructions; a sentence it always carries
    is not the customer asking for a subject. A string with no brief heading
    and no contract heading is the brief itself.
    """
    stripped = (text or "").strip()
    if not stripped:
        return []
    from app.factory.build.architect import NARRATIVE_SEPARATOR

    narrative, sep, _contract = stripped.partition(NARRATIVE_SEPARATOR)
    if sep:
        stripped = narrative.strip()
        if not stripped:
            return []
    sections = _sections(stripped)
    if sections:
        return [sections[name] for name in _CUSTOMER_HEADINGS if sections.get(name, "").strip()]
    lines = {line.strip() for line in stripped.splitlines()}
    if _CONTRACT_HEADING in lines:
        return []
    return [stripped]


def _brief_chunks(brief: Any) -> List[str]:
    """Customer text carried by ``brief``, or nothing when it cannot be read."""
    if brief is _UNSET or brief is None:
        return []
    if isinstance(brief, str):
        return _customer_from_text(brief)
    if isinstance(brief, Mapping):
        chunks: List[str] = []
        for value in brief.values():
            if isinstance(value, str):
                chunks.extend(_customer_from_text(value))
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str):
                        chunks.extend(_customer_from_text(item))
        return chunks
    text = getattr(brief, "text", None)
    if isinstance(text, str):
        return _customer_from_text(text)
    return []


def _read_brief(brief: Any) -> Tuple[Optional[str], str]:
    """``(customer text, note)``. Text is None when the brief cannot be read;
    the note is what the grade says, and every step is then kept."""
    chunks = _brief_chunks(brief)
    if not chunks:
        return None, BRIEF_UNREADABLE_NOTE
    seen: List[str] = []
    for chunk in chunks:
        if chunk not in seen:
            seen.append(chunk)
    return "\n".join(seen), ""


def _cap_asks(cap: Any) -> List[str]:
    if isinstance(cap, Mapping):
        description = cap.get("description")
        requirements = cap.get("requirements") or []
    else:
        description = getattr(cap, "description", None)
        requirements = getattr(cap, "requirements", None) or []
    out: List[str] = []
    if isinstance(description, str) and description.strip():
        out.append(description.strip())
    for req in requirements:
        if isinstance(req, str) and req.strip():
            out.append(req.strip())
    return out


def _blueprint_chunks(blueprint: Any) -> List[str]:
    bp = _as_mapping(blueprint)
    chunks: List[str] = []
    for key in _NARRATIVE_FIELDS:
        value = bp.get(key)
        if isinstance(value, str):
            chunks.extend(_customer_from_text(value))
    for cap in bp.get("capabilities") or []:
        for ask in _cap_asks(cap):
            chunks.extend(_customer_from_text(ask))
    return chunks


def brief_for_steps(
    blueprint: Any,
    *,
    supplied: Any = None,
    compiled: Any = None,
) -> Optional[str]:
    """The customer brief UI steps are derived from, or None when it cannot
    be read (the grade then keeps every step and says so).

    The supplied brief, the blueprint's own narrative and capability asks,
    and a compiled brief's customer sections. The Factory contract compiled
    beside them is not the brief.
    """
    chunks = _brief_chunks(supplied) + _blueprint_chunks(blueprint) + _brief_chunks(compiled)
    seen: List[str] = []
    for chunk in chunks:
        if chunk not in seen:
            seen.append(chunk)
    return "\n".join(seen) if seen else None


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


def _step_applies(kind: str, text: Optional[str]) -> bool:
    """True when this kind runs. No readable brief → every kind (strict).
    A kind with no subject is the product surface and always runs. Any other
    kind runs only when the brief contains its subject."""
    if text is None:
        return True
    subject = KIND_SUBJECT.get(kind)
    if not subject:
        return True
    return _subject_present(text, subject)


def _split_plan(
    blueprint: Mapping[str, Any], brief: Any
) -> Tuple[List[UiStep], List[UiStep], str]:
    text, note = _read_brief(brief)
    run: List[UiStep] = []
    skip: List[UiStep] = []
    for step in _candidate_steps(blueprint):
        (run if _step_applies(step.kind, text) else skip).append(step)
    return run, skip, note if text is None else ""


def ui_plan(blueprint: Mapping[str, Any], brief: Any = _UNSET) -> List[UiStep]:
    """The steps that will be run. A brief that cannot be read returns every
    step; a step whose subject the brief does not contain is left out."""
    run, _skip, _note = _split_plan(blueprint, brief)
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
BASE_JS = (
    "const BASE = process.env.BASE_URL;\n"
    "if (!BASE) throw new Error('BASE_URL is not set: UI run cannot happen');\n"
    "const url = (t: string) => new URL(t, BASE).toString();\n"
)

#: An invalid request must be answered honestly. A 200 is never honest.
HONEST_ERROR_STATUSES = (422, 503)

#: Per-kind Playwright body. Kept as data so every kind's assertion is
#: inspectable (and testable) on its own.
_PAGE_OK = (
    "const r = await page.goto(url(s.target)); expect(r?.status()).toBe(200);\n"
    "await page.waitForLoadState('networkidle'); expect(errs).toEqual([]);"
)
KIND_JS: Dict[str, str] = {
    "load": _PAGE_OK,
    "screen": _PAGE_OK,
    "route": "const r = await request.get(url(s.target)); expect(r.status()).toBe(200);",
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
        "const r = await request.post(url(s.target), { data: '\"not-an-object\"', headers: { 'content-type': 'application/json' } });\n"
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
        f"const QUESTION = {json.dumps(_question(blueprint, brief))};\n"
        + BASE_JS +
        "const errs: string[] = [];\n"
        "test.describe.configure({ mode: 'serial' });\n"
        "let page: any;\n"
        "test.beforeAll(async ({ browser }) => { page = await browser.newPage();\n"
        "  page.on('console', (m: any) => { if (m.type() === 'error') errs.push(m.text()); });\n"
        "  page.on('pageerror', (e: any) => errs.push(String(e))); });\n"
        "for (const s of PLAN) {\n"
        "  test(s.name, async ({ request }) => {\n"
        "    errs.length = 0;\n"
        + branches +
        "    } else { throw new Error('no assertion for ' + s.kind); }\n"
        "  });\n"
        "}\n"
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


def parse_report(
    report: Mapping[str, Any], blueprint: Mapping[str, Any], brief: Any = _UNSET
) -> UiGrade:
    """Playwright JSON reporter -> UiGrade. A planned step absent from the
    report is a failed step (a skipped step is not a passed one). A step the
    brief does not contain is not applicable: it was not run, it is not
    passed, and it is not a failure. A brief that cannot be read keeps every
    step and says so."""
    seen = report_outcomes(report)
    run, skip, note = _split_plan(blueprint, brief)
    steps = [
        {"name": st.name, "kind": st.kind, "target": st.target, "passed": seen.get(st.name, False)}
        for st in run
    ]
    errors = [] if seen else ["playwright report has no results"]
    return UiGrade(steps, errors, not_applicable=[_not_applicable_row(st) for st in skip], brief_note=note)


def _tail(text: Any) -> str:
    return " ".join(str(text or "").split())[-OUTPUT_TAIL_CHARS:]


def _playwright_run(run: Any, spec: Path, *, base_url: str, out: Path, fixtures: Path, timeout: int) -> Any:
    """The one way a UI spec is run: the grade and the toolchain check both
    come through here, with the same command and the same environment."""
    return run(
        ["npx", "playwright", "test", str(spec), "--reporter=json"],
        cwd=str(spec.parent),
        env={"PATH": RUN_PATH, "BASE_URL": base_url,
             "PLAYWRIGHT_JSON_OUTPUT_NAME": str(out), "GRADE_FIXTURES": str(fixtures)},
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


def _exit_detail(proc: Any) -> str:
    return f"exit {getattr(proc, 'returncode', None)}; stderr: {_tail(getattr(proc, 'stderr', ''))}"


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


def toolchain_check(*, run: Any = subprocess.run, which: Any = shutil.which) -> ToolchainStatus:
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
    run: Any = subprocess.run,
    brief: Any = _UNSET,
) -> UiGrade:
    if not base_url:
        return UiGrade([], ["UI run did not happen: no base URL for the rendered product"])
    if run is subprocess.run:
        toolchain = toolchain_check()
        if not toolchain.ok:
            return UiGrade([], [f"UI run did not happen: UI toolchain unavailable: {toolchain.detail}"])
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    fx = write_fixtures(blueprint, wd / "fixtures", brief)
    (wd / "grade_ui.spec.ts").write_text(render_spec(blueprint, brief), encoding="utf-8")
    out = wd / "report.json"
    fixtures = fx["pdf"].parent if fx.get("pdf") else wd / "fixtures"
    try:
        proc = _playwright_run(run, wd / "grade_ui.spec.ts", base_url=base_url, out=out,
                               fixtures=fixtures, timeout=RUN_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 -- a run that could not start is a FAIL, recorded
        return UiGrade([], [f"UI run did not happen: {type(exc).__name__}"])
    report_text = _report_text(out, proc)
    if not report_text.strip():
        return UiGrade([], [f"UI run did not happen: no playwright report: {_exit_detail(proc)}"])
    return parse_report(json.loads(report_text), blueprint, brief)
