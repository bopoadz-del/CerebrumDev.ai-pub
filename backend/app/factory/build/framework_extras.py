"""Distributions a framework feature needs that importing the framework does not.

FastAPI declares starlette and pydantic. It does NOT declare the packages that
back several of its own documented features: form parsing needs
``python-multipart``, server-side templates need ``jinja2``, signed session
cookies need ``itsdangerous``, ``EmailStr`` needs ``email-validator``. The code
that uses them imports nothing by name -- it writes ``await request.form()`` --
so an import scan sees a clean tree and the package is simply absent at runtime.

Live, this killed a whole product surface. A voice platform's Twilio webhooks
are form-encoded by definition; starlette 1.x requires ``python-multipart`` for
``application/x-www-form-urlencoded`` too, not only for ``multipart/form-data``
as the 0.4x line did. With the package undeclared every webhook answered
``422 webhook body is not readable``: the inbound half of the platform was dead
in the image, and only one test happened to post a form and catch it.

So the declaration is derived from the FEATURE, not from the import: each entry
pairs the source patterns that use a feature with the distribution that makes it
work. The scan covers the product's own code and its suite -- the release gate
runs that suite inside the image, so a test that posts a form needs the package
exactly as the route does.

Biased towards declaring. A pattern that fires on a mention in a comment costs a
few hundred kilobytes of pure Python; a pattern that does not fire costs a
production surface that answers 422 to every caller.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

#: distribution requirement, why it is needed, patterns that mean "needed".
FRAMEWORK_EXTRAS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    (
        "python-multipart>=0.0.9",
        "form parsing: starlette needs it for urlencoded bodies as well as "
        "multipart, and every Twilio/webhook form post 422s without it",
        (
            r"\.form\s*\(",
            r"\bUploadFile\b",
            r"=\s*Form\s*\(",
            r"=\s*File\s*\(",
            r"\bmultipart/form-data\b",
            r"\bapplication/x-www-form-urlencoded\b",
        ),
    ),
    (
        "jinja2>=3.1",
        "server-rendered templates",
        (r"\bJinja2Templates\b", r"\bfrom\s+jinja2\b", r"\bimport\s+jinja2\b"),
    ),
    (
        "itsdangerous>=2.1",
        "signed session cookies",
        (r"\bSessionMiddleware\b", r"\bitsdangerous\b"),
    ),
    (
        "email-validator>=2.0",
        "pydantic EmailStr validation",
        (r"\bEmailStr\b",),
    ),
    (
        "httpx>=0.27",
        "outbound HTTP from the runtime (not the test client, which is dev-only)",
        (r"\bhttpx\.(AsyncClient|Client)\b",),
    ),
)

#: where product source lives. ``tests`` is included on purpose: the release
#: gate runs the suite inside the image, so the suite's needs are the image's.
SCANNED = ("app", "scripts", "tests")


def _sources(root: Path) -> List[Path]:
    found: List[Path] = []
    for rel in SCANNED:
        base = Path(root) / rel
        if base.is_dir():
            found.extend(sorted(base.rglob("*.py")))
    return found


def framework_extras(root: Path, sources: Sequence[Path] | None = None) -> Dict[str, str]:
    """``{requirement line: why}`` for every framework feature the tree uses."""
    paths = list(sources) if sources is not None else _sources(root)
    text = []
    for path in paths:
        try:
            text.append(path.read_bytes().decode("utf-8", "replace"))
        except OSError:  # a file that cannot be read cannot be scanned
            continue
    blob = "\n".join(text)
    needed: Dict[str, str] = {}
    for requirement, why, patterns in FRAMEWORK_EXTRAS:
        if any(re.search(pattern, blob) for pattern in patterns):
            needed[requirement] = why
    return needed


def framework_lines(root: Path) -> List[str]:
    """The requirements.txt block for the extras this tree needs (with reasons)."""
    needed = framework_extras(root)
    if not needed:
        return []
    lines = ["# Framework features used by this product whose backing package", "# FastAPI does not declare."]
    for requirement, why in needed.items():
        lines.append(f"# {requirement.split('>')[0].split('=')[0]}: {why}.")
        lines.append(requirement)
    return lines


def distributions(root: Path) -> Tuple[str, ...]:
    """Just the distribution names, for dedupe against vendored obligations."""
    return tuple(
        re.split(r"[<>=!~\[; ]", requirement, 1)[0].strip().lower().replace("_", "-")
        for requirement in framework_extras(root)
    )
