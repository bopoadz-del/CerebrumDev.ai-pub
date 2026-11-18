"""H-1: is each formula function REACHED from the product's real entry points?

A record, not a gate (the gate is H-4). Read statically from the built
product's own files, by rule, with no product, block or formula name:

ENTRY POINTS, discovered from the product:
  * the server / CLI targets its packaging declares -- a ``module:object``
    or ``module:function`` reference, ``-m module`` or a ``path.py`` script
    in a root packaging file (Dockerfile, Procfile, pyproject, compose and
    other config files) that resolves to a product module;
  * the ``if __name__ == "__main__":`` block of a module that one of those
    targets runs as a script (``python app/cli.py``, ``-m pkg`` running
    ``pkg/__main__``). An imported module's guard never runs, so a guard in
    a dev script or in a formula module's own demo block is not an entry;
  * every function registered on a LIVE registrar object -- a module-level
    object (``router = APIRouter()``, ``scheduler = Scheduler()``) that a
    function is attached to by a decorator on the object
    (``@router.get(...)``, ``@scheduler.scheduled_job(...)``) or passed to
    one of its methods (``app.add_api_route(path, fn)``). An object is live
    when a declared target names it, when a live object includes it
    (``app.include_router(other)``), or when reached code uses it otherwise
    (passes it on, or starts it: ``scheduler.start()``).

REACH is a walk of the import and call graph FROM those entry points. A
formula is reached only when a reached function (or module code run at
import) calls it or passes it on -- directly, through a helper, or through a
re-export. A bare import, a name in a module nothing loads, or the same name
on an unrelated object reaches nothing.

UNDECIDED: what the walk cannot decide -- ``getattr`` on a product module
with a computed name, ``globals()`` / ``vars()`` lookups, an
``import_module`` / ``__import__`` of a computed name, a formula handed to a
product decorator at import -- is listed and never counted as reached.

A script token in a packaging file counts only in a RUN position: the
first non-flag word after a Python interpreter on the same line
(``CMD ["python", "app/seed.py"]``), never a copy line naming the file.

SHADOWING: inside a function, a name the function binds itself (a
parameter, a loop or ``with`` target, an assignment, an ``except`` name, a
nested def) hides the module-level name of the same spelling.

A handler's SIGNATURE is walked with its body: defaults and the calls inside
annotations run at definition (``rate=Depends(fn)``,
``Annotated[T, Depends(fn)]``) and wire what they name.

THE ANALYSIS'S OWN FAILURE IS NEVER CLEAN: no formula module, a formula
module that does not parse, a product module that does not parse and that
reached code imports or a packaging target runs, a module past the size or
nesting bound ("bound hit"), or no entry point found records
``status: "unknown"`` with the reason and ``unreached: None`` -- never an
empty list.

A module is read the way Python reads it (``tokenize.open``: the byte-order
mark and a declared ``coding:`` line are honoured). A module that still does
not parse is listed in ``unparsed``; when nothing reached imports it and no
target runs it, its code never runs from an entry point, so it can neither
call a formula nor register a handler, and reach stays decidable.
"""

from __future__ import annotations

import ast
import os
import re
import tokenize
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

#: A resolved reference: ("module", m) | ("func", m, qual) | ("class", m, c)
#: | ("object", m, name).
Ref = Tuple[str, ...]

#: Directories that hold no product source (tool caches, vendored trees,
#: the product's own tests -- tests are not entry points).
_NOT_SOURCE = frozenset({"__pycache__", "node_modules", "venv", "site-packages"})
_TEST_TOP = frozenset({"tests", "test"})

