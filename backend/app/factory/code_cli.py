"""FACTORY_CODE_CLI selection + DeepSeek / Kimi credential helpers.

Floor C-BRIEF dispatches one compiled brief through this CLI. Chat / architect
stay on their own LLM config (OpenRouter free in production) and must not
consume ``DEEPSEEK_API_KEY``.

DeepSeek V4 Pro is reached through **Kimi Code CLI** as an OpenAI-compat
provider (``https://api.deepseek.com`` + ``DEEPSEEK_API_KEY`` +
``deepseek-v4-pro``). That is not Claude Code. Claude Code → DeepSeek
Anthropic-compat is a leftover dead path (sess_be217f6d /
sess_401e6619 / #371 owner reject).
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional

#: Provider-agnostic name for the agentic coding CLI. ``KIMI_CODE_CLI`` stays
#: honoured so existing deployments keep working unchanged.
CODE_CLI_ENV = "FACTORY_CODE_CLI"
LEGACY_CODE_CLI_ENV = "KIMI_CODE_CLI"
CODE_PROVIDER_ENV = "FACTORY_CODE_PROVIDER"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEEPSEEK_CODE_MODEL_ENV = "DEEPSEEK_CODE_MODEL"
DEEPSEEK_BASE_URL_ENV = "DEEPSEEK_BASE_URL"

DEFAULT_KIMI_CLI = "kimi"
#: The config.toml provider table for DeepSeek under Kimi Code.
PROVIDER_DEEPSEEK = "deepseek"
#: DeepSeek's vehicle is Kimi Code CLI — never Claude Code.
DEFAULT_DEEPSEEK_CLI = "kimi"

#: DeepSeek model id the headless CodeWhale CLI actually serves. ``v4-pro``
#: was the previous default and it hung every coder call (live 2026-10-02:
#: the model call never returned); the CLI serves ``deepseek-v3`` / ``v2``.
#: An operator can still switch this per-build from the admin page without a
#: redeploy (runtime_settings coder_model), and env DEEPSEEK_CODE_MODEL still
#: wins over this default.
DEFAULT_DEEPSEEK_MODEL = "deepseek-v3"
DEFAULT_DEEPSEEK_FLASH_MODEL = "deepseek-v3"
DEEPSEEK_API_PRO_MODEL = "deepseek-v3"
DEEPSEEK_API_FLASH_MODEL = "deepseek-v3"
DEEPSEEK_OPENAI_BASE_URL = "https://api.deepseek.com"
#: Leftover #371 Claude-catalog ids. OpenAI-compat DeepSeek does not want these.
LEGACY_CLAUDE_OPUS_MODEL = "claude-opus-4-6"
LEGACY_CLAUDE_HAIKU_MODEL = "claude-haiku-4-5"
REJECTED_DEEPSEEK_CLAUDE_MODEL = "deepseek-v4-pro[1m]"
#: Leftover catalog ids -> the DeepSeek model of the same tier.
_LEGACY_MODEL_REMAP = {
    LEGACY_CLAUDE_OPUS_MODEL: DEFAULT_DEEPSEEK_MODEL,
    LEGACY_CLAUDE_HAIKU_MODEL: DEFAULT_DEEPSEEK_FLASH_MODEL,
}
REJECTED_DEEPSEEK_BARE_MODEL = "deepseek-v4-pro"
#: Leftover Claude Code Anthropic-compat URL. Not used for live DeepSeek.
DEEPSEEK_ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
DEEPSEEK_AUTO_COMPACT_WINDOW = "786432"
DEFAULT_DEEPSEEK_CONTEXT = "1048576"
_DEEPSEEK_CONTEXT_SUFFIX_RE = re.compile(r"\[\d+[km]?\]$", re.IGNORECASE)


def _cli_basename(command: str) -> str:
    name = (command or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    return name.lower()


def is_claude_code_cli(command: Optional[str] = None) -> bool:
    return "claude" in _cli_basename(command or "")


def is_kimi_code_cli(command: Optional[str] = None) -> bool:
    return "kimi" in _cli_basename(command or "")


def deepseek_api_key() -> str:
    return os.getenv(DEEPSEEK_API_KEY_ENV, "").strip()


def factory_code_provider() -> str:
    """Who authenticates FACTORY_CODE_CLI. Not the Floor chat LLM.

    Explicit ``FACTORY_CODE_PROVIDER`` wins. Otherwise:
    * ``DEEPSEEK_API_KEY`` (or provider=deepseek) selects DeepSeek under
      Kimi Code CLI — even when ``FACTORY_CODE_CLI=kimi``.
    * ``FACTORY_CODE_PROVIDER=kimi`` keeps the historical Moonshot path
      even when a DeepSeek key is also set.
    * a leftover ``claude`` binary without DeepSeek is Anthropic-native
      (unused for Factory DeepSeek coding).
    * otherwise Kimi (historical default).
    """
    explicit = os.getenv(CODE_PROVIDER_ENV, "").strip().lower()
    if explicit == "moonshot":
        return "kimi"
    if explicit in {"deepseek", "kimi", "claude"}:
        return explicit
    if deepseek_api_key():
        return "deepseek"
    cli = (
        os.getenv(CODE_CLI_ENV, "").strip()
        or os.getenv(LEGACY_CODE_CLI_ENV, "").strip()
    )
    if cli and is_claude_code_cli(cli):
        return "claude"
    return "kimi"


def deepseek_coder_selected(command: Optional[str] = None) -> bool:
    """True when C-BRIEF should authenticate Kimi Code via DeepSeek."""
    if factory_code_provider() == "deepseek":
        return True
    cli = (command or "").strip()
    if cli and is_kimi_code_cli(cli):
        return False
    if cli and is_claude_code_cli(cli) and (
        deepseek_api_key()
        or os.getenv(CODE_PROVIDER_ENV, "").strip().lower() == "deepseek"
    ):
        # Leftover Claude binary name — still DeepSeek-selected, but
        # ``code_cli_command`` remaps the vehicle to kimi.
        return True
    return False


def code_cli_command(default: Optional[str] = None) -> str:
    """The agentic coder CLI to invoke. FACTORY_CODE_CLI wins, then legacy.

    When neither CLI name is set, DeepSeek V4 Pro defaults to ``kimi``.
    Leftover ``FACTORY_CODE_CLI=claude`` while DeepSeek is selected is
    remapped to ``kimi`` (Claude Code is not the DeepSeek vehicle).
    """
    explicit = os.getenv(CODE_CLI_ENV, "").strip() or os.getenv(
        LEGACY_CODE_CLI_ENV, ""
    ).strip()
    if explicit:
        if factory_code_provider() == "deepseek" and is_claude_code_cli(explicit):
            return DEFAULT_DEEPSEEK_CLI
        return explicit
    if default is not None:
        return default
    if factory_code_provider() == "deepseek":
        return DEFAULT_DEEPSEEK_CLI
    return DEFAULT_KIMI_CLI


def normalize_deepseek_model(model: str) -> str:
    """Strip ``[1m]`` and map leftover Claude-catalog ids to DeepSeek catalog.

    Live Claude Code rejected both ``deepseek-v4-pro[1m]`` and (after #371)
    remapped to ``claude-opus-4-6``. OpenAI-compat DeepSeek wants the
    catalog id ``deepseek-v4-pro``. Leftover Render ``ANTHROPIC_MODEL`` /
    ``DEEPSEEK_CODE_MODEL=claude-opus-*`` values map back.
    """
    raw = (model or "").strip()
    stripped = _DEEPSEEK_CONTEXT_SUFFIX_RE.sub("", raw).strip() or raw
    # The leftover ids are the configuration constants above, matched
    # exactly -- never a model-name prefix.
    return _LEGACY_MODEL_REMAP.get(stripped, stripped)


def normalize_deepseek_claude_model(model: str) -> str:
    """Legacy alias — DeepSeek no longer remaps *to* Claude catalog ids."""
    return normalize_deepseek_model(model)


def deepseek_code_model() -> str:
    """Primary coding model for the Kimi → DeepSeek OpenAI-compat subprocess.

    An operator override set from the admin page wins over everything: when the
    configured model stops responding, the operator must be able to switch it
    without cloud access (live 2026-10-02).
    """
    try:
        from app.core.runtime_settings import CODER_MODEL, get as _rt_get

        override = _rt_get(CODER_MODEL)
        if override:
            return normalize_deepseek_model(override)
    except Exception:  # noqa: BLE001 — settings never break model resolution
        pass
    raw = os.getenv(DEEPSEEK_CODE_MODEL_ENV, "").strip()
    if raw:
        return normalize_deepseek_model(raw)
    leftover = os.getenv("ANTHROPIC_MODEL", "").strip()
    if leftover:
        return normalize_deepseek_model(leftover)
    leftover_opus = os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "").strip()
    if leftover_opus:
        return normalize_deepseek_model(leftover_opus)
    return DEFAULT_DEEPSEEK_MODEL


def deepseek_flash_model() -> str:
    leftover = os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "").strip() or os.getenv(
        "CLAUDE_CODE_SUBAGENT_MODEL", ""
    ).strip()
    if leftover:
        return normalize_deepseek_model(leftover)
    return DEFAULT_DEEPSEEK_FLASH_MODEL


def deepseek_openai_base_url() -> str:
    return (
        os.getenv(DEEPSEEK_BASE_URL_ENV, "").strip() or DEEPSEEK_OPENAI_BASE_URL
    )


def deepseek_model_configured() -> bool:
    """True when a DeepSeek coding model id is resolvable (compiled default)."""
    return bool(deepseek_code_model())


def deepseek_cli_environ(key: Optional[str] = None) -> Dict[str, str]:
    """Kimi Code OpenAI-compat env for the DeepSeek CLI subprocess only.

    Official Kimi Code ``KIMI_MODEL_*`` is the session-only channel that
    **does** read credentials from the subprocess environment
    (https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/environment-variables.html).
    Also sets ``DEEPSEEK_*`` so operators can see the auth source.

    Does **not** set ``ANTHROPIC_*`` / ``ANTHROPIC_API_KEY`` (those would
    arm Floor/architect Claude or revive the dead Claude Code path).
    """
    token = (key if key is not None else deepseek_api_key()).strip()
    primary = normalize_deepseek_model(deepseek_code_model())
    base = deepseek_openai_base_url()
    context = (
        os.getenv("DEEPSEEK_MAX_CONTEXT_SIZE", "").strip() or DEFAULT_DEEPSEEK_CONTEXT
    )
    effort = os.getenv("DEEPSEEK_THINKING_EFFORT", "").strip() or "high"
    return {
        DEEPSEEK_API_KEY_ENV: token,
        DEEPSEEK_BASE_URL_ENV: base,
        DEEPSEEK_CODE_MODEL_ENV: primary,
        "KIMI_MODEL_NAME": primary,
        "KIMI_MODEL_API_KEY": token,
        "KIMI_MODEL_PROVIDER_TYPE": "openai",
        "KIMI_MODEL_BASE_URL": base,
        "KIMI_MODEL_MAX_CONTEXT_SIZE": context,
        "KIMI_MODEL_THINKING_EFFORT": effort,
        "KIMI_DISABLE_TELEMETRY": "1",
        "KIMI_CODE_NO_AUTO_UPDATE": "1",
    }


def legacy_deepseek_claude_environ(key: Optional[str] = None) -> Dict[str, str]:
    """Dead Claude Code Anthropic-compat env. Not used for live DeepSeek.

    Kept so leftover Render ``ANTHROPIC_*`` names have a documented
    quarantine. Dispatch must not call this.
    """
    token = (key if key is not None else deepseek_api_key()).strip()
    primary = normalize_deepseek_model(deepseek_code_model())
    flash = normalize_deepseek_model(deepseek_flash_model())
    return {
        "ANTHROPIC_BASE_URL": DEEPSEEK_ANTHROPIC_BASE_URL,
        "ANTHROPIC_AUTH_TOKEN": token,
        "ANTHROPIC_MODEL": primary,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": primary,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": primary,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": flash,
        "CLAUDE_CODE_SUBAGENT_MODEL": flash,
        "CLAUDE_CODE_EFFORT_LEVEL": "max",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": DEEPSEEK_AUTO_COMPACT_WINDOW,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
    }


#: Keep ``--prompt <brief>`` under typical Linux ARG_MAX. Stdin still
#: carries the full brief.
KIMI_PROMPT_ARGV_MAX = 80_000
KIMI_PROMPT_STDIN_INSTRUCTION = (
    "Implement the gated Factory coder brief provided on stdin. "
    "The same brief is written at docs/coder_brief.md. "
    "Do not ask for more input."
)
# Legacy Claude Code argv helpers — unused for DeepSeek. Kept so leftover
# Anthropic-native ``FACTORY_CODE_CLI=claude`` (no DeepSeek) still has a
# headless shape.
CLAUDE_PRINT_ARGV_MAX = KIMI_PROMPT_ARGV_MAX
CLAUDE_PRINT_STDIN_INSTRUCTION = KIMI_PROMPT_STDIN_INSTRUCTION


class KimiPromptEmpty(ValueError):
    """Kimi Code ``--prompt`` requires a non-empty prompt or stdin."""


class ClaudePrintPromptEmpty(KimiPromptEmpty):
    """Legacy alias for the leftover Claude Code ``--print`` contract."""


def _is_bare_at_mention(text: str) -> bool:
    """True for a lone ``@path`` token — a file mention, not prompt input."""
    if not text.startswith("@") or "\n" in text or " " in text:
        return False
    return len(text) < 256 and "/" in text


def kimi_prompt_text(brief_text: str) -> str:
    """Non-empty coder-brief body for Kimi ``--prompt`` / stdin."""
    text = (brief_text or "").strip()
    if not text:
        raise KimiPromptEmpty(
            "Kimi Code --prompt requires a non-empty prompt; "
            "docs/coder_brief.md was empty"
        )
    if _is_bare_at_mention(text):
        raise KimiPromptEmpty(
            "Kimi Code --prompt requires the coder_brief.md content, "
            f"not a bare file mention ({text})"
        )
    return text


def claude_print_prompt(brief_text: str) -> str:
    """Legacy leftover — same empty/at-mention gate as ``kimi_prompt_text``."""
    return kimi_prompt_text(brief_text)


def kimi_prompt_argv(
    cli: str, brief_arg: str, model: Optional[str] = None
) -> list[str]:
    """Headless Kimi Code: ``--prompt <brief body>``, not a bare ``@file``.

    Official CLI: ``kimi -p "query"`` / ``kimi --prompt "query"``.
    ``--prompt`` cannot be combined with ``--yolo`` / ``--auto`` — non-
    interactive mode already uses auto permission.
    """
    prompt = kimi_prompt_text(brief_arg)
    print_arg = (
        KIMI_PROMPT_STDIN_INSTRUCTION
        if len(prompt) > KIMI_PROMPT_ARGV_MAX
        else prompt
    )
    argv = [cli, "--prompt", print_arg, "--add-dir", "."]
    if model:
        argv.extend(["--model", model])
    return argv


def kimi_prompt_log_argv(
    cli: str, brief_bytes: int, model: Optional[str] = None
) -> list[str]:
    """Session-log argv — do not dump the full brief onto the Floor log."""
    argv = [
        cli,
        "--prompt",
        f"<docs/coder_brief.md {brief_bytes} bytes>",
        "--add-dir",
        ".",
    ]
    if model:
        argv.extend(["--model", model])
    return argv


def claude_print_argv(
    cli: str, brief_arg: str, model: Optional[str] = None
) -> list[str]:
    """Leftover Anthropic-native Claude Code ``--print``. Not DeepSeek."""
    prompt = kimi_prompt_text(brief_arg)
    print_arg = (
        CLAUDE_PRINT_STDIN_INSTRUCTION
        if len(prompt) > CLAUDE_PRINT_ARGV_MAX
        else prompt
    )
    argv = [
        cli,
        "--print",
        print_arg,
        "--dangerously-skip-permissions",
        "--add-dir",
        ".",
    ]
    if model:
        argv.extend(["--model", normalize_deepseek_model(model)])
    return argv


def claude_print_log_argv(
    cli: str, brief_bytes: int, model: Optional[str] = None
) -> list[str]:
    """Leftover Claude session-log argv. Not used for DeepSeek."""
    argv = [
        cli,
        "--print",
        f"<docs/coder_brief.md {brief_bytes} bytes>",
        "--dangerously-skip-permissions",
        "--add-dir",
        ".",
    ]
    if model:
        argv.extend(["--model", normalize_deepseek_model(model)])
    return argv
