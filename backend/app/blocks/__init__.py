"""Factory dual-register of Cerebrum-Blocks ``app/blocks`` runtime helpers.

This package is not the Store registry (``get_block`` / ``BLOCK_REGISTRY``).
Those stay in Cerebrum-Blocks and are vendored into generated products by
the CLONER. Modules here are the factory-side copy of ranking/retrieval
helpers that products also consume.
"""