_TARGET = re.compile(r"(?<![\w.])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):([A-Za-z_]\w*)")
_RUN_MODULE = re.compile(r"-m[\"'\s,]+([A-Za-z_][\w.]*)")
#: Packaging text split into tokens (whitespace, quotes, commas, brackets).
_TOKEN_SPLIT = re.compile(r"[\s\"',\[\]()=]+")
_PACKAGING_SUFFIXES = frozenset({".toml", ".yaml", ".yml", ".cfg", ".ini", ".sh", ".json"})
_PACKAGING_NAMES = frozenset({"Procfile", "Makefile"})
#: A Python interpreter word: ``python``, ``python3.11``, ``/usr/bin/python3``.
_INTERPRETER = re.compile(r"(?:.*/)?python[\d.]*$")
#: Bounds on what is analysed: a module past either is not walked and the
#: record is "unknown" ("bound hit"), never a crash of the audit.
MAX_MODULE_BYTES = 1_000_000
MAX_AST_DEPTH = 200


def formula_locations() -> Tuple[str, str]:
    """Where the Factory's emitter tells the writer formulas live: the
    formula module the writer prompt names, and the formula package its
    definition rows go in (every module under it)."""
    from app.factory.build.formula_resolution import (
        FORMULAS_MODULE_REL,
        WRITER_DEFINITIONS_REL,
    )

    return FORMULAS_MODULE_REL, Path(WRITER_DEFINITIONS_REL).parent.as_posix()


