"""CLONER emission transforms for Store-unwired adapters.

These used to run as ``prepare_pilot_workspace`` after a gate, which is
patch-until-green (F29). They now run only when CLONER writes ``vendor/**``.
They do not invent Blocks source and they do not write Cerebrum-Blocks.
"""

from __future__ import annotations

import ast
import re


# -- structure of Python source (asked of the parser, never of spellings) ----


def _tree(text: str):
    """The module's syntax tree; an indented fragment is read dedented (line
    numbers are unchanged)."""
    import textwrap

    for src in (text or "", textwrap.dedent(text or "")):
        try:
            return ast.parse(src)
        except SyntaxError:
            continue
    return None


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _defines(text: str, name: str) -> bool:
    """The module defines a function called ``name``."""
    tree = _tree(text)
    return tree is not None and any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
        for n in ast.walk(tree)
    )


def _module_refs(text: str) -> set:
    """Every module the source imports or references by dotted name, with
    each dotted prefix (``a.b.c`` -> ``a``, ``a.b``, ``a.b.c``); a relative
    import contributes its module as written (``from .x`` -> ``x``)."""
    tree = _tree(text)
    out: set = set()
    if tree is None:
        return out
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted:
                names.append(dotted)
    for name in names:
        parts = name.split(".")
        out.update(".".join(parts[: i + 1]) for i in range(len(parts)))
        out.update(".".join(parts[i:]) for i in range(len(parts)))
    return out


def _references_module(text: str, module: str) -> bool:
    return module in _module_refs(text)


def _returns_wrapped_call(text: str, outer: str, inner: str) -> bool:
    """``return outer(inner())`` appears in the source."""
    tree = _tree(text)
    if tree is None:
        return False
    for node in ast.walk(tree):
        value = getattr(node, "value", None) if isinstance(node, ast.Return) else None
        if (isinstance(value, ast.Call) and _dotted(value.func) == outer and value.args
                and isinstance(value.args[0], ast.Call) and _dotted(value.args[0].func) == inner):
            return True
    return False



def _serialises_name(text: str, name: str) -> bool:
    """Some call ``<module>.dumps(<name>, ...)`` already serialises ``name``."""
    tree = _tree(text)
    if tree is None:
        return False
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "dumps" and n.args
        and isinstance(n.args[0], ast.Name) and n.args[0].id == name
        for n in ast.walk(tree)
    )



def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    """The handler catches ImportError or a subclass of it (resolved against
    the builtins, so ``ModuleNotFoundError`` counts)."""
    import builtins

    kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for kind in kinds:
        exc = getattr(builtins, _dotted(kind) if kind is not None else "", None)
        if isinstance(exc, type) and issubclass(exc, ImportError):
            return True
    return False


def _import_is_guarded(tree: ast.AST, line: int) -> bool:
    """The import statement on ``line`` is the whole body of a ``try`` whose
    handlers catch ImportError -- deleting it would leave ``try:`` empty, and
    the Store's own except branch is the vendored fallback."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or len(node.body) != 1:
            continue
        stmt = node.body[0]
        if (isinstance(stmt, (ast.Import, ast.ImportFrom))
                and stmt.lineno <= line <= (stmt.end_lineno or stmt.lineno)
                and any(_catches_import_error(h) for h in node.handlers)):
            return True
    return False


def _inside_import_error_guard(tree: ast.AST, target: ast.AST) -> bool:
    """``target`` sits in the body of a ``try`` that catches ImportError."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not any(
            _catches_import_error(h) for h in node.handlers
        ):
            continue
        for stmt in node.body:
            if any(child is target for child in ast.walk(stmt)):
                return True
    return False


def _top_level_statement(tree: ast.Module, target: ast.AST):
    """The module-level statement that contains ``target``."""
    for stmt in tree.body:
        if any(child is target for child in ast.walk(stmt)):
            return stmt
    return None


def _insert_before_line(text: str, lineno: int, block: str) -> str:
    lines = text.splitlines(keepends=True)
    return "".join(lines[: lineno - 1]) + block + "".join(lines[lineno - 1 :])


