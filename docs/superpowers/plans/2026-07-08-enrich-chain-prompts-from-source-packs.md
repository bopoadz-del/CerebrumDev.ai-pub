# Enrich Chain Prompts from Source Packs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Domain Source Pack metadata into `CerebrumDev.ai` chain generation so the LLM prompt includes domain-specific expert role, workflow, and recommended blocks, with safe fallback when no pack exists.

**Architecture:** Add a small helper to `backend/app/core/chain_generator.py` that calls the existing read-only `get_source_pack()` loader; insert its output into the system prompt between the optional-blocks list and the response rules. Keep the block registry and `validate_chain()` as the only hard gates.

**Tech Stack:** Python 3.11, FastAPI, pytest, existing `app.core.source_pack_loader`.

## Global Constraints

- Do not change deployment behavior.
- Do not modify `The_Fork`.
- Do not start `Fork2` work.
- Do not change chain validation behavior.
- Do not use source-pack `blocks` as a hard whitelist yet.
- Source-pack loader failures must fall back to empty context, never break chain generation.
- Use only `expert_prompt`, `workflow`, and `blocks` from the source pack.

---

## File Map

| File | Responsibility |
|---|---|
| `backend/app/core/chain_generator.py` | Builds chain-generation system prompt. Modified to load and inject source-pack context. |
| `backend/tests/test_chain_generator_source_packs.py` | New tests proving enrichment, missing-pack fallback, and loader-exception fallback. |

---

### Task 1: Import source-pack loader into chain generator

**Files:**
- Modify: `backend/app/core/chain_generator.py`

**Interfaces:**
- Consumes: `get_source_pack` from `app.core.source_pack_loader`
- Produces: imported symbol available to helper added in Task 2

- [ ] **Step 1: Add import**

Add below the existing imports in `backend/app/core/chain_generator.py`:

```python
from .source_pack_loader import get_source_pack
```

- [ ] **Step 2: Verify no syntax errors**

Run:

```bash
cd CerebrumDev.ai/backend
../.venv/Scripts/python -c "import app.core.chain_generator"
```

Expected: command exits 0.

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/chain_generator.py
git commit -m "chore(chain): import source pack loader"
```

---

### Task 2: Add source-pack context helper and wire it into the prompt

**Files:**
- Modify: `backend/app/core/chain_generator.py`

**Interfaces:**
- Consumes: `get_source_pack(domain: str) -> Optional[Dict[str, Any]]`
- Produces: `_build_source_pack_context(domain: str) -> str`; enriched `_build_system_prompt`

- [ ] **Step 1: Add helper function**

Insert the following helper near the top of `backend/app/core/chain_generator.py`, after the imports and before `_build_system_prompt`:

```python
def _build_source_pack_context(domain: str) -> str:
    """Return a source-pack guidance section for the system prompt.

    Returns an empty string when the domain has no source pack or when the
    shelf cannot be loaded, so chain generation never breaks because of
    metadata issues.
    """
    try:
        pack = get_source_pack(domain)
    except Exception:
        logger.exception("Failed to load source pack for domain=%s", domain)
        return ""

    if not pack:
        return ""

    blocks = ", ".join(pack.get("blocks", []))

    return (
        f"\nDomain guidance for {domain}:\n"
        f"Expert role: {pack.get('expert_prompt', '')}\n"
        f"Workflow: {pack.get('workflow', '')}\n"
        f"Recommended blocks: {blocks}\n"
        "Use recommended blocks only if they are present in the available block registry. "
        "Never invent block IDs.\n"
    )
```

- [ ] **Step 2: Insert context into system prompt**

Modify `_build_system_prompt` in `backend/app/core/chain_generator.py`:

1. Add a `source_pack_section` line after `docs_section`:

```python
source_pack_section = _build_source_pack_context(domain)
```

2. Insert `source_pack_section` into the returned prompt after the optional-blocks list and before the uploaded-documents summary. Change the prompt construction from:

```python
        "Optional Fork primitives the user can add on top:\n"
        f"{optional_list}\n"
        f"{docs_section}\n"
```

to:

```python
        "Optional Fork primitives the user can add on top:\n"
        f"{optional_list}\n"
        f"{source_pack_section}"
        f"{docs_section}\n"
```

The final `_build_system_prompt` return value should look like:

```python
    return (
        "You are an AI solution architect for CerebrumDev.ai. "
        "Your job is to help users configure and optionally extend their sovereign AI instance.\n\n"
        f"Domain: {domain}\n"
        "The platform already includes these built-in blocks automatically: "
        f"{', '.join(BUILTIN_BLOCKS)}.\n"
        "You do NOT need to propose these in the chain; they are always available.\n\n"
        "Optional Fork primitives the user can add on top:\n"
        f"{optional_list}\n"
        f"{source_pack_section}"
        f"{docs_section}\n"
        "When responding:\n"
        ...
    )
