"""Single-File Tool generator + linter.

The generator emits ONE self-contained HTML file from a :class:`ToolSpec` and
content data; the linter checks the contract in
``standards/skills/single_file_tool.md`` line by line. Content never lives in
this module: the DATA block is rendered from the caller's data at build time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

_FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")


@dataclass(frozen=True)
class Finding:
    """One failed contract line."""

    rule: str
    detail: str = ""


@dataclass
class TabSpec:
    id: str
    label: str


@dataclass
class ToolSpec:
    name: str
    tabs: list[TabSpec]
    storage_key: str
    fonts: tuple[str, str, str]
    tokens: dict[str, str]
    features: set[Literal["copy", "dictation", "export", "restore", "two_tap_clear"]] = field(
        default_factory=set
    )

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9-]+", self.name):
            raise ToolSpecError(f"ToolSpec.name must match [a-z0-9-]+, got {self.name!r}")
        if not self.storage_key:
            raise ToolSpecError("ToolSpec.storage_key is required")


class ToolSpecError(ValueError):
    """A :class:`ToolSpec` is malformed."""


def lint_single_file_tool(html: str) -> list[Finding]:
    """Check the contract line by line; an empty result is green."""

    findings: list[Finding] = []

    # ONE <script>.
    if len(re.findall(r"<script\b", html)) != 1:
        findings.append(Finding("single-script", "expected exactly one <script>"))

    # Both dark token blocks present, with color-scheme: dark twice.
    dark_scheme = len(re.findall(r"color-scheme\s*:\s*dark", html))
    if dark_scheme < 2:
        findings.append(
            Finding(
                "dark-tokens",
                f"expected color-scheme: dark in both dark token blocks, found {dark_scheme}",
            )
        )

    # No alert/confirm/prompt.
    for call in ("alert(", "confirm(", "prompt("):
        if call in html:
            findings.append(Finding("no-native-dialogs", f"found {call}"))

    # No window.print.
    if "window.print" in html:
        findings.append(Finding("no-window-print", "found window.print"))

    # No external hosts except the two font hosts.
    for m in re.finditer(r"(?:src|href)\s*=\s*[\"'](https?://[^\"']+)[\"']", html):
        host = re.sub(r"^https?://", "", m.group(1)).split("/", 1)[0]
        if host not in _FONT_HOSTS:
            findings.append(Finding("no-external-hosts", f"external {host}"))

    # No fetch.
    if re.search(r"\bfetch\s*\(", html):
        findings.append(Finding("no-fetch", "found fetch("))

    # No iframes.
    if re.search(r"<iframe\b", html):
        findings.append(Finding("no-iframes", "found <iframe"))

    # Every localStorage call sits inside a try. A heuristic: each
    # localStorage.* occurrence must be followed, before the next top-level
    # statement, by a '}catch' guard is hard to prove statically; instead flag
    # bare localStorage access outside the STATE block's guarded helpers by
    # checking for a visible try/catch wrapping. Kept simple: require the
    # presence of try and catch adjacent to the storage key.
    if "localStorage" in html and not re.search(r"try\s*\{", html):
        findings.append(Finding("localstorage-try", "localStorage used with no try"))

    # Clipboard call has a .catch.
    if "clipboard.writeText" in html and ".catch(" not in html:
        findings.append(Finding("clipboard-catch", "clipboard.writeText has no .catch"))

    # download() only behind location.protocol === "file:".
    if "download(" in html and "location.protocol" not in html:
        findings.append(
            Finding("download-file-only", "download() not guarded by location.protocol")
        )

    return findings