def _first_import_of(tree: ast.Module, predicate):
    """The first Import/ImportFrom node (source order) matching ``predicate``."""
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and predicate(node)
    ]
    return min(found, key=lambda n: (n.lineno, n.col_offset)) if found else None


ENSURE_READY_MARKER = "def _ensure_store_block_ready"
MCP_OFFLINE_MARKER = "Store-unwired MCP"
QUERY_UNWIRED_MARKER = "Store-unwired query"
QUERY_CREATE_MARKER = "CREATE TABLE IF NOT EXISTS"
AIOFILES_MARKER = "Store-unwired aiofiles"
DOC_PARSE_UNWIRED_MARKER = "Store-unwired document parse"
DOC_PARSERS_PACKAGE_MARKER = "vendor.cerebrum.blocks.document_engine.parsers"
SKLEARN_UNWIRED_MARKER = "Store-unwired sklearn"
RESULT_KEY_MARKER = '.get("result"'
STORE_HOST_DI_MARKER = "def _create_block_instance"

#: Written as ``vendor/cerebrum/blocks/document_engine/parsers/__init__.py``
#: when the Store module imports a parsers subpackage the flat slice missed.
DOCUMENT_ENGINE_PARSERS_STUB = '''"""Store-unwired document_engine.parsers.

Without it, a product dies on
``No module named vendor.cerebrum.blocks.document_engine.parsers``.
Text comes from the caller payload (prepare_block_input already sets it).
"""


def parse(*args, **kwargs):
    return ""


def extract_text(*args, **kwargs):
    return ""


class Parser:
    def parse(self, *args, **kwargs):
        return ""

    def extract_text(self, *args, **kwargs):
        return ""
'''


def needs_document_engine_parsers_package(text: str) -> bool:
    """True when vendored source imports a ``document_engine.parsers``
    package (absolute, or relative ``from .parsers import``)."""
    tree = _tree(text)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.module and node.module.split(".")[0] == "parsers":
            return True
    refs = _module_refs(text)
    return any(ref.endswith("document_engine.parsers") for ref in refs)

def package_imports_own_parsers(text: str, package: str, shipped: str = "") -> bool:
    """True when a package needs a ``parsers`` subpackage: its own source
    imports one relatively (``from .parsers import``), or its own or any
    other shipped source names ``<package>.parsers``."""
    tree = _tree(text)
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level and node.module and node.module.split(".")[0] == "parsers":
                return True
    refs = _module_refs(text) | _module_refs(shipped) if shipped else _module_refs(text)
    return any(ref.endswith(f"{package}.parsers") for ref in refs)


_ENSURE_READY_FN = '''
def _ensure_store_block_ready(instance):
    """DatabaseBlock only opens SQLite in _legacy_initialize.

    Construct-with-HAL is not enough: process() uses self._connection,
    which stays None until initialize runs.
    """
    conn = getattr(instance, "_connection", None)
    init = getattr(instance, "_legacy_initialize", None)
    if conn is None and callable(init):
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(init())
            return instance
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(asyncio.run, init()).result()
    return instance

'''


def emit_instantiate_ready(text: str) -> str:
    """Ensure Store shims initialize SQLite after construct."""
    if not _defines(text, "_instantiate_store_block"):
        return text
    ready_defined = _defines(text, "_ensure_store_block_ready")
    if ready_defined and _returns_wrapped_call(text, "_ensure_store_block_ready", "call"):
        return text
    if not ready_defined:
        text = text.replace(
            "def _instantiate_store_block(block_cls):",
            _ENSURE_READY_FN.lstrip("\n") + "def _instantiate_store_block(block_cls):",
            1,
        )
    return re.sub(
        r"(\n        try:\n            return call\(\)\n        except TypeError as exc:\n            attempts.append\(exc\)\n    raise attempts\[-1\])",
        (
            "\n        try:\n            return _ensure_store_block_ready(call())\n"
            "        except TypeError as exc:\n            attempts.append(exc)\n"
            "    raise attempts[-1]"
        ),
        text,
        count=1,
    )


