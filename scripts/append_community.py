#!/usr/bin/env python3
"""
APPEND COMMUNITY — Accumulates browser-scraped Bayut data into raw_data.json.

Usage (after running bayut_extractor.js in browser and reading window._raw slices):

    python append_community.py '<json_array_string>'

Where <json_array_string> is the combined output from reading window._raw slices.
Claude should concatenate all slices, then call this script once per community.

The script APPENDS to raw_data.json (creating it if it doesn't exist).
Run process_deals.py once ALL communities are done.

Example:
    python append_community.py '[{"uid":"bayut-123","price":2300000,...}]'

To RESET (start a fresh scan):
    python append_community.py --reset
"""

import json, sys, os

RAW_PATH = os.path.join(os.path.dirname(__file__), "..", "raw_data.json")
RAW_PATH = os.path.abspath(RAW_PATH)

def load_raw():
    if os.path.exists(RAW_PATH):
        with open(RAW_PATH) as f:
            return json.load(f)
    return {"communities": {}, "total_listings": 0}

def save_raw(data):
    with open(RAW_PATH, "w") as f:
        json.dump(data, f, indent=2)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python append_community.py '<json_array>'")
        print("       python append_community.py --reset")
        sys.exit(1)

    if sys.argv[1] == "--reset":
        save_raw({"communities": {}, "total_listings": 0})
        print("raw_data.json reset.")
        sys.exit(0)

    raw_input = sys.argv[1]

    try:
        listings = json.loads(raw_input)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON input — {e}")
        sys.exit(1)

    if not isinstance(listings, list) or len(listings) == 0:
        print("ERROR: Expected a non-empty JSON array of listings.")
        sys.exit(1)

    # Detect community from first listing
    community = listings[0].get("community", "Unknown")

    data = load_raw()
    prev_count = len(data["communities"].get(community, []))
    data["communities"][community] = listings
    data["total_listings"] = sum(len(v) for v in data["communities"].values())
    save_raw(data)

    print(f"✓ {community}: {len(listings)} listings saved (replaced {prev_count} previous).")
    print(f"  Communities done so far: {list(data['communities'].keys())}")
    print(f"  Total listings accumulated: {data['total_listings']}")
    print(f"  Saved to: {RAW_PATH}")
