"""Settings an operator supplies at deploy time are not a build failure.

Live, a platform that integrates a third-party sign-in:

    TESTER failed -- suite_red: FAILED
    tests/test_models.py::test_every_model_round_trips - KeyError: 'GOOGLE_CLIENT_ID'

Owner: "this should not be a error, it will be provided later". The Factory
ships PILOTS to a DevOps team who deploy them; a credential that team will
supply is not something a build can have, and a Factory-written test that dies
importing the product for want of it is measuring the build box, not the
product.

ONE SOURCE, TWO CONSUMERS. The Factory boots a product in two places -- the
test harness (tests/conftest.py) and the acceptance script
(scripts/acceptance.py) -- and both get the same text below, so they cannot
come to disagree about what "provided later" means.

NOTHING IS NAMED HERE. Which settings a product needs is the product's own
business, so it is read off the product:

* REQUIRED means an ``os.environ["X"]`` subscript read in the product's own
  ``app/`` tree -- the exact expression that raises KeyError. ``os.getenv`` and
  ``os.environ.get`` are left alone on purpose: they already tolerate absence,
  and standing in a value there would switch ON an optional integration
  (a junk Sentry DSN, a fake webhook URL) that the product had correctly left off.
* The VALUE is the product's own example from its ``.env.example`` when it
  gives one (so ``PORT=8000`` stays an int), else a labelled placeholder.
* Anything already set is never touched.

What this does NOT change: the delivered product. A required setting is still
required at a real boot and still fails there by name. Only the Factory's own
harness stands in for it, and the harness already refuses all outbound network,
so a stand-in credential cannot reach the service it names.
"""

from __future__ import annotations

#: Stdlib-only Python, pasted verbatim into generated files. Call
#: ``_stand_in_for_deploy_time_settings(<platform root>)`` BEFORE the first
#: import of the product's ``app`` package.
SNIPPET = '''
DEPLOY_TIME_PLACEHOLDER = "set-at-deploy"


def _stand_in_for_deploy_time_settings(root):
    """Give every setting the product REQUIRES, and the build cannot have, a
    stand-in -- so importing the product does not KeyError on a credential its
    operator supplies at deploy time. Returns the names stood in for."""
    import ast as _ast
    import os as _os
    from pathlib import Path as _Path

    root = _Path(root)
    examples = {}
    env_example = root / ".env.example"
    if env_example.is_file():
        for raw in env_example.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.split(" #", 1)[0].strip().strip("'\\"")
            if key.strip():
                examples[key.strip()] = value

    def _is_environ(node):
        if isinstance(node, _ast.Attribute) and node.attr == "environ":
            return isinstance(node.value, _ast.Name) and node.value.id == "os"
        return isinstance(node, _ast.Name) and node.id == "environ"

    required = set()
    app_dir = root / "app"
    if app_dir.is_dir():
        for path in app_dir.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, ValueError):
                continue
            for node in _ast.walk(tree):
                if (
                    isinstance(node, _ast.Subscript)
                    and isinstance(node.ctx, _ast.Load)
                    and _is_environ(node.value)
                    and isinstance(node.slice, _ast.Constant)
                    and isinstance(node.slice.value, str)
                ):
                    required.add(node.slice.value)

    stood_in = []
    for name in sorted(required):
        if name not in _os.environ:
            _os.environ[name] = examples.get(name) or DEPLOY_TIME_PLACEHOLDER
            stood_in.append(name)
    return stood_in
'''