def _is_main_guard(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    sides = [test.left, test.comparators[0]]
    names = [s.id for s in sides if isinstance(s, ast.Name)]
    consts = [s.value for s in sides if isinstance(s, ast.Constant)]
    return isinstance(test.ops[0], ast.Eq) and names == ["__name__"] and consts == ["__main__"]


def _as_chain(node: ast.AST) -> Optional[Tuple[str, List[str]]]:
    attrs: List[str] = []
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return node.id, attrs[::-1]
    return None


def _const_str(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _Module:
    def __init__(self, name: str, rel: str, tree: ast.Module, is_pkg: bool) -> None:
        self.name, self.rel, self.tree, self.is_pkg = name, rel, tree, is_pkg
        self.funcs: Dict[str, ast.AST] = {}
        self.classes: Dict[str, List[str]] = {}
        self.objects: Set[str] = set()
        self.bindings: Dict[str, Ref] = {}
        self.stars: List[str] = []
        self.main_guard: List[ast.stmt] = []
        for stmt in tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[stmt.name] = stmt
            elif isinstance(stmt, ast.ClassDef):
                methods = []
                for item in stmt.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.funcs[f"{stmt.name}.{item.name}"] = item
                        methods.append(f"{stmt.name}.{item.name}")
                self.classes[stmt.name] = methods
            elif isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
                self.objects |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
            elif isinstance(stmt, ast.If) and _is_main_guard(stmt.test):
                self.main_guard = list(stmt.body)
        for node in _shallow(tree.body):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.bindings.update(import_bindings(self, node, self.stars))

    def absolute(self, level: int, module: Optional[str]) -> str:
        if not level:
            return module or ""
        parts = self.name.split(".")
        pkg = parts if self.is_pkg else parts[:-1]
        pkg = pkg[: len(pkg) - (level - 1)] if level > 1 else pkg
        return ".".join([*pkg, module] if module else pkg)


def import_bindings(mod: _Module, node: ast.AST, stars: List[str]) -> Dict[str, Ref]:
    """The names an import statement binds in ``mod``'s namespace."""
    out: Dict[str, Ref] = {}
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.asname:
                out[alias.asname] = ("module", alias.name)
            else:
                top = alias.name.split(".")[0]
                out[top] = ("module", top)
    elif isinstance(node, ast.ImportFrom):
        base = mod.absolute(node.level, node.module)
        for alias in node.names:
            if alias.name == "*":
                stars.append(base)
            else:
                out[alias.asname or alias.name] = ("from", base, alias.name)
    return out


def _shallow(stmts: List[ast.stmt]):
    """Module-level nodes that run at import: not function bodies, not
    method bodies, not the main guard."""
    stack: List[ast.AST] = list(reversed(stmts))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.If) and _is_main_guard(node.test):
            continue
        yield node
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


class _Ctx:
    def __init__(
        self,
        module: str,
        cls: Optional[str],
        locals_: Dict[str, Ref],
        top: bool,
        shadowed: frozenset = frozenset(),
    ) -> None:
        self.module, self.cls, self.locals, self.top = module, cls, locals_, top
        self.shadowed = shadowed


def local_names(fn: ast.AST) -> frozenset:
    """The names a function binds itself (parameters, loop / with /
    comprehension targets, assignments, ``except`` names, nested defs and
    classes), less its ``global`` / ``nonlocal`` declarations. Inside the
    function these hide a module-level name of the same spelling."""
    names: Set[str] = set()
    declared: Set[str] = set()
    for sub in ast.walk(fn):
        if isinstance(sub, ast.arg):
            names.add(sub.arg)
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            names.add(sub.id)
        elif isinstance(sub, (ast.Global, ast.Nonlocal)):
            declared.update(sub.names)
        elif isinstance(sub, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and sub.name:
            names.add(sub.name)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not fn:
            names.add(sub.name)
    return frozenset(names - declared)


class _Walk:
    def __init__(self, modules: Dict[str, _Module], unparsed: Set[str] = frozenset()) -> None:
        self.mods = modules
        #: Modules that do not parse, and those of them a reached import,
        #: a run target or a literal dynamic import loads.
        self.unparsed = set(unparsed)
        self.unparsed_loaded: Set[str] = set()
        named = [*modules, *self.unparsed]
        self.packages = {".".join(m.split(".")[:i]) for m in named for i in range(1, m.count(".") + 1)}
        self.loaded: Set[str] = set()
        self.reached: Set[Ref] = set()
        self.live: Set[Ref] = set()
        self.registered: Dict[Ref, Set[Ref]] = defaultdict(set)
        self.includes: Dict[Ref, Set[Ref]] = defaultdict(set)
        self.dynamic_modules: Dict[str, str] = {}
        self.dynamic_everything: List[str] = []
        self.via_decorator: Dict[Ref, str] = {}
        self.scripts: Set[str] = set()
        self.queue: Deque[Tuple[str, Any]] = deque()

    # -- resolution ---------------------------------------------------------

    def _is_module(self, name: str) -> bool:
        return name in self.mods or name in self.unparsed or name in self.packages

    def lookup(self, module: str, name: str, depth: int = 0) -> Optional[Ref]:
        mod = self.mods.get(module)
        if mod is None or depth > 25:
            return None
        if name in mod.funcs:
            return ("func", module, name)
        if name in mod.classes:
            return ("class", module, name)
        if name in mod.objects:
            return ("object", module, name)
        if name in mod.bindings:
            return self.follow(mod.bindings[name], depth + 1)
        for star in mod.stars:
            found = self.lookup(star, name, depth + 1)
            if found:
                return found
        return None

    def follow(self, binding: Ref, depth: int = 0) -> Optional[Ref]:
        if binding[0] == "module":
            return ("module", binding[1]) if self._is_module(binding[1]) else None
        return self.attr(("module", binding[1]), binding[2], depth)

    def attr(self, ref: Ref, name: str, depth: int = 0) -> Optional[Ref]:
        if ref[0] == "module":
            sub = f"{ref[1]}.{name}"
            if self._is_module(sub):
                return ("module", sub)
            return self.lookup(ref[1], name, depth)
        if ref[0] == "class" and f"{ref[2]}.{name}" in self.mods[ref[1]].funcs:
            return ("func", ref[1], f"{ref[2]}.{name}")
        return None

    def resolve(self, chain: Tuple[str, List[str]], ctx: _Ctx) -> Tuple[Optional[Ref], Optional[Ref]]:
        """(full reference, deepest resolved prefix)."""
        base, attrs = chain
        if base in ctx.locals:
            ref = self.follow(ctx.locals[base])
        elif base in ("self", "cls") and ctx.cls:
            ref = ("class", ctx.module, ctx.cls)
        elif base in ctx.shadowed:
            ref = None
        else:
            ref = self.lookup(ctx.module, base)
        if ref is None:
            return None, None
        for name in attrs:
            nxt = self.attr(ref, name)
            if nxt is None:
                return None, ref
            ref = nxt
        return ref, ref

    # -- reach --------------------------------------------------------------

    def load(self, module: str) -> None:
        parts = module.split(".")
        for i in range(1, len(parts) + 1):
            name = ".".join(parts[:i])
            if name in self.unparsed:
                self.unparsed_loaded.add(name)
            if name in self.mods and name not in self.loaded:
                self.loaded.add(name)
                self.queue.append(("load", name))

    def reach(self, ref: Ref) -> None:
        self.load(ref[1])
        if ref[0] == "func":
            if ref not in self.reached:
                self.reached.add(ref)
                self.queue.append(("func", ref))
        elif ref[0] == "module":
            return
        elif ref[0] == "class":
            for qual in self.mods[ref[1]].classes.get(ref[2], []):
                self.reach(("func", ref[1], qual))
        elif ref[0] == "object":
            self.make_live(ref)

    def script(self, name: str) -> None:
        """``name`` runs as ``__main__``: its module code, then its main guard."""
        self.load(name)
        if self.mods[name].main_guard and name not in self.scripts:
            self.scripts.add(name)
            self.queue.append(("guard", name))

    def make_live(self, obj: Ref) -> None:
        if obj in self.live:
            return
        self.live.add(obj)
        for fn in list(self.registered[obj]):
            self.reach(fn)
        for other in list(self.includes[obj]):
            self.make_live(other)

    def register(self, obj: Ref, ref: Ref) -> None:
        targets = [ref] if ref[0] == "func" else [
            ("func", ref[1], q) for q in self.mods[ref[1]].classes.get(ref[2], [])
        ]
        for fn in targets:
            self.registered[obj].add(fn)
            if obj in self.live:
                self.reach(fn)

    def include(self, obj: Ref, other: Ref) -> None:
        self.includes[obj].add(other)
        if obj in self.live:
            self.make_live(other)

    def run(self) -> None:
        while self.queue:
            kind, item = self.queue.popleft()
            if kind == "load":
                self._load_module(item)
            elif kind == "func":
                self._walk_function(item)
            else:
                mod = self.mods[item]
                ctx = _Ctx(item, None, {}, False)
                for stmt in mod.main_guard:
                    self.visit(stmt, ctx)

    def _load_module(self, name: str) -> None:
        mod = self.mods[name]
        ctx = _Ctx(name, None, {}, True)
        for stmt in mod.tree.body:
            self.visit(stmt, ctx)
        for stmt in mod.tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in stmt.decorator_list:
                    self._decorated(("func", name, stmt.name), deco, ctx)

    def _decorated(self, fn: Ref, deco: ast.expr, ctx: _Ctx) -> None:
        call = deco if isinstance(deco, ast.Call) else None
        chain = _as_chain(call.func if call else deco)
        if call:
            for arg in [*call.args, *(k.value for k in call.keywords)]:
                self.visit(arg, ctx)
        if chain is None:
            return
        if chain[1]:
            prefix, _ = self.resolve((chain[0], chain[1][:-1]), ctx)
            if prefix and prefix[0] == "object":
                self.register(prefix, fn)
                return
        full, _ = self.resolve(chain, ctx)
        if full and full[0] == "func":
            self.reach(full)
            self.via_decorator.setdefault(fn, f"{full[1]}:{full[2]}")

    def _walk_function(self, ref: Ref) -> None:
        node = self.mods[ref[1]].funcs[ref[2]]
        locals_: Dict[str, Ref] = {}
        stars: List[str] = []
        for sub in ast.walk(node):
            if isinstance(sub, (ast.Import, ast.ImportFrom)):
                locals_.update(import_bindings(self.mods[ref[1]], sub, stars))
        cls = ref[2].split(".")[0] if "." in ref[2] else None
        self._signature(node, _Ctx(ref[1], cls, {}, False))
        ctx = _Ctx(ref[1], cls, locals_, False, local_names(node))
        for stmt in node.body:  # type: ignore[attr-defined]
            self.visit(stmt, ctx)

    def _signature(self, node: ast.AST, ctx: _Ctx) -> None:
        """A signature's defaults and the calls in its annotations run when
        the function is defined, in the enclosing scope (its parameters hide
        nothing there): what they name is wired to the function."""
        args: ast.arguments = node.args  # type: ignore[attr-defined]
        for param in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if param is None or param.annotation is None:
                continue
            for sub in ast.walk(param.annotation):
                if isinstance(sub, ast.Call):
                    self.visit(sub, ctx)
        for default in [*args.defaults, *args.kw_defaults]:
            if default is not None:
                self.visit(default, ctx)

    def visit(self, node: ast.AST, ctx: _Ctx) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not ctx.top:  # a nested def runs with its parent
                for child in [*node.decorator_list, *node.body]:
                    self.visit(child, ctx)
            return
        if isinstance(node, ast.ClassDef) and ctx.top:
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.visit(item, ctx)
            return
        if isinstance(node, ast.If) and ctx.top and _is_main_guard(node.test):
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            self._imported(node, ctx)
            return
        if isinstance(node, ast.Call) and self._call(node, ctx):
            return
        if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
            chain = _as_chain(node)
            if chain is not None:
                self._use(chain, ctx)
                return
        for child in ast.iter_child_nodes(node):
            self.visit(child, ctx)

    def _imported(self, node: ast.AST, ctx: _Ctx) -> None:
        mod = self.mods[ctx.module]
        if isinstance(node, ast.Import):
            for alias in node.names:
                self.load(alias.name)
            return
        assert isinstance(node, ast.ImportFrom)
        base = mod.absolute(node.level, node.module)
        self.load(base)
        for alias in node.names:
            if self._is_module(f"{base}.{alias.name}"):
                self.load(f"{base}.{alias.name}")

    def _use(self, chain: Tuple[str, List[str]], ctx: _Ctx) -> None:
        full, partial = self.resolve(chain, ctx)
        if full:
            self.reach(full)
        elif partial and partial[0] == "object":
            self.reach(partial)

    def _call(self, node: ast.Call, ctx: _Ctx) -> bool:
        """True when the call was fully handled here."""
        chain = _as_chain(node.func)
        args = [*node.args, *(k.value for k in node.keywords)]
        if chain is None:
            return False
        base, attrs = chain
        last = attrs[-1] if attrs else base
        builtin = (
            not attrs
            and base not in ctx.locals
            and base not in ctx.shadowed
            and self.lookup(ctx.module, base) is None
        )
        if builtin and base == "getattr" and args:
            return self._getattr(args, ctx)
        if builtin and base in ("globals", "vars", "locals"):
            target = self._target_module(args[0], ctx) if args else ctx.module
            if target:
                self.dynamic_modules.setdefault(target, f"{base}() in {ctx.module}")
            for arg in args:
                self.visit(arg, ctx)
            return True
        if last in ("import_module", "__import__") and self.resolve(chain, ctx)[0] is None:
            name = _const_str(args[0]) if args else None
            if name and self._is_module(name):
                self.reach(("module", name))
            elif not name:
                self.dynamic_everything.append(f"{last}(<computed name>) in {ctx.module}")
            for arg in args[1:]:
                self.visit(arg, ctx)
            return True
        if attrs:
            prefix, _ = self.resolve((base, attrs[:-1]), ctx)
            if prefix and prefix[0] == "object":
                attached = False
                for arg in args:
                    arg_chain = _as_chain(arg)
                    ref = self.resolve(arg_chain, ctx)[0] if arg_chain else None
                    if ref and ref[0] in ("func", "class"):
                        self.register(prefix, ref)
                        attached = True
                    elif ref and ref[0] == "object":
                        self.include(prefix, ref)
                        attached = True
                    else:
                        self.visit(arg, ctx)
                if not attached:
                    self.make_live(prefix)
                return True
        return False

    def _target_module(self, node: ast.AST, ctx: _Ctx) -> Optional[str]:
        chain = _as_chain(node)
        full = self.resolve(chain, ctx)[0] if chain else None
        if full and full[0] in ("module", "class"):
            return full[1]
        return None

    def _getattr(self, args: List[ast.expr], ctx: _Ctx) -> bool:
        target = self._target_module(args[0], ctx)
        name = _const_str(args[1]) if len(args) > 1 else None
        if target is None:
            for arg in args:
                self.visit(arg, ctx)
            return True
        chain = _as_chain(args[0])
        owner = self.resolve(chain, ctx)[0] if chain else None
        if name is not None and owner is not None:
            found = self.attr(owner, name)
            if found:
                self.reach(found)
        else:
            self.dynamic_modules.setdefault(target, f"getattr(<computed name>) in {ctx.module}")
        for arg in args[2:]:
            self.visit(arg, ctx)
        return True


def _product_modules(root: Path) -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d not in _NOT_SOURCE
            and not (rel_dir == Path(".") and d in _TEST_TOP)
        )
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            parts = [*rel_dir.parts, filename[:-3]]
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts and all(p.isidentifier() for p in parts):
                found[".".join(parts)] = Path(dirpath) / filename
    return found


def _is_packaging(path: Path) -> bool:
    """A root file that declares how the product runs (not a README)."""
    return path.is_file() and (
        path.name in _PACKAGING_NAMES
        or path.name.startswith("Dockerfile")
        or path.suffix in _PACKAGING_SUFFIXES
    )


def script_tokens(text: str) -> List[str]:
    """The tokens of ``text`` in a run position: the first non-flag word
    after a Python interpreter on the same line. A copy line, a comment or
    any other line naming a file gives none."""
    out: List[str] = []
    for line in text.splitlines():
        after_python = False
        for token in filter(None, _TOKEN_SPLIT.split(line)):
            if after_python and not token.startswith("-"):
                out.append(token)
            if _INTERPRETER.match(token):
                after_python = True
            elif not token.startswith("-"):
                after_python = False
    return out


def _declared_targets(
    root: Path, modules: Set[str], files: Dict[Path, str]
) -> List[Tuple[str, str, Optional[str]]]:
    """(kind, module, attribute) for each target a root packaging file names.
    A script counts only when a token in a run position names one of the
    product's own module files (``files``: resolved path -> module name)."""
    out: List[Tuple[str, str, Optional[str]]] = []
    for path in sorted(root.iterdir()):
        if not _is_packaging(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for module, attr in _TARGET.findall(text):
            if module in modules:
                out.append(("object", module, attr))
        for module in _RUN_MODULE.findall(text):
            if module in modules:
                out.append(("run", module, None))
        for token in script_tokens(text):
            try:
                name = files.get((root / token).resolve())
            except (OSError, ValueError):
                name = None
            if name in modules:
                out.append(("run", name, None))
    return sorted(set(out), key=lambda t: (t[0], t[1], t[2] or ""))


def _depth(tree: ast.AST) -> int:
    """The deepest nesting in ``tree``, measured without recursion."""
    deepest, stack = 0, [(tree, 1)]
    while stack:
        node, depth = stack.pop()
        deepest = max(deepest, depth)
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return deepest


def parse_bounded(path: Path) -> Tuple[Optional[ast.Module], Optional[str]]:
    """(tree, None), or (None, "bound hit: ...") for a module past the size
    or nesting bound, or one the parser runs out of recursion or memory on.
    Other parse failures raise to the caller ("does not parse")."""
    size = path.stat().st_size
    if size > MAX_MODULE_BYTES:
        return None, f"bound hit: {size} bytes, limit {MAX_MODULE_BYTES}"
    try:
        with tokenize.open(path) as fh:  # BOM and coding line, as Python reads it
            source = fh.read()
        tree = ast.parse(source)
    except (RecursionError, MemoryError) as exc:
        return None, f"bound hit: {type(exc).__name__} while parsing"
    depth = _depth(tree)
    if depth > MAX_AST_DEPTH:
        return None, f"bound hit: nesting {depth}, limit {MAX_AST_DEPTH}"
    return tree, None


def unknown_record(reason: str, formula_modules: List[str]) -> Dict[str, Any]:
    """The record of an analysis that could not decide: never clean."""
    return {
        "status": "unknown",
        "analysis_error": reason,
        "formula_modules": formula_modules,
        "entry_points": [],
        "reached": None,
        "unreached": None,
        "undecided": None,
    }


def formula_reach(product_root: Any) -> Dict[str, Any]:
    """The H-1 reach record for the product at ``product_root``."""
    root = Path(product_root)
    module_rel, package_rel = formula_locations()
    paths = _product_modules(root) if root.is_dir() else {}
    formula_names = sorted(
        name for name, path in paths.items()
        if path.relative_to(root).as_posix() == module_rel
        or path.relative_to(root).as_posix().startswith(package_rel + "/")
    )
    formula_rels = [paths[n].relative_to(root).as_posix() for n in formula_names]
    if not formula_names:
        return unknown_record(
            f"no formula module: neither {module_rel} nor a module under {package_rel}/ exists",
            [],
        )
    modules: Dict[str, _Module] = {}
    broken: Dict[str, str] = {}
    bounded: List[str] = []
    for name, path in paths.items():
        rel = path.relative_to(root).as_posix()
        try:
            tree, bound = parse_bounded(path)
        except (OSError, SyntaxError, ValueError, UnicodeDecodeError) as exc:
            broken[name] = f"{rel} ({type(exc).__name__})"
            continue
        if tree is None:
            bounded.append(f"{rel} ({bound})")
            continue
        modules[name] = _Module(name, rel, tree, path.name == "__init__.py")
    broken_formulas = sorted(broken[n] for n in formula_names if n in broken)
    if broken_formulas:
        return unknown_record(f"formula module does not parse: {', '.join(broken_formulas)}", formula_rels)
    if bounded:
        return unknown_record(f"bound hit: {', '.join(sorted(bounded))}", formula_rels)

    walk = _Walk(modules, set(broken))
    entries: List[str] = []
    files = {path.resolve(): name for name, path in paths.items()}
    for kind, module, attr in _declared_targets(root, {*modules, *broken}, files):
        walk.load(module)
        if kind == "run":
            entries.append(f"run {module}")
            for name in (module, f"{module}.__main__"):
                if name in modules:
                    walk.script(name)
        else:
            ref = walk.lookup(module, attr or "")
            if ref:
                entries.append(f"target {module}:{attr}")
                walk.reach(ref)
    walk.run()
    if walk.unparsed_loaded:
        loaded = sorted(broken[n] for n in walk.unparsed_loaded)
        return unknown_record(
            f"product module does not parse and reached code loads it: {', '.join(loaded)}",
            formula_rels,
        )
    entries += [f"main guard {name}" for name in sorted(walk.scripts)]
    for obj in sorted(walk.live):
        entries += [f"registered on {obj[1]}:{obj[2]}: {f[1]}:{f[2]}" for f in sorted(walk.registered[obj])]
    if not entries:
        return unknown_record(
            "no entry point found: no server or CLI target in the root packaging files",
            formula_rels,
        )

    reached: List[str] = []
    unreached: List[str] = []
    undecided: List[Dict[str, str]] = []
    for name in formula_names:
        for func in sorted(f for f in modules[name].funcs if "." not in f and not f.startswith("_")):
            ref = ("func", name, func)
            label = f"{name}:{func}"
            if ref in walk.reached:
                reached.append(label)
                continue
            why = (
                walk.dynamic_modules.get(name)
                or (walk.dynamic_everything[0] if walk.dynamic_everything else None)
                or (f"handed to decorator {walk.via_decorator[ref]}" if ref in walk.via_decorator else None)
            )
            if why:
                undecided.append({"formula": label, "why": why})
            else:
                unreached.append(label)
    return {
        "status": "analysed",
        "analysis_error": None,
        "formula_modules": formula_rels,
        "entry_points": sorted(set(entries)),
        "reached": reached,
        "unreached": unreached,
        "undecided": undecided,
        "unparsed": sorted(broken.values()),
    }
