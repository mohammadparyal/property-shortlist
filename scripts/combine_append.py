#!/usr/bin/env python3
"""
COMBINE APPEND — Combines fresh PF listings with existing Bayut listings in raw_data.json.

Use this script (NOT append_community.py) when running a MIXED scan that has BOTH
Bayut and PropertyFinder data. append_community.py REPLACES community data entirely,
which would wipe out Bayut listings when you save PF data for the same community.

This script:
  1. Reads fresh PF listings from stdin (JSON array)
  2. Keeps existing Bayut listings for that community from raw_data.json
  3. Combines both arrays and saves back

Usage (pipe assembled PF JSON to stdin):
    python3 combine_append.py < /tmp/community_raw.json

    # Or inline:
    echo '[{...}]' | python3 combine_append.py

Workflow:
    1. Run bayut_extractor.js for all 8 communities → save via append_community.py
    2. For each community, run pf_extractor.js → assemble chunks → pipe to THIS script
    3. Run process_deals.py when all done

To RESET (start a fresh scan):
    python3 combine_append.py --reset
"""

import json
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_PATH   = os.path.abspath(os.path.join(SCRIPT_DIR, '..', 'raw_data.json'))


def load_raw():
    if os.path.exists(RAW_PATH):
        with open(RAW_PATH) as f:
            return json.load(f)
    return {"communities": {}, "total_listings": 0}


def save_raw(data):
    with open(RAW_PATH, "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--reset":
        save_raw({"communities": {}, "total_listings": 0})
        print("raw_data.json reset.")
        sys.exit(0)

    raw_input = sys.stdin.read().strip()

    try:
        pf_listings = json.loads(raw_input)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON input — {e}")
        sys.exit(1)

    if not isinstance(pf_listings, list) or len(pf_listings) == 0:
        print("ERROR: Expected a non-empty JSON array of listings.")
        sys.exit(1)

    community = pf_listings[0].get("community", "Unknown")

    data = load_raw()
    existing = data["communities"].get(community, [])

    # Keep Bayut listings, replace PF listings with fresh ones
    bayut_listings = [l for l in existing if l.get("uid", "").startswith("bayut-")]
    combined = bayut_listings + pf_listings

    data["communities"][community] = combined
    data["total_listings"] = sum(len(v) for v in data["communities"].values())
    save_raw(data)

    print(f"✓ {community}: {len(bayut_listings)} Bayut + {len(pf_listings)} PF = {len(combined)} total")
    print(f"  Communities done: {list(data['communities'].keys())}")
    print(f"  Total listings: {data['total_listings']}")
