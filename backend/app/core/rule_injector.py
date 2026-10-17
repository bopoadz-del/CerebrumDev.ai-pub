import os
import logging
from pathlib import Path
from typing import List
from .rule_parser import parse_rules

logger = logging.getLogger(__name__)

STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")


def inject_rules(session_id: str, domain: str, rules: List[str]) -> str:
    """Generate a modified container stub with injected rules and save it to session storage."""
    parsed = parse_rules(rules)
    rules_dir = Path(STORAGE_PATH) / "sessions" / session_id / "container"
    rules_dir.mkdir(parents=True, exist_ok=True)

    modified_path = rules_dir / f"{domain}_container_with_rules.py"

    snippets = "\n".join(r["code_snippet"] for r in parsed)
    content = f'''"""
Auto-generated domain container for {domain}.
Session: {session_id}
Injected rules:
{chr(10).join("- " + r for r in rules) if rules else "(none)"}
"""

class {domain.title().replace("_", "")}Container:
    name = "{domain}_with_rules"
    domain = "{domain}"

    def __init__(self, config=None):
        self.config = config or {{}}
        self.rules = {parsed!r}

    def apply_rules(self, context):
        """Apply injected business rules."""
{chr(10).join("        " + line for line in snippets.splitlines()) if snippets else "        pass"}
        return context

    def run_chain(self, chain, inputs):
        """Execute the approved orchestrator chain."""
        result = inputs
        for connection in chain.get("connections", []):
            block = chain["blocks"][connection["from"]]
            next_block = chain["blocks"][connection["to"]]
            # Placeholder wiring
            result = {{"from": block["id"], "to": next_block["id"], "data": result}}
        return result
'''
    modified_path.write_text(content, encoding="utf-8")
    logger.info("Injected rules into %s", modified_path)
    return str(modified_path)
