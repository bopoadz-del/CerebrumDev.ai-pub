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

import logging
import re
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)


class PublishedKernelUnreadable(RuntimeError):
    """The Factory's own published kernel source could not be read.

    The requirements block is derived from the FEATURE the shipped tree uses,
    and the kernel the Factory vendors into every product is part of that tree.
    If its source cannot be read the scan cannot know which backing packages it
    needs -- and silently omitting them ships a product whose kernel routes fail
    at the first request (an upload route 422s for want of python-multipart).
    So a read failure fails the build loudly here rather than letting a broken
    source quietly drop dependencies.
    """


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


def _published_kernel_texts() -> List[str]:
    """Source text of every kernel file the Factory publishes into a product.

    The requirements block is derived from the FEATURE a file uses, not from an
    import, and the kernel the Factory vendors into every product is part of the
    tree it ships -- a kernel route that posts a form needs the same backing
    package a product route does. But the kernel files are COPIED into the
    product's ``app/`` by the kernel-publish stamp, which may run after this
    scan: on the first pass the tree has no kernel yet, on the second it does,
    and the block flips between passes. Scanning the Factory's own published
    kernel source directly makes the feature detectable on the first pass,
    independent of stamp order, so a single build ships the package the kernel
    needs (a kernel ``UploadFile`` route 422s without ``python-multipart``).
    """
    try:
        from app.factory.build.domain_acceptance import product_kernel_files

        return [text for _rel, text in product_kernel_files()]
    except Exception as exc:  # a broken/missing source must fail loudly, not drop deps
        logger.warning(
            "published kernel source unreadable; failing the requirements build "
            "rather than silently dropping its framework dependencies: %s",
            exc,
        )
        raise PublishedKernelUnreadable(
            "could not read the Factory's published kernel source to derive the "
            "requirements framework block"
        ) from exc


def framework_extras(
    root: Path,
    sources: Sequence[Path] | None = None,
    *,
    include_published_kernel: bool = False,
) -> Dict[str, str]:
    """``{requirement line: why}`` for every framework feature the tree uses.

    By default the scan is the product tree alone (``app``/``scripts``/
    ``tests``): a feature is declared because the product uses it, not because
    some other tree might. ``include_published_kernel`` additionally scans the
    kernel source the Factory vendors into every product. The requirements
    build sets it so a feature the published kernel uses (its upload route needs
    ``python-multipart``) is declared on the first pass, before the
    kernel-publish stamp has copied the kernel into ``app/`` -- without it the
    declaration depends on stamp order and the single-pass build ships a product
    that 422s its own upload route (see ``_published_kernel_texts``).
    """
    paths = list(sources) if sources is not None else _sources(root)
    text = []
    for path in paths:
        try:
            text.append(path.read_bytes().decode("utf-8", "replace"))
        except OSError:  # a file that cannot be read cannot be scanned
            continue
    if include_published_kernel:
        text.extend(_published_kernel_texts())
    blob = "\n".join(text)
    needed: Dict[str, str] = {}
    for requirement, why, patterns in FRAMEWORK_EXTRAS:
        if any(re.search(pattern, blob) for pattern in patterns):
            needed[requirement] = why
    return needed


def framework_lines(root: Path, *, include_published_kernel: bool = False) -> List[str]:
    """The requirements.txt block for the extras this tree needs (with reasons)."""
    needed = framework_extras(root, include_published_kernel=include_published_kernel)
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