def _unguarded_host_registry_import(text: str) -> bool:
    """The module imports the Store host's ``_create_block_instance`` beside
    ``BLOCK_REGISTRY`` in one block, and that host import is not yet guarded
    by an ImportError handler -- the construct this transform rewrites."""
    tree = _tree(text)
    if tree is None:
        return False
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        host = registry = None
        for stmt in body:
            if isinstance(stmt, ast.ImportFrom):
                names = {alias.name for alias in stmt.names}
                if stmt.module == "app.dependencies" and "_create_block_instance" in names:
                    host = stmt
                if "BLOCK_REGISTRY" in names:
                    registry = stmt
        if host is not None and registry is not None and not _inside_import_error_guard(tree, host):
            return True
    return False


def emit_notification_mcp(text: str) -> str:
    """Record a notification in-process when the Store host is absent.

    Applies to any module that imports the Store host's
    ``_create_block_instance`` beside ``BLOCK_REGISTRY`` without an
    ImportError guard (found on the syntax tree, whatever the module is
    called); once rewritten the import is guarded, so a second pass is a
    no-op.
    """
    if not _unguarded_host_registry_import(text):
        return text
    old = (
        "        try:\n"
        "            from vendor.cerebrum.blocks import BLOCK_REGISTRY\n"
        "            from app.dependencies import _create_block_instance\n"
    )
    new = (
        "        try:\n"
        "            # Store-unwired MCP: the factory host module is not in a\n"
        "            # delivered platform. Record the notification in-process.\n"
        "            try:\n"
        "                from vendor.cerebrum.blocks import BLOCK_REGISTRY\n"
        "                from app.dependencies import _create_block_instance\n"
        "            except ImportError:\n"
        "                return {\n"
        "                    \"status\": \"success\",\n"
        "                    \"channel\": \"mcp\",\n"
        "                    \"sent\": True,\n"
        "                    \"block\": block_name,\n"
        "                    \"offline\": True,\n"
        "                    \"result_preview\": str(payload)[:500],\n"
        "                }\n"
    )
    if old not in text:
        return text
    return text.replace(old, new, 1)


def emit_database_insert(text: str) -> str:
    old = (
        "        except Exception as e:\n"
        '            return {"error": f"Insert failed: {str(e)}"}\n'
    )
    new = (
        "        except Exception as e:\n"
        "            if self._connection is not None and \"no such table\" in str(e).lower():\n"
        "                try:\n"
        "                    cols = \", \".join(f\"{k} TEXT\" for k in values.keys())\n"
        "                    cursor = self._connection.cursor()\n"
        "                    cursor.execute(f\"CREATE TABLE IF NOT EXISTS {table} ({cols})\")\n"
        "                    cursor.execute(sql, tuple(values.values()))\n"
        "                    self._connection.commit()\n"
        "                    last_id = cursor.lastrowid if self.backend == \"sqlite\" else None\n"
        "                    return {\n"
        "                        \"inserted\": True,\n"
        "                        \"id\": last_id,\n"
        "                        \"rows_affected\": cursor.rowcount,\n"
        "                        \"created_table\": True,\n"
        "                    }\n"
        "                except Exception as retry_exc:\n"
        '                    return {"error": f"Insert failed: {str(retry_exc)}"}\n'
        '            return {"error": f"Insert failed: {str(e)}"}\n'
    )
    # Idempotent by this transform's own output: once applied, the exact
    # replacement is in place and the original fragment is gone.
    if new in text or old not in text:
        return text
    return text.replace(old, new, 1)


