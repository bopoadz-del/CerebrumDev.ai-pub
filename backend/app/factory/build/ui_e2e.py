"""The UI a pilot serves must work, not resemble work.

``gate_ui_surface`` checked that declared .tsx files exist and are not tiny.
That is a facade check: FinOps (sess_065fc3eac75c4f62) passed it while
shipping a React app nothing builds -- no node step in its Dockerfile, just
``COPY . .`` -- so the real UI was dead source, and the served console drove
exactly one capability.

A pilot is handed to a DevOps team to deploy and test. What it serves has to
reach the platform end to end: more than one capability, the formulas it
ships, and an answer that carries its authority label. And it must serve ONE
UI -- a second, unbuilt one is decoration.
"""

from __future__ import annotations

from app.factory.build.brief_gates import UI_END_TO_END_CHECK

import sys
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:  # pragma: no cover
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = UI_END_TO_END_CHECK
UI_NOT_WIRED = "ui_not_wired_end_to_end"
UI_NOT_BUILT = "ui_shipped_unbuilt"

#: Keys an answer may carry its authority/precedence label under.
LABEL_KEYS = ("label", "labels", "authority", "precedence", "basis", "layer")

UI_E2E_PROBE = r'''
import ast, json, os, re, sys, tempfile
os.environ["STORAGE_PATH"] = tempfile.mkdtemp(prefix="ui-gate-")
os.environ.setdefault("PLATFORM_TOKEN", "dev-local-token")
sys.path.insert(0, os.getcwd())
from pathlib import Path

findings = []
advisory = []


def finding(text):
    findings.append(text)


def _imports_formulas(path):
    """The handler imports the product's formulas module (syntax tree)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[-1] == "formulas" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "formulas" or any(a.name == "formulas" for a in node.names):
                return True
    return False


def _dockerfile_builds(directory):
    """The Dockerfile, read as instructions: the image builds ``directory``
    when a COPY/ADD brings it in (or a WORKDIR enters it) and a RUN follows."""
    path = Path("Dockerfile")
    if not path.is_file():
        return False
    entered = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line[0] == "#":
            continue
        keyword, _, rest = line.partition(" ")
        keyword = keyword.upper()
        args = [a for a in rest.split() if a[:2] != "--"]
        if keyword in ("COPY", "ADD") and any(Path(a).parts[:1] == (directory,) for a in args[:-1]):
            entered = True
        elif keyword == "WORKDIR" and args and directory in Path(args[0]).parts:
            entered = True
        elif keyword == "RUN" and entered:
            return True
    return False


page = Path("app/static/index.html")
if not page.is_file():
    finding("the platform serves no UI: app/static/index.html is missing")
    print("UI_PROBE=" + json.dumps({"findings": findings}))
    raise SystemExit(0)

html = page.read_text(encoding="utf-8", errors="replace")
paths = sorted(set(re.findall(r"/v1/[A-Za-z0-9_/\-]+", html)))
caps = sorted(
    p.stem for p in Path("app/actions").glob("*.py") if p.stem != "__init__"
) if Path("app/actions").is_dir() else []
literal = sorted({c for c in caps if any(p.rstrip("/").endswith("/" + c) for p in paths)})
# A console that asks /v1/capabilities and builds "/v1/" + id drives whatever
# the product ships -- that is better than naming two, not worse.
discovers = "/v1/capabilities" in html and any(
    marker in html for marker in ('"/v1/" +', "'/v1/' +", "/v1/${", "`/v1/${")
)
ui_caps = caps if discovers else literal

# ONE UI: a frontend that ships with a build script must be built by the image.
if Path("frontend/package.json").is_file():
    pkg = json.loads(Path("frontend/package.json").read_text(encoding="utf-8", errors="replace") or "{}")
    builds = bool((pkg.get("scripts") or {}).get("build"))
    if builds and not _dockerfile_builds("frontend"):
        advisory.append(
            "frontend/ ships with a build script and the Dockerfile never builds it: "
            "the image serves app/static/index.html while the real UI is dead source. "
            "Build it in the image, or do not ship it"
        )

from fastapi.testclient import TestClient
from app.main import app

# What the product declares it serves. A UI may reference an optional
# surface (RAG, exports) that this product does not ship; that is not a
# broken route. A DECLARED route that fails is.
declared = set()
try:
    spec = json.loads(Path("openapi.json").read_text(encoding="utf-8", errors="replace"))
    declared = set((spec.get("paths") or {}).keys())
except Exception:
    declared = set()

AUTH = {"Authorization": "Bearer " + os.environ.get("PLATFORM_TOKEN", "dev-local-token")}
answered, labelled, formula_used = [], False, False
with TestClient(app) as client:
    for path in paths:
        if "{" in path or path.rstrip("/") == "/v1":
            continue
        if declared and path not in declared:
            continue  # an optional surface this product does not ship
        try:
            resp = client.get(path, headers=AUTH)
        except Exception as exc:
            finding("the UI calls %s and it raised %s" % (path, type(exc).__name__))
            continue
        # 405 is the route saying "not with that verb" -- it exists, and the
        # UI may well be POSTing to it. Only absence and failure count.
        if resp.status_code in (404, 500, 501, 502):
            finding("the UI calls %s and the platform answers %s" % (path, resp.status_code))
            continue
        if resp.status_code == 405:
            answered.append(path)
            continue
        answered.append(path)
        try:
            body = resp.json()
        except Exception:
            body = None
        blob = json.dumps(body) if body is not None else ""
        if any('"%s"' % key in blob for key in __LABEL_KEYS__):
            labelled = True

need = min(2, len(caps)) if caps else 0
if len(ui_caps) < need:
    finding(
        "the served UI drives %d of %d capability(ies) (%s): a pilot is "
        "deployed and tested, so its UI must reach the product -- read "
        "/v1/capabilities and build the routes from it"
        % (len(ui_caps), len(caps), ", ".join(ui_caps) or "none")
    )

# The formulas the product ships must be reachable from what the UI drives.
if Path("app/formulas.py").is_file():
    for cap in ui_caps:
        handler = Path("app/actions") / (cap + ".py")
        if handler.is_file() and _imports_formulas(handler):
            formula_used = True
            break
    if ui_caps and not formula_used:
        finding(
            "app/formulas.py ships and no capability the UI drives uses it: "
            "the formula layer is not reachable from the product"
        )

if Path("app/authority.py").is_file() and answered and not labelled:
    advisory.append(
        "no answer the UI asks for carries its authority label: the reasoning "
        "layer ships and the UI cannot show which layer an answer came from"
    )

print("UI_PROBE=" + json.dumps({"findings": findings, "advisory": advisory, "answered": answered, "ui_caps": ui_caps}))
'''


