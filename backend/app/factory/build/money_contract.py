"""A platform that computes money computes it for the locale the USER declared.

FinOps (sess_065fc3eac75c4f62) was a finance platform for a Dubai business.
Its brief named no country, and the coding agent hardcoded UK VAT
(``STANDARD_VAT_RATE = 0.2``) and GBP -- so every net/VAT split it computed
was wrong for the UAE.

The locale is a typed answer, never a reading of prose:

* The Floor asks the user for a country (ISO 3166 alpha-2) and a currency
  (ISO 4217), validated by SHAPE only. They live on the session's product
  design and on the blueprint (``blueprint.locale``).
* The Factory stamps ``docs/declared_locale.json`` (the declared pair) and
  ``app/money_settings.py`` into every workspace. The settings module serves
  country / currency / tax rate at RUN time -- deploy environment first, the
  declared record second -- so no currency, country or rate is ever a value
  in Factory or generated code.
* A model declares which of its fields are money (``MONEY_FIELDS``, emitted
  from the field's declared type).

The gate (writer_contract) decides on the syntax tree:

* no money fields and no declared currency -> not applicable;
* money fields but no declared currency -> WITHHELD("no currency declared");
  never a frozen guess;
* declared -> FAIL when a module carries the declared currency or country
  code as a literal, or scales a money value by a numeric literal other than
  the identities 0 and 1 (a tax / fx / discount rate typed into code), or by
  any factor that is not taken from ``app.money_settings``.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set

MONEY_ASSUMED = "money_assumed_without_a_brief"

#: Where the declared pair lives in a product workspace (data, not code).
DECLARED_LOCALE_REL = Path("docs") / "declared_locale.json"
#: The run-time accessor every money path reads.
MONEY_SETTINGS_REL = Path("app") / "money_settings.py"
MONEY_SETTINGS_MODULE = "app.money_settings"
#: Deploy-time settings the operator sets (names only; values never here).
COUNTRY_ENV = "PLATFORM_COUNTRY"
CURRENCY_ENV = "PLATFORM_CURRENCY"
TAX_RATE_ENV = "PLATFORM_TAX_RATE"
#: Every other rate the customer owns (a fee, an fx rate, a discount, a
#: commission) is a named operator setting: ``PLATFORM_RATE_<NAME>``. The
#: product names the rate; the Factory names none.
RATE_ENV_PREFIX = "PLATFORM_RATE_"

WITHHELD_NO_CURRENCY = "no currency declared"

#: ISO shapes. Shape, not membership: no country or currency is listed.
_COUNTRY_SHAPE = re.compile(r"[A-Z]{2}")
_CURRENCY_SHAPE = re.compile(r"[A-Z]{3}")

#: Factors that do not change an amount's scale (identity / zeroing).
_IDENTITY_FACTORS = frozenset({0, 1})


def shaped_country(raw: Any) -> Optional[str]:
    value = str(raw or "").strip().upper()
    return value if _COUNTRY_SHAPE.fullmatch(value) else None


def shaped_currency(raw: Any) -> Optional[str]:
    value = str(raw or "").strip().upper()
    return value if _CURRENCY_SHAPE.fullmatch(value) else None


def declared_locale(country: Any, currency: Any) -> Optional[Dict[str, str]]:
    """The typed pair, or None when either is missing or mis-shaped."""
    c, cur = shaped_country(country), shaped_currency(currency)
    if not c or not cur:
        return None
    return {"country": c, "currency": cur}


def locale_of(blueprint: Any) -> Optional[Dict[str, str]]:
    raw = blueprint.get("locale") if isinstance(blueprint, Mapping) else getattr(blueprint, "locale", None)
    if not isinstance(raw, Mapping):
        return None
    return declared_locale(raw.get("country"), raw.get("currency"))


def render_money_settings() -> str:
    """The product's run-time money settings. Carries no value of its own."""
    return f'''"""Money settings for this platform -- read at run time, never typed in.

Country and currency come from the deploy environment ({COUNTRY_ENV},
{CURRENCY_ENV}), else from the locale the customer declared
({DECLARED_LOCALE_REL.as_posix()}). The tax rate is an operator setting
({TAX_RATE_ENV}); with none set, money paths that need it refuse.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[1]


class MoneySettingMissing(RuntimeError):
    """A money path needs a setting nobody declared."""


def _declared() -> dict:
    path = _ROOT / {DECLARED_LOCALE_REL.as_posix()!r}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {{}}
    except (OSError, ValueError):
        return {{}}


def _setting(env: str, key: str) -> Optional[str]:
    value = (os.getenv(env) or "").strip()
    if value:
        return value
    declared = _declared().get(key)
    return str(declared).strip() if declared else None


def country() -> str:
    value = _setting({COUNTRY_ENV!r}, "country")
    if not value:
        raise MoneySettingMissing("no country declared ({COUNTRY_ENV})")
    return value


def currency() -> str:
    value = _setting({CURRENCY_ENV!r}, "currency")
    if not value:
        raise MoneySettingMissing("no currency declared ({CURRENCY_ENV})")
    return value


def tax_rate() -> float:
    raw = (os.getenv({TAX_RATE_ENV!r}) or "").strip()
    if not raw:
        raise MoneySettingMissing("no tax rate set ({TAX_RATE_ENV})")
    return float(raw)


_RATE_NAME = re.compile(r"[a-z][a-z0-9_]*")


def rate(name: str) -> float:
    """A named rate the operator sets (a fee, an fx rate, a discount):
    {RATE_ENV_PREFIX}<NAME>. With none set, the money path that needs it
    refuses -- a rate is never a value in code."""
    key = str(name or "").strip().lower()
    if not _RATE_NAME.fullmatch(key):
        raise ValueError(f"a rate name is lower_snake_case, not {{name!r}}")
    env = {RATE_ENV_PREFIX!r} + key.upper()
    raw = (os.getenv(env) or "").strip()
    if not raw:
        raise MoneySettingMissing(f"no {{key}} rate set ({{env}})")
    return float(raw)
'''


