#!/usr/bin/env python3
"""
Monthly invoice runner — reads a manifest of client YAML files and generates
an invoice for each. A failure on one client is logged but does not abort the rest.

Usage (normally via launchd, but can be run manually):
    python3 run_monthly_invoices.py
"""

import subprocess
import sys
from pathlib import Path

import yaml


SCRIPT_DIR = Path(__file__).parent
MANIFEST_FILE  = SCRIPT_DIR / "monthly_manifest.yaml"
GENERATE_SCRIPT = SCRIPT_DIR / "generate_invoice.py"


def main():
    if not MANIFEST_FILE.exists():
        print(f"Manifest not found: {MANIFEST_FILE}")
        sys.exit(1)

    with open(MANIFEST_FILE, "r") as f:
        manifest = yaml.safe_load(f)

    clients = manifest.get("clients", [])

    if not clients:
        print("No clients listed in manifest — nothing to do.")
        sys.exit(0)

    print(f"Running monthly invoices for {len(clients)} client(s)...")

    failures = []

    for entry in clients:
        client_yaml = entry["config"]
        raw_qty     = entry.get("qty", 1)
        qty_list    = raw_qty if isinstance(raw_qty, list) else [raw_qty]

        print(f"\n── {client_yaml} (qty: {qty_list}) ──")
        result = subprocess.run(
            [sys.executable, str(GENERATE_SCRIPT), "-c", client_yaml,
             "-qt"] + [str(q) for q in qty_list],
            capture_output=False
        )
        if result.returncode != 0:
            print(f"FAILED: {client_yaml} exited with code {result.returncode}")
            failures.append(client_yaml)

    print(f"\nDone. {len(clients) - len(failures)}/{len(clients)} succeeded.")

    if failures:
        print(f"Failed: {', '.join(failures)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
