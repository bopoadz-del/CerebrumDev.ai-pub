"""WRITER dispatch control.

The live loop is full-pipeline autopilot - COLLECTOR -> CLONER -> WRITER ->
TESTER -> STORE_MANAGER - with no pause between CLONER and WRITER and no
external BA. The WRITER dispatches to the headless CodeWhale (DeepSeek)
worker when ``FACTORY_CODEWHALE_WRITER`` is on. The Cursor cli-pivot branch
is gone from ``run_writer``: CodeWhale is the only dispatch.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

CODEWHALE_WRITER_ENV = "FACTORY_CODEWHALE_WRITER"


def writer_uses_codewhale(env: Optional[Mapping[str, str]] = None) -> bool:
    """True when the WRITER dispatches to the headless CodeWhale worker.

    ``env=None`` means the live process environment: the runner invokes the
    role handler with only ctx, so env is ALWAYS None in production and the
    switch must read os.environ - without that fallback the worker seam never
    arms and the deterministic template path authors zero artifacts, refused
    as writer_no_output (live-factory failure sess_b9db05967cb94e6f).
    """
    blob = os.environ if env is None else env
    return str(blob.get(CODEWHALE_WRITER_ENV, "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
