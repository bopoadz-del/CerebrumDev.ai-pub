"""Pilot cycle helpers.

``prepare_pilot_workspace`` is gone. Store-unwired adapter corrections run
in CLONER emission (``offline_adapters``) so a later gate cannot rewrite
``vendor/**``. Opening a pilot cycle must not patch the product tree.
"""
