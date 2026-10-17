# Design: Enrich Chain Generation Prompts from Source Packs

## Goal
Wire `Cerebrum-Blocks` Domain Source Pack metadata into `CerebrumDev.ai` chain generation so the LLM receives domain-specific guidance (expert role, workflow, recommended blocks) while keeping the block registry and chain validator as the authoritative gate.

## Context
- `Cerebrum-Blocks` owns `block_store/shelves/source_packs.json`, describing how each domain should think and which blocks it needs.
- `CerebrumDev.ai` already reads that shelf via `app/core/source_pack_loader.py` and exposes it through `GET /v1/domains/source-packs`.
- `app/core/chain_generator.py` currently builds a system prompt from built-ins, optional blocks, and uploaded-document summaries; it has no source-pack awareness.

## Scope
- Modify only `CerebrumDev.ai/backend/app/core/chain_generator.py` and add focused tests.
- Do not change deployment, `The_Fork`, `Fork2`, chain validation, or block registry logic.
- Include a clean fallback so missing or broken source packs preserve today's behavior.

## Design

### Prompt enrichment helper

Add a helper to `chain_generator.py`:

```python
def _build_source_pack_context(domain: str) -> str:
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

The helper:
- Returns an empty string if the source pack is missing.
- Catches any loader/engine-discovery exception, logs it, and returns an empty string.
- Uses only `expert_prompt`, `workflow`, and `blocks`.
- Adds a safety line reminding the model that the block registry is the authority.

### Prompt integration

`_build_system_prompt(available_blocks, domain, docs_summary)` inserts the source-pack context after the optional-blocks list and before the response instructions.

Resulting prompt order:
1. Role and job description.
2. Domain, built-ins note, optional blocks list.
3. **Domain source-pack guidance (new, optional).**
4. Uploaded documents summary (if any).
5. Response rules and chain JSON format.

### Error handling and fallback

| Condition | Behavior |
|---|---|
| Source pack exists | Enriched prompt is used. |
| Source pack missing | Legacy prompt, no breakage. |
| Source pack loader raises | Log exception, legacy prompt, no breakage. |

### Testing

New test file: `CerebrumDev.ai/backend/tests/test_chain_generator_source_packs.py`

1. **Source pack present** — patch `get_source_pack` to return a fake legal pack; assert the system prompt contains `expert_prompt`, `workflow`, and each recommended block, plus the safety line.
2. **Source pack missing** — patch `get_source_pack` to return `None`; assert the prompt does not contain "Domain guidance" and matches the legacy shape.
3. **Source pack loader exception** — patch `get_source_pack` to raise `SourcePackLoaderError`; assert prompt generation succeeds and no domain guidance is inserted.
4. **Full suite** — run `python -m pytest tests -q` and confirm all existing tests still pass.

## Trade-offs considered

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| A — inline enrichment in `chain_generator.py` | One file changed, minimal diff, easy to test | Slightly couples generator to loader | **Chosen** |
| B — new `prompt_context.py` module | Cleaner boundary, easier future extension | Overkill for a single narrow enrichment | Rejected |
| C — caller passes context | Explicit data flow | Pushes prompt concern into orchestration | Rejected |

## Risks and mitigations

- **Risk:** Source pack guidance overrides registry authority.  
  **Mitigation:** Add explicit safety line; keep `validate_chain()` as the hard gate.
- **Risk:** Shelf loading failure breaks chain generation.  
  **Mitigation:** Catch and log exceptions, fall back to empty context.
- **Risk:** Prompt becomes too long.  
  **Mitigation:** Use only three source-pack fields; omit use cases, examples, inputs, outputs for now.

## Follow-up work (out of scope)

- Consume richer source-pack metadata (use cases, example prompts, expected inputs/outputs) when chain generation proves it needs more direction.
- Consider whether source-pack `blocks` should eventually feed a prompt-level whitelist, but only after validation behavior is intentionally changed.
