"""One-session coder dispatch + owner Pause/Stop control.

The WRITER compiles one brief, then this module hands it to FACTORY_CODE_CLI.
A keyed production Floor must have that binary AND credentials for the
selected coder (Kimi/Moonshot: ``~/.kimi-code/config.toml`` with a usable
``default_model``; DeepSeek: ``DEEPSEEK_API_KEY`` for Kimi Code →
DeepSeek V4 Pro over OpenAI-compat) or fail-closed with
``FACTORY_CODE_CLI_UNAVAILABLE`` / ``FACTORY_CODE_CLI_CREDENTIALS_MISSING``
/ ``FACTORY_CODE_CLI_NO_MODEL`` BEFORE it claims the coding agent has
taken over. A CLI exit of ``No model configured`` is also
``FACTORY_CODE_CLI_NO_MODEL`` (still ``FACTORY_CODE_CLI_FAILED`` honesty).
A 404 / Permission denied / leftover Claude Code ``unrecognized_model``
on the configured model is ``FACTORY_CODE_CLI_MODEL_DENIED`` (distinct
from NO_MODEL). A ``429`` / insufficient-balance / account-suspended
exit is ``FACTORY_CODE_CLI_BILLING`` (still ``FACTORY_CODE_CLI_FAILED``
honesty). Claude Code is not the DeepSeek vehicle.
When STEP 0 inventory has **zero gaps** (all capabilities REUSE-present),
that named billing/auth miss must not discard the factory-grounded emit +
harvest keep-path (#333/#336/#337). Remaining GENERATE inventory_gaps
then fall through to the factory coder LLM (same gated brief /
``generate_from_compiled_brief`` path the Floor already uses for README
via OpenRouter) — not a new per-capability handle() loop, and not a
claimed ≥2h CLI session. HTTP oneshot is CI-only
(``FACTORY_BRIEF_HTTP_ONESHOT=1``).

Control is a file the Floor writes. The dispatcher polls it: pause waits,
stop terminates the session. Owner eyes are the monitor.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import shutil
import subprocess
import time
from http import HTTPStatus
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from app.factory.build.agent_process import (
    agent_popen_kwargs,
    kill_agent_tree,
    terminate_agent_tree,
    track_agent,
)
from app.factory.build.failure_kinds import TIMEOUT, record_failure_kind
from app.factory.build.workflow_accept import (
    handler_has_prepared_event_bus_step,
    handler_satisfies_event_bus_contract,
)

logger = logging.getLogger("cerebrumdev.factory.coder_session")

BRIEF_REL = Path("docs") / "coder_brief.md"
LOG_REL = Path("docs") / "coder_session.log"
CONTROL_REL = Path("docs") / "coder_control.json"
RECEIPT_REL = Path("docs") / "coder_receipt.json"

CONTROL_RUN = "run"
CONTROL_PAUSE = "pause"
CONTROL_STOP = "stop"

BRIEF_DISPATCH_ENV = "FACTORY_BRIEF_DISPATCH"
BRIEF_HTTP_ONESHOT_ENV = "FACTORY_BRIEF_HTTP_ONESHOT"
NAMED_BLOCKER_CLI = "FACTORY_CODE_CLI_UNAVAILABLE"
NAMED_BLOCKER_CLI_CREDS = "FACTORY_CODE_CLI_CREDENTIALS_MISSING"
NAMED_BLOCKER_CLI_FAILED = "FACTORY_CODE_CLI_FAILED"
NAMED_BLOCKER_CLI_NO_MODEL = "FACTORY_CODE_CLI_NO_MODEL"
NAMED_BLOCKER_CLI_MODEL_DENIED = "FACTORY_CODE_CLI_MODEL_DENIED"
NAMED_BLOCKER_CLI_BILLING = "FACTORY_CODE_CLI_BILLING"
#: DeepSeek CLI was ready but C-BRIEF never ran, or the run SUCCESS-ed
#: thin templates (stub_rate≈1.0 / written=0) before a real stage-1 wall.
#: Store-complete REUSE/COMPOSE is not a skip (sess_9d0b43c81b2b4620).
NAMED_BLOCKER_CLI_UNUSED = "FACTORY_CODE_CLI_UNUSED"
#: Kimi/DeepSeek CLI exited 0 but harvest found no agent-written handlers.
#: Factory-grounded fill after that exit is not C-BRIEF authorship
#: (sess_4e1ec7afa3894dc8 / #368 class under the kimi vehicle).
NAMED_BLOCKER_CLI_NO_AUTHORSHIP = "FACTORY_CODE_CLI_NO_AUTHORSHIP"
#: Named empty-harvest reasons folded into NO_AUTHORSHIP detail (not new
#: honesty classes). Fail-closed stays FACTORY_CODE_CLI_NO_AUTHORSHIP.
CLI_EMPTY_DESCRIBED_NOT_WRITTEN = "described-not-written"
CLI_EMPTY_EMPTY_COMPLETION = "empty-completion"
#: Why "wrong-path" is no longer a named reason: it was read from the CLI's
#: prose (``Write(...)`` / ``Wrote ...``), and the CLI log carries no typed
#: tool record to read it from. The classifier is advisory -- it names a
#: reason inside NO_AUTHORSHIP, never a class -- and says so in the detail.
CLI_EMPTY_WRITE_TARGETS_UNREAD = (
    "write targets not itemised: the CLI log carries no typed tool record"
)
FACTORY_STAGING_DIRNAME = ".factory-staging"
#: CLI/LLM wrote some handlers, but below the launching-ready full-pilot
#: floor (need = min(5, max(1, n_required)); unknown n_required keeps
#: need=5). Distinct from written=0 / stub_rate=1.0
#: (``FACTORY_CODE_CLI_NO_AUTHORSHIP``).
from app.factory.build.authorship import SOURCE_CODER_CLI  # noqa: E402
from app.factory.build.model_call import CLOSED, MODEL_CALL_STATE  # noqa: E402

NAMED_BLOCKER_CLI_THIN_AUTHORSHIP = "FACTORY_CODE_CLI_THIN_AUTHORSHIP"


def named_blocker_of(detail: object) -> str:
    """The named blocker a refusal leads with (``<BLOCKER>: <why>``), or ''.

    Every named refusal is written ``f"{NAMED_BLOCKER_...}: ..."``; the token
    before the first colon is the typed field, read by position -- not by
    searching the sentence that follows it.
    """
    head, sep, _rest = str(detail or "").partition(":")
    return head.strip() if sep else ""
#: CLI wrote ``def handle(`` for a GENERATE gap but harvest dropped it
#: only because event_bus keepability failed. Distinct from "never wrote".
NAMED_BLOCKER_CLI_UNKEEPABLE_EVENT_BUS = "FACTORY_CODE_CLI_UNKEEPABLE_EVENT_BUS"
#: In-flight FACTORY_CODE_CLI was terminated after the staged 30→45
#: wall — not UNUSED (never started) and not a zero-harvest exit 0.
NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL = "FACTORY_CODE_CLI_HUNG_KILLED_BY_WALL"
#: Process restart (Render deploy) left a model_call NOTE with no live
#: WRITER / FACTORY_CODE_CLI worker. Not a silent 7230s watchdog lie.
NAMED_BLOCKER_CLI_ORPHANED = "FACTORY_CODE_CLI_ORPHANED"
NAMED_BLOCKER_STOPPED = "CODER_SESSION_STOPPED"
NAMED_BLOCKER_PAUSED = "CODER_SESSION_PAUSED"
CLI_PREFLIGHT_BLOCKERS = frozenset(
    {NAMED_BLOCKER_CLI, NAMED_BLOCKER_CLI_CREDS, NAMED_BLOCKER_CLI_NO_MODEL}
)
#: Named billing/auth misses that still allow factory-grounded REUSE
#: emit + harvest for verified REUSE rows (sess_d5789a91). GENERATE
#: gaps on the same miss use the factory coder LLM second leg.
CLI_AUTH_BILLING_BLOCKERS = frozenset(
    {
        NAMED_BLOCKER_CLI_BILLING,
        NAMED_BLOCKER_CLI_CREDS,
        NAMED_BLOCKER_CLI_NO_MODEL,
        NAMED_BLOCKER_CLI_MODEL_DENIED,
    }
)
#: CLI honesty classes that fall through GENERATE gaps to the factory
#: coder LLM. UNAVAILABLE is included (no binary); generic FAILED only
#: when the detail is a billing miss. NO_MODEL stays fail-closed.
CLI_GENERATE_LLM_FALLTHROUGH_BLOCKERS = frozenset(
    {
        NAMED_BLOCKER_CLI_BILLING,
        NAMED_BLOCKER_CLI_CREDS,
        NAMED_BLOCKER_CLI,
    }
)
KEEP_PATH_FACTORY_GROUNDED_REUSE = "factory_grounded_reuse"
UNRECOGNIZED_MODEL_HINT = "unrecognized_model"
#: A coder CLI that tags its own error: ``[claude-code:unrecognized_model]``.
_CLI_ERROR_TAG_RE = re.compile(r"\[[a-z][a-z0-9-]*:([a-z_]+)\]", re.IGNORECASE)
#: The protocol token that opens an HTTP status line (RFC 9112:
#: ``HTTP-version SP status-code``): ``HTTP`` or ``HTTP/<version>``.
HTTP_PROTOCOL_TOKEN = "HTTP"
_STATUS_NUMBER_RE = re.compile(r"(?<![\w.])([45]\d\d)(?![\w.])")
#: Provider answers that refuse the ACCOUNT rather than the request:
#: unauthenticated, unpaid, or over its limit. 429 is here because a real
#: provider answers account suspension with it (Moonshot, sess_d5789a91:
#: "429 Too Many Requests -- suspended due to insufficient balance"), and a
#: throttled account cannot do the work now either: both fall through to the
#: factory LLM the same way.
_BILLING_STATUSES = (
    HTTPStatus.UNAUTHORIZED,
    HTTPStatus.PAYMENT_REQUIRED,
    HTTPStatus.TOO_MANY_REQUESTS,
)
#: Provider answers that mean the MODEL was refused: forbidden, not found.
_MODEL_DENIED_STATUSES = (HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND)

#: Moonshot Open Platform ids for ``[providers.kimi]`` +
#: ``https://api.moonshot.ai/v1``. Kimi Code CLI 0.41 config-files still
#: show managed ``kimi-code/k3`` / ``k3`` (``managed:kimi-code``,
#: ``https://api.kimi.com/coding/v1``) which needs TTY ``/login``. That
#: pair 404s / Permission-denied on a platform key. Docs:
#: https://platform.moonshot.ai/docs/guide/start-using-kimi-api
#: https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files
KIMI_CODE_MODEL_ENV = "KIMI_CODE_MODEL"
KIMI_CODE_MODEL_ID_ENV = "KIMI_CODE_MODEL_ID"
DEFAULT_KIMI_CODE_MODEL = "kimi-k3"
DEFAULT_KIMI_CODE_MODEL_ID = "kimi-k3"
#: Managed /login catalog aliases → Moonshot API (alias, model id).
_MANAGED_TO_MOONSHOT = {
    "kimi-code/k3": (DEFAULT_KIMI_CODE_MODEL, DEFAULT_KIMI_CODE_MODEL_ID),
    "k3": (DEFAULT_KIMI_CODE_MODEL, DEFAULT_KIMI_CODE_MODEL_ID),
    "kimi-code/kimi-for-coding": ("kimi-k2.7-code", "kimi-k2.7-code"),
    "kimi-for-coding": ("kimi-k2.7-code", "kimi-k2.7-code"),
    "kimi-code/kimi-for-coding-highspeed": (
        "kimi-k2.7-code-highspeed",
        "kimi-k2.7-code-highspeed",
    ),
    "kimi-for-coding-highspeed": (
        "kimi-k2.7-code-highspeed",
        "kimi-k2.7-code-highspeed",
    ),
}
_BROKEN_MANAGED_IDS = frozenset(
    {"k3", "kimi-for-coding", "kimi-for-coding-highspeed"}
)
_MODEL_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
#: The top-level TOML key a Kimi Code config names its model with.
DEFAULT_MODEL_KEY = "default_model"
_MODEL_FIELD_RE = re.compile(
    r'(?m)^\s*model\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S+))'
)
#: One-line operator note. Dashboard clicks stay owner-gated.
OWNER_GATED_CLI_LOG = (
    "FACTORY_CODE_CLI / DEEPSEEK_API_KEY / KIMI_CODE_API_KEY owner-gated "
    "on Render — not claimed set"
)


class CodeCliUnavailable(RuntimeError):
    """Operator/config class: FACTORY_CODE_CLI is not on the factory host."""

    blocker = NAMED_BLOCKER_CLI


class CodeCliCredentialsMissing(CodeCliUnavailable):
    """Selected coder is on PATH but its credentials are missing.

    Kimi: ``~/.kimi-code/config.toml`` absent. DeepSeek:
    ``DEEPSEEK_API_KEY`` unset while DeepSeek is the selected coder.
    """

    blocker = NAMED_BLOCKER_CLI_CREDS


class CodeCliFailed(RuntimeError):
    """CLI ran and exited non-zero. Not a silent template success."""

    blocker = NAMED_BLOCKER_CLI_FAILED


class CodeCliNoModelConfigured(CodeCliUnavailable):
    """No usable default_model (headless /login is not available).

    Preflight class: generate-start / brief dispatch must refuse before
    WRITER takeover. Mid-run CLI ``No model configured`` uses the same
    named blocker (still ``FACTORY_CODE_CLI_FAILED`` honesty).
    """

    blocker = NAMED_BLOCKER_CLI_NO_MODEL


class CodeCliModelDenied(CodeCliFailed):
    """CLI ran but the configured model 404'd / was unrecognized."""

    blocker = NAMED_BLOCKER_CLI_MODEL_DENIED


class CodeCliBillingFailed(CodeCliFailed):
    """CLI ran; Moonshot/account billing refused the session.

    Still ``FACTORY_CODE_CLI_FAILED`` honesty — never a ≥2h CLI session.
    Empty-gap REUSE may continue factory-grounded emit + harvest.
    """

    blocker = NAMED_BLOCKER_CLI_BILLING


def brief_dispatch_enabled() -> bool:
    """Default ON. FACTORY_BRIEF_DISPATCH=0 restores per-capability shots.

    DeepSeek-ready C-BRIEF is not optional: leftover ``FACTORY_BRIEF_DISPATCH=0``
    must not send a keyed Kimi→DeepSeek Floor through per-capability
    OpenRouter factory-LLM shots.
    """
    raw = os.getenv(BRIEF_DISPATCH_ENV, "1").strip().lower()
    if raw not in {"0", "false", "no", "off"}:
        return True
    return deepseek_cli_ready()


