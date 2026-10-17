# Dependency adjudications

Dated notes for advisories that remain after the Linux resolve. Each
remaining finding has a registry row, a dated note, and a test twin in
`backend/tests/test_dep_audit.py`. CI runs `scripts/dep_audit.py`, which
audits `backend/requirements.txt` (the factory pin set). A local-env
scan on GitHub Actions also reports the runner's `setuptools`; that is
not a factory pin and is not adjudicated with `--ignore-vuln`.

A scanner summary is not evidence. Read the package's declared
`Requires-Dist` (and, for a pin we keep, `pip-audit`'s `fix_versions`)
before writing a note.

## Rules

1. **Upgrade when the graph allows it.** Do not adjudicate a finding whose
   fix satisfies every installed package's declared constraint.
2. **No bare suppressions.** `pip-audit --ignore-vuln` and
   `npm audit --ignore` are forbidden in workflows. The only ignore path
   is this registry, and only while its evidence still holds.
3. **A fix appearing later is a red check.** If `fix_versions` becomes
   non-empty and the installed pin is still below it, the note is stale.
   Upgrade or rewrite the note. Do not widen the ignore.

## What this pass did (2026-09-11, Linux)

- **python-multipart 0.0.20 → 0.0.32.** Six PYSEC rows
  (1852 / 3036 / 3037 / 3038 / 3039 / 3040). FastAPI 0.141.1 and
  Starlette 1.6.0 both declare `python-multipart>=0.0.18` with **no
  ceiling**. Highest published fix is `0.0.31`; latest is `0.0.32`.
  Upgraded, not adjudicated.
- **chromadb 1.5.9 — four unique PYSEC ids, no fix.** Latest published
  equals the pin. Factory uses `PersistentClient` only. See
  [2026-09-11-chromadb.md](2026-09-11-chromadb.md).
- **npm.** Production (`--omit=dev`) was already clean. Three dev
  transitives (`baseline-browser-mapping`, `brace-expansion`,
  `browserslist`) had fixes; the lockfile was bumped. Not adjudicated.

## Click (the previous miss)

A prior workplan claimed `click` needed a ceiling. That is a scanner
story, not a declared constraint of this factory graph.

- `gTTS 2.5.4` declares `click<8.2,>=7.1`. `gTTS` is a Store-block
  distribution (`DISTRIBUTIONS["gtts"]`), **not** a factory runtime pin.
- `huggingface_hub` (installed) declares `click<9.0.0,>=8.4.2`.
- `uvicorn` declares `click>=7.0`.
- Factory pins `click==8.5.0` (already past `8.3.3`).

Do not invent a `click<8.2` ceiling because a Store-only package
declares one. The test twin locks this.

## Adding a note

1. Run `python3 scripts/dep_audit.py` on Linux.
2. For each remaining id: read `importlib.metadata.requires` / PyPI
   `requires_dist` for every package that constrains it.
3. Add a registry row with `date`, `note`, `decision`, `evidence`.
4. Write the dated note. Name the declared constraint, not the scanner
   blurb.
5. Extend `backend/tests/test_dep_audit.py` so deleting the note or
   the evidence check fails.
