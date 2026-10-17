# Domain Intelligence Compiler (mission Phase 4)

Turns a donor repository into a **candidate** Domain Pack with exact
provenance, then validates it against the Reasoning Kernel's real schemas.

## Pipeline

```
scout  ->  compile  ->  validate  ->  package  ->  publish / install
```

- `scout` — AST-based discovery of candidate formulas, rules, workflows and
  approvals with file/line evidence and a test-evidence index. Never reads
  docstrings as code; unparseable files are skipped (their defs are not
  executable).
- `compile` — generates a candidate pack. **Every artifact is
  `candidate`**; expression/inputs/output are not auto-extracted (a machine
  must not silently certify what it guessed). Emits duplicate and
  contradiction flags, missing-test templates, workflow diagrams
  (stateDiagram-v2), role-matrix drafts and oracle templates.
- `validate` — imports the kernel's `DomainPack` schema from the
  Cerebrum-Blocks checkout (`CEREBRUM_BLOCKS_PATH` env override) and
  refuses non-candidate certifications and provenance gaps.
- `package` / `publish` / `install` — write the pack to a domain-pack root
  or a generated product. Packaging refuses invalid packs.

## Usage (from `backend/`)

```
.venv-factory\Scripts\python.exe -m app.domain_compiler.cli scout   --repo C:\path\to\donor --commit HASH --paths app\core --out scout.json
.venv-factory\Scripts\python.exe -m app.domain_compiler.cli compile --report scout.json --domain-id my_ops --name "My Ops" --out .\packed
.venv-factory\Scripts\python.exe -m app.domain_compiler.cli validate --pack .\packed\my_ops\pack.json
.venv-factory\Scripts\python.exe -m app.domain_compiler.cli publish  --pack .\packed\my_ops\pack.json --dest C:\path\to\Cerebrum-Blocks
.venv-factory\Scripts\python.exe -m app.domain_compiler.cli install  --pack .\packed\my_ops\pack.json --product C:\path\to\product
```

## Certification policy

The compiler emits `candidate` only. Promotion to
`technically_verified` / `domain_review_required` / `domain_approved`
happens exclusively through the Store's gate process.