def render_declared_locale(locale: Optional[Mapping[str, str]]) -> str:
    body: Dict[str, Any] = {"schema": "declared_locale.v1"}
    body.update(dict(locale or {}))
    return json.dumps(body, indent=2, sort_keys=True) + "\n"


def _read_workspace_text(workspace: Any, rel: Path) -> Optional[str]:
    """``rel`` read through a workspace handle (staging, then destination) or
    a bare root; None when it is absent or unreadable."""
    try:
        if isinstance(workspace, (str, Path)):
            return (Path(workspace) / rel).read_text(encoding="utf-8")
        return workspace.read_text(rel)
    except Exception:  # noqa: BLE001 -- an unreadable record declares nothing
        return None


def recorded_locale(workspace: Any) -> Optional[Dict[str, str]]:
    """The pair the tree's declared record already carries, or None."""
    text = _read_workspace_text(workspace, DECLARED_LOCALE_REL)
    try:
        raw = json.loads(text) if text else None
    except ValueError:
        return None
    if not isinstance(raw, Mapping):
        return None
    return declared_locale(raw.get("country"), raw.get("currency"))


def stamped_locale(workspace: Any, blueprint: Any) -> Optional[Dict[str, str]]:
    """The pair the stamp writes: the build's declared one (its blueprint --
    the session's typed intake). With none declared there, the pair the tree
    already declares stays: a re-stamp never erases a declaration, and never
    invents one.

    Live (PR #719 replay, cerebrum-builds 38031528519): the registry stamp
    rendered build/plt_7056c46ae14f4c4b's AE / AED record with no blueprint,
    blanked it, and the certified 22/22 build stopped at its console build."""
    return locale_of(blueprint) or recorded_locale(workspace)


def emit_money_artifacts(workspace: Any, blueprint: Any) -> None:
    """Stamp the declared pair and the settings module (Factory-owned)."""
    from app.factory.build.data_lifecycle import write_workspace_text

    locale = stamped_locale(workspace, blueprint)
    write_workspace_text(workspace, DECLARED_LOCALE_REL, render_declared_locale(locale))
    write_workspace_text(workspace, MONEY_SETTINGS_REL, render_money_settings())


def money_brief_lines(blueprint: Any) -> List[str]:
    """What the writer builds for money, rendered into the C-BRIEF (BUILD).

    States the declared pair (or that none was declared) and the structure to
    build; the gate below measures exactly that structure.
    """
    locale = locale_of(blueprint)
    declared = (
        f"The customer declared country {locale['country']} and currency "
        f"{locale['currency']}; they are served at run time by "
        f"{MONEY_SETTINGS_MODULE} (country(), currency(), tax_rate()), "
        f"which the Factory ships."
        if locale
        else (
            "The customer declared no country or currency. Money paths still "
            f"read {MONEY_SETTINGS_MODULE}; until a currency is declared the "
            "money check reads WITHHELD (no currency declared)."
        )
    )
    return [
        "- MONEY: " + declared,
        "- MONEY: declare every money field with type money in its model spec "
        "(the emitted model lists them in MONEY_FIELDS). Take currency, "
        f"country and every rate from {MONEY_SETTINGS_MODULE} at the point of use: "
        "tax_rate() for tax, rate('<name>') for any other rate the customer owns "
        f"(a fee, an fx rate, a discount -- the operator sets {RATE_ENV_PREFIX}<NAME>). "
        "Scale an amount only by a factor read from it; keep codes and rates "
        "out of the source.",
    ]


# -- the verdict ---------------------------------------------------------------


