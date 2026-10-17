# Known Issues

This file tracks smoke-test friction items and other actionable issues that are not yet fixed. Each entry should contain enough detail to be picked up independently.

## 1. `cerebrum init` cannot select deployed mode from a pipe — RESOLVED

**Source:** Smoke test — CLI onboarding.

`cerebrum init` now accepts a `--mode {configurator,deployed}` flag and defaults to `configurator` with a visible stderr notice when stdin is not a TTY.

**Resolution:**
- `cerebrum init --mode deployed` selects deployed mode non-interactively.
- Non-TTY stdin no longer blocks; it defaults to `configurator` and prints instructions for using `--mode`.
- Tests cover flag selection, non-TTY defaulting, interactive prompt choice, and invalid prompt fallback.

**Commit reference:** `fix/cli-friction-pipe-utf8` (Cerebrum-Blocks PR #24, merge `f0ea8a74`).

## 2. CLI crashes on Windows cp1252 without `PYTHONIOENCODING=utf-8` — RESOLVED

**Source:** Smoke test — Windows CLI usage.

The CLI now forces UTF-8 output at import time by reconfiguring `sys.stdout` and `sys.stderr` to `encoding="utf-8"` with `errors="replace"`. This prevents `UnicodeEncodeError` on Windows cp1252 terminals when printing box-drawing characters, non-ASCII block names, or document content.

**Resolution:**
- Added `cli/cerebrum_cli/_encoding.py` with `ensure_utf8_output()`.
- Called from `main.py` at import time.
- Tests verify stream reconfiguration on Windows and that non-ASCII text prints without raising.

**Commit reference:** `fix/cli-friction-pipe-utf8` (Cerebrum-Blocks PR #24, merge `f0ea8a74`).

## 3. Edge/package internal auth mismatch — RESOLVED

**Source:** Smoke test — edge package runtime.

The edge packager generates a random `CEREBRUM_MASTER_KEY` for the deployed package, but the Cerebrum-Blocks engine expects `cb_dev_key` (or another configured dev key) for internal self-calls. This mismatch causes authenticated endpoints inside the edge package to fail with 401/403 errors.

**Resolution:** Both packagers now wire the generated key as the single source of truth:
- `CEREBRUM_MASTER_KEY` is set to the minted key so the engine's `APIKeyAuth` validates it.
- `CB_DEV_KEY` is set to the same key for legacy container paths.
- `CEREBRUM_API_KEY_CDEV` / `CEREBRUM_API_KEY_PLATFORM` are set to the same key.
- The dropped CLI `config.toml` uses the same key.
- A boot test verifies the generated package accepts its own key and rejects a wrong key.

**Commit reference:** `feat/platform-default-pinned-engine`.

## 4. No semantic embedding backend in bare requirements → RAG unavailable on default install — RESOLVED

**Source:** Smoke test — default install RAG path.

The bare requirements (`requirements.txt`) did not include a semantic embedding backend. As a result, RAG was unavailable on a default install even when vectors had been uploaded. The deployed router fell back to a degraded note, and retrieval could not run.

**Resolution:**
- Added `fastembed>=0.6.0` to `backend/requirements.txt` as the default ONNX sentence embedder.
- The default model is `BAAI/bge-small-en-v1.5` (~67 MB ONNX weights, 384 dimensions, CPU-only, no torch).
- `upload_processor.py` now tries the engine's `zvec` block first, then fastembed, then a non-semantic hash fallback of last resort marked as `degraded`.
- The deployed router probes/embeds queries using the same provider recorded in `vectors.json` (zvec or fastembed).
- Heavier backends (`sentence-transformers`, `marker-pdf`) are grouped in the new `backend/requirements-embeddings-full.txt` extras file.

**Commit reference:** `feat/default-onnx-embedding`.
