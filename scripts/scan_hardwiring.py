#!/usr/bin/env python3
"""The NO HARDWIRING tripwire.

Owner's rule (2026-10-02): a failing case is never fixed by name. No per-case
branches, no literal document strings, no test-case IDs, no rescue functions,
no per-case switches. This script finds the SHAPE of a by-name fix in shipped
code and rejects any new one. It knows no case names itself -- that would be
the very thing it exists to stop.

Forms it recognises (in code only; comments and docstrings are exempt, because
a session cited in a comment is the reason a mechanism exists, while an
identifier in the path is a fixed point):

  session_id    a build-session literal (``sess_14e8b0b2``) in code
  live_snapshot a photographed product (``LIVE_VETCARE_REUSE_ACCEPT_BLOCKS``)
  rescue_fn     ``def _rescue_*``
  needle_list   ``*_NEEDLES = (...)``
  rescue_knob   ``*_RESCUE`` / ``*_BONUS`` / ``*_EXTRA_K`` knobs
  product_literal  a string literal EQUAL to a name that belongs to some
                product -- a capability id, product id or name, or vertical.
                The set is loaded at run time from the Store registry, every
                build on record (local workspaces and the cerebrum-builds
                branches) and the golden blueprints
                (scripts/known_product_literals.py). Nothing is hand-listed.
  word_list     a literal collection of >=2 strings whose MEMBERS are searched
                for inside other text: ``any(k in text for k in WORDS)``, a
                loop/comprehension testing ``k in text``, ``text.find(k)`` /
                ``re.search(k, ...)``, or ``"|".join(WORDS)`` into a regex.
                That is classification by vocabulary -- routing, detecting or
                deciding by words. Exact-token membership against a closed
                vocabulary (``mode in ("zip", "github_repo")``) and key lookups
                on a mapping (``for k in KEYS: if k in d: d[k]``) are contract
                checks, not word lists. Members loaded from data at run time
                are not literals and are not this form.
  probe_id      a probe / test-case id used as an exact string literal
                (``"R18"``, ``"E1"``) -- the shape of a photographed probe set.
                Ids live in app/factory/build/probe_set.json; code asks by
                shape/class/name, and an ordinal scale is an enum referenced
                by member, never a spelled id.
  phrase_match  a single string literal used as a decision input against
                text (``"x" in text``, ``text.startswith("x")``), or a
                compiled module-scope regex whose pattern holds words, applied
                to text. Deciding by a phrase is classification by vocabulary
                exactly like word_list; structure (AST, typed fields, magic
                bytes, enum members) is the replacement.
  block_name_dispatch  a string literal EQUAL to a Store block id used to
                pick behaviour for a block: compared (==, !=, in, not in)
                against an expression that carries block ids (a name made of
                block / blocks / bid / bids / roster / peers / vendored, or a
                variable bound by iterating one), as a subscript into one, in a
                ``case`` on one, assigned to one (``EVENT_BUS_BLOCK =
                "event_bus"`` is the same branch one step removed), or as the
                keys of a table (a dict literal whose keys are all block ids:
                two or more, or one at module scope). The ids are loaded at run
                time from CEREBRUM_BLOCKS_ROOT/block_registry/*/block.json --
                none is listed here -- and with no Store the gate fails
                closed. A site that must treat a kind of block differently
                reads what the block DECLARES (capability_class, reads/writes,
                preconditions, factory_attach), never its id.
  blueprint_name  a string literal EQUAL (case-insensitively) to a release-
                cycle pool blueprint's id, name or vertical. The release bar
                builds rotating blueprints from backend/tests/repro_pool; the
                Factory must never branch on which one is running, and a fix
                that mentions one is rejected. The set is read from the pool
                files by scripts/repro_pool.py -- adding a blueprint extends
                the gate -- and with no readable pool the gate fails closed.

What this cannot see: a per-case branch keyed on a field NAME (``if name ==
"status"``) or an answer table (``{"database": "query"}``) has no lexical
signature. Those are caught by the classification and by the unseen-brief
gate, not by this lint. The lint is the tripwire; it is not the acceptance.

Pre-doctrine forms are grandfathered in ``scripts/hardwiring_baseline.json``.
The baseline may only shrink: ``--write-baseline`` records what is there
after a deletion batch; it is never used to admit a new form. Exit 1 is a
rejection, not a warning: if the gate fails your diff, your diff is the problem.

    python scripts/scan_hardwiring.py                 # gate (CI)
    python scripts/scan_hardwiring.py --report        # every form, by file
    python scripts/scan_hardwiring.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
#: Every backend/app package that handles user or agent text or decides
#: behaviour, scanned like the Factory itself: the HTTP routers (the user's
#: chat), all of core (auth, grounding, LLM routing, deploy, RAG), and the
#: kernel / compiler / DNA / workbench packages. A root may be a directory or
#: a single file. ``change_requests`` / ``resident_engineer`` name autonomy
#: levels by enum member (``AutonomyLevel.L3``) and record keys by typed
#: field (``ItemField.audit_trail``), never by spelled literal.
DEFAULT_ROOTS = (
    "backend/app/factory",
    "backend/app/routers",
    "backend/app/core",
    "backend/app/blocks",
    "backend/app/cerebrum_product_kernel",
    "backend/app/domain_compiler",
    "backend/app/models",
    "backend/app/product_dna",
    "backend/app/workbench",
    "backend/app/change_requests",
    "backend/app/resident_engineer",
    "backend/app/main.py",
)
BASELINE = ROOT / "scripts" / "hardwiring_baseline.json"

#: form -> pattern over a single token's text (NAME or STRING, never COMMENT).
FORMS: Dict[str, re.Pattern] = {
    "session_id": re.compile(r"sess_[0-9a-f]{6,}"),
    "live_snapshot": re.compile(r"^LIVE_[A-Z0-9_]+$"),
    "rescue_fn": re.compile(r"^_rescue_[A-Za-z0-9_]+$"),
    "needle_list": re.compile(r"^[A-Z0-9_]*_NEEDLES$"),
    "rescue_knob": re.compile(r"^[A-Z0-9_]+_(?:RESCUE|BONUS|EXTRA_K)$"),
    # A probe id is a letter and one or two digits as the WHOLE string
    # literal ("R18", "E1"): the form a comparison or a photographed set uses.
    # An id inside a longer message ("G5 stopped the run") is prose. ENFORCED:
    # probe ids live in app/factory/build/probe_set.json with their class and
    # shape; code asks by shape/class/name and never spells an id.
    "probe_id": re.compile(r"^[A-Z]-?\d{1,2}$"),
}
#: Forms matched against string literals (the literal's body, quotes and
#: prefix stripped); every other form is matched against NAME tokens.
STRING_ONLY_FORMS = {"probe_id", "session_id"}
#: Whole-body match only: the literal IS the id, not a sentence containing it.
EXACT_LITERAL_FORMS = {"probe_id"}
DEFAULT_FORMS = (
    "session_id",
    "live_snapshot",
    "rescue_fn",
    "needle_list",
    "rescue_knob",
    "product_literal",
    "word_list",
    "probe_id",
    "phrase_match",
    "block_name_dispatch",
    "blueprint_name",
)
#: Forms decided by a loaded set rather than a pattern.
DATA_FORMS = ("product_literal", "block_name_dispatch", "blueprint_name")
#: The release cycle's rotation pool (scripts/repro_pool.py reads it).
POOL_SCRIPT = ROOT / "scripts" / "repro_pool.py"


class NoStoreRegistry(RuntimeError):
    """block_name_dispatch was asked for and no Store registry is reachable."""


class NoReproPool(RuntimeError):
    """blueprint_name was asked for and the rotation pool cannot be read."""


def load_pool_literals() -> FrozenSet[str]:
    """Every rotation-pool blueprint's id, name and vertical, lower-cased.

    Read from the pool files, never listed here. An unreadable or empty pool
    is a failure, never an empty set: a gate that loads nothing passes
    everything.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("repro_pool", POOL_SCRIPT)
    if spec is None or spec.loader is None or not POOL_SCRIPT.is_file():
        raise NoReproPool(f"blueprint_name needs {POOL_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        names = mod.identity_literals(mod.load_pool())
    except mod.PoolError as exc:
        raise NoReproPool(f"blueprint_name: {exc}") from exc
    if not names:
        raise NoReproPool("blueprint_name: the rotation pool names nothing")
    return names


def load_block_ids(root: Optional[str] = None) -> FrozenSet[str]:
    """Every Store block id, from ``<root>/block_registry/*/block.json``.

    ``root`` defaults to CEREBRUM_BLOCKS_ROOT (CI checks the pinned Store out
    there). No registry is a failure, never an empty set: a gate that loads
    nothing passes everything.
    """
    import os

    base = root or os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    registry = Path(base) / "block_registry" if base else None
    if registry is None or not registry.is_dir():
        raise NoStoreRegistry(
            "block_name_dispatch needs the Store: set CEREBRUM_BLOCKS_ROOT to a "
            "Cerebrum-Blocks checkout with block_registry/ (got %r)" % (base,)
        )
    ids = set()
    for path in sorted(registry.glob("*/block.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        bid = data.get("id") if isinstance(data, dict) else None
        ids.add(str(bid or path.parent.name))
    if not ids:
        raise NoStoreRegistry("block_name_dispatch: %s holds no block.json" % registry)
    return frozenset(ids)


def load_known_literals() -> FrozenSet[str]:
    """Every known product name, lower-cased (builds-repo part is additive)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "known_product_literals", ROOT / "scripts" / "known_product_literals.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    known = mod.load()
    return frozenset(k.strip().lower() for k in known if k.strip())


#: Forms computed by a function over the syntax tree rather than a token
#: pattern (selectable with --form like any other).
AST_FORMS = ("word_list", "phrase_match", "block_name_dispatch")


def _literal_body(text: str) -> str:
    body = re.sub(r"^[rbRBfFuU]*", "", text)
    if body[:3] in ('"""', "'''"):
        return body[3:-3]
    return body[1:-1] if len(body) >= 2 else body

Hit = Tuple[str, str, str]  # (file, form, token)


def _docstring_spans(source: str) -> List[Tuple[int, int]]:
    """Line ranges of every docstring, so a citation inside one is exempt."""
    spans: List[Tuple[int, int]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return spans
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant
            ) and isinstance(body[0].value.value, str):
                spans.append((body[0].lineno, body[0].end_lineno or body[0].lineno))
    return spans


def _in_spans(line: int, spans: Iterable[Tuple[int, int]]) -> bool:
    return any(a <= line <= b for a, b in spans)


def _str_collection(node: ast.AST) -> Optional[List[str]]:
    if (isinstance(node, ast.Call) and getattr(node.func, "id", None) in
            ("frozenset", "set", "tuple", "list") and node.args):
        node = node.args[0]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        elts = node.elts
        if len(elts) >= 2 and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts):
            return [e.value for e in elts]
    return None


_TEXT_SEARCH_CALLS = ("search", "match", "fullmatch", "findall", "finditer",
                      "startswith", "endswith", "find", "rfind", "index", "count")


_TEXT_CALLS = ("lower", "upper", "casefold", "strip", "lstrip", "rstrip",
               "read_text", "join", "format", "replace", "decode", "getvalue", "sub")


def _text_names(scope: ast.AST) -> set:
    """Names that hold TEXT in ``scope``: str-annotated parameters and locals
    assigned from a string-producing expression."""
    out = set()
    args = getattr(scope, "args", None)
    if isinstance(args, ast.arguments):
        for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            ann = a.annotation
            if isinstance(ann, ast.Name) and ann.id == "str":
                out.add(a.arg)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and _is_text(node.value, out):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id not in out:
                        out.add(tgt.id)
                        changed = True
    return out


def _is_text(expr: ast.AST, names: set) -> bool:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return True
    if isinstance(expr, ast.JoinedStr):
        return True
    if isinstance(expr, ast.Name):
        return expr.id in names
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Attribute) and func.attr in _TEXT_CALLS:
            return True
        if isinstance(func, ast.Name) and func.id == "str":
            return True
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return _is_text(expr.left, names) or _is_text(expr.right, names)
    if isinstance(expr, ast.BoolOp):
        return any(_is_text(v, names) for v in expr.values)
    if isinstance(expr, ast.Subscript):
        return _is_text(expr.value, names)
    return False


def _searched_in_text(body: Iterable[ast.AST], var: str, text: set = frozenset()) -> bool:
    """Is ``var`` searched for INSIDE text (substring), not looked up as a key?

    Python's ``in`` is substring search only on a string. The haystack must be
    evidently TEXT -- a string-producing call, a str-annotated parameter, or a
    local assigned from one. Membership in a set, list or mapping is exact
    equality against a closed vocabulary, not a word list.
    """
    maps = set()
    for n in body:
        for c in ast.walk(n):
            if isinstance(c, ast.Subscript) and isinstance(c.slice, ast.Name) and c.slice.id == var:
                maps.add(ast.dump(c.value))
            if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr in ("get", "pop", "setdefault") and c.args
                    and isinstance(c.args[0], ast.Name) and c.args[0].id == var):
                maps.add(ast.dump(c.func.value))
    for n in body:
        for c in ast.walk(n):
            if (isinstance(c, ast.Compare) and isinstance(c.left, ast.Name) and c.left.id == var
                    and any(isinstance(op, (ast.In, ast.NotIn)) for op in c.ops)):
                if all(ast.dump(r) in maps for r in c.comparators):
                    continue  # key equality on a mapping, not substring
                if any(_is_text(r, set(text)) for r in c.comparators):
                    return True
            if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr in _TEXT_SEARCH_CALLS
                    and any(isinstance(a, ast.Name) and a.id == var for a in c.args)):
                receiver = c.func.value
                if (isinstance(receiver, ast.Name) and receiver.id == "re") or _is_text(receiver, set(text)):
                    return True
    return False


