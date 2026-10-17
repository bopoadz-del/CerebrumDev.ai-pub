# Design Spec: Soft Chain-Quality Checks for Domain v2 Blocks

## 1. Background

`Cerebrum-Blocks` now publishes Domain Source Pack shelves that describe, for each domain, how the factory should think and which blocks are recommended. `CerebrumDev.ai` reads those shelves via `app/core/source_pack_loader.py` and injects source-pack metadata into the chain-generation system prompt in `app/core/chain_generator.py`.

The prompt enrichment tells the LLM:

- The domain v2 block (e.g., `legal_v2`) is the primary domain-specific analysis block.
- For domain-specific analysis, prefer the domain v2 block over generic built-ins such as `llm_enhancer`, `knowledge`, `memory`, `vector_search`, or `validation_pipeline`.

Despite this guidance, live Render smoke tests against `kimi-k2.7-code:cloud` show that the model still sometimes proposes chains that pass `validate_chain()` but omit the domain v2 block. For example, a legal contract-review request produced:

```text
auth, chat, document_engine, knowledge, llm_enhancer, local_drive, memory, ocr_v2, validation_pipeline
```

The chain is valid, but it substitutes generic built-ins for `legal_v2`.

This is no longer a prompt problem. It is a chain-quality control problem. The next step is a soft quality check, not more prompt polishing.

## 2. Goal

Add a post-generation quality check that detects when a proposed chain is valid but likely incomplete because it omits the domain v2 block for the selected domain. The check must be soft: it does not block validation, approval, or deployment.

## 3. Non-goals

The following are explicitly out of scope for this design and for the first implementation PR:

- Do not change `validate_chain()` semantics.
- Do not hard-reject chains yet.
- Do not auto-mutate chains yet.
- Do not make source-pack blocks a hard whitelist.
- Do not touch `The_Fork`.
- Do not start `Fork2`.
- Do not change deployment configuration.
- Do not change model/provider config.

## 4. Proposed behavior

Trigger conditions for a warning:

1. The session has a domain.
2. A source pack exists for that domain.
3. The source pack's `blocks` list contains exactly one domain v2 block (a block id ending in `_v2`).
4. The LLM proposes a chain.
5. The chain passes existing validation (`validate_chain()`).
6. The proposed chain does not include the domain v2 block.

When all conditions are met:

- Mark chain quality as `needs_review`.
- Add a warning explaining the missing domain v2 block.
- Include the suggested block id.
- Keep `validation_passed = true`.
- Do not block preview or approval.

When any trigger condition is missing (no source pack, no v2 block, no chain, validation failed, or v2 block already present), quality status is `ok` and no warning is emitted.

## 5. Response shape

Extend the chain-generation result with a small `quality` metadata object.

### Needs review case

```json
{
  "message": "...",
  "chain": {
    "blocks": [...],
    "connections": [...]
  },
  "rules": [...],
  "validation_passed": true,
  "quality": {
    "status": "needs_review",
    "warnings": [
      {
        "code": "missing_domain_v2_block",
        "message": "This legal chain does not include legal_v2, the primary legal analysis block.",
        "suggested_block": "legal_v2"
      }
    ]
  }
}
```

### Clean case

```json
{
  "message": "...",
  "chain": {
    "blocks": [...],
    "connections": [...]
  },
  "rules": [...],
  "validation_passed": true,
  "quality": {
    "status": "ok",
    "warnings": []
  }
}
```

## 6. Detection logic

First implementation:

```python
def check_chain_quality(domain: str, chain: dict | None, validation_passed: bool) -> dict:
    """Return soft quality metadata for a proposed chain."""
    if not chain or not validation_passed:
        return {"status": "ok", "warnings": []}

    try:
        pack = get_source_pack(domain)
    except Exception:
        logger.exception("Failed to load source pack for quality check: domain=%s", domain)
        return {"status": "ok", "warnings": []}

    if not pack:
        return {"status": "ok", "warnings": []}

    v2_blocks = [b for b in pack.get("blocks", []) if b.endswith("_v2")]
    if len(v2_blocks) != 1:
        return {"status": "ok", "warnings": []}

    domain_v2 = v2_blocks[0]
    proposed_ids = {b.get("id") for b in chain.get("blocks", [])}

    if domain_v2 in proposed_ids:
        return {"status": "ok", "warnings": []}

    return {
        "status": "needs_review",
        "warnings": [
            {
                "code": "missing_domain_v2_block",
                "message": (
                    f"This {domain} chain does not include {domain_v2}, "
                    f"the primary {domain} analysis block."
                ),
                "suggested_block": domain_v2,
            }
        ],
    }
```

## 7. Edge cases

| Condition | Behavior |
|---|---|
| No source pack found | No warning; quality `ok`. |
| Source pack loader fails | Log exception; no warning; quality `ok`. |
| Source pack has no `_v2` block | No warning; quality `ok`. |
| Source pack has multiple `_v2` blocks | No warning; quality `ok`. (Ambiguous; skip.) |
| No chain proposed | No warning; quality `ok`. |
| Chain validation fails | Validation failure is primary; quality `ok` or omitted. |
| Chain already includes domain v2 | Quality `ok`. |
| Chain includes a different domain's v2 block | Still warn if current domain's v2 block is missing. |

## 8. Tests for future implementation

The implementation PR should include tests covering:

- Legal chain with `legal_v2` → quality `ok`, empty warnings.
- Legal chain without `legal_v2` → quality `needs_review`, warning with `suggested_block: legal_v2`.
- Missing source pack → no warning.
- Source pack loader exception → no warning, exception logged.
- Source pack with no `_v2` block → no warning.
- No chain proposed → no warning.
- Invalid chain → validation failure remains primary.

## 9. Rollout

### Phase 1: Backend quality metadata only

Add `check_chain_quality()` and include its result in the chain-generation response. No UI changes. This is the recommended next implementation PR.

### Phase 2: UI displays warning

Frontend consumes `quality.status` and `quality.warnings` and shows a non-blocking warning to the user.

### Phase 3: Optional one-click suggestion

Frontend offers a button to append the suggested domain v2 block to the chain. The user must explicitly approve the mutation.

### Phase 4: Hard policy (future)

After enough smoke data and user feedback, consider making the missing-domain-v2 warning a blocker or an auto-fix. This is intentionally deferred.

## 10. Final recommendation

Implement **Phase 1 only** as the next PR. Add the soft quality metadata to the backend response without changing validation, approval, or deployment behavior. This gives the factory a machine-readable signal that a chain may be incomplete while preserving user agency.