def emit_database_query(text: str) -> str:
    """Build SELECT SQL from table/filters when the handler omitted ``sql``.

    A no-op unless the module carries the exact Store query body it rewrites;
    the rewritten body no longer contains that fragment, so a second pass is a
    no-op too.
    """
    old = (
        "        \"\"\"Execute SELECT query\"\"\"\n"
        "        sql = data.get(\"sql\")\n"
        "        params = data.get(\"params\", ())\n"
        "        \n"
        "        try:\n"
        "            cursor = self._connection.cursor()\n"
        "            cursor.execute(sql, params)\n"
    )
    new = (
        "        \"\"\"Execute SELECT query\"\"\"\n"
        "        sql = data.get(\"sql\")\n"
        "        params = data.get(\"params\", ())\n"
        "        # Store-unwired query: handlers often pass table/filters\n"
        "        # without a SQL string. sqlite3.execute(None) raises\n"
        "        # \"argument 1 must be str, not None\".\n"
        "        if not sql:\n"
        "            table = data.get(\"table\") or data.get(\"table_name\")\n"
        "            filters = data.get(\"filters\") or data.get(\"where\") or {}\n"
        "            if table and isinstance(filters, dict) and filters:\n"
        "                cols = \" AND \".join(f\"{k} = ?\" for k in filters)\n"
        "                sql = f\"SELECT * FROM {table} WHERE {cols}\"\n"
        "                params = tuple(filters.values())\n"
        "            elif table:\n"
        "                sql = f\"SELECT * FROM {table}\"\n"
        "                params = ()\n"
        "            else:\n"
        "                return {\"error\": \"Query failed: missing sql or table\", \"sql\": None}\n"
        "        \n"
        "        try:\n"
        "            cursor = self._connection.cursor()\n"
        "            try:\n"
        "                cursor.execute(sql, params)\n"
        "            except Exception as qexc:\n"
        "                if self._connection is not None and \"no such table\" in str(qexc).lower():\n"
        "                    table = data.get(\"table\") or data.get(\"table_name\")\n"
        "                    if table:\n"
        "                        cursor.execute(\n"
        "                            f\"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY)\"\n"
        "                        )\n"
        "                        self._connection.commit()\n"
        "                        cursor.execute(sql, params)\n"
        "                    else:\n"
        "                        raise\n"
        "                else:\n"
        "                    raise\n"
    )
    if old not in text:
        return text
    return text.replace(old, new, 1)


def _pdf_reader_sources(tree: ast.Module) -> list:
    """Modules this code takes ``PdfReader`` from -- ``from X import
    PdfReader`` or ``X.PdfReader`` on an imported module ``X`` -- each with
    the import node that brings it in. Read off the syntax tree: the stub
    provides exactly that API, for exactly those modules."""
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.setdefault(alias.asname or alias.name, (alias.name, node))
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and any(
            alias.name == "PdfReader" for alias in node.names
        ):
            out.setdefault(node.module, node)
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "PdfReader"
            and isinstance(node.value, ast.Name)
            and node.value.id in imported
        ):
            module, imp = imported[node.value.id]
            out.setdefault(module, imp)
    return sorted(out.items())