def word_lists(source: str) -> List[Tuple[int, str]]:
    """(line, name) of every literal word list searched for inside text."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    named: Dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if _str_collection(node.value):
                for tgt in targets:
                    if isinstance(tgt, ast.Name):
                        named[tgt.id] = node.lineno

    def resolve(it: ast.AST) -> Optional[Tuple[int, str]]:
        vals = _str_collection(it)
        if vals:
            return it.lineno, "inline:" + vals[0]
        if isinstance(it, ast.Name) and it.id in named:
            return named[it.id], it.id
        return None

    scopes = [tree] + [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
    text_in: Dict[int, set] = {}
    for scope in scopes:
        names = _text_names(scope)
        for n in ast.walk(scope):
            text_in.setdefault(id(n), set()).update(names)

    out = set()
    for node in ast.walk(tree):
        gens: List[Tuple[ast.AST, List[ast.AST]]] = []
        if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            gens = [(g, [node.elt, *g.ifs]) for g in node.generators]
        elif isinstance(node, ast.For):
            gens = [(node, list(node.body))]
        for gen, body in gens:
            if isinstance(gen.target, ast.Name):
                hit = resolve(gen.iter)
                if hit and _searched_in_text(body, gen.target.id, text_in.get(id(node), set())):
                    out.add(hit)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "join" and node.args
                and isinstance(node.func.value, ast.Constant) and node.func.value.value == "|"):
            hit = resolve(node.args[0])
            if hit:
                out.add(hit)
    return sorted(out)


#: A phrase literal carries a word: a run of two or more letters.
_PHRASE_WORD = re.compile(r"[A-Za-z]{2,}")
#: Path-shaped literals are structure, not phrases: a path segment, a URL
#: scheme, or a bare file extension (owner: path/filename/extension checks).
_PATH_SHAPED = re.compile(r"^(?:[^\s]*/[^\s]*|\.[A-Za-z0-9]{1,8})$")
_PHRASE_SEARCH_CALLS = _TEXT_SEARCH_CALLS + ("rindex",)


def _is_phrase(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(_PHRASE_WORD.search(value))
        and not _PATH_SHAPED.match(value)
    )


#: Regex structure that carries letters without being words: a character
#: class ([A-Za-z]) or an escape (\b, \s, \d).
_REGEX_STRUCTURE = re.compile(r"\[(?:\\.|[^\]])*\]|\\.")
_COMPILED_TEXT_METHODS = ("search", "match", "fullmatch", "findall", "finditer", "split", "sub", "subn")


def _regex_is_phrase(pattern: object) -> bool:
    """A compiled pattern is a phrase when a word survives once its classes
    and escapes are removed: ``FROM\\s+records`` is; ``[A-Z]{2}`` is not."""
    if not isinstance(pattern, str):
        return False
    return bool(_PHRASE_WORD.search(_REGEX_STRUCTURE.sub(" ", pattern)))


def _module_compiled_patterns(tree: ast.AST) -> Dict[str, str]:
    """name -> literal for module-scope ``NAME = re.compile("<literal>", ...)``."""
    out: Dict[str, str] = {}
    for node in getattr(tree, "body", []):
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and value.func.attr == "compile" and isinstance(value.func.value, ast.Name)
                and value.func.value.id == "re" and value.args
                and isinstance(value.args[0], ast.Constant)
                and _regex_is_phrase(value.args[0].value)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                out[target.id] = value.args[0].value
    return out


#: The one data file whose patterns may be applied to text (owner ruling
#: 2026-10-05): the secret scrubber's redaction formats. A module that loads
#: it may only SUBSTITUTE with them; a match used to decide anything is a
#: phrase check like any other.
SECRET_PATTERNS_FILE = "secret_patterns.json"
_MATCH_DECISION_METHODS = ("search", "match", "fullmatch", "findall", "finditer")


def _loads_secret_patterns(tree: ast.AST) -> bool:
    """The file's name is used to REACH it: an operand of a path join
    (``root / "secret_patterns.json"``) or an argument of a call (``open(...)``,
    ``Path(...)``), directly or through a name bound to it. A module that only
    binds the name -- this gate defining its rule -- loads nothing."""

    def names_file(n: ast.AST) -> bool:
        return (isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value.endswith(SECRET_PATTERNS_FILE))

    aliases = {
        t.id
        for n in ast.walk(tree) if isinstance(n, ast.Assign) and names_file(n.value)
        for t in n.targets if isinstance(t, ast.Name)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            operands, alias_ok = (node.left, node.right), True
        elif isinstance(node, ast.Call):
            # An alias reaches the file through a constructor/opener call
            # (open(F), Path(F)); a method on another value (s.endswith(F))
            # only compares with it.
            operands, alias_ok = node.args, isinstance(node.func, ast.Name)
        else:
            continue
        for n in operands:
            if names_file(n) or (alias_ok and isinstance(n, ast.Name) and n.id in aliases):
                return True
    return False


def _match_decisions(tree: ast.AST) -> List[Tuple[int, str]]:
    """(line, method) of every regex match call whose result sits in a test
    position -- if/while/elif, a conditional expression, assert, a boolean
    operator or a comprehension filter."""
    tests: List[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            tests.append(node.test)
        elif isinstance(node, ast.BoolOp):
            tests.extend(node.values)
        elif isinstance(node, ast.comprehension):
            tests.extend(node.ifs)
    out: List[Tuple[int, str]] = []
    for test in tests:
        for node in ast.walk(test):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _MATCH_DECISION_METHODS):
                out.append((node.lineno, f"secret pattern used as a decision: .{node.func.attr}()"))
    return out


def _code_templates(tree: ast.AST) -> List[Tuple[int, str]]:
    """(line offset, source) of every string constant that is itself Python
    code the Factory emits -- a probe, a harness -- so a phrase check hidden
    inside a template is scanned like any other code. An f-string template's
    replacement fields are stood in by ``None``."""
    out: List[Tuple[int, str]] = []
    # A constant inside an f-string is a fragment of that template, scanned
    # with it -- never a second template of its own.
    inside_fstring = {
        id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values
    }
    for node in ast.walk(tree):
        src = None
        if id(node) in inside_fstring:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            src = node.value
        elif isinstance(node, ast.JoinedStr):
            src = "".join(
                str(v.value) if isinstance(v, ast.Constant) else "None" for v in node.values
            )
        if not src or src.count("\n") < 5:
            continue
        try:
            inner = ast.parse(src)
        except SyntaxError:
            continue
        if any(isinstance(x, (ast.FunctionDef, ast.Import, ast.ImportFrom)) for x in ast.walk(inner)):
            out.append((node.lineno - 1, src))
    return out


def phrase_matches(source: str, offset: int = 0) -> List[Tuple[int, str]]:
    """(line, literal) of every single string literal used as a decision input
    against TEXT: ``"lit" in text``, ``text.startswith/endswith/find/...("lit")``,
    ``re.search/match/...("lit", ...)``, and ``text == "a sentence"``. Text is
    recognised exactly as for the word-list form. Docstrings and comments are
    prose and exempt; path-shaped literals are structure."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    spans = _docstring_spans(source)
    scopes = [tree] + [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
    text_in: Dict[int, set] = {}
    for scope in scopes:
        names = _text_names(scope)
        for n in ast.walk(scope):
            text_in.setdefault(id(n), set()).update(names)
    out: List[Tuple[int, str]] = []

    def is_text(expr: ast.AST, names: set) -> bool:
        return not isinstance(expr, ast.Constant) and _is_text(expr, names)

    # A phrase compiled once at module scope and applied to text later is the
    # same decision as re.search("<phrase>", text) -- caught at the use site.
    compiled = _module_compiled_patterns(tree)

    for node in ast.walk(tree):
        names = text_in.get(id(node), set())
        if (compiled and isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _COMPILED_TEXT_METHODS
                and isinstance(node.func.value, ast.Name) and node.func.value.id in compiled):
            haystack_at = 1 if node.func.attr in ("sub", "subn") else 0
            if len(node.args) > haystack_at and is_text(node.args[haystack_at], names):
                out.append((node.lineno, compiled[node.func.value.id]))
        if isinstance(node, ast.Compare):
            if (isinstance(node.left, ast.Constant) and _is_phrase(node.left.value)
                    and any(isinstance(o, (ast.In, ast.NotIn)) for o in node.ops)
                    and any(is_text(c, names) for c in node.comparators)):
                out.append((node.lineno, node.left.value))
            for op, comp in zip(node.ops, node.comparators):
                if not isinstance(op, (ast.Eq, ast.NotEq)):
                    continue
                for lit, other in ((node.left, comp), (comp, node.left)):
                    if (isinstance(lit, ast.Constant) and isinstance(lit.value, str)
                            and " " in lit.value.strip() and _is_phrase(lit.value)
                            and is_text(other, names)):
                        out.append((node.lineno, lit.value))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _PHRASE_SEARCH_CALLS):
            recv = node.func.value
            if isinstance(recv, ast.Name) and recv.id == "re":
                if node.args and isinstance(node.args[0], ast.Constant) and _is_phrase(node.args[0].value):
                    out.append((node.lineno, node.args[0].value))
            elif is_text(recv, names) and node.args:
                first = node.args[0]
                lits = [first] if isinstance(first, ast.Constant) else (
                    list(first.elts) if isinstance(first, ast.Tuple) else [])
                out.extend((node.lineno, lit.value) for lit in lits
                           if isinstance(lit, ast.Constant) and _is_phrase(lit.value))
    if _loads_secret_patterns(tree):
        out.extend(_match_decisions(tree))
    hits = [(ln + offset, lit) for ln, lit in out if not _in_spans(ln, spans)]
    for off, src in _code_templates(tree):
        hits.extend(phrase_matches(src, offset + off))
    return hits