def _render_probe() -> str:
    return UI_E2E_PROBE.replace("__LABEL_KEYS__", repr(LABEL_KEYS))


def render_ui_tests(specs: "dict") -> str:
    """The UI suite TESTER stamps, so the agent sees a UI failure in-pass.

    Eight suites were stamped and none touched the UI, so the agent could not
    know it had a UI problem until it had yielded and ``ui_end_to_end``
    rejected the build -- a whole rework round for something a red test shows
    in the same pass. The prompt already tells the agent to run
    ``pytest -m "not pilot"`` before yielding; this puts the UI in that run.

    It asserts ONLY what the gate already treats as fatal. The unbuilt
    frontend and the missing authority label are ``advisory`` in the probe
    above and are deliberately absent here: the bar does not move, the
    detection moves earlier. Asserting them here would raise the bar through
    the tester, which is the gate change this replaced.
    """
    return '''"""The served UI reaches the product -- the same bar ui_end_to_end holds.

Run before you yield. A failure here is a failure the gate would have found
after you yielded, at the cost of a full rework round.
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

import pytest


def _imports_formulas(path):
    """The handler imports the product's formulas module (syntax tree)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[-1] == "formulas" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "formulas" or any(a.name == "formulas" for a in node.names):
                return True
    return False


def _dockerfile_builds(directory):
    """The Dockerfile, read as instructions: the image builds ``directory``
    when a COPY/ADD brings it in (or a WORKDIR enters it) and a RUN follows."""
    path = Path("Dockerfile")
    if not path.is_file():
        return False
    entered = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line[0] == "#":
            continue
        keyword, _, rest = line.partition(" ")
        keyword = keyword.upper()
        args = [a for a in rest.split() if a[:2] != "--"]
        if keyword in ("COPY", "ADD") and any(Path(a).parts[:1] == (directory,) for a in args[:-1]):
            entered = True
        elif keyword == "WORKDIR" and args and directory in Path(args[0]).parts:
            entered = True
        elif keyword == "RUN" and entered:
            return True
    return False
from fastapi.testclient import TestClient

PAGE = Path("app/static/index.html")


def _capabilities():
    if not Path("app/actions").is_dir():
        return []
    return sorted(
        p.stem for p in Path("app/actions").glob("*.py") if p.stem != "__init__"
    )


def _html():
    if not PAGE.is_file():
        pytest.fail(
            "the platform serves no UI: app/static/index.html is missing"
        )
    return PAGE.read_text(encoding="utf-8", errors="replace")


def _ui_paths(html):
    return sorted(set(re.findall(r"/v1/[A-Za-z0-9_/\\-]+", html)))


def _driven(html):
    """Capabilities the UI reaches: named outright, or discovered at runtime."""
    caps = _capabilities()
    paths = _ui_paths(html)
    discovers = "/v1/capabilities" in html and any(
        marker in html
        for marker in ('"/v1/" +', "'/v1/' +", "/v1/${", "`/v1/${")
    )
    if discovers:
        return caps
    return sorted(
        {c for c in caps if any(p.rstrip("/").endswith("/" + c) for p in paths)}
    )


def test_the_platform_serves_a_ui():
    assert _html().strip(), "app/static/index.html is empty"


def test_the_ui_reaches_more_than_one_capability():
    caps = _capabilities()
    if not caps:
        pytest.skip("this product ships no capabilities")
    driven = _driven(_html())
    need = min(2, len(caps))
    assert len(driven) >= need, (
        "the served UI drives %d of %d capability(ies) (%s): read "
        "/v1/capabilities and build the routes from it"
        % (len(driven), len(caps), ", ".join(driven) or "none")
    )


def test_every_route_the_ui_calls_answers():
    from app.main import app

    html = _html()
    declared = set()
    spec = Path("openapi.json")
    if spec.is_file():
        try:
            declared = set(
                (json.loads(spec.read_text(encoding="utf-8")).get("paths") or {}).keys()
            )
        except Exception:
            declared = set()

    token = os.environ.get("PLATFORM_TOKEN", "dev-local-token")
    auth = {"Authorization": "Bearer " + token}
    dead = []
    with TestClient(app) as client:
        for path in _ui_paths(html):
            if "{" in path or path.rstrip("/") == "/v1":
                continue
            if declared and path not in declared:
                continue  # an optional surface this product does not ship
            try:
                resp = client.get(path, headers=auth)
            except Exception as exc:
                dead.append("%s raised %s" % (path, type(exc).__name__))
                continue
            # 405 means the route exists and wants another verb.
            if resp.status_code in (404, 500, 501, 502):
                dead.append("%s answered %d" % (path, resp.status_code))
    assert not dead, "the UI calls routes the platform does not answer: %s" % dead


def test_the_formulas_are_reachable_from_the_ui():
    if not Path("app/formulas.py").is_file():
        pytest.skip("this product ships no formula layer")
    driven = _driven(_html())
    if not driven:
        pytest.skip("no capability is driven from the UI yet")
    for cap in driven:
        handler = Path("app/actions") / (cap + ".py")
        if handler.is_file() and _imports_formulas(handler):
            return
    pytest.fail(
        "app/formulas.py ships and no capability the UI drives uses it: "
        "the formula layer is not reachable from the product"
    )
'''