@dataclass
class MoneyVerdict:
    status: str  # "n/a" | "PASS" | "WITHHELD" | "FAIL"
    reason: str = ""
    findings: List[str] = field(default_factory=list)


def _read_declared(workspace: Path) -> Optional[Dict[str, str]]:
    try:
        raw = json.loads((workspace / DECLARED_LOCALE_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping):
        return None
    return declared_locale(raw.get("country"), raw.get("currency"))


def _str_list(node: ast.AST) -> List[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def declared_money_fields(workspace: Path) -> Set[str]:
    """Field names the product's models declare as money (``MONEY_FIELDS``)."""
    names: Set[str] = set()
    for path in sorted((workspace / "app").rglob("*.py")) if (workspace / "app").is_dir() else []:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "MONEY_FIELDS":
                        names.update(_str_list(node.value))
    return names


def _settings_aliases(tree: ast.AST) -> Set[str]:
    """Local names bound to the money settings module or its accessors."""
    aliases: Set[str] = set()
    package, _, leaf = MONEY_SETTINGS_MODULE.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == MONEY_SETTINGS_MODULE:
                    aliases.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == MONEY_SETTINGS_MODULE:
                aliases.update(a.asname or a.name for a in node.names)
            elif node.module == package:
                aliases.update(a.asname or a.name for a in node.names if a.name == leaf)
    return aliases


def _names_in(node: ast.AST) -> Set[str]:
    out: Set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.add(sub.value)
    return out


def _money_locals(tree: ast.AST, money: Set[str]) -> Set[str]:
    """Locals assigned from an expression that reads a money field."""
    held: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _names_in(node.value) & money:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    held.add(target.id)
    return held


def _is_money_operand(node: ast.AST, money_terms: Set[str]) -> bool:
    return bool(_names_in(node) & money_terms)


def _reads_settings(node: ast.AST, aliases: Set[str]) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in aliases:
            return True
        if isinstance(sub, ast.Attribute):
            root = sub
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in aliases:
                return True
    return False


def _module_findings(path: Path, rel: str, locale: Mapping[str, str], money: Set[str]) -> List[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    found: List[str] = []
    codes = {locale["currency"], locale["country"]}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in codes:
            found.append(f"{rel}:{node.lineno}: the declared code {node.value!r} is a literal -- read it from {MONEY_SETTINGS_MODULE}")
    if not money:
        return found
    aliases = _settings_aliases(tree)
    terms = set(money) | _money_locals(tree, money)
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, (ast.Mult, ast.Div, ast.FloorDiv)):
            continue
        for amount, factor in ((node.left, node.right), (node.right, node.left)):
            if not _is_money_operand(amount, terms) or _is_money_operand(factor, terms):
                continue
            if isinstance(factor, ast.Constant) and isinstance(factor.value, (int, float)) and not isinstance(factor.value, bool):
                if factor.value in _IDENTITY_FACTORS:
                    break
                found.append(f"{rel}:{node.lineno}: a money value is scaled by the literal {factor.value!r} -- a rate is a setting ({MONEY_SETTINGS_MODULE}.tax_rate())")
                break
            if not _reads_settings(factor, aliases):
                found.append(
                    f"{rel}:{node.lineno}: a money value is scaled by a factor not taken from {MONEY_SETTINGS_MODULE}"
                    f" -- `{_snippet(node)}` scales a money value by `{_snippet(factor)}`; read that factor from "
                    f"{MONEY_SETTINGS_MODULE} (tax_rate(), or rate('<name>') for a rate the customer owns)"
                )
            break
    # A nested expression (a * b / c) is visited once per operator on one line:
    # one finding per distinct line and text.
    return list(dict.fromkeys(found))


def _snippet(node: ast.AST, limit: int = 80) -> str:
    """The expression as the writer wrote it, so the rework names the code."""
    try:
        text = ast.unparse(node)
    except Exception:  # noqa: BLE001 -- a finding never fails on its own quote
        return "?"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def money_verdict(workspace: Path) -> MoneyVerdict:
    workspace = Path(workspace)
    locale = _read_declared(workspace)
    money = declared_money_fields(workspace)
    if locale is None:
        if money:
            return MoneyVerdict("WITHHELD", WITHHELD_NO_CURRENCY)
        return MoneyVerdict("n/a", "no money fields and no declared currency")
    findings: List[str] = []
    app_dir = workspace / "app"
    settings = (workspace / MONEY_SETTINGS_REL).resolve()
    for path in sorted(app_dir.rglob("*.py")) if app_dir.is_dir() else []:
        if path.resolve() == settings:
            continue
        rel = path.relative_to(workspace).as_posix()
        findings.extend(_module_findings(path, rel, locale, money))
    if findings:
        return MoneyVerdict("FAIL", MONEY_ASSUMED, findings)
    return MoneyVerdict("PASS", f"money reads the declared {locale['country']}/{locale['currency']}")
