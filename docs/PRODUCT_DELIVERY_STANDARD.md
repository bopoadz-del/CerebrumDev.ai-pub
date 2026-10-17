# Cerebrum Product Delivery Standard — operating model

**The factory's coder is the headless CodeWhale worker (DeepSeek). No one else writes product code.**

The deterministic template path is a fallback scaffold, not governed product
code: a build whose handlers are not coding-agent-stamped is refused at the
``writer_no_output`` gate.

This is the permanent Cerebrum Product Delivery Standard: one universal
execution system, with the domain intelligence inserted at the top.

## The rules

1. **The standard prompt is immutable.** It lives at
   `backend/app/factory/standards/product_delivery_standard.md` and its
   sha256 is pinned by tests. Products never edit it. If it ever must change,
   it changes for every product at once, in one deliberate factory PR.
2. **Per product, attach a short Domain Pack** — platform name, domain,
   modules, authoritative calculations, high-impact actions, rules. See
   `backend/app/factory/standards/domain_packs/buildops_construction.md`
   for the canonical shape. Healthcare, insurance, retail: swap the pack,
   nothing else.
3. **The factory assembles the brief.** `factory.delivery_standard.render()`
   (or `POST /v1/factory/delivery-standard/render`) fills every slot and
   fails closed — the coder never receives a partial brief.
4. **The workbench hands the brief to the CodeWhale worker.** When a change request
   carries `platform` + `domain_pack`, the brief is the full rendered
   standard (``candidate/kimi_prompt.md`` — legacy artifact name; the
   executor is the CodeWhale worker, not Kimi Code). Otherwise the legacy
   CR-scoped brief is used, honestly labeled. A one-sided or incomplete
   pack raises —
   it never silently downgrades.

## Why

Proven on Projecttwo (2026-07-22): one prompt of this shape drove a
complete pilot platform — spec, backend, frontend, tests, deployment config —
in a single uninterrupted execution cycle. The standard is that prompt,
generalized: the domain pack is the only thing that changes per product.

## API

```
GET  /v1/factory/delivery-standard          → standard hash + required keys
POST /v1/factory/delivery-standard/render   → { platform, domain_pack } → brief
```

Both are entitlement-gated like every other factory run.