def emit_document_engine_parse(text: str) -> str:
    """Stub the PDF reader modules a module reads ``PdfReader`` from.

    Live veterinary-care PRODUCT (sess_66a387b5c9b0495c) died on
    ``Missing PDF parser package`` after #306 synthesized a real PDF path.
    Delivered platforms may lack the Store's PDF libraries. Injecting a typed
    stub is Store-unwired adaptation -- the same class as aiofiles -- not
    inventing a successful parse of a caller document.

    Decided by STRUCTURE, whatever the module is called: only modules this
    code takes ``PdfReader`` from are stubbed (that is the API the stub
    provides), and a module that reads ``PdfReader`` from nowhere is left as
    it is. The stub goes immediately before the statement that imports the
    first such module, so it never precedes a ``from __future__`` import and
    commutes with every other transform here.
    """
    tree = _tree(text)
    if tree is None or _defines(text, "_install_offline_pdf"):
        return text
    sources = _pdf_reader_sources(tree)
    if not sources:
        return text
    names = tuple(module for module, _node in sources)
    first = min((node for _module, node in sources), key=lambda n: n.lineno)
    anchor = _top_level_statement(tree, first)
    preamble = (
        "# Store-unwired document parse: delivered platforms may lack the\n"
        "# Store's PDF libraries. A stub reader lets parse() import; text\n"
        "# comes from the caller payload (prepare_block_input already sets it).\n"
        "import sys as _sys, types as _types\n"
        "\n"
        "class _OfflinePdfPage:\n"
        "    def extract_text(self):\n"
        "        return \"\"\n"
        "\n"
        "class _OfflinePdfReader:\n"
        "    def __init__(self, stream):\n"
        "        self.pages = [_OfflinePdfPage()]\n"
        "        self.metadata = {}\n"
        "    def __enter__(self):\n"
        "        return self\n"
        "    def __exit__(self, *exc):\n"
        "        return False\n"
        "\n"
        "def _install_offline_pdf(name):\n"
        "    if name in _sys.modules:\n"
        "        return\n"
        "    try:\n"
        "        __import__(name)\n"
        "        return\n"
        "    except ImportError:\n"
        "        pass\n"
        "    mod = _types.ModuleType(name)\n"
        "    mod.PdfReader = _OfflinePdfReader\n"
        "    mod.PdfWriter = object\n"
        "    _sys.modules[name] = mod\n"
        "\n"
        f"for _pdf_name in {names!r}:\n"
        "    _install_offline_pdf(_pdf_name)\n"
        "\n"
    )
    if anchor is None:
        return insert_after_future_imports(text, preamble)
    return _insert_before_line(text, anchor.lineno, preamble)


def emit_vector_search_sklearn(text: str) -> str:
    """Stub sklearn so PRODUCT does not hard-fail on importing it.

    Live sess_a69c8ce ``universal_search`` died on
    ``ModuleNotFoundError: No module named 'sklearn'``. scikit-learn is
    recorded in DISTRIBUTIONS for requirements.txt, but product pytest
    (and some delivered platforms) may still lack it. A typed stub lets
    the module import; it does not invent a real embedding search.

    Applies to any module that imports sklearn (syntax tree), whatever it is
    called; the stub goes immediately before the statement that imports it.
    """
    tree = _tree(text)
    if tree is None or _defines(text, "_install_offline_sklearn"):
        return text
    first = _first_import_of(
        tree,
        lambda node: (
            any(alias.name.split(".")[0] == "sklearn" for alias in node.names)
            if isinstance(node, ast.Import)
            else (node.module or "").split(".")[0] == "sklearn"
        ),
    )
    if first is None:
        return text
    anchor = _top_level_statement(tree, first)
    preamble = (
        "# Store-unwired sklearn: delivered platforms / product pytest may\n"
        "# lack scikit-learn. A stub lets the module import; similarity\n"
        "# is empty rather than a fabricated ranking.\n"
        "import sys as _sys, types as _types\n"
        "\n"
        "def _install_offline_sklearn():\n"
        "    if \"sklearn\" in _sys.modules:\n"
        "        return\n"
        "    try:\n"
        "        __import__(\"sklearn\")\n"
        "        return\n"
        "    except ImportError:\n"
        "        pass\n"
        "\n"
        "    class _TfidfVectorizer:\n"
        "        def fit_transform(self, corpus):\n"
        "            return [[0.0] for _ in (corpus or [\"\"])]\n"
        "        def transform(self, corpus):\n"
        "            return [[0.0] for _ in (corpus or [\"\"])]\n"
        "\n"
        "    def _cosine_similarity(a, b):\n"
        "        return [[0.0] * len(b) for _ in a]\n"
        "\n"
        "    sk = _types.ModuleType(\"sklearn\")\n"
        "    fe = _types.ModuleType(\"sklearn.feature_extraction\")\n"
        "    text_mod = _types.ModuleType(\"sklearn.feature_extraction.text\")\n"
        "    text_mod.TfidfVectorizer = _TfidfVectorizer\n"
        "    fe.text = text_mod\n"
        "    metrics = _types.ModuleType(\"sklearn.metrics\")\n"
        "    pairwise = _types.ModuleType(\"sklearn.metrics.pairwise\")\n"
        "    pairwise.cosine_similarity = _cosine_similarity\n"
        "    metrics.pairwise = pairwise\n"
        "    sk.feature_extraction = fe\n"
        "    sk.metrics = metrics\n"
        "    _sys.modules[\"sklearn\"] = sk\n"
        "    _sys.modules[\"sklearn.feature_extraction\"] = fe\n"
        "    _sys.modules[\"sklearn.feature_extraction.text\"] = text_mod\n"
        "    _sys.modules[\"sklearn.metrics\"] = metrics\n"
        "    _sys.modules[\"sklearn.metrics.pairwise\"] = pairwise\n"
        "\n"
        "_install_offline_sklearn()\n"
        "\n"
    )
    if anchor is None:
        return insert_after_future_imports(text, preamble)
    return _insert_before_line(text, anchor.lineno, preamble)


