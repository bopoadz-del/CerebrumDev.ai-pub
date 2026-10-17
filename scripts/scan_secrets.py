#!/usr/bin/env python3
"""Fail on likely committed secrets on non-test paths. Exit 0 (clean) or 1.

Flags:
  - 32+ char hex/base64 assigned to a (?i)(secret|token|key|password) name
  - AWS access key ids  AKIA[0-9A-Z]{16}
  - PEM private key headers  -----BEGIN ... PRIVATE KEY-----
  - the hardcoded key-derivation constant family "cerebrum_dev_secret_v<N>" (any version)
"""
import os
import re
import sys

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "env", "node_modules",
             "generated", ".postgres", "dist", "build", "site-packages"}
SKIP_FILES = {"scan_secrets.py"}  # the scanner contains the patterns it detects
# secret-ish name = long hex/base64 literal
ASSIGN = re.compile(
    r"(?i)(secret|token|key|password)\w*\s*[:=]\s*['\"]([A-Za-z0-9+/=]{32,})['\"]"
)
AKIA = re.compile(r"AKIA[0-9A-Z]{16}")
PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
CONST = re.compile(r"cerebrum_dev_secret_v\d+")
# placeholder assignments that are obviously not real secrets
PLACEHOLDER = re.compile(r"(?i)(your[-_]?|example|placeholder|changeme|xxxx|<|\.\.\.)")


def is_test(rel: str) -> bool:
    return rel.startswith(("tests/", "tests\\")) or "/tests/" in rel or "\\tests\\" in rel


def main() -> int:
    hits = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES:
                continue
            if f.endswith((".pyc", ".png", ".jpg", ".jpeg", ".gif", ".webp",
                           ".pdf", ".zip", ".db", ".jsonl")):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, ".")
            if is_test(rel):
                continue
            try:
                if os.path.getsize(p) > 2_000_000:  # skip large data/model files
                    continue
                text = open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for m in ASSIGN.finditer(text):
                val = m.group(2)
                line = text[:m.start()].count("\n") + 1
                if PLACEHOLDER.search(m.group(0)):
                    continue
                hits.append(f"{rel}:{line} long secret-like literal ({m.group(1)})")
            for rx, label in ((AKIA, "AWS key id"), (PEM, "PEM private key"),
                              (CONST, "hardcoded dev-secret constant")):
                for m in rx.finditer(text):
                    line = text[:m.start()].count("\n") + 1
                    hits.append(f"{rel}:{line} {label}")
    if hits:
        sys.stdout.write("POSSIBLE SECRETS ON NON-TEST PATHS:\n")
        for h in sorted(set(hits)):
            sys.stdout.write("  " + h + "\n")
        sys.stdout.write(f"TOTAL: {len(set(hits))}\n")
        return 1
    sys.stdout.write("NO SECRETS DETECTED ON NON-TEST PATHS.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