def gate_ui_end_to_end(ctx: "GateContext") -> "GateResult":
    """The served UI reaches the platform, and the pilot serves one UI."""
    from app.factory.build.gates import GateResult

    if not (ctx.workspace / "app" / "main.py").is_file():
        return GateResult(
            ok=True,
            gate=GATE_NAME,
            detail="no app to serve a UI from — nothing claimed, nothing owed",
        )

    proc = ctx.run([sys.executable, "-c", _render_probe()])
    line = [
        ln for ln in (proc.stdout or "").splitlines() if ln.startswith("UI_PROBE=")
    ]
    if not line:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason=UI_NOT_WIRED,
            detail=(
                "the UI probe did not report: the product did not boot far "
                "enough to serve its own console"
            ),
            findings=[(proc.stderr or "").strip().splitlines()[-1][:200]] if proc.stderr else [],
        )

    import json as _json

    payload = _json.loads(line[-1].split("=", 1)[1])
    findings: List[str] = list(payload.get("findings") or [])
    # Real, reported, and not yet fatal: the unbuilt frontend needs a node
    # stage in the emitted Dockerfile, and the authority label needs the
    # routes to carry one. Both are owed; neither is the writer's to fix in
    # one pass, and failing on them today would stop every build.
    advisory: List[str] = list(payload.get("advisory") or [])
    if findings:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason=UI_NOT_WIRED,
            detail=findings[0],
            findings=findings + advisory,
            payload={"answered": payload.get("answered") or [], "advisory": advisory},
        )
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail=(
            "the served UI drives %d capability(ies) and every route it calls answers"
            % len(payload.get("ui_caps") or [])
        ),
        findings=advisory,
        payload={"answered": payload.get("answered") or [], "advisory": advisory},
    )