def http_oneshot_enabled() -> bool:
    """CI/dev escape only. Production Floor must not set this."""
    raw = os.getenv(BRIEF_HTTP_ONESHOT_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def brief_requires_cli() -> bool:
    """Keyed brief path must dispatch via FACTORY_CODE_CLI.

    Unkeyed / ``FACTORY_CODER_ENABLED=0`` still uses honest templates.
    ``FACTORY_BRIEF_HTTP_ONESHOT=1`` keeps the CI oneshot contract.
    ``ENV=test`` does not refuse at generate-start unless
    ``FACTORY_BRIEF_REQUIRE_CLI=1`` (the mutation) **or** DeepSeek CLI
    is already ready (kimi on PATH + ``DEEPSEEK_API_KEY``). Production
    (``ENV=production``) and keyed non-test hosts fail-closed.

    Cursor executor keys (``writer_uses_cli_pivot``) take Generate off
    FACTORY_CODE_CLI — do not demand Kimi/DeepSeek credentials then.

    Leftover ``FACTORY_BRIEF_REQUIRE_CLI=0`` must not treat a ready
    DeepSeek+kimi session as optional — that is how sess_b9fbae7
    coded via in-process OpenRouter while health showed a ready CLI.
    """
    if http_oneshot_enabled():
        return False
    from app.factory.coder import coder_enabled

    if not coder_enabled():
        return False
    if deepseek_cli_ready():
        return True
    if not brief_dispatch_enabled():
        return False
    require = os.getenv("FACTORY_BRIEF_REQUIRE_CLI", "").strip().lower()
    if require in {"0", "false", "no", "off"}:
        return False
    if require in {"1", "true", "yes", "on"}:
        return True
    return os.getenv("ENV", "").strip().lower() != "test"


def cli_unavailable_detail(command: Optional[str] = None) -> str:
    """Named-class operator text. Names the env, not a dashboard click."""
    from app.factory.coder import (
        CODE_CLI_ENV,
        DEEPSEEK_API_KEY_ENV,
        LEGACY_CODE_CLI_ENV,
        code_cli_command,
        factory_code_provider,
    )

    cli = (command or code_cli_command()).strip() or "kimi"
    provider = factory_code_provider()
    creds = (
        f"{DEEPSEEK_API_KEY_ENV} (Kimi Code → DeepSeek V4 Pro OpenAI-compat)"
        if provider == "deepseek"
        else "KIMI_CODE_API_KEY writes ~/.kimi-code/config.toml "
        "(Moonshot). DeepSeek uses DEEPSEEK_API_KEY under kimi, not Claude Code"
    )
    return (
        f"{NAMED_BLOCKER_CLI}: {cli!r} is not an executable on this host. "
        f"Set {CODE_CLI_ENV} (wins) or {LEGACY_CODE_CLI_ENV} to the agentic "
        "coder binary (`kimi`, or an absolute path) and provide "
        f"CLI credentials ({creds}). HTTP oneshot is not a FACTORY_CODE_CLI "
        f"session; set {BRIEF_HTTP_ONESHOT_ENV}=1 only for CI. "
        f"{OWNER_GATED_CLI_LOG}."
    )


def kimi_credentials_home() -> Path:
    return Path(os.environ.get("KIMI_CODE_HOME") or (Path.home() / ".kimi-code"))


def kimi_credentials_file() -> Path:
    return kimi_credentials_home() / "config.toml"


def credentials_file_present() -> bool:
    return kimi_credentials_file().is_file()


def cli_requires_kimi_credentials(command: Optional[str] = None) -> bool:
    """Moonshot Kimi Code authenticates via config.toml.

    Credentials are expected when the resolved command is kimi (default
    name, ``KIMI_CODE_CLI``, or a path whose basename contains ``kimi``)
    and DeepSeek is **not** the selected coder. DeepSeek under kimi uses
    ``DEEPSEEK_API_KEY`` (boot writes ``[providers.deepseek]``).
    """
    from app.factory.coder import code_cli_command, deepseek_coder_selected

    if deepseek_coder_selected(command):
        return False
    cli = (command or code_cli_command()).strip()
    names = [Path(cli).name.lower()] if cli else []
    resolved = resolve_code_cli(cli) if cli else resolve_code_cli()
    if resolved:
        names.append(Path(resolved).name.lower())
    from app.factory.code_cli import is_kimi_code_cli

    return any(is_kimi_code_cli(name) for name in names)


def cli_requires_deepseek_credentials(command: Optional[str] = None) -> bool:
    """DeepSeek V4 Pro authenticates Kimi Code via ``DEEPSEEK_API_KEY``."""
    from app.factory.coder import deepseek_coder_selected

    return deepseek_coder_selected(command)


def cli_credentials_ok(command: Optional[str] = None) -> bool:
    if cli_requires_deepseek_credentials(command):
        from app.factory.coder import deepseek_api_key

        return bool(deepseek_api_key())
    if not cli_requires_kimi_credentials(command):
        return True
    return credentials_file_present()


def read_kimi_config_text() -> str:
    dest = kimi_credentials_file()
    if not dest.is_file():
        return ""
    try:
        return dest.read_text(encoding="utf-8")
    except OSError:
        return ""


def config_has_usable_default_model(text: str) -> bool:
    """True when config.toml has default_model and a matching [models] row.

    Kimi Code CLI 0.41 requires the alias in the ``models`` table. Empty,
    whitespace, or managed leftovers (``kimi-code/k3`` / ``k3``) are not
    usable on a headless Moonshot Floor.
    """
    alias = config_default_model(text)
    if not alias:
        return False
    model_id = config_model_id(text, alias)
    if is_broken_managed_model(alias, model_id):
        return False
    if not config_has_model_alias(text, alias):
        return False
    return bool(model_id)


def cli_default_model_ok(command: Optional[str] = None) -> bool:
    """Credentials-only config.toml is not a usable Kimi model."""
    if cli_requires_deepseek_credentials(command):
        from app.factory.code_cli import deepseek_model_configured

        return deepseek_model_configured()
    if not cli_requires_kimi_credentials(command):
        return True
    if not credentials_file_present():
        return True
    return config_has_usable_default_model(read_kimi_config_text())


def deepseek_cli_ready(command: Optional[str] = None) -> bool:
    """True when C-BRIEF must use Kimi Code → DeepSeek, not factory LLM.

    Health-ready photograph: ``FACTORY_CODE_CLI=kimi`` (DeepSeek default)
    is an executable, ``DEEPSEEK_API_KEY`` is present, and a DeepSeek coding
    model id is resolvable. HTTP oneshot stays the CI escape.

    When this is true, Floor Approve must dispatch the compiled brief
    through FACTORY_CODE_CLI even if STEP 0 is 100% REUSE/COMPOSE
    (zero GENERATE gaps). Empty ``inventory_gaps`` is not a skip.
    """
    if http_oneshot_enabled():
        return False
    from app.factory.coder import (
        coder_enabled,
        deepseek_api_key,
        deepseek_coder_selected,
    )

    if not coder_enabled():
        return False
    if not deepseek_coder_selected(command):
        return False
    if not deepseek_api_key():
        return False
    if not cli_available(command):
        return False
    if not cli_credentials_ok(command):
        return False
    return cli_default_model_ok(command)


def is_agent_written_source(source: str) -> bool:
    """True for real coder LLM / CLI authorship — not factory-grounded fill.

    Delegates to ``authorship.is_coding_agent_source`` (#375) so Floor
    counters and the SUCCESS gate stay on one definition.
    """
    from app.factory.build.authorship import is_coding_agent_source

    return is_coding_agent_source(source)


def _handler_text_is_factory_grounded(text: str) -> bool:
    """The handler's WRITER-role stamp names a Factory grounded emitter."""
    from app.factory.build.authorship import (
        is_factory_grounded_source,
        stamped_writer_source,
    )

    return is_factory_grounded_source(stamped_writer_source(text))


def _workspace_handler_is_factory_grounded(root: Path, capability_id: str) -> bool:
    name = str(capability_id).replace("-", "_")
    path = Path(root) / "app" / "actions" / f"{name}.py"
    if not path.is_file():
        return False
    try:
        return _handler_text_is_factory_grounded(path.read_text(encoding="utf-8"))
    except OSError:
        return False


def _handler_roots(root: Any) -> List[Path]:
    """Staging first, then destination — same order as RoleWorkspace reads.

    A rework WRITER starts with an empty staging tree. Work-id / harvest
    / receipt union must still see keepable handlers and the prior
    coder_receipt already committed to destination (sess_5782f226 run8:
    empty staging re-listed the same gaps and overwrote a 10-id receipt).
    persist_accept stays staging-only — leftover dest GENERATE must not
    look present while staging is empty.
    """
    if root is None:
        return []
    staging = Path(getattr(root, "workspace", root))
    out = [staging]
    dest = getattr(root, "destination", None)
    if dest is None:
        return out
    dest_p = Path(dest)
    try:
        if dest_p.resolve() != staging.resolve():
            out.append(dest_p)
    except OSError:
        if dest_p != staging:
            out.append(dest_p)
    return out


def _unique_ids(*groups: Any) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for group in groups:
        for item in group or ():
            cid = str(item or "").strip()
            if cid and cid not in seen:
                seen.add(cid)
                out.append(cid)
    return out


def harvest_factory_planted_ids(
    root: Optional[Path],
    compiled: Any = None,
    dispatch: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Keepable factory-grounded REUSE plants already on disk.

    GENERATE persist envelopes stay out (``generate_persist_ids`` /
    remaining ``inventory_gaps``). Those are still not C-BRIEF
    authorship — counting them would reopen the #374 thin SUCCESS path.
    """
    roots = _handler_roots(root)
    if not roots:
        return []
    exclude = set()
    if dispatch is not None:
        exclude.update(
            str(x).strip()
            for x in (dispatch.get("generate_persist_ids") or ())
            if str(x).strip()
        )
        exclude.update(
            str(x).strip()
            for x in (dispatch.get("inventory_gaps") or ())
            if str(x).strip()
        )
    candidates: List[str] = []
    if compiled is not None:
        exclude.update(inventory_gap_ids(compiled))
        candidates.extend(reuse_inventory_ids(compiled))
    if dispatch is not None:
        candidates.extend(
            str(x).strip()
            for x in (dispatch.get("factory_planted_ids") or ())
            if str(x).strip()
        )
        candidates.extend(
            str(x).strip()
            for x in (dispatch.get("kept_handler_ids") or ())
            if str(x).strip()
        )
    if not candidates:
        for base in roots:
            actions = base / "app" / "actions"
            if actions.is_dir():
                for path in sorted(actions.glob("*.py")):
                    if path.stem != "__init__":
                        candidates.append(path.stem)
    out: List[str] = []
    seen: set = set()
    for cid in candidates:
        cid = str(cid or "").strip()
        if not cid or cid in seen or cid in exclude:
            continue
        if not any(
            _workspace_handler_is_factory_grounded(base, cid) for base in roots
        ):
            continue
        if _reuse_needs_hole_fill(root, cid):
            continue
        seen.add(cid)
        out.append(cid)
    return out


def _real_agent_written(
    snapshot: Mapping[str, Any],
    state: Optional[Mapping[str, Any]] = None,
    workspace: Any = None,
) -> int:
    """Count CLI/LLM harvest plus keepable Factory-planted REUSE handlers.

    Factory-grounded GENERATE persist envelopes stay excluded. An empty
    ``cli_authored_ids`` after exit 0 is still not CLI authorship — but
    a later phase shot must not hide keepable plants already on disk
    (sess_5782f226 run7: written=0 / stub_rate=0.0 after PHASE 2/3).
    """
    dispatch = dict((state or {}).get("brief_dispatch") or {})
    planted = list(dispatch.get("factory_planted_ids") or [])
    if workspace is not None and not planted:
        planted = harvest_factory_planted_ids(
            workspace,
            dispatch=dispatch,
        )
    if "cli_authored_ids" in dispatch:
        return len(
            _unique_ids(
                dispatch.get("cli_authored_ids"),
                dispatch.get("factory_llm_written_ids"),
                dispatch.get("handler_ids"),
                planted,
            )
        )
    if snapshot.get("cli_or_llm_written") is not None:
        return int(snapshot.get("cli_or_llm_written") or 0) + len(
            _unique_ids(planted)
        )
    return int(snapshot.get("agent_written") or 0)


def cli_dispatch_attempted(
    state: Optional[Mapping[str, Any]] = None, ledger: Any = None
) -> bool:
    """True when WRITER actually started FACTORY_CODE_CLI (via=cli)."""
    dispatch = dict((state or {}).get("brief_dispatch") or {})
    if str(dispatch.get("via") or "") == "cli":
        return True
    if ledger is None:
        return False
    for event in getattr(ledger, "events", lambda: ())():
        payload = getattr(event, "payload", None) or {}
        if str(payload.get("source") or "") == SOURCE_CODER_CLI:
            return True
    return False


def thin_stub_success_blocked(
    *,
    snapshot: Mapping[str, Any],
    elapsed_s: float,
    state: Optional[Mapping[str, Any]] = None,
    ledger: Any = None,
    workspace: Any = None,
    plan: Any = None,
    blueprint: Any = None,
) -> Optional[str]:
    """Named blocker: DeepSeek CLI ready, no real CLI/LLM authorship.

    sess_9d0b43c81b2b4620 / sess_4e1ec7afa3894dc8: Store-green SUCCESS
    with ``written=0`` / stub_rate≈1.0 while health showed DeepSeek+kimi
    ready. Factory-grounded hole-fill after a CLI exit 0 is not authorship.
    After a stage-1 wall the run must still fail-closed — never thin
    SUCCESS from templates when the CLI was ready.
    """
    if not deepseek_cli_ready():
        return None
    written = _real_agent_written(snapshot, state, workspace=workspace)
    try:
        stub_rate = float(snapshot.get("stub_rate") or 0.0)
    except (TypeError, ValueError):
        stub_rate = 0.0
    from app.factory.build.authorship import (
        full_pilot_authorship_need,
        n_required_capabilities_from,
    )

    n_required = n_required_capabilities_from(
        snapshot,
        workspace,
        state=state,
        plan=plan,
        blueprint=blueprint,
    )
    if n_required is None:
        n_required = n_required_capabilities_from(
            state, workspace, plan=plan, blueprint=blueprint
        )
    need = full_pilot_authorship_need(n_required)

    if written >= need:
        return None
    attempted = bool(snapshot.get("cli_attempted")) or cli_dispatch_attempted(
        state, ledger
    )
    dispatch = dict((state or {}).get("brief_dispatch") or {})
    via_cli = str(dispatch.get("via") or "") == "cli" or attempted
    from app.factory.build.budget_inspect import STAGE_1_S

    hung = str(dispatch.get("blocker") or "") == NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL
    if hung and written == 0:
        return (
            f"{NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL}: FACTORY_CODE_CLI was "
            "killed after the staged coder wall while a model call was still "
            f"open (written={written}, stub_rate={stub_rate}, "
            f"elapsed={float(elapsed_s):.0f}s). Not UNUSED — do not SUCCESS "
            "a Store-green pilot from pure templates."
        )
    unkeepable = list(dispatch.get("cli_unkeepable_event_bus_ids") or [])
    if (
        via_cli
        and written == 0
        and (
            str(dispatch.get("blocker") or "")
            == NAMED_BLOCKER_CLI_UNKEEPABLE_EVENT_BUS
            or unkeepable
        )
    ):
        return (
            f"{NAMED_BLOCKER_CLI_UNKEEPABLE_EVENT_BUS}: FACTORY_CODE_CLI "
            f"wrote handle() for {unkeepable or 'GENERATE gap(s)'} but "
            "harvest failed only event_bus keepability "
            f"(written={written}, stub_rate={stub_rate}). Not a never-wrote "
            "miss — do not SUCCESS thin templates."
        )
    if (
        via_cli
        and "cli_authored_ids" in dispatch
        and len(list(dispatch.get("cli_authored_ids") or [])) == 0
        and written == 0
    ):
        return (
            f"{NAMED_BLOCKER_CLI_NO_AUTHORSHIP}: DeepSeek/kimi "
            "FACTORY_CODE_CLI exited without harvested agent-written "
            f"handlers (written={written}, stub_rate={stub_rate}). "
            "A CLI exit 0 is not C-BRIEF authorship — do not SUCCESS "
            "thin templates."
        )
    if 0 < written < need:
        cli_n = len(list(dispatch.get("cli_authored_ids") or []))
        n_req = f", n_required={n_required}" if n_required is not None else ""
        return (
            f"{NAMED_BLOCKER_CLI_THIN_AUTHORSHIP}: authorship is below "
            f"the full-pilot floor (written={written}, "
            f"cli_authored_ids={cli_n}, "
            f"need ≥{need}{n_req}). "
            "Do not SUCCESS a Store-green pilot from thin authorship."
        )
    if not attempted:
        return (
            f"{NAMED_BLOCKER_CLI_UNUSED}: DeepSeek FACTORY_CODE_CLI is ready "
            "but C-BRIEF was not dispatched (via≠cli). Store-complete "
            "REUSE/COMPOSE is not a skip — do not SUCCESS thin templates "
            f"(written={written}, stub_rate={stub_rate})."
        )
    return (
        f"{NAMED_BLOCKER_CLI_UNUSED}: DeepSeek FACTORY_CODE_CLI is ready "
        f"but authorship is still thin (written={written}, "
        f"stub_rate={stub_rate}, elapsed={float(elapsed_s):.0f}s"
        f"{'' if float(elapsed_s) + 1.0 < float(STAGE_1_S) else ', after stage-1 wall'}). "
        "Do not SUCCESS a Store-green pilot from pure templates."
    )


def kimi_prompt_model_alias() -> str:
    """Alias for documented ``kimi --model`` on headless ``--prompt``.

    DeepSeek-selected C-BRIEF uses the DeepSeek catalog id
    (``deepseek-v4-pro``). Moonshot prefers the file's usable
    ``default_model``, then ``KIMI_CODE_MODEL`` / ``kimi-k3``.
    """
    from app.factory.coder import deepseek_code_model, deepseek_coder_selected

    if deepseek_coder_selected():
        return deepseek_code_model()
    text = read_kimi_config_text()
    alias = config_default_model(text)
    if alias and config_has_usable_default_model(text):
        return alias
    return kimi_code_default_model()


def cli_no_model_detail(command: Optional[str] = None) -> str:
    """Named-class operator text for binary + file, no usable default_model."""
    from app.factory.coder import (
        CODE_CLI_ENV,
        DEFAULT_DEEPSEEK_MODEL,
        code_cli_command,
    )

    cli = (command or code_cli_command()).strip() or "kimi"
    if cli_requires_deepseek_credentials(command):
        return (
            f"{NAMED_BLOCKER_CLI_NO_MODEL}: {cli!r} is an executable on this "
            "host but no DeepSeek coding model is configured. Set "
            f"DEEPSEEK_CODE_MODEL (default {DEFAULT_DEEPSEEK_MODEL}) for the "
            "Kimi Code OpenAI-compat subprocess. "
            f"{CODE_CLI_ENV} / CEREBRUM_LLM_API_KEY / ANTHROPIC_MODEL do not "
            "configure the DeepSeek coding model. HTTP oneshot is not a "
            f"FACTORY_CODE_CLI session; set {BRIEF_HTTP_ONESHOT_ENV}=1 only "
            f"for CI. A templated pilot zip is not a ≥2h CLI session. "
            f"{OWNER_GATED_CLI_LOG}."
        )
    dest = kimi_credentials_file()
    return (
        f"{NAMED_BLOCKER_CLI_NO_MODEL}: {cli!r} is an executable on this host "
        f"and {dest} is present (credentials_file_present=true) but has no "
        "usable default_model / [models] entry. Headless Floor cannot run "
        "`kimi` /login. Set KIMI_CODE_API_KEY so boot writes default_model "
        f"(KIMI_CODE_MODEL, default {DEFAULT_KIMI_CODE_MODEL} / Moonshot API) "
        f"and [models.\"{DEFAULT_KIMI_CODE_MODEL}\"]. {CODE_CLI_ENV} / "
        "CEREBRUM_LLM_API_KEY do not configure the Kimi Code model. HTTP "
        "oneshot is not a FACTORY_CODE_CLI session; set "
        f"{BRIEF_HTTP_ONESHOT_ENV}=1 only for CI. A templated pilot zip is "
        f"not a ≥2h CLI session. {OWNER_GATED_CLI_LOG}."
    )


def cli_credentials_missing_detail(command: Optional[str] = None) -> str:
    """Named-class operator text for binary-present / credentials-absent."""
    from app.factory.coder import (
        CODE_CLI_ENV,
        DEEPSEEK_API_KEY_ENV,
        DEFAULT_DEEPSEEK_MODEL,
        code_cli_command,
    )

    cli = (command or code_cli_command()).strip() or "kimi"
    if cli_requires_deepseek_credentials(command):
        return (
            f"{NAMED_BLOCKER_CLI_CREDS}: {cli!r} is an executable on this host "
            f"but {DEEPSEEK_API_KEY_ENV} is unset. DeepSeek is the selected "
            "FACTORY_CODE_CLI coder (FACTORY_CODE_PROVIDER=deepseek or "
            f"{DEEPSEEK_API_KEY_ENV}). Set {DEEPSEEK_API_KEY_ENV} so boot "
            "writes [providers.deepseek] (OpenAI-compat "
            f"https://api.deepseek.com, model default {DEFAULT_DEEPSEEK_MODEL}) "
            "and injects KIMI_MODEL_* on the kimi subprocess. "
            f"{CODE_CLI_ENV} / CEREBRUM_LLM_API_KEY / ANTHROPIC_API_KEY do "
            "not authenticate DeepSeek. Claude Code is not the DeepSeek "
            "vehicle. HTTP oneshot is not a FACTORY_CODE_CLI session; set "
            f"{BRIEF_HTTP_ONESHOT_ENV}=1 only for CI. {OWNER_GATED_CLI_LOG}."
        )
    dest = kimi_credentials_file()
    return (
        f"{NAMED_BLOCKER_CLI_CREDS}: {cli!r} is an executable on this host but "
        f"{dest} is missing (credentials_file_present=false). "
        "Set KIMI_CODE_API_KEY so boot writes [providers.kimi] and "
        "default_model (KIMI_CODE_MODEL, default kimi-k3 / Moonshot API) into that file "
        f"(or place the file yourself). {CODE_CLI_ENV} / CEREBRUM_LLM_API_KEY "
        "do not authenticate the Kimi Code CLI. HTTP oneshot is not a "
        f"FACTORY_CODE_CLI session; set {BRIEF_HTTP_ONESHOT_ENV}=1 only for CI. "
        f"{OWNER_GATED_CLI_LOG}."
    )


def raise_if_cli_session_unready() -> None:
    """Fail-closed before generate-start claims the coding agent took over.

    Cursor-keyed Generate uses ``run_cli_pivot`` (not FACTORY_CODE_CLI).
    Skip Kimi/DeepSeek credential preflight when those keys are present.

    The headless CodeWhale worker (FACTORY_CODEWHALE_WRITER=1) is its own
    executor seam: it needs no kimi/cursor binary or credentials file, so
    the FACTORY_CODE_CLI preflight must not gate it (live-factory failure
    sess_b9db05967cb94e6f: generate refused 503 with FACTORY_CODE_CLI=cursor
    before the worker dispatch was ever reached).
    """
    from app.factory.build.roles_handlers import writer_uses_codewhale

    if writer_uses_codewhale(os.environ):
        return
    if not brief_requires_cli():
        return
    if not cli_available():
        raise CodeCliUnavailable(cli_unavailable_detail())
    if not cli_credentials_ok():
        raise CodeCliCredentialsMissing(cli_credentials_missing_detail())
    if not cli_default_model_ok():
        raise CodeCliNoModelConfigured(cli_no_model_detail())


def coder_artifact_paths(root: Path) -> Dict[str, Path]:
    root = Path(root)
    return {
        "brief": root / BRIEF_REL,
        "log": root / LOG_REL,
        "control": root / CONTROL_REL,
        "receipt": root / RECEIPT_REL,
    }


def write_control(root: Path, action: str) -> Dict[str, Any]:
    """Owner Pause / Stop / Resume. Written outside the role workspace."""
    action = str(action or "").strip().lower()
    if action == "resume":
        action = CONTROL_RUN
    if action not in {CONTROL_RUN, CONTROL_PAUSE, CONTROL_STOP}:
        raise ValueError(f"unknown coder control action: {action!r}")
    payload = {"action": action, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = Path(root) / CONTROL_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def read_control(root: Path) -> str:
    path = Path(root) / CONTROL_REL
    if not path.is_file():
        return CONTROL_RUN
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CONTROL_RUN
    action = str((data or {}).get("action") or CONTROL_RUN).strip().lower()
    if action == "resume":
        return CONTROL_RUN
    return action if action in {CONTROL_RUN, CONTROL_PAUSE, CONTROL_STOP} else CONTROL_RUN


def writer_staging_dir(output_dir: Path) -> Path:
    """RoleRunner WRITER staging sibling of ``output_dir``.

    Live log after RoleError (before ``ws.commit()``) lands at
    ``{output_dir.parent}/.{output_dir.name}.staging-writer/docs/coder_session.log``.
    """
    output_dir = Path(output_dir)
    return output_dir.parent / f".{output_dir.name}.staging-writer"


def coder_session_log_candidates(root: Path) -> List[Path]:
    """Destination log plus documented WRITER staging variants.

    Prefer this list when dest ``docs/coder_session.log`` is missing or
    empty — one-off jobs do not mount ``/app/storage``, and a WRITER
    RoleError leaves the live CLI stdout on the staging sibling.
    """
    dest = Path(getattr(root, "destination", None) or getattr(root, "workspace", root))
    try:
        dest = dest.resolve()
    except OSError:
        dest = Path(dest)
    staging_attr = getattr(root, "workspace", None)
    out: List[Path] = []
    seen: set = set()

    def _add(path: Path) -> None:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            return
        seen.add(key)
        out.append(Path(path))

    _add(dest / LOG_REL)
    if staging_attr is not None:
        _add(Path(staging_attr) / LOG_REL)
    _add(writer_staging_dir(dest) / LOG_REL)
    parent = dest.parent
    prefix = f".{dest.name}.staging-"
    try:
        if parent.is_dir():
            for child in sorted(parent.iterdir()):
                if child.is_dir() and child.name.startswith(prefix):
                    _add(child / LOG_REL)
    except OSError:
        pass
    _add(dest / FACTORY_STAGING_DIRNAME / LOG_REL)
    return out


def resolve_coder_session_log(root: Path) -> Optional[Path]:
    """Prefer a non-empty coder log, then the newer file."""
    best_path: Optional[Path] = None
    best_key: Optional[Tuple[bool, float]] = None
    for path in coder_session_log_candidates(root):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        key = (bool(text.strip()), stat.st_mtime)
        if best_key is None or key > best_key:
            best_key = key
            best_path = path
    return best_path


def read_coder_session_log(root: Path) -> str:
    path = resolve_coder_session_log(root)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_log_tail(root: Path, *, max_chars: int = 8000) -> str:
    text = read_coder_session_log(root)
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def read_receipt(root: Path) -> Dict[str, Any]:
    path = Path(root) / RECEIPT_REL
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def session_status(root: Path) -> Dict[str, Any]:
    """Fields stamped onto build_status for the Floor monitor."""
    control = read_control(root)
    receipt = read_receipt(root)
    brief = Path(root) / BRIEF_REL
    resolved = resolve_coder_session_log(root)
    return {
        "coder_control": control,
        "coder_log": read_log_tail(root),
        "coder_brief_present": brief.is_file(),
        "coder_log_present": bool(resolved and resolved.is_file()),
        "coder_receipt": receipt,
        "brief_dispatch": receipt.get("via") or ("compiled" if brief.is_file() else None),
    }


def _append_log(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line if line.endswith("\n") else line + "\n")


def wait_if_paused(
    root: Path,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 0.25,
    deadline: Optional[float] = None,
) -> str:
    """Block while the owner has PAUSE set. Returns the control action."""
    while True:
        action = read_control(root)
        if action != CONTROL_PAUSE:
            return action
        if deadline is not None and clock() >= deadline:
            return CONTROL_STOP
        sleep(poll_s)


def resolve_code_cli(command: Optional[str] = None) -> Optional[str]:
    """Absolute path or PATH name that ``cli_available`` would accept."""
    from app.factory.coder import code_cli_command

    cli = (command or code_cli_command()).strip()
    if not cli:
        return None
    path = Path(cli).expanduser()
    if path.is_file() and os.access(path, os.X_OK):
        return str(path)
    found = shutil.which(cli)
    if found:
        return found
    name = path.name if path.name else cli
    extras = [
        Path("/usr/local/bin") / name,
        Path("/usr/bin") / name,
        Path("/app/.local/bin") / name,
        Path.home() / ".local" / "bin" / name,
    ]
    for candidate in extras:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def cli_available(command: Optional[str] = None) -> bool:
    return resolve_code_cli(command) is not None


def probe_code_cli() -> Dict[str, Any]:
    """Health / operator view of FACTORY_CODE_CLI (binary, not workbench flag).

    When Cursor executor keys are present (same predicate as
    ``writer_uses_cli_pivot`` / #418), Generate/Continue uses Cursor BA —
    Kimi/DeepSeek CLI credentials are unused. Do not emit
    ``FACTORY_CODE_CLI_CREDENTIALS_MISSING`` (or UNAVAILABLE / NO_MODEL)
    as a Floor blocker in that case.
    """
    from app.factory.coder import (
        code_cli_command,
        deepseek_api_key,
        deepseek_code_model,
        factory_code_provider,
    )

    command = code_cli_command()
    resolved = resolve_code_cli(command)
    provider = factory_code_provider()
    cursor_ba = False
    wants_deepseek = cli_requires_deepseek_credentials(command)
    wants_kimi = cli_requires_kimi_credentials(command)
    kimi_file = credentials_file_present()
    config_text = read_kimi_config_text() if kimi_file else ""
    kimi_alias = config_default_model(config_text) if kimi_file else ""
    model_ok = cli_default_model_ok(command)
    deepseek_key = bool(deepseek_api_key())
    if wants_deepseek:
        default_model = deepseek_code_model()
        default_configured = bool(model_ok)
    else:
        default_model = kimi_alias or None
        default_configured = bool(model_ok) if kimi_file else False
    probe: Dict[str, Any] = {
        "command": command,
        "provider": provider,
        "available": bool(resolved),
        "resolved": resolved,
        "credentials_file_present": kimi_file,
        "deepseek_key_present": deepseek_key,
        "default_model": default_model or None,
        "default_model_configured": default_configured,
        "requires_cli": brief_requires_cli(),
        "requires_kimi_credentials": wants_kimi,
        "requires_deepseek_credentials": wants_deepseek,
        "cursor_ba_available": cursor_ba,
    }
    if cursor_ba:
        return probe
    if not resolved:
        probe["blocker"] = NAMED_BLOCKER_CLI
        probe["error"] = cli_unavailable_detail(command)
    elif brief_requires_cli() and not cli_credentials_ok(command):
        probe["blocker"] = NAMED_BLOCKER_CLI_CREDS
        probe["error"] = cli_credentials_missing_detail(command)
    elif brief_requires_cli() and not model_ok:
        probe["blocker"] = NAMED_BLOCKER_CLI_NO_MODEL
        probe["error"] = cli_no_model_detail(command)
    return probe


def _moonshot_alias(name: str) -> str:
    mapped = _MANAGED_TO_MOONSHOT.get(name)
    return mapped[0] if mapped else name


def _moonshot_model_id(name: str) -> str:
    mapped = _MANAGED_TO_MOONSHOT.get(name)
    if mapped:
        return mapped[1]
    return name.rsplit("/", 1)[-1] if name else DEFAULT_KIMI_CODE_MODEL_ID


def kimi_code_model_env_set() -> bool:
    return bool(
        os.getenv(KIMI_CODE_MODEL_ENV, "").strip()
        or os.getenv(KIMI_CODE_MODEL_ID_ENV, "").strip()
    )


def kimi_code_default_model() -> str:
    """Alias written as ``default_model``. ``KIMI_CODE_MODEL`` overrides.

    Managed ``kimi-code/k3`` / ``k3`` (Kimi Code CLI 0.41 complete example)
    map to Moonshot ``kimi-k3`` so a platform key on api.moonshot.ai does
    not 404. Headless Floor cannot ``/login``.
    """
    raw = os.getenv(KIMI_CODE_MODEL_ENV, "").strip()
    if raw and _MODEL_ALIAS_RE.match(raw):
        return _moonshot_alias(raw)
    return DEFAULT_KIMI_CODE_MODEL


def kimi_code_model_id(alias: Optional[str] = None) -> str:
    """API model id for the ``[models]`` table (Moonshot catalog).

    ``KIMI_CODE_MODEL_ID`` wins. Managed leftovers (``k3``) map to
    ``kimi-k3``. Otherwise the last path segment of the alias.
    """
    override = os.getenv(KIMI_CODE_MODEL_ID_ENV, "").strip()
    if override and _MODEL_ALIAS_RE.match(override):
        return _moonshot_model_id(override)
    name = alias or kimi_code_default_model()
    return _moonshot_model_id(name)


def _top_level_key_lines(text: str, key: str) -> List[Tuple[int, str]]:
    """``(line index, value)`` for every top-level ``key = value`` line, read
    by the TOML grammar: the root table ends at the first ``[table]`` header,
    a line's key is what precedes ``=``, its value is what ``tomllib`` reads
    from that line ("" when the value is empty or not valid TOML)."""
    import tomllib

    out: List[Tuple[int, str]] = []
    for index, raw in enumerate((text or "").split("\n")):
        line = raw.strip()
        if line.startswith("["):
            break
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.partition("=")[0].strip().strip("\"'")
        if name != key:
            continue
        try:
            value = tomllib.loads(line).get(key)
        except tomllib.TOMLDecodeError:
            value = None
        out.append((index, str(value).strip() if isinstance(value, str) else ""))
    return out


def _set_top_level_key(text: str, key: str, value: str) -> str:
    """Write ``key = "value"`` as the first line, dropping every existing
    top-level assignment of *key* (empty or not)."""
    lines = (text or "").split("\n")
    drop = {index for index, _ in _top_level_key_lines(text, key)}
    body = "\n".join(line for i, line in enumerate(lines) if i not in drop).lstrip("\n")
    return f'{key} = "{value}"\n' + ("\n" + body if body else "")


def config_default_model(text: str) -> str:
    for _, value in _top_level_key_lines(text, DEFAULT_MODEL_KEY):
        if value:
            return value
    return ""


def _config_providers(text: str) -> frozenset:
    """Provider tables a Kimi Code config.toml declares, read as TOML."""
    import tomllib

    try:
        data = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError:
        return frozenset()
    providers = data.get("providers")
    return frozenset(providers) if isinstance(providers, dict) else frozenset()


def config_has_kimi_provider(text: str) -> bool:
    from app.factory.code_cli import DEFAULT_KIMI_CLI

    return DEFAULT_KIMI_CLI in _config_providers(text)


def config_has_deepseek_provider(text: str) -> bool:
    from app.factory.code_cli import PROVIDER_DEEPSEEK

    return PROVIDER_DEEPSEEK in _config_providers(text)


def _deepseek_provider_block(key: str, base_url: str) -> str:
    return (
        "[providers.deepseek]\n"
        'type = "openai"\n'
        f'api_key = "{key}"\n'
        f'base_url = "{base_url}"\n'
    )


def _deepseek_model_table_block(alias: str, model_id: str) -> str:
    return (
        f'[models."{alias}"]\n'
        'provider = "deepseek"\n'
        f'model = "{model_id}"\n'
        "max_context_size = 1048576\n"
    )


def _model_table_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias)
    return re.compile(
        rf'(?ms)^\[models\.(?:"{escaped}"|\'{escaped}\'|{escaped})\][^\[]*(?=^\[|\Z)'
    )


def config_has_model_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    return _model_table_pattern(alias).search(text or "") is not None


def config_model_id(text: str, alias: str) -> str:
    """``model =`` field under ``[models."<alias>"]``."""
    if not alias:
        return ""
    block = _model_table_pattern(alias).search(text or "")
    if not block:
        return ""
    match = _MODEL_FIELD_RE.search(block.group(0))
    if not match:
        return ""
    return (match.group(1) or match.group(2) or match.group(3) or "").strip()


def is_broken_managed_model(alias: str, model_id: str = "") -> bool:
    """True for Kimi Code /login catalog names that 404 on Moonshot API."""
    if alias in _MANAGED_TO_MOONSHOT:
        return True
    mid = (model_id or "").rsplit("/", 1)[-1]
    return mid in _BROKEN_MANAGED_IDS


def config_needs_kimi_model_rewrite(
    text: str, alias: str, model_id: str
) -> bool:
    """Whether boot should mutate ``default_model`` / ``[models]``."""
    current = config_default_model(text)
    if not current:
        return True
    current_id = config_model_id(text, current)
    if not config_has_model_alias(text, current):
        return True
    if is_broken_managed_model(current, current_id):
        return True
    if kimi_code_model_env_set() and (
        current != alias or current_id != model_id
    ):
        return True
    return False


def _model_context_size(model_id: str) -> int:
    # Moonshot K3 is 1M; Kimi Code CLI 0.41 k3 example matches. Others 256k.
    tail = (model_id or "").rsplit("/", 1)[-1].lower()
    return 1048576 if tail in {"k3", "kimi-k3"} else 262144


def _provider_block(key: str, base_url: str) -> str:
    return (
        "[providers.kimi]\n"
        'type = "kimi"\n'
        f'api_key = "{key}"\n'
        f'base_url = "{base_url}"\n'
    )


def _model_table_block(alias: str, model_id: str) -> str:
    return (
        f'[models."{alias}"]\n'
        'provider = "kimi"\n'
        f'model = "{model_id}"\n'
        f"max_context_size = {_model_context_size(model_id)}\n"
    )


def _ensure_trailing_newline(text: str) -> str:
    if text and not text.endswith("\n"):
        return text + "\n"
    return text


def _replace_or_append_model_table(
    text: str, alias: str, model_id: str
) -> Tuple[str, bool]:
    block = _model_table_block(alias, model_id)
    pattern = _model_table_pattern(alias)
    if pattern.search(text):
        current_id = config_model_id(text, alias)
        if current_id == model_id:
            return text, False
        return pattern.sub(block.rstrip() + "\n\n", text), True
    out = _ensure_trailing_newline(text)
    if out and not out.endswith("\n\n"):
        out += "\n"
    return out + block, True


def apply_kimi_code_default_model(
    text: str, *, alias: Optional[str] = None, model_id: Optional[str] = None
) -> Tuple[str, bool]:
    """Converge ``default_model`` + ``[models]`` to alias/id. Keeps keys."""
    alias = alias or kimi_code_default_model()
    model_id = model_id or kimi_code_model_id(alias)
    out = text or ""
    current = config_default_model(out)
    mutated = False
    if not current:
        out = _set_top_level_key(out, DEFAULT_MODEL_KEY, alias)
        current = alias
        mutated = True
    elif current != alias:
        out = _set_top_level_key(out, DEFAULT_MODEL_KEY, alias)
        out = _model_table_pattern(current).sub("", out)
        current = alias
        mutated = True
    out, table_mutated = _replace_or_append_model_table(out, current, model_id)
    return out, mutated or table_mutated


def apply_deepseek_kimi_backend(
    text: str,
    *,
    key: str,
    model: str,
    base_url: str,
) -> Tuple[str, bool]:
    """Write ``[providers.deepseek]`` + default_model for Kimi Code CLI."""
    from app.factory.code_cli import DEFAULT_DEEPSEEK_MODEL

    alias = (model or DEFAULT_DEEPSEEK_MODEL).strip() or DEFAULT_DEEPSEEK_MODEL
    out = text or ""
    mutated = False
    current = config_default_model(out)
    if current != alias:
        out = _set_top_level_key(out, DEFAULT_MODEL_KEY, alias)
        mutated = True
    if not config_has_deepseek_provider(out):
        out = _ensure_trailing_newline(out)
        if out and not out.endswith("\n\n"):
            out += "\n"
        out = out + _deepseek_provider_block(key, base_url)
        mutated = True
    else:
        # Refresh api_key / base_url in place.
        pattern = re.compile(
            r'(?ms)^\[providers\.deepseek\][^\[]*(?=^\[|\Z)'
        )
        replacement = _deepseek_provider_block(key, base_url).rstrip() + "\n\n"
        if pattern.search(out):
            out = pattern.sub(replacement, out)
            mutated = True
    block = _deepseek_model_table_block(alias, alias)
    table_pat = _model_table_pattern(alias)
    if table_pat.search(out):
        current_id = config_model_id(out, alias)
        if current_id != alias or 'provider = "deepseek"' not in (
            table_pat.search(out).group(0) if table_pat.search(out) else ""
        ):
            out = table_pat.sub(block.rstrip() + "\n\n", out)
            mutated = True
    else:
        out = _ensure_trailing_newline(out)
        if out and not out.endswith("\n\n"):
            out += "\n"
        out = out + block
        mutated = True
    return out, mutated


def provider_statuses(output: str) -> List[int]:
    """HTTP error statuses the provider answered, as the CLI printed them.

    Read from the shapes a status takes, never from the words around it:
    an HTTP status line (``HTTP 429``, ``HTTP/1.1 402``), a code followed by
    its own standard reason phrase (``404 Not Found`` -- the phrase comes
    from ``http.HTTPStatus``), a code introducing an error envelope
    (``Error code: 402 - {...}``), or an error object carrying an integer
    ``status`` / ``status_code`` / ``code``.
    """
    blob = output or ""
    found: List[int] = []

    def _keep(value: Any) -> None:
        try:
            code = int(value)
        except (TypeError, ValueError):
            return
        if 400 <= code <= 599 and code not in found:
            found.append(code)

    for line in blob.splitlines():
        tokens = line.split()
        for word, after in zip(tokens, tokens[1:]):
            protocol, _, version = word.partition("/")
            if protocol == HTTP_PROTOCOL_TOKEN and (not version or version.replace(".", "").isdigit()):
                if len(after) == 3 and after.isdigit():
                    _keep(after)
    for match in _STATUS_NUMBER_RE.finditer(blob):
        code = int(match.group(1))
        rest = blob[match.end():]
        try:
            phrase = HTTPStatus(code).phrase
        except ValueError:
            continue
        if rest.lstrip(" :-").lower().startswith(phrase.lower()) or rest.startswith((" - {", " {")):
            _keep(code)
    for envelope in _error_envelopes(blob):
        for key in ("status", "status_code", "code"):
            _keep(envelope.get(key))
        inner = envelope.get("error")
        if isinstance(inner, dict):
            for key in ("status", "status_code", "code"):
                _keep(inner.get(key))
    return found


def _error_envelopes(blob: str) -> List[Dict[str, Any]]:
    """Every JSON / Python-literal object in ``blob`` that carries ``error``."""
    out: List[Dict[str, Any]] = []
    depth, start = 0, None
    for index, ch in enumerate(blob):
        if ch == "{":
            if depth == 0:
                start = index
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                text = blob[start:index + 1]
                value: Any = None
                try:
                    value = json.loads(text)
                except ValueError:
                    try:
                        value = ast.literal_eval(text)
                    except (ValueError, SyntaxError):
                        value = None
                if isinstance(value, dict) and ("error" in value or "status" in value):
                    out.append(value)
                start = None
    return out


def _cli_tagged_kind(blob: str) -> str:
    """The kind a coder CLI tags its own error with: ``[<cli>:<kind>]``."""
    match = _CLI_ERROR_TAG_RE.search(blob or "")
    return match.group(1).lower() if match else ""


def _extract_unrecognized_model_id(blob: str) -> str:
    """The rejected id from the JSON object the CLI printed with its
    ``[<cli>:unrecognized_model]`` tag -- decoded as JSON, never searched."""
    decoder = json.JSONDecoder()
    for line in (blob or "").splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            obj, _ = decoder.raw_decode(line[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and str(obj.get("model") or "").strip():
            return str(obj["model"]).strip()
    return ""


def classify_cli_exit(
    code: int, output: str, *, model_configured: bool = True
) -> Tuple[str, str]:
    """Named fail-closed class for a non-zero FACTORY_CODE_CLI exit.

    ``FACTORY_CODE_CLI_FAILED`` stays the generic honesty class. A more
    specific ``FACTORY_CODE_CLI_NO_MODEL`` fires when the Factory's own
    configuration has no usable model for the selected CLI
    (``model_configured`` -- the caller passes ``cli_default_model_ok``),
    read from the config the Factory writes, never from the CLI's words
    (headless /login is not a Floor path).
    ``FACTORY_CODE_CLI_MODEL_DENIED`` fires on 404 / Permission denied
    for the configured model (live tip after #324: ``k3`` /
    ``kimi-code/k3`` on Moonshot) and on Claude Code
    ``[claude-code:unrecognized_model]`` (sess_be217f6d:
    ``deepseek-v4-pro[1m]``; sess_401e6619: bare ``deepseek-v4-pro``).
    ``FACTORY_CODE_CLI_BILLING`` fires on
    Moonshot ``429`` account-suspended / insufficient balance
    (sess_d5789a91). A templated pilot zip is not a ≥2h CLI session.
    """
    from app.factory.code_cli import (
        DEFAULT_DEEPSEEK_MODEL,
        DEEPSEEK_API_PRO_MODEL,
        DEEPSEEK_CODE_MODEL_ENV,
        REJECTED_DEEPSEEK_BARE_MODEL,
        REJECTED_DEEPSEEK_CLAUDE_MODEL,
    )

    exit_bit = f"CLI exited {code}"
    blob = output or ""
    if not model_configured:
        return (
            NAMED_BLOCKER_CLI_NO_MODEL,
            (
                f"{NAMED_BLOCKER_CLI_NO_MODEL}: {exit_bit} — No model configured. "
                "Headless Floor cannot run `kimi` /login. Set KIMI_CODE_MODEL / "
                "KIMI_CODE_MODEL_ID "
                f"(default {DEFAULT_KIMI_CODE_MODEL} / "
                f"{DEFAULT_KIMI_CODE_MODEL_ID}, Moonshot API) so boot writes "
                "default_model into ~/.kimi-code/config.toml. A templated "
                f"pilot zip is not a ≥2h CLI session. {OWNER_GATED_CLI_LOG}."
            ),
        )
    statuses = provider_statuses(blob)
    billing = any(s in _BILLING_STATUSES for s in statuses)
    if _cli_tagged_kind(blob) == UNRECOGNIZED_MODEL_HINT and not billing:
        bad = _extract_unrecognized_model_id(blob) or REJECTED_DEEPSEEK_BARE_MODEL
        return (
            NAMED_BLOCKER_CLI_MODEL_DENIED,
            (
                f"{NAMED_BLOCKER_CLI_MODEL_DENIED}: {exit_bit} — coder CLI "
                f"rejected model {bad!r} (unrecognized_model). Claude Code "
                f"is not the DeepSeek vehicle (it rejects "
                f"{REJECTED_DEEPSEEK_CLAUDE_MODEL} and bare "
                f"{REJECTED_DEEPSEEK_BARE_MODEL}, and remaps to claude-opus). "
                f"Set {DEEPSEEK_CODE_MODEL_ENV} to "
                f"{DEFAULT_DEEPSEEK_MODEL} / {DEEPSEEK_API_PRO_MODEL} and "
                f"FACTORY_CODE_CLI=kimi (OpenAI-compat "
                f"https://api.deepseek.com). Still "
                f"{NAMED_BLOCKER_CLI_FAILED} honesty — not a ≥2h CLI "
                f"session; no OpenRouter fallthrough. {OWNER_GATED_CLI_LOG}."
            ),
        )
    if any(s in _MODEL_DENIED_STATUSES for s in statuses) and not billing:
        return (
            NAMED_BLOCKER_CLI_MODEL_DENIED,
            (
                f"{exit_bit} — configured model 404 / Permission denied. "
                "Headless Floor cannot run `kimi` /login. Set "
                "KIMI_CODE_MODEL / KIMI_CODE_MODEL_ID (default "
                f"{DEFAULT_KIMI_CODE_MODEL} / {DEFAULT_KIMI_CODE_MODEL_ID}, "
                "Moonshot api.moonshot.ai — not managed kimi-code/k3). Boot "
                "rewrites ~/.kimi-code/config.toml. A templated pilot zip "
                f"is not a ≥2h CLI session. {OWNER_GATED_CLI_LOG}."
            ),
        )
    if billing:
        return (
            NAMED_BLOCKER_CLI_BILLING,
            (
                f"{NAMED_BLOCKER_CLI_BILLING}: {exit_bit} — coder account "
                "billing/auth refused the session (the provider answered "
                "401 / 402 / 429: unauthenticated, unpaid or over its limit). "
                "Still FACTORY_CODE_CLI_FAILED "
                "honesty — not a ≥2h CLI session. Verified REUSE continues "
                "factory-grounded emit + harvest; GENERATE inventory_gaps "
                "fall through to the factory coder LLM and stay listed "
                f"until artifacts land. {OWNER_GATED_CLI_LOG}."
            ),
        )
    return NAMED_BLOCKER_CLI_FAILED, exit_bit


_FENCE_RE = re.compile(r"```(?:[A-Za-z0-9_+-]*)\s*\n")
_CLI_CMD_LINE_RE = re.compile(r"^\$\s+\S+.*$", re.MULTILINE)


def _cli_described_not_written(blob: str) -> bool:
    """The session printed a fenced code block and the harvest is empty (the
    only caller runs on an empty harvest): code described, not written."""
    return bool(_FENCE_RE.search(blob or ""))


def _cli_empty_completion(blob: str) -> bool:
    remainder = _CLI_CMD_LINE_RE.sub("", blob or "")
    remainder = re.sub(r"^\[FACTORY_[^\]]*\]\s*.*$", "", remainder, flags=re.MULTILINE)
    return not remainder.strip()


def classify_cli_empty(log_text: str) -> str:
    """Named reason for CLI exit 0 + empty harvest.

    Returns ``described-not-written`` or ``empty-completion``. Advisory:
    it does not change the ``FACTORY_CODE_CLI_NO_AUTHORSHIP`` honesty class.
    """
    blob = log_text or ""
    # A prose refusal has no structural signal (it is the agent's words), so
    # it is not told apart here; neither is where the session wrote (see
    # CLI_EMPTY_WRITE_TARGETS_UNREAD). An exit-0 session that wrote nothing
    # is classified by what its log structurally shows.
    if _cli_described_not_written(blob):
        return CLI_EMPTY_DESCRIBED_NOT_WRITTEN
    if _cli_empty_completion(blob):
        return CLI_EMPTY_EMPTY_COMPLETION
    return CLI_EMPTY_EMPTY_COMPLETION


def _no_authorship_detail(log_text: str) -> str:
    empty_reason = classify_cli_empty(log_text)
    reason_bit = f" ({empty_reason}; {CLI_EMPTY_WRITE_TARGETS_UNREAD})"
    return (
        f"{NAMED_BLOCKER_CLI_NO_AUTHORSHIP}: FACTORY_CODE_CLI "
        f"kimi/deepseek exited 0 without harvested "
        f"agent-written handlers{reason_bit}. A CLI exit 0 is not C-BRIEF "
        "authorship — do not SUCCESS thin templates."
    )


def _wire_deepseek_cli_credentials() -> Dict[str, Any]:
    """Validate DeepSeek and write Kimi ``[providers.deepseek]``.

    Official DeepSeek env (``KIMI_MODEL_*`` / ``DEEPSEEK_*``) is applied
    to the FACTORY_CODE_CLI **subprocess** at dispatch. Does not set
    process-wide ``ANTHROPIC_*``.
    """
    from app.factory.code_cli import (
        DEEPSEEK_OPENAI_BASE_URL,
        deepseek_openai_base_url,
    )
    from app.factory.coder import (
        DEEPSEEK_API_KEY_ENV,
        DEFAULT_DEEPSEEK_MODEL,
        deepseek_api_key,
        deepseek_cli_environ,
        deepseek_code_model,
        deepseek_coder_selected,
        factory_code_provider,
    )

    selected = deepseek_coder_selected() or factory_code_provider() == "deepseek"
    key = deepseek_api_key()
    provider = factory_code_provider()
    if not selected and not key:
        return {
            "ok": False,
            "wrote": False,
            "mutated": False,
            "reason": f"{DEEPSEEK_API_KEY_ENV} unset",
            "provider": provider,
        }
    if selected and not key:
        logger.info(OWNER_GATED_CLI_LOG)
        return {
            "ok": False,
            "wrote": False,
            "mutated": False,
            "reason": f"{DEEPSEEK_API_KEY_ENV} unset",
            "provider": "deepseek",
        }
    env = deepseek_cli_environ(key)
    model = (
        env.get("DEEPSEEK_CODE_MODEL")
        or env.get("KIMI_MODEL_NAME")
        or deepseek_code_model()
        or DEFAULT_DEEPSEEK_MODEL
    )
    if not selected:
        logger.info(
            "DEEPSEEK_API_KEY present but FACTORY_CODE_PROVIDER=%s — "
            "not switching default_model off Moonshot",
            provider,
        )
        return {
            "ok": True,
            "wrote": False,
            "mutated": False,
            "reason": "deepseek key present but FACTORY_CODE_PROVIDER=kimi",
            "provider": provider,
            "model": model,
            "cli": "kimi",
            "subprocess_env_keys": sorted(env),
        }
    dest = kimi_credentials_file()
    home = kimi_credentials_home()
    existed = dest.is_file()
    existing = dest.read_text(encoding="utf-8") if existed else ""
    base = deepseek_openai_base_url() or DEEPSEEK_OPENAI_BASE_URL
    text, mutated = apply_deepseek_kimi_backend(
        existing, key=key, model=model, base_url=base
    )
    home.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    wrote = not existed
    logger.info(
        "wired DeepSeek V4 Pro under Kimi Code CLI "
        "(model=%s base=%s path=%s; ANTHROPIC_* not exported process-wide)",
        model,
        env.get("KIMI_MODEL_BASE_URL") or base,
        dest,
    )
    return {
        "ok": True,
        "wrote": wrote,
        "mutated": bool(existed and mutated),
        "reason": "deepseek wired",
        "provider": "deepseek",
        "model": model,
        "cli": "kimi",
        "path": str(dest),
        "subprocess_env_keys": sorted(env),
    }


def _merge_cli_credential_result(
    kimi: Dict[str, Any], deepseek: Dict[str, Any]
) -> Dict[str, Any]:
    """Keep the Kimi write shape; surface DeepSeek when it is the live coder."""
    out = dict(kimi)
    out["deepseek"] = dict(deepseek)
    if deepseek.get("ok") and not out.get("ok"):
        out["ok"] = True
        out["reason"] = deepseek.get("reason") or out.get("reason")
        out.setdefault("model", deepseek.get("model"))
        out["provider"] = "deepseek"
    elif (
        not deepseek.get("ok")
        and deepseek.get("provider") == "deepseek"
        and not out.get("ok")
    ):
        out["reason"] = deepseek.get("reason") or out.get("reason")
        out["provider"] = "deepseek"
    elif out.get("ok"):
        out.setdefault("provider", "kimi")
    return out


def ensure_code_cli_credentials() -> Dict[str, Any]:
    """Write ~/.kimi-code/config.toml from KIMI_CODE_API_KEY when present.

    Also writes ``default_model`` (``KIMI_CODE_MODEL`` /
    ``KIMI_CODE_MODEL_ID``, default Moonshot ``kimi-k3``) so
    non-interactive ``kimi --prompt`` works without TTY ``/login``.
    Mutates an existing credentials-only file and a #324
    ``kimi-code/k3`` / ``k3`` file that 404s on api.moonshot.ai. Does
    not install the binary. Does not create a new file when the secret
    is unset — owner-gated stays owner-gated. A credentials-only file
    that already has ``[providers.kimi]`` is still repaired with
    ``default_model`` on the next start. Does not claim the Render
    dashboard is set. Does not claim Floor re-prove.

    When DeepSeek is selected (or ``DEEPSEEK_API_KEY`` is set), writes
    ``[providers.deepseek]`` (OpenAI-compat) into the same kimi
    config.toml and records ``KIMI_MODEL_*`` subprocess env. Does not
    set ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_MODEL`` on the process
    (Floor chat stays off DeepSeek). Claude Code is not the vehicle.
    """
    from app.factory.coder import factory_code_provider

    deepseek = _wire_deepseek_cli_credentials()
    key = (
        os.getenv("KIMI_CODE_API_KEY", "").strip()
        or os.getenv("KIMI_CODE_KEY", "").strip()
    )
    if factory_code_provider() == "deepseek":
        # DeepSeek owns default_model. Still record a Moonshot provider
        # block if the historical key is present, but do not switch
        # default_model back to kimi-k3.
        if not key:
            return _merge_cli_credential_result(
                {
                    "ok": bool(deepseek.get("ok")),
                    "wrote": False,
                    "mutated": False,
                    "reason": deepseek.get("reason") or "KIMI_CODE_API_KEY unset",
                    "path": deepseek.get("path"),
                    "model": deepseek.get("model"),
                },
                deepseek,
            )
        dest = kimi_credentials_file()
        existing = dest.read_text(encoding="utf-8") if dest.is_file() else ""
        if not config_has_kimi_provider(existing):
            home = kimi_credentials_home()
            home.mkdir(parents=True, exist_ok=True)
            base_url = os.getenv(
                "KIMI_CODE_BASE_URL", "https://api.moonshot.ai/v1"
            ).strip()
            text = _ensure_trailing_newline(existing)
            text = text + ("\n" if text else "") + _provider_block(key, base_url)
            dest.write_text(text, encoding="utf-8")
            return _merge_cli_credential_result(
                {
                    "ok": True,
                    "wrote": True,
                    "mutated": bool(existing),
                    "path": str(dest),
                    "model": deepseek.get("model"),
                    "reason": "wrote kimi provider; deepseek owns default_model",
                },
                deepseek,
            )
        return _merge_cli_credential_result(
            {
                "ok": True,
                "wrote": False,
                "mutated": False,
                "path": str(dest) if dest.is_file() else None,
                "model": deepseek.get("model") or config_default_model(existing),
                "reason": "already present",
            },
            deepseek,
        )
    home = kimi_credentials_home()
    dest = kimi_credentials_file()
    alias = kimi_code_default_model()
    model_id = kimi_code_model_id(alias)
    existed = dest.is_file()
    existing = dest.read_text(encoding="utf-8") if existed else ""
    has_provider = config_has_kimi_provider(existing)
    current_model = config_default_model(existing)
    current_id = config_model_id(existing, current_model)
    rewrite_model = config_needs_kimi_model_rewrite(existing, alias, model_id)
    if not key:
        # Credentials-only file (key already in toml) must still get
        # default_model on next start. Do not create a new file without
        # the owner-gated secret.
        if existed and has_provider and rewrite_model:
            home.mkdir(parents=True, exist_ok=True)
            text, mutated_model = apply_kimi_code_default_model(
                existing, alias=alias, model_id=model_id
            )
            dest.write_text(text, encoding="utf-8")
            written_alias = config_default_model(text) or alias
            written_id = config_model_id(text, written_alias) or model_id
            logger.info(
                "repaired Kimi Code CLI default_model in %s (model=%s id=%s; API key unset)",
                dest,
                written_alias,
                written_id,
            )
            return _merge_cli_credential_result(
                {
                    "ok": True,
                    "wrote": False,
                    "mutated": bool(mutated_model),
                    "path": str(dest),
                    "model": written_alias,
                    "model_id": written_id,
                    "reason": "mutated",
                },
                deepseek,
            )
        logger.info(OWNER_GATED_CLI_LOG)
        return _merge_cli_credential_result(
            {
                "ok": False,
                "wrote": False,
                "mutated": False,
                "reason": "KIMI_CODE_API_KEY unset",
            },
            deepseek,
        )
    if has_provider and current_model and not rewrite_model:
        return _merge_cli_credential_result(
            {
                "ok": True,
                "wrote": False,
                "mutated": False,
                "path": str(dest),
                "model": current_model,
                "model_id": current_id,
                "reason": "already present",
            },
            deepseek,
        )
    home.mkdir(parents=True, exist_ok=True)
    base_url = os.getenv("KIMI_CODE_BASE_URL", "https://api.moonshot.ai/v1").strip()
    text = existing
    wrote_provider = False
    if not has_provider:
        text = _ensure_trailing_newline(text)
        text = text + ("\n" if text else "") + _provider_block(key, base_url)
        wrote_provider = True
    apply_alias = alias
    if (
        current_model
        and not is_broken_managed_model(current_model, current_id)
        and not kimi_code_model_env_set()
    ):
        apply_alias = current_model
    text, mutated_model = apply_kimi_code_default_model(
        text, alias=apply_alias, model_id=kimi_code_model_id(apply_alias)
    )
    dest.write_text(text, encoding="utf-8")
    written_alias = config_default_model(text) or apply_alias
    written_id = config_model_id(text, written_alias) or model_id
    logger.info(
        "wrote Kimi Code CLI credentials to %s (model=%s id=%s; binary still required)",
        dest,
        written_alias,
        written_id,
    )
    return _merge_cli_credential_result(
        {
            "ok": True,
            "wrote": wrote_provider,
            "mutated": bool(existed and mutated_model),
            "path": str(dest),
            "model": written_alias,
            "model_id": written_id,
            "reason": "mutated" if existed and mutated_model else "wrote",
        },
        deepseek,
    )


@dataclass
class DispatchResult:
    via: str
    ok: bool
    detail: str
    specs: Dict[str, Any] = field(default_factory=dict)
    handlers: Dict[str, str] = field(default_factory=dict)
    kept_handler_ids: List[str] = field(default_factory=list)
    #: Handlers the CLI actually wrote — not factory-grounded hole-fill.
    cli_authored_ids: List[str] = field(default_factory=list)
    #: Keepable REUSE keep-path plants (persist / event_bus). Not CLI
    #: authorship and not GENERATE persist envelopes.
    factory_planted_ids: List[str] = field(default_factory=list)
    model: str = ""
    blocker: Optional[str] = None
    reuse_keep_path: bool = False
    factory_llm_generate_fallthrough: bool = False
    factory_llm_generate_ids: List[str] = field(default_factory=list)
    factory_llm_written_ids: List[str] = field(default_factory=list)
    factory_llm_model: str = ""
    generate_persist_ids: List[str] = field(default_factory=list)
    #: GENERATE/REUSE ids whose on-disk ``handle()`` failed only event_bus
    #: keepability — not a never-wrote miss.
    cli_unkeepable_event_bus_ids: List[str] = field(default_factory=list)
    receipt: Dict[str, Any] = field(default_factory=dict)
    #: This shot's harvest before receipt union. A later empty CLI must
    #: not hide a needed miss just because prior ids were merged on.
    shot_cli_authored_ids: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "via": self.via,
            "ok": self.ok,
            "detail": self.detail,
            "model": self.model,
            "blocker": self.blocker,
            "reuse_keep_path": self.reuse_keep_path,
            "factory_llm_generate_fallthrough": self.factory_llm_generate_fallthrough,
            "factory_llm_generate_ids": list(self.factory_llm_generate_ids),
            "factory_llm_written_ids": list(self.factory_llm_written_ids),
            "factory_llm_model": self.factory_llm_model,
            "generate_persist_ids": list(self.generate_persist_ids),
            "cli_unkeepable_event_bus_ids": list(self.cli_unkeepable_event_bus_ids),
            "handler_ids": sorted(self.handlers),
            "kept_handler_ids": sorted(self.kept_handler_ids),
            "cli_authored_ids": sorted(self.cli_authored_ids),
            "factory_planted_ids": sorted(self.factory_planted_ids),
            "spec_ids": sorted(self.specs),
            "receipt": dict(self.receipt),
        }

    def artifact_source(self, cid: str) -> str:
        """Authorship label for a dispatch-owned spec/handler.

        GENERATE-gap factory-LLM writes credit the factory model
        (OpenRouter / keyed Floor LLM), never FACTORY_CODE_CLI.
        """
        gap_ids = set(self.factory_llm_generate_ids or ())
        written = set(self.factory_llm_written_ids or ())
        persist = set(self.generate_persist_ids or ())
        if cid in written or (cid in gap_ids and cid in (self.specs or {})):
            return (
                f"coder LLM ({self.factory_llm_model})"
                if self.factory_llm_model
                else "coder LLM (factory)"
            )
        if cid in persist:
            from app.factory.build.persist_accept import FACTORY_GROUNDED_PERSIST_SOURCE

            return FACTORY_GROUNDED_PERSIST_SOURCE
        if self.model:
            return f"coder LLM ({self.model})"
        return "compiled-brief oneshot"


def inventory_gap_ids(compiled: Any) -> List[str]:
    """STEP 0 GENERATE/GAP ids. Empty does **not** skip FACTORY_CODE_CLI.

    Store-complete REUSE/COMPOSE still needs the CLI to bind and deepen
    handlers. This list only gates the OpenRouter GENERATE fallthrough
    (#367), not C-BRIEF dispatch.
    """
    out: List[str] = []
    for item in getattr(compiled, "inventory", ()) or ():
        if getattr(item, "is_gap", False):
            cid = str(getattr(item, "capability_id", "") or "").strip()
            if cid:
                out.append(cid)
    return out


def reuse_inventory_ids(compiled: Any) -> List[str]:
    """Verified REUSE rows — factory-grounded emit may keep these."""
    out: List[str] = []
    for item in getattr(compiled, "inventory", ()) or ():
        if getattr(item, "is_gap", False):
            continue
        cid = str(getattr(item, "capability_id", "") or "").strip()
        if cid:
            out.append(cid)
    return out


def _reuse_needs_hole_fill(root: Optional[Path], capability_id: str) -> bool:
    """True when a REUSE cap has no keepable on-disk handler yet."""
    roots = _handler_roots(root)
    if not roots:
        return True
    name = str(capability_id).replace("-", "_")
    for base in roots:
        path = base / "app" / "actions" / f"{name}.py"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _is_keepable_handler(text):
            return False
    return True


def cbrief_work_ids(compiled: Any, root: Optional[Path] = None) -> List[str]:
    """C-BRIEF work list: GENERATE gaps plus REUSE that still need hole-fill.

    sess_d10dfc2890f7487b logged ``gaps=[]`` on an all-REUSE
    insurance-distribution inventory (workflow/team/validation/…).
    Store-complete REUSE still needs persist / event_bus bind. Empty
    GENERATE ``inventory_gaps`` must not be claimed as "no work".
    """
    out: List[str] = []
    seen: set = set()
    for cid in inventory_gap_ids(compiled):
        if cid in seen:
            continue
        # Keepable on-disk handlers (CLI or Factory plant) are landed work.
        # sess_5782f226 run7 re-dispatched the same 6 gaps every later
        # phase because GENERATE ids never dropped after the plant.
        if root is not None and not _reuse_needs_hole_fill(root, cid):
            continue
        seen.add(cid)
        out.append(cid)
    for cid in reuse_inventory_ids(compiled):
        if cid in seen:
            continue
        if _reuse_needs_hole_fill(root, cid):
            seen.add(cid)
            out.append(cid)
    return out


def remaining_cbrief_work_ids(
    compiled: Any, result: DispatchResult, root: Optional[Path] = None
) -> List[str]:
    """Work items still open after harvest / persist / keep-path."""
    landed = (
        set(result.cli_authored_ids or ())
        | set(result.kept_handler_ids or ())
        | set(getattr(result, "generate_persist_ids", None) or ())
        | set(result.factory_llm_written_ids or ())
        | set(getattr(result, "factory_planted_ids", None) or ())
    )
    return [cid for cid in cbrief_work_ids(compiled, root) if cid not in landed]


EMPTY_CLI_WORK_STATE_KEY = "cbrief_empty_cli_work"


def record_unauthored_cli_work(
    state: Optional[Mapping[str, Any]],
    work_ids: Sequence[str],
    result: DispatchResult,
) -> List[str]:
    """Remember work a CLI shot already exited 0 without writing.

    Later phases / TESTER rework must not re-dispatch the same gaps
    until the 2700s wall burns (sess_5782f226 run7).
    """
    if not isinstance(state, dict):
        return []
    authored = set(result.cli_authored_ids or ()) | set(result.handlers or ())
    prev = [
        str(x).strip()
        for x in (state.get(EMPTY_CLI_WORK_STATE_KEY) or ())
        if str(x).strip()
    ]
    for cid in work_ids:
        name = str(cid or "").strip()
        if name and name not in authored and name not in prev:
            prev.append(name)
    state[EMPTY_CLI_WORK_STATE_KEY] = prev
    return list(prev)


def remaining_phase_cli_work(
    compiled: Any,
    root: Optional[Path],
    state: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """C-BRIEF work still owed and not already proven empty by a prior shot."""
    empty = {
        str(x).strip()
        for x in ((state or {}).get(EMPTY_CLI_WORK_STATE_KEY) or ())
        if str(x).strip()
    }
    return [
        cid for cid in cbrief_work_ids(compiled, root) if cid not in empty
    ]


def phase_authorship_miss(
    compiled: Any,
    result: DispatchResult,
    root: Optional[Path],
    work_at_start: Sequence[str],
) -> Optional[str]:
    """Phase-level miss when a needed CLI shot left work still open.

    An empty later-phase shot (plant/reuse, no remaining work) is not a
    miss. A needed shot that still has open work after harvest + plant
    fails immediately — do not burn the stage-2 wall re-dispatching.
    """
    if not work_at_start:
        return None
    if str(getattr(result, "via", "") or "") != "cli":
        return None
    if result.blocker and result.blocker != NAMED_BLOCKER_CLI_NO_AUTHORSHIP:
        return None
    shot = getattr(result, "shot_cli_authored_ids", None)
    if shot is None:
        shot = result.cli_authored_ids
    if shot or result.factory_llm_written_ids or result.handlers:
        return None
    remaining = remaining_cbrief_work_ids(compiled, result, root)
    if not remaining:
        return None
    return (
        f"{NAMED_BLOCKER_CLI_NO_AUTHORSHIP}: WRITER phase C-BRIEF "
        f"exited without harvested handlers for {list(remaining)} "
        f"(written={len(result.cli_authored_ids or [])}, "
        f"planted={len(result.factory_planted_ids or [])}). "
        "Fail closed at this phase — do not re-dispatch the same gaps "
        "until the wall burns. A CLI exit 0 is not C-BRIEF authorship."
    )


def merge_writer_phase_dispatch(
    prior: Optional[DispatchResult], later: DispatchResult
) -> DispatchResult:
    """Union harvest across three-phase WRITER shots.

    A later plant/reuse CLI that harvests written=0 must not replace
    backend cli_authored_ids / factory_planted_ids.
    """
    if prior is None:
        return later
    later.cli_authored_ids = _unique_ids(
        prior.cli_authored_ids, later.cli_authored_ids
    )
    later.factory_planted_ids = _unique_ids(
        prior.factory_planted_ids, later.factory_planted_ids
    )
    later.factory_llm_written_ids = _unique_ids(
        prior.factory_llm_written_ids, later.factory_llm_written_ids
    )
    later.generate_persist_ids = _unique_ids(
        prior.generate_persist_ids, later.generate_persist_ids
    )
    later.kept_handler_ids = _unique_ids(
        prior.kept_handler_ids, later.kept_handler_ids
    )
    later.cli_unkeepable_event_bus_ids = _unique_ids(
        prior.cli_unkeepable_event_bus_ids, later.cli_unkeepable_event_bus_ids
    )
    if prior.handlers or later.handlers:
        later.handlers = {**dict(prior.handlers or {}), **dict(later.handlers or {})}
    if prior.specs or later.specs:
        later.specs = {**dict(prior.specs or {}), **dict(later.specs or {})}
    if later.blocker == NAMED_BLOCKER_CLI_NO_AUTHORSHIP and (
        later.cli_authored_ids
        or later.factory_llm_written_ids
        or later.handlers
    ):
        # Plants-only must not clear the named miss — that is still not
        # C-BRIEF authorship (#374). Prior CLI authored ids do.
        later.blocker = (
            prior.blocker
            if prior.blocker != NAMED_BLOCKER_CLI_NO_AUTHORSHIP
            else None
        )
        later.detail = prior.detail or later.detail
    if not later.via:
        later.via = prior.via
    if later.model == "" and prior.model:
        later.model = prior.model
    return later


def dispatch_from_state(state: Optional[Mapping[str, Any]]) -> Optional[DispatchResult]:
    """Rehydrate a prior brief_dispatch so later phases can merge into it."""
    return dispatch_from_receipt(dict((state or {}).get("brief_dispatch") or {}))


def dispatch_from_receipt(raw: Optional[Mapping[str, Any]]) -> Optional[DispatchResult]:
    """Rehydrate harvest ids from coder_receipt.json / brief_dispatch."""
    blob = dict(raw or {})
    if not blob:
        return None
    if not any(
        blob.get(key)
        for key in (
            "via",
            "cli_authored_ids",
            "factory_planted_ids",
            "factory_llm_written_ids",
            "kept_handler_ids",
            "handler_ids",
            "blocker",
        )
    ):
        return None
    return DispatchResult(
        via=str(blob.get("via") or ""),
        ok=bool(blob.get("ok")),
        detail=str(blob.get("detail") or ""),
        blocker=blob.get("blocker"),
        model=str(blob.get("model") or ""),
        handlers={str(k): "" for k in (blob.get("handler_ids") or [])},
        kept_handler_ids=list(blob.get("kept_handler_ids") or []),
        cli_authored_ids=list(blob.get("cli_authored_ids") or []),
        factory_planted_ids=list(blob.get("factory_planted_ids") or []),
        factory_llm_written_ids=list(blob.get("factory_llm_written_ids") or []),
        generate_persist_ids=list(blob.get("generate_persist_ids") or []),
        cli_unkeepable_event_bus_ids=list(
            blob.get("cli_unkeepable_event_bus_ids") or []
        ),
        reuse_keep_path=bool(blob.get("reuse_keep_path")),
    )


def prior_dispatch_for_receipt(ctx: Any) -> Optional[DispatchResult]:
    """Union in-memory brief_dispatch with on-disk receipts (dest + staging)."""
    prior = dispatch_from_state(getattr(ctx, "state", None))
    for base in _handler_roots(getattr(ctx, "workspace", None)):
        disk = dispatch_from_receipt(read_receipt(base))
        if disk is None:
            continue
        prior = merge_writer_phase_dispatch(prior, disk) if prior else disk
    return prior


def remaining_inventory_gaps(compiled: Any, result: DispatchResult) -> List[str]:
    """Gaps still open after factory-LLM writes land. Fail-closed until then."""
    landed = set(result.factory_llm_written_ids or ()) | set(
        getattr(result, "generate_persist_ids", None) or ()
    )
    return [cid for cid in inventory_gap_ids(compiled) if cid not in landed]


def remaining_unauthored_generate_gaps(compiled: Any, result: DispatchResult) -> List[str]:
    """GENERATE ids the CLI did not author and persist has not landed."""
    authored = set(result.cli_authored_ids or ())
    return [
        cid
        for cid in remaining_inventory_gaps(compiled, result)
        if cid not in authored
    ]


def cli_miss_allows_generate_llm(result: DispatchResult) -> bool:
    """Billing / credentials / unavailable honesty — factory LLM may write gaps."""
    if result.ok or result.via == "http_oneshot":
        return False
    if result.blocker in CLI_GENERATE_LLM_FALLTHROUGH_BLOCKERS:
        return True
    return False


def should_factory_llm_generate_gaps(
    compiled: Any, result: DispatchResult
) -> bool:
    """Named CLI miss + remaining GENERATE gaps → one factory-LLM brief shot.

    A ready DeepSeek+kimi session must not fall through to OpenRouter
    (sess_b9fbae7 photographed in-process minimax-m3:free while health
    already showed a ready DeepSeek CLI).

    Empty ``inventory_gaps`` (all REUSE/COMPOSE) must not be treated as
    "skip CLI" — that is a dispatch decision, not this fallthrough.
    """
    if deepseek_cli_ready():
        return False
    if not cli_miss_allows_generate_llm(result):
        return False
    return bool(inventory_gap_ids(compiled))


def should_factory_grounded_generate_persist(
    compiled: Any, result: DispatchResult
) -> bool:
    """CLI finished without authored GENERATE gaps → persist envelope.

    DeepSeek-ready Floor must not reopen OpenRouter fallthrough. Persist
    emit is not CLI authorship and does not credit factory-LLM bodies.
    """
    if result.factory_llm_generate_fallthrough:
        return False
    if result.via != "cli":
        return False
    if result.blocker in {
        NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL,
        NAMED_BLOCKER_STOPPED,
        "CODER_SESSION_CRASHED",
    }:
        return False
    if not remaining_unauthored_generate_gaps(compiled, result):
        return False
    if result.ok:
        return True
    return result.blocker in {
        NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
        NAMED_BLOCKER_CLI_UNKEEPABLE_EVENT_BUS,
    }


def should_keep_factory_grounded_reuse(compiled: Any, result: DispatchResult) -> bool:
    """Verified REUSE + named billing/auth CLI miss → keep emit/harvest.

    Live sess_d5789a91 (VetCare Hub / veterinary-care): inventory_gaps=[]
    and FACTORY_CODE_CLI exited 1 with Moonshot 429 / insufficient
    balance. Harvest was skipped because ``result.ok`` was false, then
    WRITER labeled persist/event_bus emit as a deterministic template and
    budget_inspect hard-stopped at stub_rate≈0.833 / SCAFFOLD.

    Mixed plans (REUSE + GENERATE, live veterinary_care_core gap) still
    harvest the REUSE rows. GENERATE gaps are a separate factory-LLM leg.
    GENERATE-only plans do not claim this keep-path.
    """
    if result.ok:
        return False
    if result.blocker not in CLI_AUTH_BILLING_BLOCKERS:
        return False
    return bool(reuse_inventory_ids(compiled))


def factory_grounded_source_for(
    capability_id: str, block_ids: Optional[Sequence[str]] = None
) -> str:
    """Authorship label for factory-grounded persist / event_bus emit."""
    from app.factory.build.persist_accept import FACTORY_GROUNDED_PERSIST_SOURCE
    from app.factory.build.workflow_accept import needs_grounded_event_bus_handler

    if needs_grounded_event_bus_handler(capability_id, block_ids):
        from app.factory.build.workflow_accept import (
            FACTORY_GROUNDED_EVENT_BUS_SOURCE,
        )

        return FACTORY_GROUNDED_EVENT_BUS_SOURCE
    return FACTORY_GROUNDED_PERSIST_SOURCE


def emit_factory_grounded_reuse_keep_path(
    root: Path, compiled: Any, *, only_missing: bool = False
) -> List[str]:
    """Write persist / event_bus handlers for REUSE caps, then harvest can keep.

    Does not claim a CLI session. Does not write GENERATE/GAP caps — those
    fall through to the factory coder LLM after a billing/auth miss.
    ``only_missing`` fills holes after a partial CLI write without
    overwriting a keepable handler.
    """
    from app.factory.build.reuse_accept import harvest_block_default_actions
    from app.factory.build.roles_handlers import (
        _capability_handler_body,
        _handler_module,
    )

    root = Path(root)
    written: List[str] = []
    actions = root / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    for item in getattr(compiled, "inventory", ()) or ():
        if getattr(item, "is_gap", False):
            continue
        if not getattr(item, "handler_source", "") and not getattr(
            item, "verified_present", None
        ):
            continue
        cid = str(getattr(item, "capability_id", "") or "").strip()
        if not cid:
            continue
        bids = [
            str(b)
            for b in (
                getattr(item, "verified_present", None)
                or getattr(item, "block_ids", None)
                or ()
            )
            if str(b).strip()
        ]
        name = cid.replace("-", "_")
        path = actions / f"{name}.py"
        if only_missing and path.is_file():
            written.append(cid)
            continue
        body = _capability_handler_body(cid, bids)
        source = factory_grounded_source_for(cid, bids)
        defaults = harvest_block_default_actions(bids, root)
        path.write_text(
            _handler_module(
                cid, bids, body, source, defaults, entity=name
            ),
            encoding="utf-8",
        )
        written.append(cid)
    return written


def apply_factory_llm_generate_gaps(
    ctx: Any, compiled: Any, result: DispatchResult
) -> DispatchResult:
    """Second leg: one factory-LLM compiled-brief shot for GENERATE gaps.

    Receipt stays ``ok=false`` with the CLI billing/auth blocker. Not
    ``FACTORY_BRIEF_HTTP_ONESHOT`` and not a ≥2h CLI session. GENERATE
    handlers are dispatch-grounded on disk (alembic entity + the pure-
    dispatch envelope; the ROUTE owns tenant-scoped persistence) even when
    the factory LLM is empty, raises, or
    keys a body under an alias (sess_336246 ``veterinary_care_core`` vs
    ``vetcare_hub_veterinary_core``).
    """
    if not should_factory_llm_generate_gaps(compiled, result):
        return result
    from app.factory.build.persist_accept import (
        bind_generate_artifacts,
        emit_factory_grounded_generate_persist,
    )
    from app.factory.coder import CoderError, coder_enabled, generate_from_compiled_brief

    if not coder_enabled():
        # Unkeyed / coder-disabled still uses honest templates. Persist
        # emit is the billing keep-path after a factory-LLM attempt.
        return result
    gap_ids = inventory_gap_ids(compiled)
    result.factory_llm_generate_fallthrough = True
    result.factory_llm_generate_ids = list(gap_ids)
    root = _workspace_root(ctx)
    brief = compiled.text if hasattr(compiled, "text") else str(compiled)
    suffix = (
        "\n\n## GENERATE-GAP FALLTHROUGH\n"
        "FACTORY_CODE_CLI missed (billing/credentials/unavailable). "
        f"Write ONLY these GENERATE inventory_gaps: {gap_ids!r}. "
        "Do not claim a ≥2h CLI session. Do not invent REUSE block ids.\n"
    )
    ctx.note(
        (
            f"FACTORY_CODE_CLI {result.blocker} — factory coder LLM for "
            f"{len(gap_ids)} GENERATE gap(s); not a ≥2h CLI session"
        ),
        stage="dispatch",
        source="factory coder LLM (GENERATE gaps)",
        model_call=True,
        done=0,
        total=1,
    )
    llm: Dict[str, Any] = {}
    try:
        llm = generate_from_compiled_brief(
            brief=brief + suffix,
            capabilities=list(gap_ids),
            product_name=compiled.product_name,
            vertical=compiled.vertical,
        )
    except CoderError as exc:
        _append_log(
            root / LOG_REL,
            f"[factory-llm] GENERATE-gap fallthrough after {result.blocker} "
            f"failed: {exc}",
        )
        ctx.state.setdefault("coder_failures", {})["factory_llm_generate"] = str(exc)
        ctx.note(
            f"factory coder LLM GENERATE fallthrough failed: {exc}",
            stage="dispatch",
            source="factory coder LLM (GENERATE gaps)",
            done=0,
            total=1,
        )
    model = str(llm.get("model") or "")
    result.factory_llm_model = model
    written: List[str] = []
    raw_specs = bind_generate_artifacts(gap_ids, llm.get("specs") or {})
    raw_handlers = bind_generate_artifacts(gap_ids, llm.get("handlers") or {})
    for cid in gap_ids:
        spec = raw_specs.get(cid)
        if isinstance(spec, dict) and spec.get("entity"):
            result.specs[cid] = spec
        body = raw_handlers.get(cid)
        if isinstance(body, str) and body.strip():
            result.handlers[cid] = body
            written.append(cid)
    result.factory_llm_written_ids = written
    if written and model:
        persist_source = f"coder LLM ({model})"
    elif written:
        persist_source = "coder LLM (factory)"
    else:
        persist_source = "factory-grounded persist"
    landed = emit_factory_grounded_generate_persist(
        root,
        compiled,
        handlers=result.handlers,
        specs=result.specs,
        source=persist_source,
    )
    result.generate_persist_ids = list(landed)
    _append_log(
        root / LOG_REL,
        "[factory-llm] persist-grounded GENERATE emit "
        f"after {result.blocker}: {landed}",
    )
    _append_log(
        root / LOG_REL,
        f"[factory-llm] GENERATE-gap fallthrough after {result.blocker}: "
        f"attempted={gap_ids} written={written} persist={landed} model={model}",
    )
    ctx.note(
        (
            f"{result.blocker} — factory coder LLM wrote {len(written)}/"
            f"{len(gap_ids)} GENERATE gap(s); persist emit {len(landed)}; "
            "not a ≥2h CLI session"
        ),
        stage="dispatch",
        source=(
            f"coder LLM ({model})" if model else "factory coder LLM (GENERATE gaps)"
        ),
        done=1 if landed else 0,
        total=1,
    )
    return result


def apply_factory_grounded_generate_persist(
    ctx: Any, compiled: Any, result: DispatchResult
) -> DispatchResult:
    """Persist-grounded GENERATE after CLI harvest without OpenRouter.

    Same ``emit_factory_grounded_generate_persist`` envelope as billing
    fallthrough. Does not call ``generate_from_compiled_brief``. Does not
    add persist ids to ``cli_authored_ids`` or ``factory_llm_written_ids``.
    """
    if not should_factory_grounded_generate_persist(compiled, result):
        return result
    from app.factory.build.persist_accept import (
        FACTORY_GROUNDED_PERSIST_SOURCE,
        emit_factory_grounded_generate_persist,
    )

    root = _workspace_root(ctx)
    authored = set(result.cli_authored_ids or ())
    force = [
        cid
        for cid in (result.cli_unkeepable_event_bus_ids or ())
        if cid not in authored
    ]
    gap_ids = remaining_unauthored_generate_gaps(compiled, result)
    landed = emit_factory_grounded_generate_persist(
        root,
        compiled,
        handlers={},
        specs=result.specs,
        source=FACTORY_GROUNDED_PERSIST_SOURCE,
        force_ids=force,
    )
    landed = [cid for cid in landed if cid not in authored]
    result.generate_persist_ids = list(
        dict.fromkeys([*result.generate_persist_ids, *landed])
    )
    _append_log(
        root / LOG_REL,
        "[factory-persist] persist-grounded GENERATE emit after CLI "
        f"harvest (no OpenRouter, no CLI authorship): {landed} "
        f"unkeepable_event_bus={force}",
    )
    ctx.note(
        (
            "FACTORY_CODE_CLI harvest left "
            f"{len(gap_ids)} GENERATE gap(s); persist emit {len(landed)}; "
            "not CLI authorship and not a ≥2h CLI session"
        ),
        stage="dispatch",
        source=FACTORY_GROUNDED_PERSIST_SOURCE,
        done=1 if landed else 0,
        total=1,
    )
    return result


def write_dispatch_receipt(
    ctx: Any,
    compiled: Any,
    result: DispatchResult,
) -> Dict[str, Any]:
    """Persist coder_receipt.json. Honesty fields stay even on keep-path.

    A later empty CLI must not replace a prior good harvest. Union planted
    + authored ids from state and dest/staging receipts; never write an
    empty ``cli_authored_ids`` over a non-empty prior (sess_5782f226 run8).
    Shot-level emptiness stays on ``shot_cli_authored_ids`` so a needed
    miss can still fail-closed.
    """
    if result.shot_cli_authored_ids is None:
        result.shot_cli_authored_ids = list(result.cli_authored_ids or [])
    prior = prior_dispatch_for_receipt(ctx)
    if prior is not None:
        merge_writer_phase_dispatch(prior, result)
    receipt = {
        "via": result.via,
        "ok": result.ok,
        "detail": result.detail,
        "blocker": result.blocker,
        "honesty_class": (
            NAMED_BLOCKER_CLI_FAILED
            if result.blocker
            in CLI_AUTH_BILLING_BLOCKERS | {NAMED_BLOCKER_CLI_FAILED}
            else result.blocker
        ),
        "keep_path": (
            KEEP_PATH_FACTORY_GROUNDED_REUSE if result.reuse_keep_path else None
        ),
        "reuse_keep_path": result.reuse_keep_path,
        "factory_llm_generate_fallthrough": result.factory_llm_generate_fallthrough,
        "factory_llm_generate_ids": list(result.factory_llm_generate_ids),
        "factory_llm_written_ids": list(result.factory_llm_written_ids),
        "factory_llm_model": result.factory_llm_model,
        "generate_persist_ids": list(result.generate_persist_ids),
        "cli_authored_ids": sorted(result.cli_authored_ids),
        "factory_planted_ids": sorted(result.factory_planted_ids),
        "cli_unkeepable_event_bus_ids": list(result.cli_unkeepable_event_bus_ids),
        "model": result.model,
        "product_id": compiled.product_id,
        "vertical": compiled.vertical,
        "capabilities": list(compiled.capabilities),
        "inventory_reuse": [
            item.capability_id
            for item in compiled.inventory
            if item.verified_present and not item.missing
        ],
        "inventory_gaps": remaining_inventory_gaps(compiled, result),
        "inventory_work": remaining_cbrief_work_ids(
            compiled, result, root=_workspace_root(ctx)
        ),
        "harvested_spec_ids": sorted(result.specs),
        "kept_handler_ids": [
            cid
            for cid in result.kept_handler_ids
            if cid not in set(result.factory_llm_generate_ids or ())
        ],
    }
    result.kept_handler_ids = list(receipt["kept_handler_ids"])
    result.receipt = receipt
    root = _workspace_root(ctx)
    try:
        ctx.workspace.write_text(
            RECEIPT_REL,
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        )
    except Exception:  # noqa: BLE001 — receipt must not fail the role
        _write_receipt(root, receipt)
    return receipt


def refresh_receipt_harvest(
    ctx: Any, compiled: Any, result: DispatchResult
) -> DispatchResult:
    """Re-harvest after WRITER emit so models/handlers land on the receipt."""
    root = _workspace_root(ctx)
    _merge_workspace_harvest(result, root, list(compiled.capabilities))
    write_dispatch_receipt(ctx, compiled, result)
    return result


def _write_receipt(root: Path, payload: Mapping[str, Any]) -> None:
    path = Path(root) / RECEIPT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _workspace_root(ctx: Any) -> Path:
    ws = getattr(ctx, "workspace", None)
    if ws is None:
        raise RuntimeError("dispatch requires a role workspace")
    return Path(getattr(ws, "workspace", ws))


def write_brief_artifacts(ctx: Any, compiled: Any) -> None:
    """Persist the compiled brief + a running control file (WRITER lanes)."""
    from app.factory.build.architect import dispatch_text

    # NARRATIVE (architect, when attached) + CONTRACT verbatim; the CONTRACT
    # alone when no narrative is attached (FACTORY_BRIEF_NARRATIVE off/shadow).
    text = dispatch_text(compiled) if hasattr(compiled, "text") else str(compiled)
    ctx.workspace.write_text(BRIEF_REL, text if text.endswith("\n") else text + "\n")
    if not (Path(_workspace_root(ctx)) / CONTROL_REL).is_file():
        ctx.workspace.write_text(
            CONTROL_REL,
            json.dumps({"action": CONTROL_RUN, "updated_at": "start"}) + "\n",
        )
    ctx.workspace.write_text(LOG_REL, "")
    ctx.note(
        "compiled one gated brief (TARGET / STEP 0 INVENTORY / DO / ACCEPTANCE)",
        stage="brief",
        done=1,
        total=1,
        source="brief compiler",
    )


def _brief_text(root: Path, compiled: Any) -> str:
    """Gated ``docs/coder_brief.md`` body for ``--prompt`` / stdin."""
    path = Path(root) / BRIEF_REL
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if (text or "").strip():
            return text
    from app.factory.build.architect import dispatch_text

    return dispatch_text(compiled)


def _feed_cli_stdin(proc: subprocess.Popen, payload: str) -> None:
    """Write the brief once and close stdin (Kimi ``--prompt`` contract)."""
    if proc.stdin is None:
        return
    try:
        proc.stdin.write(payload)
    except BrokenPipeError:
        return
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass


def _cli_clock(ctx: Any) -> Callable[[], float]:
    """Prefer the runner clock so inspect bumps and CLI walls share a timeline."""
    box = getattr(ctx, "deadline_box", None) or {}
    clock = box.get("clock") if isinstance(box, Mapping) else None
    if callable(clock):
        return clock
    return time.monotonic


def cli_dispatch_timeout_s(leftover_s: Optional[float] = None) -> float:
    """FACTORY_CODE_CLI wait + Floor ``deadline_s`` for one C-BRIEF.

    Tracks ``FACTORY_CODER_TIMEOUT_S`` via ``attempt_wall_s()`` so a
    production 7200s pin is ~7230 (timeout + grace), not a frozen
    ``STAGE_1_S - 15`` (1785). sess_2fba31ab1a194a73 died at 1896s
    against that 1785s model-call deadline while stage inspect / Floor
    still advertised a longer watchdog.

    A leftover stage-1 box cannot slash a higher timeout.
    ``FACTORY_CODER_BUDGET_S`` ≥ stage-1 is honoured the same way
    (Render 7200). DeepSeek C-BRIEF is a ≥2h-capable session, so the
    production ceiling + grace (7230) wins over the HTTP hang default
    (1200 → 2400). Test-scale timeouts (< 60s) keep the leftover / 30s
    formula so hung-CLI tests stay tight.
    """
    from app.factory.build.budget_inspect import CEILING_S, STAGE_1_S
    from app.factory.coder import coder_budget_s
    from app.factory.llm_watchdog import (
        LEGACY_TIMEOUT_BAND_MIN_S,
        MODEL_CALL_GRACE_S,
        attempt_wall_s,
        call_timeout_s,
    )

    timeout_s = 1500.0
    if leftover_s is not None:
        timeout_s = max(30.0, float(leftover_s) - 15.0)

    per = call_timeout_s()
    if per < LEGACY_TIMEOUT_BAND_MIN_S:
        return timeout_s

    timeout_s = max(timeout_s, float(attempt_wall_s()))
    budget = coder_budget_s()
    if budget >= STAGE_1_S:
        timeout_s = max(timeout_s, float(budget) + MODEL_CALL_GRACE_S)
    if deepseek_cli_ready():
        timeout_s = max(
            timeout_s,
            float(STAGE_1_S) - 15.0,
            float(CEILING_S) + MODEL_CALL_GRACE_S,
        )
    return timeout_s


def cli_watchdog_remaining_s(
    *, leftover_s: Optional[float], elapsed_s: float
) -> float:
    """Floor ``deadline_s`` from *this* NOTE so age + deadline ≈ the CLI wall.

    A 30→45 inspect bump must not advertise leftover-15 (~885s) and
    slash a 7230s C-BRIEF watchdog.
    """
    wall = cli_dispatch_timeout_s(leftover_s=leftover_s)
    remaining = max(30.0, float(wall) - max(0.0, float(elapsed_s)))
    if leftover_s is not None:
        remaining = max(remaining, max(30.0, float(leftover_s) - 15.0))
    return remaining


def _cli_live_deadline(
    ctx: Any, timeout_s: float, started_mono: float
) -> Optional[float]:
    """CLI wall: remapped dispatch timeout, grown by a 30→45 inspect bump.

    ``max`` so leftover COLLECTOR/CLONER / stage-1 time cannot slash a
    remapped FACTORY_CODER_TIMEOUT_S (7200 → ~7230) C-BRIEF, while a
    stage-2 box can still expire a short test-scale session.
    """
    now = float(_cli_clock(ctx)())
    candidates: List[float] = []
    if timeout_s > 0:
        candidates.append(started_mono + float(timeout_s))
    if hasattr(ctx, "coder_time_left"):
        left = ctx.coder_time_left()
        if left is not None:
            candidates.append(now + max(0.0, float(left) - 15.0))
    return max(candidates) if candidates else None


def _pulse_stage_inspect(ctx: Any) -> None:
    """Let a quiet CLI still hit staged inspect without a Floor NOTE."""
    box = getattr(ctx, "deadline_box", None) or {}
    pulse = box.get("inspect") if isinstance(box, Mapping) else None
    if callable(pulse):
        try:
            pulse()
        except Exception:  # noqa: BLE001 — inspect must never fail the CLI
            logger.exception("FACTORY_CODE_CLI stage inspect pulse failed")


def _run_cli_session(
    ctx: Any,
    compiled: Any,
    *,
    timeout_s: float,
) -> DispatchResult:
    from app.factory.code_cli import (
        KimiPromptEmpty,
        claude_print_argv,
        claude_print_log_argv,
        kimi_prompt_argv,
        kimi_prompt_log_argv,
    )
    from app.factory.coder import (
        code_cli_command,
        deepseek_cli_environ,
        deepseek_code_model,
        deepseek_coder_selected,
        is_claude_code_cli,
    )

    root = _workspace_root(ctx)
    log_path = root / LOG_REL
    cli = resolve_code_cli() or code_cli_command()
    stdin_payload: Optional[str] = None
    brief_text = _brief_text(root, compiled)
    # Leftover Anthropic-native Claude (no DeepSeek) keeps --print.
    # DeepSeek always uses Kimi --prompt + OpenAI-compat env.
    if is_claude_code_cli(cli) and not deepseek_coder_selected(cli):
        try:
            cmd = claude_print_argv(cli, brief_text)
        except KimiPromptEmpty as exc:
            _append_log(log_path, f"[{NAMED_BLOCKER_CLI_FAILED}] {exc}")
            return DispatchResult(
                via="cli",
                ok=False,
                detail=str(exc),
                blocker=NAMED_BLOCKER_CLI_FAILED,
                model=cli,
            )
        stdin_payload = brief_text
        _append_log(
            log_path,
            f"$ {' '.join(claude_print_log_argv(cli, len(brief_text)))}",
        )
    else:
        model = ""
        if deepseek_coder_selected(cli):
            model = deepseek_code_model()
        elif cli_requires_kimi_credentials(cli):
            alias = kimi_prompt_model_alias()
            if alias and _MODEL_ALIAS_RE.match(alias):
                model = alias
        try:
            cmd = kimi_prompt_argv(cli, brief_text, model=model or None)
        except KimiPromptEmpty as exc:
            _append_log(log_path, f"[{NAMED_BLOCKER_CLI_FAILED}] {exc}")
            return DispatchResult(
                via="cli",
                ok=False,
                detail=str(exc),
                blocker=NAMED_BLOCKER_CLI_FAILED,
                model=cli,
            )
        stdin_payload = brief_text
        _append_log(
            log_path,
            f"$ {' '.join(kimi_prompt_log_argv(cli, len(brief_text), model=model or None))}",
        )
    session_env = os.environ.copy()
    if deepseek_coder_selected(cli):
        session_env.update(deepseek_cli_environ())
    logger.info(
        "FACTORY_CODE_CLI C-BRIEF dispatch via=cli command=%s "
        "gaps=%s work=%s reuse=%s timeout_s=%.0f",
        cli,
        inventory_gap_ids(compiled),
        cbrief_work_ids(compiled, root),
        reuse_inventory_ids(compiled),
        timeout_s,
    )
    dispatch_payload: Dict[str, Any] = {
        "stage": "dispatch",
        "model_call": True,
        "source": SOURCE_CODER_CLI,
        "done": 0,
        "total": 1,
    }
    if timeout_s > 0:
        dispatch_payload["deadline_s"] = float(timeout_s)
    ctx.note(
        f"dispatching compiled brief via FACTORY_CODE_CLI ({cli})",
        **dispatch_payload,
    )
    popen_kw: Dict[str, Any] = {
        "cwd": str(root),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        "env": session_env,
        # The agent's shell gets its own process group: a group-wide signal
        # it sends can never reach the Factory server (agent_process).
        **agent_popen_kwargs(),
    }
    if stdin_payload is not None:
        popen_kw["stdin"] = subprocess.PIPE
    try:
        proc = track_agent(subprocess.Popen(cmd, **popen_kw))
    except FileNotFoundError:
        return DispatchResult(
            via="unavailable",
            ok=False,
            detail=f"{cli} not found on PATH",
            blocker=NAMED_BLOCKER_CLI,
        )
    if stdin_payload is not None:
        _feed_cli_stdin(proc, stdin_payload)

    clock = _cli_clock(ctx)
    started_mono = float(clock())
    deadline = _cli_live_deadline(ctx, timeout_s, started_mono)
    stopped = False
    hung_killed = False
    try:
        while True:
            _pulse_stage_inspect(ctx)
            deadline = _cli_live_deadline(ctx, timeout_s, started_mono)
            action = wait_if_paused(root, deadline=deadline, clock=clock)
            if action == CONTROL_STOP:
                stopped = True
                _append_log(log_path, "[owner STOP]")
                terminate_agent_tree(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    kill_agent_tree(proc)
                break
            if deadline is not None and clock() >= deadline:
                hung_killed = True
                _append_log(
                    log_path,
                    f"[{NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL}] "
                    "budget wall — stopping CLI session",
                )
                terminate_agent_tree(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    kill_agent_tree(proc)
                break
            if proc.poll() is not None:
                break
            if proc.stdout is None:
                time.sleep(0.1)
                continue
            line = proc.stdout.readline()
            if line:
                _append_log(log_path, line.rstrip("\n"))
                ctx.note(line.strip()[:200], stage="dispatch", source="coder CLI")
            else:
                time.sleep(0.05)
        # Drain remainder.
        if proc.stdout is not None:
            rest = proc.stdout.read() or ""
            if rest.strip():
                _append_log(log_path, rest.rstrip("\n"))
        code = proc.wait(timeout=2) if proc.poll() is None else proc.returncode
    except Exception as exc:  # noqa: BLE001
        if proc.poll() is None:
            kill_agent_tree(proc)
        return DispatchResult(
            via="cli",
            ok=False,
            detail=f"CLI session crashed: {exc}",
            blocker="CODER_SESSION_CRASHED",
        )

    if stopped or read_control(root) == CONTROL_STOP:
        return DispatchResult(
            via="cli",
            ok=False,
            detail="owner stopped the coder session",
            blocker=NAMED_BLOCKER_STOPPED,
        )
    if hung_killed:
        ctx.note(
            f"{NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL}: budget wall stopped the CLI session",
            stage="dispatch",
            source=SOURCE_CODER_CLI,
            blocker=NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL,
            **{MODEL_CALL_STATE: CLOSED},
        )
        return DispatchResult(
            via="cli",
            ok=False,
            detail=(
                f"{NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL}: FACTORY_CODE_CLI "
                "was still running when the staged coder wall expired. "
                "Not FACTORY_CODE_CLI_UNUSED — the in-flight session was "
                "killed after inspect, not skipped."
            ),
            blocker=NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL,
            model=cli,
        )
    if code != 0:
        log_text = ""
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
        blocker, detail = classify_cli_exit(
            int(code or 1), log_text, model_configured=cli_default_model_ok(cli)
        )
        return DispatchResult(
            via="cli",
            ok=False,
            detail=detail,
            blocker=blocker,
            model=cli,
        )
    ctx.note(
        "FACTORY_CODE_CLI session finished",
        stage="dispatch",
        source=SOURCE_CODER_CLI,
        done=1,
        total=1,
        **{MODEL_CALL_STATE: CLOSED},
    )
    return DispatchResult(
        via="cli",
        ok=True,
        detail=f"CLI session completed ({cli})",
        model=cli,
    )


def _http_oneshot(ctx: Any, compiled: Any) -> DispatchResult:
    """One HTTP call for the whole job when the CLI is not installed.

    Still not a per-capability micro-loop. Stubbed in CI.
    """
    from app.factory.coder import CoderError, coder_enabled, generate_from_compiled_brief

    if not coder_enabled():
        return DispatchResult(
            via="skipped",
            ok=False,
            detail="coder disabled — templates will author the workspace",
        )
    ctx.note(
        "FACTORY_CODE_CLI unavailable — one HTTP oneshot of the compiled brief",
        stage="dispatch",
        source="coder LLM",
        model_call=True,
        done=0,
        total=1,
    )
    try:
        result = generate_from_compiled_brief(
            brief=compiled.text,
            capabilities=list(compiled.capabilities),
            product_name=compiled.product_name,
            vertical=compiled.vertical,
        )
    except CoderError as exc:
        return DispatchResult(
            via="http_oneshot",
            ok=False,
            detail=str(exc),
            blocker="BRIEF_HTTP_ONESHOT_FAILED",
        )
    ctx.note(
        "compiled-brief HTTP oneshot returned artifacts",
        stage="dispatch",
        source=f"coder LLM ({result.get('model')})",
        done=1,
        total=1,
    )
    return DispatchResult(
        via="http_oneshot",
        ok=True,
        detail="one HTTP oneshot of the compiled brief",
        specs=dict(result.get("specs") or {}),
        handlers=dict(result.get("handlers") or {}),
        model=str(result.get("model") or ""),
    )




def _module_level_names(text: str) -> frozenset:
    """Names a module defines at top level (functions, classes, assignments),
    read from its syntax tree -- not from substrings of its source."""
    import ast

    try:
        tree = ast.parse(text or "")
    except SyntaxError:
        return frozenset()
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(x.id for x in targets if isinstance(x, ast.Name))
    return frozenset(names)


def _has_brief_workflow_steps(text: str) -> bool:
    """True when a handler constructs workflow / event_bus steps."""
    blob = text or ""
    from app.factory.build.workflow_accept import (
        handler_builds_workflow_children,
        handler_constructs_event_bus_step,
    )

    return handler_builds_workflow_children(blob) or handler_constructs_event_bus_step(blob)


def _is_keepable_handler(text: str) -> bool:
    """CLI / oneshot wrote a complete capability module, not a fragment.

    Prepared brief-driven event_bus steps must survive the fallback
    envelope even when the module omitted CAPABILITY_ID. Unprepared
    ``{'block': 'event_bus', 'input': payload}`` must NOT be kept —
    including an unprepared step_1 (appointment_scheduling after #325)
    and a prepared step_1 plus an unprepared step_2
    (appointment_booking class after #323). A factory wrap around raw
    children is not keepable. That is how PRODUCT
    ``workflow: step_1 (event_bus): error`` locked in
    (sess_14e690829d1f4282).
    """
    blob = text or ""
    names = _module_level_names(blob)
    if "handle" not in names:
        return False
    if not handler_satisfies_event_bus_contract(
        blob, require_prepared_step=_has_brief_workflow_steps(blob)
    ):
        return False
    if "CAPABILITY_ID" in names:
        return True
    return handler_has_prepared_event_bus_step(blob) or _has_brief_workflow_steps(blob)


def _is_event_bus_unkeepable_handler(text: str) -> bool:
    """True when ``handle()`` exists but keepability fails only on event_bus."""
    blob = text or ""
    if "handle" not in _module_level_names(blob):
        return False
    if _is_keepable_handler(blob):
        return False
    return not handler_satisfies_event_bus_contract(
        blob, require_prepared_step=_has_brief_workflow_steps(blob)
    )


def _merge_workspace_harvest(
    result: DispatchResult,
    root: Path,
    capability_ids: Sequence[str],
) -> None:
    """Keep workspace specs/handlers; do not let the envelope overwrite them."""
    harvested_specs, kept = harvest_cli_artifacts(root, capability_ids)
    if harvested_specs:
        result.specs.update(harvested_specs)
    merged = list(dict.fromkeys([*result.kept_handler_ids, *kept]))
    result.kept_handler_ids = merged
    if harvested_specs or kept:
        _append_log(
            root / LOG_REL,
            "[harvest] workspace "
            f"specs={sorted(harvested_specs)} kept_handlers={merged}",
        )
    # Same-session CLI: prefer on-disk PREPARED workflow steps over a
    # thin / unprepared JSON body. Unprepared disk steps are not kept —
    # the factory wrapper must run prepare_block_input. Do not do this
    # for HTTP oneshot — leftover files from a red PRODUCT round would
    # pin the failing handler and block rework.
    if result.via != "cli":
        return
    for cid in list(result.handlers):
        name = str(cid).replace("-", "_")
        path = Path(root) / "app" / "actions" / f"{name}.py"
        if not path.is_file():
            continue
        try:
            disk = path.read_text(encoding="utf-8")
        except OSError:
            continue
        body = result.handlers.get(cid) or ""
        disk_prepared = handler_satisfies_event_bus_contract(
            disk
        ) and (
            handler_has_prepared_event_bus_step(disk)
            or _has_brief_workflow_steps(disk)
        )
        body_prepared = handler_has_prepared_event_bus_step(body)
        if disk_prepared and not body_prepared:
            result.handlers.pop(cid, None)
            if cid not in result.kept_handler_ids:
                result.kept_handler_ids.append(cid)


def specs_from_models_source(text: str) -> Dict[str, Any]:
    """Read MODELS / FIELDS / CONSTRAINTS the CLI (or factory) wrote.

    Used so FACTORY_CODE_CLI domain specs are not discarded in favour of
    the fallback envelope — that mismatch is how every capability then
    refused a payload built from the overwritten model.
    """
    if not (text or "").strip():
        return {}
    ns: Dict[str, Any] = {}
    try:
        exec(compile(text, "models.py", "exec"), ns)  # noqa: S102 — workspace artifact
    except Exception:  # noqa: BLE001 — harvest must not fail the role
        return {}
    models = ns.get("MODELS")
    if not isinstance(models, dict):
        return {}
    out: Dict[str, Any] = {}
    for cap_id, cls in models.items():
        names = list(getattr(cls, "FIELDS", None) or [])
        constraints = dict(getattr(cls, "CONSTRAINTS", None) or {})
        fields = []
        for name in names:
            field: Dict[str, Any] = {
                "name": str(name),
                "type": "str",
                "required": True,
            }
            extra = constraints.get(name)
            if isinstance(extra, dict):
                field.update(extra)
            fields.append(field)
        entity = getattr(cls, "ENTITY", None) or str(cap_id)
        out[str(cap_id)] = {
            "entity": str(entity).replace("-", "_"),
            "fields": fields,
            "model": None,
        }
    return out


def harvest_cli_artifacts(
    root: Path,
    capability_ids: Sequence[str],
) -> tuple:
    """Collect CLI-written specs + keepable handler ids from the workspace."""
    root = Path(root)
    specs: Dict[str, Any] = {}
    models_py = root / "app" / "models.py"
    if models_py.is_file():
        try:
            specs = specs_from_models_source(
                models_py.read_text(encoding="utf-8")
            )
        except OSError:
            specs = {}
    kept: List[str] = []
    for cid in capability_ids:
        name = str(cid).replace("-", "_")
        path = root / "app" / "actions" / f"{name}.py"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _is_keepable_handler(text):
            kept.append(str(cid))
    return specs, kept


def harvest_unkeepable_event_bus_ids(
    root: Path,
    capability_ids: Sequence[str],
) -> List[str]:
    """Gap/REUSE ids whose on-disk ``handle()`` failed only event_bus keepability."""
    root = Path(root)
    found: List[str] = []
    for cid in capability_ids:
        name = str(cid).replace("-", "_")
        path = root / "app" / "actions" / f"{name}.py"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _is_event_bus_unkeepable_handler(text):
            found.append(str(cid))
    return found


def dispatch_compiled_brief(ctx: Any, compiled: Any) -> DispatchResult:
    """Hand the compiled brief to the agentic coder. One session.

    DeepSeek-ready Floor Approve always starts FACTORY_CODE_CLI here,
    including inventories that are 100% REUSE/COMPOSE (no GENERATE gaps).
    """
    root = _workspace_root(ctx)
    left = ctx.coder_time_left() if hasattr(ctx, "coder_time_left") else None
    timeout_s = cli_dispatch_timeout_s(leftover_s=left)
    if deepseek_cli_ready():
        logger.info(
            "FACTORY_CODE_CLI C-BRIEF dispatch starting command=%s "
            "gaps=%s work=%s reuse=%s timeout_s=%.0f",
            resolve_code_cli() or "",
            inventory_gap_ids(compiled),
            cbrief_work_ids(compiled, root),
            reuse_inventory_ids(compiled),
            timeout_s,
        )

    if cli_available() and brief_requires_cli() and not cli_credentials_ok():
        detail = cli_credentials_missing_detail()
        logger.error("%s", OWNER_GATED_CLI_LOG)
        _append_log(root / LOG_REL, f"[{NAMED_BLOCKER_CLI_CREDS}] {detail}")
        ctx.note(
            f"{NAMED_BLOCKER_CLI_CREDS} — coding session never opened",
            stage="dispatch",
            source="brief dispatch",
            done=0,
            total=1,
        )
        result = DispatchResult(
            via="unavailable",
            ok=False,
            detail=detail,
            blocker=NAMED_BLOCKER_CLI_CREDS,
        )
    elif cli_available() and brief_requires_cli() and not cli_default_model_ok():
        detail = cli_no_model_detail()
        logger.error("%s", OWNER_GATED_CLI_LOG)
        _append_log(root / LOG_REL, f"[{NAMED_BLOCKER_CLI_NO_MODEL}] {detail}")
        ctx.note(
            f"{NAMED_BLOCKER_CLI_NO_MODEL} — coding session never opened",
            stage="dispatch",
            source="brief dispatch",
            done=0,
            total=1,
        )
        result = DispatchResult(
            via="unavailable",
            ok=False,
            detail=detail,
            blocker=NAMED_BLOCKER_CLI_NO_MODEL,
        )
    elif cli_available():
        result = _run_cli_session(ctx, compiled, timeout_s=timeout_s)
        hung = result.blocker == NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL
        if result.ok or hung:
            # #318 keep-path: prefer on-disk workflow/event_bus steps over a
            # thin JSON body so the fallback envelope cannot overwrite them.
            # Hung/timeout after a long C-BRIEF may still have keepable
            # files on disk — harvest them (sess_d10dfc28 class).
            _merge_workspace_harvest(result, root, list(compiled.capabilities))
            result.cli_authored_ids = [
                cid
                for cid in result.kept_handler_ids
                if not _workspace_handler_is_factory_grounded(root, cid)
            ]
            result.cli_unkeepable_event_bus_ids = harvest_unkeepable_event_bus_ids(
                root, list(compiled.capabilities)
            )
            # Partial CLI (live ~2/5) must not leave REUSE routes importing
            # missing app.actions modules. Fill holes only; do not credit
            # factory-grounded fill as CLI authorship (receipt stays CLI-ok
            # for via=cli, but cli_authored_ids stay the pre-fill harvest).
            filled = emit_factory_grounded_reuse_keep_path(
                root, compiled, only_missing=True
            )
            if filled:
                # Always re-harvest after a plant. Gating this on a
                # non-empty cli_authored_ids left kept_handler_ids stale
                # so later phases still saw the same 6 gaps.
                _merge_workspace_harvest(
                    result, root, list(compiled.capabilities)
                )
                result.cli_authored_ids = [
                    cid
                    for cid in result.kept_handler_ids
                    if not _workspace_handler_is_factory_grounded(root, cid)
                ]
            result.factory_planted_ids = harvest_factory_planted_ids(
                getattr(ctx, "workspace", root),
                compiled=compiled,
                dispatch=result.to_dict(),
            )
            if (
                deepseek_cli_ready()
                and result.ok
                and not result.cli_authored_ids
                and not result.handlers
            ):
                gap_unkeepable = [
                    cid
                    for cid in result.cli_unkeepable_event_bus_ids
                    if cid in set(inventory_gap_ids(compiled))
                ]
                if gap_unkeepable:
                    result.blocker = NAMED_BLOCKER_CLI_UNKEEPABLE_EVENT_BUS
                    result.detail = (
                        f"{NAMED_BLOCKER_CLI_UNKEEPABLE_EVENT_BUS}: "
                        "FACTORY_CODE_CLI wrote handle() for "
                        f"{gap_unkeepable} but harvest failed only "
                        "event_bus keepability. Not a never-wrote miss — "
                        "do not SUCCESS thin templates."
                    )
                else:
                    result.blocker = NAMED_BLOCKER_CLI_NO_AUTHORSHIP
                    log_text = ""
                    try:
                        log_path = resolve_coder_session_log(root) or (
                            Path(root) / LOG_REL
                        )
                        log_text = log_path.read_text(
                            encoding="utf-8", errors="replace"
                        )
                    except OSError:
                        log_text = ""
                    result.detail = _no_authorship_detail(log_text)
                ctx.note(
                    result.detail,
                    stage="dispatch",
                    source="coder CLI",
                    done=0,
                    total=1,
                )
        elif should_keep_factory_grounded_reuse(compiled, result):
            # sess_d5789a91: CLI 429 / insufficient balance with empty
            # inventory_gaps. Harvest used to run only on result.ok, so
            # factory-grounded persist / event_bus emit was discarded and
            # budget_inspect hard-stopped as a thin SCAFFOLD.
            emitted = emit_factory_grounded_reuse_keep_path(root, compiled)
            result.reuse_keep_path = bool(emitted)
            _merge_workspace_harvest(result, root, list(compiled.capabilities))
            result.factory_planted_ids = harvest_factory_planted_ids(
                getattr(ctx, "workspace", root),
                compiled=compiled,
                dispatch=result.to_dict(),
            )
            _append_log(
                root / LOG_REL,
                "[harvest] factory-grounded REUSE keep-path after "
                f"{result.blocker}: emitted={emitted} "
                f"kept={list(result.kept_handler_ids)}",
            )
            ctx.note(
                (
                    f"{result.blocker} — factory-grounded REUSE keep-path "
                    f"({len(result.kept_handler_ids)} handler(s)); "
                    "not a ≥2h CLI session"
                ),
                stage="dispatch",
                source="factory-grounded reuse keep-path",
                done=1 if result.kept_handler_ids else 0,
                total=1,
            )
    elif http_oneshot_enabled():
        _append_log(
            root / LOG_REL,
            f"[{NAMED_BLOCKER_CLI}] {BRIEF_HTTP_ONESHOT_ENV}=1 — "
            "HTTP oneshot (CI/dev escape, not a FACTORY_CODE_CLI session)",
        )
        result = _http_oneshot(ctx, compiled)
        if result.via == "skipped":
            result.blocker = NAMED_BLOCKER_CLI
        elif result.ok:
            # Harvest only a successful oneshot. A skipped / failed shot
            # must not treat the previous round's files as "kept" or a
            # rework pass cannot regenerate the failing capability.
            _merge_workspace_harvest(result, root, list(compiled.capabilities))
    else:
        from app.factory.coder import coder_enabled

        if not coder_enabled():
            _append_log(
                root / LOG_REL,
                f"[{NAMED_BLOCKER_CLI}] coder disabled — templates will author the workspace",
            )
            result = DispatchResult(
                via="skipped",
                ok=False,
                detail="coder disabled — templates will author the workspace",
                blocker=NAMED_BLOCKER_CLI,
            )
        else:
            detail = cli_unavailable_detail()
            logger.error("%s", OWNER_GATED_CLI_LOG)
            _append_log(root / LOG_REL, f"[{NAMED_BLOCKER_CLI}] {detail}")
            ctx.note(
                f"{NAMED_BLOCKER_CLI} — coding session never opened",
                stage="dispatch",
                source="brief dispatch",
                done=0,
                total=1,
            )
            result = DispatchResult(
                via="unavailable",
                ok=False,
                detail=detail,
                blocker=NAMED_BLOCKER_CLI,
            )

    if (
        not result.ok
        and not result.reuse_keep_path
        and should_keep_factory_grounded_reuse(compiled, result)
    ):
        emitted = emit_factory_grounded_reuse_keep_path(root, compiled)
        result.reuse_keep_path = bool(emitted)
        _merge_workspace_harvest(result, root, list(compiled.capabilities))
        _append_log(
            root / LOG_REL,
            "[harvest] factory-grounded REUSE keep-path after "
            f"{result.blocker}: emitted={emitted} "
            f"kept={list(result.kept_handler_ids)}",
        )

    apply_factory_llm_generate_gaps(ctx, compiled, result)
    apply_factory_grounded_generate_persist(ctx, compiled, result)
    if not result.factory_planted_ids:
        result.factory_planted_ids = harvest_factory_planted_ids(
            getattr(ctx, "workspace", root),
            compiled=compiled,
            dispatch=result.to_dict(),
        )
    write_dispatch_receipt(ctx, compiled, result)
    ctx.state["brief_dispatch"] = result.to_dict()
    ctx.state["compiled_brief"] = {
        "product_id": compiled.product_id,
        "vertical": compiled.vertical,
        "missing_reuse": list(compiled.missing_reuse),
        "capabilities": list(compiled.capabilities),
        # The checks this brief turns on: the runner reads them to decide
        # which gate failures may reach the writer (brief_gates).
        "acceptance_checks": list(compiled.acceptance_checks),
    }
    if result.blocker:
        ctx.state.setdefault("coder_failures", {})["brief_dispatch"] = (
            f"{result.blocker}: {result.detail}"
        )
        if result.blocker == NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL:
            record_failure_kind(ctx.state, "brief_dispatch", TIMEOUT)
    return result
