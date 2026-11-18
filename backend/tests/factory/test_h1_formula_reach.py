"""H-1 (advisor 02:16Z, grader 02:25Z): formula reach FROM the product's
entry points, on invented products written to disk.

Every product here is invented for the test. The record is read through
``formula_reach`` and through the writer's audit (``audit_product_formulas``),
the path a real build takes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build import formula_resolution
from app.factory.build.formula_reach import formula_reach

_SERVER = {
    "Dockerfile": 'FROM python:3.11-slim\nCMD ["uvicorn", "app.main:app", "--port", "8000"]\n'
}
_FORMULAS = {
    "app/formulas.py": "def alpha(x):\n    return x * 2\n\n\ndef beta(x):\n    return x + 1\n"
}
_MAIN = {
    "app/__init__.py": "",
    "app/routers/__init__.py": "",
    "app/main.py": (
        "from fastapi import FastAPI\n"
        "from app.routers import quotes\n\n"
        "app = FastAPI()\n"
        "app.include_router(quotes.router)\n"
    ),
}


def _route(body: str, imports: str = "from app import formulas\n") -> dict:
    return {
        "app/routers/quotes.py": (
            "from fastapi import APIRouter\n"
            + imports
            + "\nrouter = APIRouter()\n\n\n"
            + '@router.get("/quote")\n'
            + "def quote(x: float, cfg=None):\n"
            + body
        )
    }


def _write(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


_OLD_ROUTER = (
    "from fastapi import APIRouter\nfrom app import formulas\n\n"
    "router = APIRouter()\n\n\ndef helper():\n    return 0\n\n\n"
    "@router.get('/old')\ndef old_route():\n    return formulas.beta(1)\n"
)

_CASES = {
    "direct_call_from_a_route": (
        _route("    return {'v': formulas.alpha(x)}\n"),
        ["app.formulas:alpha"],
        ["app.formulas:beta"],
    ),
    "through_a_helper": (
        {
            **_route("    return {'v': price(x)}\n", "from app.services import price\n"),
            "app/services.py": (
                "from app import formulas\n\n\ndef price(x):\n    return formulas.beta(x)\n"
            ),
        },
        ["app.formulas:beta"],
        ["app.formulas:alpha"],
    ),
    "through_a_re_export": (
        {
            **_route("    return {'v': beta(x)}\n", "from app.calc import beta\n"),
            "app/calc/__init__.py": "from app.formulas import beta\n\n__all__ = ['beta']\n",
        },
        ["app.formulas:beta"],
        ["app.formulas:alpha"],
    ),
    "passed_on_not_called": (
        _route("    return {'v': list(map(formulas.alpha, [x]))}\n"),
        ["app.formulas:alpha"],
        ["app.formulas:beta"],
    ),
    "imported_but_never_called_from_an_entry": (
        {
            **_route("    return {'v': x}\n", "from app.formulas import alpha, beta\n"),
            "app/services.py": (
                "from app.formulas import alpha\n\n\ndef unused():\n    return alpha(1)\n"
            ),
        },
        [],
        ["app.formulas:alpha", "app.formulas:beta"],
    ),
    "named_only_in_a_dead_module": (
        {
            **_route("    return {'v': formulas.alpha(x)}\n"),
            "app/legacy.py": "from app.formulas import beta\n\n\ndef old():\n    return beta(1)\n",
            "app/routers/old.py": _OLD_ROUTER,
        },
        ["app.formulas:alpha"],
        ["app.formulas:beta"],
    ),
    "a_route_module_imported_but_its_router_never_included": (
        {
            **_route(
                "    return {'v': formulas.alpha(x) + old.helper()}\n",
                "from app import formulas\nfrom app.routers import old\n",
            ),
            "app/routers/old.py": _OLD_ROUTER,
        },
        ["app.formulas:alpha"],
        ["app.formulas:beta"],
    ),
    "a_name_collision_with_an_unrelated_attribute": (
        _route("    beta = 3\n    return {'v': formulas.alpha(x) + cfg.beta + beta}\n"),
        ["app.formulas:alpha"],
        ["app.formulas:beta"],
    ),
    "all_wired": (
        _route("    return {'v': formulas.alpha(x) + formulas.beta(x)}\n"),
        ["app.formulas:alpha", "app.formulas:beta"],
        [],
    ),
    "a_formula_calling_a_formula": (
        {
            **_route("    return {'v': formulas.alpha(x)}\n"),
            "app/formulas.py": (
                "def alpha(x):\n    return beta(x) * 2\n\n\ndef beta(x):\n    return x + 1\n"
            ),
        },
        ["app.formulas:alpha", "app.formulas:beta"],
        [],
    ),
}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_reach_is_walked_from_the_entry_points(tmp_path, case):
    files, reached, unreached = _CASES[case]
    root = _write(tmp_path, {**_SERVER, **_MAIN, **_FORMULAS, **files})
    record = formula_reach(root)
    assert record["status"] == "analysed", record
    assert record["analysis_error"] is None
    assert record["reached"] == reached
    assert record["unreached"] == unreached
    assert record["undecided"] == []
    assert "target app.main:app" in record["entry_points"]


@pytest.mark.parametrize(
    "body",
    [
        "    return {'v': getattr(formulas, cfg)(x)}\n",
        "    return {'v': vars(formulas)[cfg](x)}\n",
    ],
)
def test_string_dispatch_is_undecided_never_reached(tmp_path, body):
    root = _write(tmp_path, {**_SERVER, **_MAIN, **_FORMULAS, **_route(body)})
    record = formula_reach(root)
    assert record["status"] == "analysed"
    assert record["reached"] == []
    assert record["unreached"] == []
    assert [u["formula"] for u in record["undecided"]] == [
        "app.formulas:alpha",
        "app.formulas:beta",
    ]
    assert all("app.routers.quotes" in u["why"] for u in record["undecided"])


@pytest.mark.parametrize(
    ("name", "expected"), [("'alpha'", ["app.formulas:alpha"]), ("'gamma'", [])]
)
def test_getattr_with_a_literal_name_is_decided(tmp_path, name, expected):
    body = f"    return {{'v': getattr(formulas, {name}, None)}}\n"
    record = formula_reach(_write(tmp_path, {**_SERVER, **_MAIN, **_FORMULAS, **_route(body)}))
    assert record["reached"] == expected
    assert record["undecided"] == []


@pytest.mark.parametrize(
    ("files", "error"),
    [
        (
            {"app/formulas.py": "def alpha(:\n    pass\n"},
            "product module does not parse: app/formulas.py",
        ),
        ({"app/formulas.py": None}, "no formula module"),
        ({"Dockerfile": None, "app/main.py": "app = object()\n"}, "no entry point found"),
        ({"app/services.py": "def broken(:\n"}, "product module does not parse: app/services.py"),
    ],
)
def test_the_analysis_failing_is_unknown_never_clean(tmp_path, files, error):
    """Corrects #257's first slice, which pinned a missing or unparseable
    formula module as an empty (clean) list."""
    merged = {**_SERVER, **_MAIN, **_FORMULAS, **_route("    return formulas.alpha(x)\n")}
    merged.update(files)
    root = _write(tmp_path, {k: v for k, v in merged.items() if v is not None})
    record = formula_reach(root)
    assert record["status"] == "unknown"
    assert error in record["analysis_error"]
    assert record["unreached"] is None and record["reached"] is None


@pytest.mark.parametrize(
    ("files", "entry"),
    [
        (
            {
                "app/cli.py": (
                    "from app import formulas\n\n"
                    "if __name__ == '__main__':\n    print(formulas.beta(1))\n"
                )
            },
            "main guard app.cli",
        ),
        (
            {
                "pyproject.toml": '[project.scripts]\nquote = "app.cli:main"\n',
                "app/cli.py": "from app import formulas\n\n\ndef main():\n    print(formulas.beta(1))\n",
            },
            "target app.cli:main",
        ),
        (
            {
                "Procfile": "worker: python -m app.worker\n",
                "app/worker/__init__.py": "",
                "app/worker/__main__.py": "from app.formulas import beta\n\nbeta(2)\n",
            },
            "run app.worker",
        ),
        (
            {
                "Procfile": "release: python ./app/seed.py --once\n",
                "app/seed.py": "from app.formulas import beta\n\nbeta(3)\n",
            },
            "run app.seed",
        ),
    ],
)
def test_a_cli_entry_is_discovered_from_packaging_or_a_main_guard(tmp_path, files, entry):
    root = _write(tmp_path, {**_FORMULAS, "app/__init__.py": "", **files})
    record = formula_reach(root)
    assert record["status"] == "analysed", record
    assert entry in record["entry_points"]
    assert record["reached"] == ["app.formulas:beta"]
    assert record["unreached"] == ["app.formulas:alpha"]


@pytest.mark.parametrize(("started", "expected"), [(True, ["app.formulas:beta"]), (False, [])])
def test_a_scheduled_job_is_an_entry_once_the_product_starts_its_scheduler(
    tmp_path, started, expected
):
    jobs = (
        "from apscheduler.schedulers.background import BackgroundScheduler\n"
        "from app import formulas\n\nscheduler = BackgroundScheduler()\n\n\n"
        "@scheduler.scheduled_job('interval', hours=24)\ndef nightly():\n    formulas.beta(1)\n"
    )
    main = (
        _MAIN["app/main.py"]
        + "from app import jobs\n"
        + ("jobs.scheduler.start()\n" if started else "")
    )
    files = {
        **_SERVER,
        **_MAIN,
        **_FORMULAS,
        **_route("    return x\n"),
        "app/jobs.py": jobs,
        "app/main.py": main,
    }
    record = formula_reach(_write(tmp_path, files))
    assert record["reached"] == expected


@pytest.mark.parametrize(
    ("location", "imports", "call", "label"),
    [
        (
            "app/formulas/rates.py",
            "from app.formulas import rates\n",
            "rates.beta(x)",
            "app.formulas.rates:beta",
        ),
        (
            "app/formulas/__init__.py",
            "from app import formulas\n",
            "formulas.beta(x)",
            "app.formulas:beta",
        ),
    ],
)
def test_formulas_are_found_where_the_emitter_puts_them(tmp_path, location, imports, call, label):
    files = {
        **_SERVER,
        **_MAIN,
        **_route(f"    return {call}\n", imports),
        location: "def beta(x):\n    return x\n\n\ndef unused(x):\n    return x\n",
    }
    record = formula_reach(_write(tmp_path, files))
    assert record["status"] == "analysed", record
    assert location in record["formula_modules"]
    assert record["reached"] == [label]
    assert record["unreached"] == [label.replace(":beta", ":unused")]


@pytest.mark.parametrize(("wired", "unreached"), [(True, []), (False, ["app.formulas:beta"])])
def test_the_writer_audit_records_the_reach(tmp_path, wired, unreached):
    body = (
        "    return formulas.alpha(x) + formulas.beta(x)\n"
        if wired
        else "    return formulas.alpha(x)\n"
    )
    root = _write(tmp_path, {**_SERVER, **_MAIN, **_FORMULAS, **_route(body)})
    assert formula_resolution.audit_product_formulas(root, [], []) == []
    rec = json.loads((root / formula_resolution.FORMULA_FINDINGS_REL).read_text(encoding="utf-8"))
    assert rec["errors"] == []
    assert rec["reach"]["status"] == "analysed"
    assert rec["reach"]["unreached"] == unreached
