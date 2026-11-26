# CLAUDE.md

The binding instructions for this repo are in [AGENTS.md](AGENTS.md); read it first.

## Owner rule: small core first (26 Nov 2026)

Every new build and every project starts with a small core only:

- a RAG corpus of at most 30 pages, drawn from different document types;
- a few formulas;
- a couple of skills;
- one kernel;
- a couple of encoded-knowledge items.

Nothing else is loaded until that core is tested and working, with no
hardwiring, and passes every live UI test (a real browser at phone width on
the deployed build). Only then is the full content added. Never load
everything at once and then start trial and error.

The core is chosen by rule from what the Store declares and what the brief
needs. No product, kit, document or formula is named in Factory code to pick
it.
