"""F-8b -- Layer-1 UI grader (Playwright, headless).

The UI script is GENERATED from the product's own blueprint: the screens it
declares (``ui_modules``) and the routes its capabilities imply
(``/v1/<capability_id>``). Fixtures (a PDF and an XLSX) are written from the
blueprint's own summary and capability text -- nothing canned.

Steps (ORDERS_1730Z Layer 1): app loads; login/tenant bind; chat-box "+"
attach a real PDF and XLSX; parsed status appears; a cited answer with a
number renders; an invalid request is answered honestly with 422/503 (never
a 200); every declared route returns 200 and renders with no console errors.

No base URL, no Playwright, or no report = the UI run did not happen = FAIL.
A skipped UI run is never a pass.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

RUN_TIMEOUT_S = 600

#: The fixed step kinds every product must pass. Their TARGETS come from
#: the blueprint; the kinds are the owner's Layer-1 list.
STEP_KINDS = ("load", "bind", "attach_pdf", "attach_xlsx", "parsed", "cited_answer", "honest_error")


@dataclass(frozen=True)
class UiStep:
    name: str
    kind: str
    target: str


@dataclass
class UiGrade:
    steps: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

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
        return {"ok": self.ok, "ran": self.ran, "failing": self.failing, "steps": self.steps}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(text).lower()).strip("_")


def ui_plan(blueprint: Mapping[str, Any]) -> List[UiStep]:
    caps = [str(c.get("id")) for c in blueprint.get("capabilities") or [] if c.get("id")]
    first = caps[0] if caps else ""
    steps = [
        UiStep("load:/", "load", "/"),
        UiStep("bind:tenant", "bind", str(blueprint.get("product_id") or "")),
        UiStep("attach:pdf", "attach_pdf", "fixture.pdf"),
        UiStep("attach:xlsx", "attach_xlsx", "fixture.xlsx"),
        UiStep("parsed:status", "parsed", "fixture.pdf"),
        UiStep("answer:cited", "cited_answer", first),
        UiStep(f"honest_error:/v1/{first}", "honest_error", f"/v1/{first}"),
    ]
    for mod in blueprint.get("ui_modules") or []:
        steps.append(UiStep(f"screen:{_slug(mod)}", "screen", f"/#/{_slug(mod)}"))
    for cid in caps:
        steps.append(UiStep(f"route:/v1/{cid}", "route", f"/v1/{cid}"))
    return steps


def _question(blueprint: Mapping[str, Any]) -> str:
    caps = blueprint.get("capabilities") or [{}]
    return f"What does the attached document say about {str(caps[0].get('description') or '').split('.')[0][:120]}?"


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


def write_fixtures(blueprint: Mapping[str, Any], dest: Path) -> Dict[str, Path]:
    dest.mkdir(parents=True, exist_ok=True)
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


def render_spec(blueprint: Mapping[str, Any]) -> str:
    """The Playwright test file. One ``test`` per plan step, titled by name.
    A step whose kind has no body is a hard error, never an empty pass."""
    plan = ui_plan(blueprint)
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
        f"const QUESTION = {json.dumps(_question(blueprint))};\n"
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


def parse_report(report: Mapping[str, Any], blueprint: Mapping[str, Any]) -> UiGrade:
    """Playwright JSON reporter -> UiGrade. A planned step absent from the
    report is a failed step (a skipped step is not a passed one)."""
    seen: Dict[str, bool] = {}

    def walk(suite: Mapping[str, Any]) -> None:
        for spec in suite.get("specs") or []:
            results = [r for t in spec.get("tests") or [] for r in t.get("results") or []]
            seen[str(spec.get("title"))] = step_passed(results)
        for child in suite.get("suites") or []:
            walk(child)

    for s in report.get("suites") or []:
        walk(s)
    steps = [
        {"name": st.name, "kind": st.kind, "target": st.target, "passed": seen.get(st.name, False)}
        for st in ui_plan(blueprint)
    ]
    errors = [] if seen else ["playwright report has no results"]
    return UiGrade(steps, errors)


def grade_ui(
    blueprint: Mapping[str, Any],
    workdir: Path | str,
    *,
    base_url: Optional[str],
    run: Any = subprocess.run,
) -> UiGrade:
    if not base_url:
        return UiGrade([], ["UI run did not happen: no base URL for the rendered product"])
    if run is subprocess.run and not shutil.which("npx"):
        return UiGrade([], ["UI run did not happen: npx/playwright not available"])
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    fx = write_fixtures(blueprint, wd / "fixtures")
    (wd / "grade_ui.spec.ts").write_text(render_spec(blueprint), encoding="utf-8")
    out = wd / "report.json"
    try:
        run(
            ["npx", "playwright", "test", str(wd / "grade_ui.spec.ts"), "--reporter=json"],
            cwd=str(wd),
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "BASE_URL": base_url,
                 "PLAYWRIGHT_JSON_OUTPUT_NAME": str(out), "GRADE_FIXTURES": str(fx["pdf"].parent)},
            timeout=RUN_TIMEOUT_S,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # noqa: BLE001 -- a run that could not start is a FAIL, recorded
        return UiGrade([], [f"UI run did not happen: {type(exc).__name__}"])
    if not out.is_file():
        return UiGrade([], ["UI run did not happen: no playwright report"])
    return parse_report(json.loads(out.read_text("utf-8")), blueprint)
