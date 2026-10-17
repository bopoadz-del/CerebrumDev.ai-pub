# Guardrails — CerebrumDev.ai

What this system is certified to do, what it is not certified to do, and
which integrations are mock or stub. Modeled on the pilot-guardrails
standard used in The Fork.

## Certified for

- **Self-serve free trial with server-enforced caps.** Signup → first
  generated prototype in 4 API calls with no human in the loop (verified
  live 2026-07-29). Generations, daily chat messages and exports are
  metered per account; over-limit returns 429 with the upgrade path.
- **Cross-account isolation.** Sessions and products return non-leaking
  404s across accounts; credentials are stored hashed (PBKDF2-200k).
- **Grounded chat.** Every LLM reply passes the grounding stage before
  streaming; blocked drafts never reach the stream; every verdict is
  persisted to an append-only audit log.
- **Honest failure.** CLI-backed workbench paths fail loudly
  (`kimi_error` + 502), never silently substituting a stub; health and
  readiness report evaluated capability, not configuration.
- **Deterministic product generation** from an approved blueprint,
  including product DNA with checksums, schema-validated DNA documents,
  and the steward runtime with its own grounding + audit ledger.

## Not certified for

- **Unattended production traffic on generated products.** They are
  prototypes; see KNOWN_LIMITATIONS.md.
- **Domain-expert-free operation.** Blueprint quality without an LLM key
  is generic; with one, drafts still require the owner's review and
  approval (human authority is the designed contract).
- **Multi-instance deployment on default storage** (sqlite + disk
  sessions are single-instance).
- **Charging money until Stripe is configured** and
  `BILLING_ENFORCEMENT=on`.

## Mock / stub inventory (what a caller actually sees)

| Surface | State | Label the caller sees |
| --- | --- | --- |
| Chat LLM with `KIMI_MOCK` | mock | `/ready` reports `llm_mock: true`, never `llm_configured` |
| Architect without an LLM key | deterministic fallback | generic "Product Platform" blueprint (see KNOWN_LIMITATIONS) |
| Email without SMTP | dev-token mode | register/reset responses carry `mode: "dev_token"` + the token |
| Stripe unconfigured | inert | checkout returns `503 stripe_not_configured` |
| Kimi workbench without CLI | inoperative | `/health` `kimi_workbench.cli_ok: false`; runs fail 502 `cli_not_found` |
| Resident engineer / build mode / CR intake | parked by design | flags default off; status endpoints say so |
| Connectors in generated products | stubs | labeled `not_implemented` until credentials supplied |

Anything discovered claiming more than this document allows is a bug —
file it as such.
