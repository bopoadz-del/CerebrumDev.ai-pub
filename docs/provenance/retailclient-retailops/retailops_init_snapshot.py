"""RetailOps generated-runtime package.

This package is the *generated product* runtime for the CEREBRUM RetailOps
pilot. It bundles a pinned snapshot of the Cerebrum-Blocks generic action
contract + Retail kit (under :mod:`app.retailops.kit`) and adds the platform
runtime: PostgreSQL/pgvector persistence, hybrid retrieval, ingestion,
citations, LLM providers, a LangGraph orchestrator, generic action APIs and
audit persistence.

The exact Cerebrum-Blocks commit the kit was vendored from is recorded in
``BLOCKS_COMMIT`` (see build metadata / docs).
"""

RETAILOPS_VERSION = "1.0.0"

# Exact Cerebrum-Blocks commit the vendored kit (app/retailops/kit) was built
# from. Update together with app/retailops/kit when re-vendoring.
BLOCKS_COMMIT = "4a5ad84246f885764ba05d1ab1398dc0d2b56be3"