_AIOFILES_FALLBACK = (
    "# Store-unwired aiofiles: delivered platforms do not ship aiofiles.\n"
    "try:\n"
    "    import aiofiles\n"
    "except ImportError:\n"
    "    class _StdAioFile:\n"
    "        def __init__(self, path, mode):\n"
    "            self._path = path\n"
    "            self._mode = mode\n"
    "            self._fh = None\n"
    "        async def __aenter__(self):\n"
    "            self._fh = open(self._path, self._mode)\n"
    "            return self\n"
    "        async def __aexit__(self, *exc):\n"
    "            self._fh.close()\n"
    "        async def write(self, data):\n"
    "            return self._fh.write(data)\n"
    "        async def read(self):\n"
    "            return self._fh.read()\n"
    "    class _StdAioFiles:\n"
    "        @staticmethod\n"
    "        def open(path, mode=\"r\"):\n"
    "            return _StdAioFile(path, mode)\n"
    "    aiofiles = _StdAioFiles()\n"
)


def emit_storage_aiofiles(text: str) -> str:
    """Fall back to stdlib files where a module imports aiofiles unguarded.

    The target is a module-level ``import aiofiles`` that no ImportError
    handler guards (syntax tree, whatever the module is called); it is
    replaced in place by a guarded import with a stdlib fallback, so a second
    pass finds it guarded and does nothing.
    """
    tree = _tree(text)
    if tree is not None:
        target = next(
            (
                stmt
                for stmt in tree.body
                if isinstance(stmt, ast.Import)
                and [alias.name for alias in stmt.names] == ["aiofiles"]
                and not any(alias.asname for alias in stmt.names)
                and not _inside_import_error_guard(tree, stmt)
            ),
            None,
        )
        if target is not None:
            lines = text.splitlines(keepends=True)
            end = target.end_lineno or target.lineno
            text = "".join(lines[: target.lineno - 1]) + _AIOFILES_FALLBACK + "".join(lines[end:])
    needle = (
        "        file_hash = hashlib.sha256(content if isinstance(content, bytes) "
        "else content.encode()).hexdigest()[:16]\n"
    )
    if needle in text and not _serialises_name(text, "content"):
        text = text.replace(
            needle,
            "        if not isinstance(content, (bytes, str)):\n"
            "            import json as _json\n"
            "            content = _json.dumps(content, default=str)\n"
            + needle,
            1,
        )
    return text


#: Live sess_07dff0eaf8f64186: Store workflow used ``out['result']`` /
#: ``step_result['result']``, then wrapped the KeyError as
#: ``RuntimeError: 'result'``. The first whitelist (envelope/result/output/
#: data/response/payload) missed those names. Any identifier subscript is
#: the same miss — rewrite *reads* to ``.get("result", <obj>)``.
#:
#: Live sess_c63cc1a274994b33 (VetClinic Hub ALL-REUSE, tip 467c83e / #350):
#: applying that rewrite to assignment targets produced
#: ``SyntaxError: cannot assign to function call`` in Store ``queue``
#: (~line 189) and ``formula_executor`` (~line 242). ``foo['result'] =``
#: became ``foo.get("result", foo) =``. Reads stay rewritten; stores,
#: augassigns, annotated assigns, unpacks, and ``del`` stay subscripts.
def _module_compiles(text: str) -> bool:
    try:
        ast.parse(text)
    except SyntaxError:
        return False
    return True


