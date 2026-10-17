"""Quick legal-domain smoke test for legal_v2 inclusion."""
import json
import sys
import zipfile
from pathlib import Path

import httpx

BASE = "http://localhost:8001"
HEADERS = {"Authorization": "Bearer cb_dev_key"}


def main() -> int:
    # 1. Create session
    r = httpx.post(f"{BASE}/v1/sessions/", headers=HEADERS, json={"domain": "legal"})
    r.raise_for_status()
    sess = r.json()
    sid = sess["session_id"]
    print(f"Session: {sid}")

    # 2. Set domain
    r = httpx.post(f"{BASE}/v1/sessions/{sid}/chat", headers=HEADERS, json={"message": "Set domain to legal."}, timeout=60)
    r.raise_for_status()
    print("Domain set.")

    # 3. Send contract review prompt
    prompt = (
        "I want to build a legal contract review platform.\n\n"
        "Users will upload PDF or scanned contract documents.\n"
        "The platform should extract text, OCR scanned pages, identify legal risks, summarize obligations, "
        "flag missing clauses, and let users ask questions about the contract.\n\n"
        "Always flag:\n"
        "- termination clauses\n"
        "- payment obligations\n"
        "- liability caps\n"
        "- governing law\n"
        "- dispute resolution\n"
        "- missing signature pages\n\n"
        "Build the platform using only available Cerebrum store blocks."
    )
    r = httpx.post(f"{BASE}/v1/sessions/{sid}/chat", headers=HEADERS, json={"message": prompt}, timeout=120)
    r.raise_for_status()
    print("Chat sent.")

    # 4. Get chain
    r = httpx.get(f"{BASE}/v1/sessions/{sid}/chain/preview", headers=HEADERS)
    print(f"Preview status: {r.status_code}")
    if r.status_code != 200:
        print(r.text)
        return 1
    preview = r.json()
    print("Proposed chain:")
    print(json.dumps(preview, indent=2))

    block_ids = {b["id"] for b in preview.get("chain", {}).get("blocks", [])}
    if "legal_v2" not in block_ids:
        print("ERROR: legal_v2 not in proposed chain")
        return 1
    print("SUCCESS: legal_v2 is in proposed chain")

    # 5. Approve
    r = httpx.post(f"{BASE}/v1/sessions/{sid}/chain/approve", headers=HEADERS, timeout=120)
    print(f"Approve status: {r.status_code}")
    print(r.json())

    # 6. Package
    r = httpx.post(f"{BASE}/v1/sessions/{sid}/deploy?target=platform", headers=HEADERS, timeout=300)
    print(f"Package status: {r.status_code}")
    if r.status_code != 200:
        print(r.text)
        return 1
    pkg = r.json()
    print(pkg)

    # 7. Download and inspect zip
    r = httpx.get(f"{BASE}/v1/sessions/{sid}/deploy/package?variant=platform", headers=HEADERS)
    print(f"Download status: {r.status_code}, size: {len(r.content)}")
    zip_path = Path(f"/tmp/legal_v2_pkg_{sid}.zip")
    zip_path.write_bytes(r.content)
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        checks = [
            "engine/",
            "engine/app/",
            "engine/block_store/",
            "app/containers/legal.py",
            "app/blocks/legal_v2.py",
            "default_chain.json",
            "build_metadata.json",
            "Dockerfile",
        ]
        for c in checks:
            print(f"  {c}: {'YES' if c in names else 'NO'}")
        df = zf.read("Dockerfile").decode()
        print(f"  Dockerfile has COPY engine/: {'COPY engine/ /app' in df}")
        print(f"  Dockerfile no git clone: {'git clone' not in df.lower()}")
        print(f"  Dockerfile no invalid COPY: {'2>/dev/null' not in df and '|| true' not in df}")
        bm = json.loads(zf.read("build_metadata.json"))
        print("  build_metadata engine:")
        print(json.dumps(bm.get("engine", {}), indent=4))

    return 0


if __name__ == "__main__":
    sys.exit(main())