#: Name parts that mark an expression as carrying block ids. A name made of
#: one of these (``bid``, ``block_ids``, ``vendored_blocks``, ``peers``,
#: ``dual`` -- the dual-registered ids) holds Store block ids by the
#: codebase's own naming; a variable bound by iterating one inherits it.
_BLOCK_CARRIER_PARTS = frozenset(
    {"block", "blocks", "bid", "bids", "roster", "peers", "vendored", "dual"}
)


def _str_literals(node: ast.AST) -> List[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def _carries_blocks(expr: ast.AST, bound: Set[str]) -> bool:
    for n in ast.walk(expr):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else None
        if name and (name in bound or set(name.lower().split("_")) & _BLOCK_CARRIER_PARTS):
            return True
    return False


def _block_bound_names(tree: ast.AST) -> Set[str]:
    """Names bound by iterating a block-carrying expression (``for mod in
    block_mods``), to a fixed point."""
    bound: Set[str] = set()
    while True:
        before = len(bound)
        for n in ast.walk(tree):
            pairs = []
            if isinstance(n, (ast.For, ast.AsyncFor)):
                pairs.append((n.target, n.iter))
            elif isinstance(n, ast.comprehension):
                pairs.append((n.target, n.iter))
            for target, it in pairs:
                if _carries_blocks(it, bound):
                    bound.update(t.id for t in ast.walk(target) if isinstance(t, ast.Name))
        if len(bound) == before:
            return bound


def block_name_dispatches(source: str, block_ids: FrozenSet[str]) -> List[Tuple[int, str]]:
    """(line, id) for every Store block id used to pick behaviour for a block.

    See the module docstring (block_name_dispatch) for the shapes.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    bound = _block_bound_names(tree)
    module_values = {
        id(stmt.value)
        for stmt in getattr(tree, "body", [])
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None
    }
    out: List[Tuple[int, str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare) and any(
            isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)) for op in n.ops
        ):
            sides = [n.left, *n.comparators]
            for i, side in enumerate(sides):
                others = sides[:i] + sides[i + 1:]
                hits = [v for v in _str_literals(side) if v in block_ids]
                if hits and any(_carries_blocks(o, bound) for o in others):
                    out.extend((n.lineno, v) for v in hits)
        elif isinstance(n, ast.Dict) and n.keys:
            keys = [k.value for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if len(keys) == len(n.keys) and all(k in block_ids for k in keys):
                if len(keys) >= 2 or id(n) in module_values:
                    out.extend((n.lineno, k) for k in keys)
        elif isinstance(n, ast.Subscript):
            hits = [v for v in _str_literals(n.slice) if v in block_ids]
            if hits and _carries_blocks(n.value, bound):
                out.extend((n.lineno, v) for v in hits)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            lits = _str_literals(n.value)
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if lits and all(v in block_ids for v in lits) and any(
                _carries_blocks(t, set()) for t in targets
            ):
                out.extend((n.lineno, v) for v in lits)
        elif isinstance(n, ast.Match) and _carries_blocks(n.subject, bound):
            for case in n.cases:
                for p in ast.walk(case.pattern):
                    if isinstance(p, ast.MatchValue):
                        out.extend(
                            (p.lineno, v) for v in _str_literals(p.value) if v in block_ids
                        )
    return out


def scan_file(
    path: Path,
    forms: Iterable[str] = DEFAULT_FORMS,
    known: Optional[FrozenSet[str]] = None,
    block_ids: Optional[FrozenSet[str]] = None,
    pool_names: Optional[FrozenSet[str]] = None,
) -> List[Tuple[int, str, str]]:
    """(line, form, token) for every hardwiring form in the file's CODE."""
    # utf-8-sig: a byte-order mark would make ast.parse fail, and a file whose
    # docstrings cannot be located would have every docstring citation
    # counted as code -- a false hit the file's author could not see.
    source = path.read_text(encoding="utf-8-sig", errors="replace")
    spans = _docstring_spans(source)
    out: List[Tuple[int, str, str]] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return out
    forms = tuple(forms)
    active = [(f, FORMS[f]) for f in forms if f in FORMS]
    literals = known if (known and "product_literal" in forms) else frozenset()
    blueprints = pool_names if (pool_names and "blueprint_name" in forms) else frozenset()
    if "word_list" in forms:
        out.extend((line, "word_list", name) for line, name in word_lists(source))
    if "phrase_match" in forms:
        out.extend((line, "phrase_match", lit) for line, lit in phrase_matches(source))
    if block_ids and "block_name_dispatch" in forms:
        out.extend(
            (line, "block_name_dispatch", bid)
            for line, bid in block_name_dispatches(source, block_ids)
        )
    for tok in tokens:
        if tok.type not in (tokenize.NAME, tokenize.STRING):
            continue
        if _in_spans(tok.start[0], spans):
            continue
        if literals and tok.type is tokenize.STRING:
            body = _literal_body(tok.string)
            if body.strip().lower() in literals:
                out.append((tok.start[0], "product_literal", body.strip()))
        if blueprints and tok.type is tokenize.STRING:
            body = _literal_body(tok.string)
            if body.strip().lower() in blueprints:
                out.append((tok.start[0], "blueprint_name", body.strip()))
        for form, pattern in active:
            if form in STRING_ONLY_FORMS:
                if tok.type is not tokenize.STRING:
                    continue
                body = _literal_body(tok.string)
                if form in EXACT_LITERAL_FORMS:
                    if pattern.match(body):
                        out.append((tok.start[0], form, body))
                else:
                    for m in pattern.finditer(body):
                        out.append((tok.start[0], form, m.group(0)))
            elif tok.type is tokenize.NAME and pattern.match(tok.string):
                out.append((tok.start[0], form, tok.string))
    return out


def scan(
    roots: Iterable[str],
    forms: Iterable[str] = DEFAULT_FORMS,
    known: Optional[FrozenSet[str]] = None,
    block_ids: Optional[FrozenSet[str]] = None,
    pool_names: Optional[FrozenSet[str]] = None,
) -> Dict[str, List[Tuple[int, str, str]]]:
    found: Dict[str, List[Tuple[int, str, str]]] = {}
    forms = tuple(forms)
    if known is None and "product_literal" in forms:
        known = load_known_literals()
    if block_ids is None and "block_name_dispatch" in forms:
        block_ids = load_block_ids()
    if pool_names is None and "blueprint_name" in forms:
        pool_names = load_pool_literals()
    for root in roots:
        base = ROOT / root
        paths = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in paths:
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            hits = scan_file(path, forms, known, block_ids, pool_names)
            if hits:
                found[path.relative_to(ROOT).as_posix()] = hits
    return found


def as_counts(found: Dict[str, List[Tuple[int, str, str]]]) -> Dict[str, Dict[str, Dict[str, int]]]:
    """file -> form -> token -> count. Line numbers are deliberately dropped:
    a baseline keyed on lines would churn on every unrelated edit."""
    out: Dict[str, Dict[str, Dict[str, int]]] = {}
    for file, hits in found.items():
        per_form: Dict[str, Counter] = defaultdict(Counter)
        for _line, form, token in hits:
            per_form[form][token] += 1
        out[file] = {form: dict(sorted(c.items())) for form, c in sorted(per_form.items())}
    return out


def total(counts: Dict[str, Dict[str, Dict[str, int]]]) -> int:
    return sum(n for forms in counts.values() for toks in forms.values() for n in toks.values())


def _baseline_forms() -> List[str]:
    if not BASELINE.is_file():
        return []
    return list(json.loads(BASELINE.read_text(encoding="utf-8")).get("forms") or [])


def load_baseline() -> Dict[str, Dict[str, Dict[str, int]]]:
    if not BASELINE.is_file():
        return {}
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    return data.get("files") or {}


def new_forms(
    current: Dict[str, Dict[str, Dict[str, int]]],
    baseline: Dict[str, Dict[str, Dict[str, int]]],
) -> List[Tuple[str, str, str, int, int]]:
    """(file, form, token, now, allowed) for every count above the baseline."""
    out = []
    for file, forms in current.items():
        for form, toks in forms.items():
            for token, n in toks.items():
                allowed = baseline.get(file, {}).get(form, {}).get(token, 0)
                if n > allowed:
                    out.append((file, form, token, n, allowed))
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", help="directory to scan (repeatable)")
    ap.add_argument(
        "--form",
        action="append",
        choices=sorted(set(FORMS) | set(AST_FORMS)),
        help="add an opt-in form (repeatable); default: " + ", ".join(DEFAULT_FORMS),
    )
    ap.add_argument("--report", action="store_true", help="print every form by file and exit 0")
    ap.add_argument("--write-baseline", action="store_true", help="record the current forms (after a deletion batch)")
    args = ap.parse_args(argv)
    roots = tuple(args.root) if args.root else DEFAULT_ROOTS
    forms = tuple(dict.fromkeys(DEFAULT_FORMS + tuple(args.form or ())))

    try:
        found = scan(roots, forms)
    except (NoStoreRegistry, NoReproPool) as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1
    current = as_counts(found)
    now = total(current)

    if args.report:
        for file, hits in found.items():
            print(file)
            for line, form, token in hits:
                print(f"  {line:5d}  {form:14s} {token}")
        print(f"\n{now} hardwiring form(s) in shipped code under {', '.join(roots)}")
        return 0

    baseline = load_baseline()
    before = total(baseline)

    if args.write_baseline:
        # A rule that did not exist when the baseline was written grandfathers
        # what already stands, once. Every form that WAS in the baseline may
        # only shrink.
        old_forms = set(_baseline_forms())
        def _total_in(counts, keep):
            return sum(n for forms_ in counts.values() for f, toks in forms_.items() if f in keep for n in toks.values())
        prior = _total_in(current, old_forms) if old_forms else now
        if baseline and prior > before:
            print(
                f"REFUSED: the baseline may only shrink ({before} -> {prior}). "
                "Delete the new form; never admit it.",
                file=sys.stderr,
            )
            return 1
        BASELINE.write_text(
            json.dumps(
                {"schema": "hardwiring_baseline.v1", "roots": list(roots), "forms": list(forms), "total": now, "files": current},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"baseline written: {before} -> {now}")
        return 0

    added = new_forms(current, baseline)
    if added:
        print("REJECTED: new hardwiring form(s) in shipped code -- fix the mechanism, not the case:", file=sys.stderr)
        for file, form, token, n, allowed in added:
            lines = [ln for ln, f, tok in found.get(file, []) if f == form and tok == token]
            where = ",".join(str(ln) for ln in lines) or "?"
            print(
                f"  {file}:{where}: {form} {token!r} x{n} (baseline allows {allowed})",
                file=sys.stderr,
            )
        print(
            f"\n{len(added)} new form(s). The baseline ({before}) may only shrink; "
            "it is never regenerated to admit a new form.",
            file=sys.stderr,
        )
        return 1
    print(f"hardwiring gate: no new form. shipped-code forms {now} (baseline {before}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