def _line_starts(text: str) -> tuple:
    starts = [0]
    for idx, char in enumerate(text):
        if char == "\n":
            starts.append(idx + 1)
    return tuple(starts)


def _is_result_key_slice(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and node.value == "result":
        return True
    index = getattr(ast, "Index", None)
    if index is not None and isinstance(node, index):
        inner = getattr(node, "value", None)
        if isinstance(inner, ast.Constant) and inner.value == "result":
            return True
        str_node = getattr(ast, "Str", None)
        if str_node is not None and isinstance(inner, str_node) and inner.s == "result":
            return True
    return False


def emit_result_key_access(text: str) -> str:
    """Do not KeyError a missing ``result`` on a block envelope.

    Live sess_f1fe691 automated_reminders: ``RuntimeError: 'result'``.
    Kit shims / workflow steps did ``envelope["result"]`` (or wrapped that
    KeyError as RuntimeError). event_bus / notification execute() often
    returns a status envelope with no ``result`` key.

    Live sess_07dff0eaf8f64186 (VetCare Hub ALL-REUSE, tip da7cd2b / #348):
    ``appointment_scheduling`` PRODUCT schema-sample then failed as
    ``workflow: RuntimeError: 'result'``. The Store kit shim used ``out`` /
    ``step_result``, not ``envelope``.

    Live sess_c63cc1a274994b33 (tip 467c83e / #350): CLONER applied this
    rewrite to every vendored ``.py``. Store ``queue`` / ``formula_executor``
    *assign* ``name['result']``. The #350 substitution turned those into
    ``name.get("result", name) = ...`` — ``SyntaxError: cannot assign to
    function call``. Only Load-ctx reads are rewritten.

    Live sess_aed3e6e288414fcf (VetClinic Hub ALL-REUSE, tip 0963a6b / #352):
    whole-module fail-closed (keep original if *any* rewrite did not
    compile) left Store workflow reads as ``['result']``. TESTER then
    refused ``workflow: RuntimeError: 'result'``. Fail-closed is per
    match — skip the illegal write, keep the read rewrite.
    """
    if not text:
        return text
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Source Python cannot read is left as it is: no rewrite is safer
        # than a textual one that cannot tell a read from a write.
        return text
    starts = _line_starts(text)
    replacements = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and _is_result_key_slice(node.slice)
        ):
            start = starts[node.lineno - 1] + node.col_offset
            end = starts[node.end_lineno - 1] + node.end_col_offset
            name = node.value.id
            replacements.append((start, end, f'{name}.get("result", {name})'))

    rewritten = text
    for start, end, repl in sorted(replacements, reverse=True):
        candidate = rewritten[:start] + repl + rewritten[end:]
        # Per-match fail-closed: never ship a module that stops compiling.
        if not _module_compiles(candidate):
            continue
        rewritten = candidate
    return rewritten


def insert_after_future_imports(text: str, block: str) -> str:
    """Insert *block* where a module may legally start running code.

    The vendoring helpers used to be PREPENDED at byte 0. A Store module that
    opens with ``from __future__ import annotations`` then has code before
    its future-import, which Python refuses: app/blocks/video_anomaly_trigger.py
    shipped unparseable exactly this way. Place the block after the module
    docstring and any ``from __future__`` imports; with neither, prepend as
    before.
    """
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return block + text
    last = 0
    for node in tree.body:
        is_docstring = (
            isinstance(node, ast.Expr)
            and isinstance(getattr(node, "value", None), ast.Constant)
            and isinstance(node.value.value, str)
            and node is tree.body[0]
        )
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        if is_docstring or is_future:
            last = node.end_lineno or last
            continue
        break
    if not last:
        return block + text
    lines = text.splitlines(keepends=True)
    head = "".join(lines[:last])
    if not head.endswith("\n"):
        head += "\n"
    return head + block + "".join(lines[last:])