```

- [ ] **Step 3: Verify prompt builds without a real source pack**

Run:

```bash
cd CerebrumDev.ai/backend
../.venv/Scripts/python -c "
from app.core.chain_generator import _build_system_prompt
print(_build_system_prompt([], 'legal', ''))
"
```

Expected: prompt prints successfully, contains legacy sections, no domain guidance because `CEREBRUM_BLOCKS_ROOT` is unset and loader will fail silently.

- [ ] **Step 4: Commit**

```bash
git add backend/app/core/chain_generator.py
git commit -m "feat(chain): inject source pack context into chain generator prompt"
```

---

### Task 3: Add tests for source-pack prompt enrichment

**Files:**
- Create: `backend/tests/test_chain_generator_source_packs.py`

**Interfaces:**
- Consumes: `_build_system_prompt` from `app.core.chain_generator`
- Produces: passing tests for enrichment, missing pack, and loader exception

- [ ] **Step 1: Write the test file**

Create `backend/tests/test_chain_generator_source_packs.py`:

```python
"""Tests for source-pack enrichment of chain-generation prompts."""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

import pytest

from app.core.chain_generator import _build_system_prompt
from app.core.source_pack_loader import SourcePackLoaderError


LEGAL_SOURCE_PACK: Dict[str, Any] = {
    "id": "legal",
    "domain": "legal",
    "name": "Legal Source Pack",
    "description": "contract review",
    "expert_prompt": "You are a senior legal analyst reviewing contracts.",
    "workflow": "1) ingest documents 2) OCR if needed 3) legal analysis 4) chat",
    "use_cases": ["review contracts"],
    "example_prompts": ["flag risky clauses"],
    "expected_inputs": ["contracts"],
    "expected_outputs": ["risk flags"],
    "blocks": ["pdf", "ocr", "chat", "image", "legal_v2"],
}


def test_build_system_prompt_includes_source_pack_context():
    """When a source pack exists, its guidance appears in the system prompt."""
    with patch(
        "app.core.chain_generator.get_source_pack", return_value=LEGAL_SOURCE_PACK
    ):
        prompt = _build_system_prompt([], "legal", "")

    assert "Domain guidance for legal" in prompt
    assert LEGAL_SOURCE_PACK["expert_prompt"] in prompt
    assert LEGAL_SOURCE_PACK["workflow"] in prompt
    for block in LEGAL_SOURCE_PACK["blocks"]:
        assert block in prompt
    assert "Use recommended blocks only if they are present" in prompt
    assert "Never invent block IDs" in prompt


def test_build_system_prompt_omits_guidance_when_source_pack_missing():
    """When no source pack exists, the legacy prompt shape is preserved."""
    with patch("app.core.chain_generator.get_source_pack", return_value=None):
        prompt = _build_system_prompt([], "legal", "")

    assert "Domain guidance" not in prompt
    assert "Expert role:" not in prompt
    assert "Workflow:" not in prompt
    assert "You are an AI solution architect for CerebrumDev.ai" in prompt
    assert "Chain JSON format:" in prompt


def test_build_system_prompt_falls_back_on_source_pack_loader_error():
    """A source-pack loader failure must not break prompt generation."""
    with patch(
        "app.core.chain_generator.get_source_pack",
        side_effect=SourcePackLoaderError("shelf missing"),
    ):
        prompt = _build_system_prompt([], "legal", "")

    assert "Domain guidance" not in prompt
    assert "Expert role:" not in prompt
    assert "Workflow:" not in prompt
    assert "You are an AI solution architect for CerebrumDev.ai" in prompt
```

- [ ] **Step 2: Run the new tests**

```bash
cd CerebrumDev.ai/backend
../.venv/Scripts/python -m pytest tests/test_chain_generator_source_packs.py -v
```

Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_chain_generator_source_packs.py
git commit -m "test(chain): source pack prompt enrichment and fallback"
```

---

### Task 4: Run the full backend suite and finalize

**Files:**
- None (verification only)

- [ ] **Step 1: Run full backend test suite**

```bash
cd CerebrumDev.ai/backend
../.venv/Scripts/python -m pytest tests -q
```

Expected: all tests pass (baseline was 105 passed; no new failures).

- [ ] **Step 2: Run frontend gate (if changed)**

No frontend changes are expected. If CI runs it, let CI prove it. Locally optional:

```bash
cd CerebrumDev.ai/frontend
npm ci
npm run build
npm run lint
```

Expected: build and lint pass.

- [ ] **Step 3: Final commit and branch push**

```bash
git push -u origin feat/chain-source-pack-context
```

---

## Self-Review Checklist

- [ ] Spec coverage: helper enrichment, prompt insertion point, fallback on missing pack, fallback on loader error, safety line, tests for all three cases, full suite green.
- [ ] No placeholders: every step has exact file paths, code, and commands.
- [ ] Type consistency: `get_source_pack` signature matches `source_pack_loader.py`; `_build_source_pack_context` returns `str`.
- [ ] Scope guard: only `chain_generator.py` and one new test file change; no validation, deployment, or Fork changes.