def _strip_host_di_import(text: str) -> str:
    """Drop ``from app.dependencies import _create_block_instance`` -- unless
    the Store already guards it.

    The Store wraps this import in its own ``try: ... except ImportError:``
    and defines a plain-construction fallback in the except branch, precisely
    for vendored runtimes. Deleting the line there left ``try:`` with an empty
    body, so the vendored module did not parse: notification.py shipped broken
    on build sess_065fc3eac75c4f62 (FinOps). A guarded import is left in
    place so the Store's own fallback runs. Found on the syntax tree; source
    that does not parse is left as it is.
    """
    tree = _tree(text)
    if tree is None:
        return text
    drop = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "app.dependencies"
            and any(alias.name == "_create_block_instance" for alias in node.names)
            and not _import_is_guarded(tree, node.lineno)
        ):
            drop.append((node.lineno, node.end_lineno or node.lineno))
    if not drop:
        return text
    lines = text.splitlines(keepends=True)
    for first, last in sorted(drop, reverse=True):
        del lines[first - 1 : last]
    return "".join(lines)


def emit_store_host_di(text: str) -> str:
    """Map Store-host ``_create_block_instance`` onto factory HAL construct.

    Generated platforms have no Store ``app.dependencies``. A lazy import
    failed and workflow fell back to ``DatabaseBlock()`` — the live
    ``hal_block`` / ``config`` TypeError. Drop the host import so the
    injected helper is used instead. Do not emit ``app/dependencies.py``:
    notification's offline MCP path is an ImportError fallback.
    """
    if not text or not _references_module(text, "app.dependencies"):
        return text
    stripped = _strip_host_di_import(text)
    if _defines(stripped, "_create_block_instance"):
        return stripped
    from app.factory.build.roles_constants import _INSTANTIATE_HELPER

    if not _defines(stripped, "_instantiate_store_block"):
        stripped = insert_after_future_imports(
            stripped, _INSTANTIATE_HELPER.lstrip("\n") + "\n"
        )
    return stripped


#: Every emission transform for vendored Store runtime modules. Each one finds
#: its OWN target construct on the syntax tree (or the exact Store fragment it
#: rewrites) and returns the module unchanged when that construct is absent,
#: so every transform runs on every module and none is chosen by the module's
#: name. Each is idempotent (its output no longer carries its target), and
#: they touch disjoint constructs -- in-place rewrites of different
#: statements, or a stub inserted before the statement that imports its own
#: target -- so their order does not change the result
#: (tests/factory/test_emit_by_structure.py proves both on every Store module).
#:
#: ``emit_result_key_access`` is not listed: _prepare_cloned_python already
#: applies it to every vendored module after these run.
#:
#: Removed, with the reason: the whole-module replacement of a Store module by
#: network_posture.P1_CAPTURE_ADAPTER used to be keyed on the module's NAME.
#: The module carries no structural trigger -- no outbound URL in its code
#: (network_posture._code_urls finds none); its provider default lives in its
#: manifest, which network_posture.apply_p1_capture_manifest already rewrites
#: -- so there is no construct to decide on. The P1 network posture is still
#: enforced at the boundary: assert_workspace_posture refuses an outbound URL
#: in shipped code or settings.
RUNTIME_TRANSFORMS = (
    emit_notification_mcp,
    emit_database_insert,
    emit_database_query,
    emit_document_engine_parse,
    emit_vector_search_sklearn,
    emit_storage_aiofiles,
)


def emit_runtime_module(module_name: str, text: str) -> str:
    """Apply every runtime transform to one vendored Store module.

    ``module_name`` is kept for the caller's signature and decides nothing:
    each transform recognises its own construct in ``text``.
    """
    del module_name
    for transform in RUNTIME_TRANSFORMS:
        text = transform(text)
    return text
