#!/usr/bin/env python3
"""
PROCESS COMBINED — Reads raw_data_combined.json, applies low-rise heuristic,
matches developers, computes psf, sorts newest first, writes:
  - combined_deals.json
  - combined.html (updates the const DATA = {...}; block)

Usage:
    python scripts/process_combined.py
"""

import json
import os
import re
from datetime import datetime

# ─── PATHS ───────────────────────────────────────────────────────────────────
BASE     = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW_PATH = os.path.join(BASE, "raw_data_combined.json")
JSON_OUT = os.path.join(BASE, "combined_deals.json")
HTML_OUT = os.path.join(BASE, "combined.html")
DEVS_PATH = os.path.join(BASE, "scripts", "developers.json")
TODAY    = datetime.now().strftime("%Y-%m-%d")

# ─── Low-rise heuristic ─────────────────────────────────────────────────────
LOW_RISE_BLOCKLIST = re.compile(
    r"(?i)\b(tower|highrise|high[-\s]?rise|skyline|tallest|sky[-\s]?villa|penthouse)\b"
)


def atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def load_raw():
    if not os.path.exists(RAW_PATH):
        print(f"⚠ {RAW_PATH} not found — producing empty deals file")
        return {"listings": [], "last_updated": TODAY, "total_listings": 0}
    with open(RAW_PATH) as f:
        return json.load(f)


def load_developers():
    if not os.path.exists(DEVS_PATH):
        print(f"⚠ {DEVS_PATH} not found — skipping developer matching")
        return {"tiers": {}, "developers": []}
    with open(DEVS_PATH) as f:
        return json.load(f)


def match_developer(devs_data, community, cluster, title):
    """Match developer aliases against community/cluster/title (word-boundary, lowercase).

    Score each candidate match by (field_priority, alias_length). The longest alias
    in the most-specific field wins — so cluster "Binghatti Crystals" beats community
    "JVC", and community "DAMAC Hills 2" beats a title-only match on "damac".
    Field priority: cluster (3) > community (2) > title (1) — clusters are
    building-specific, community is a structured field (reliable), title is
    marketing copy and most likely to produce false positives.

    Returns (dev_name, dev_tier, dev_match_field) or (None, None, None)."""
    fields = [
        ("cluster",   (cluster   or "").lower(), 3),
        ("community", (community or "").lower(), 2),
        ("title",     (title     or "").lower(), 1),
    ]
    best = None  # (score_tuple, dev_name, dev_tier, field_name)
    for dev in devs_data.get("developers", []):
        for alias in dev.get("aliases", []):
            a = alias.lower().strip()
            if not a:
                continue
            pattern = r"\b" + re.escape(a) + r"\b"
            for field_name, field_val, field_pri in fields:
                if not field_val:
                    continue
                if re.search(pattern, field_val):
                    score = (field_pri, len(a))
                    if best is None or score > best[0]:
                        best = (score, dev.get("name"), dev.get("tier"), field_name)
                    break  # don't match same alias in lower-priority fields
    if best is None:
        return None, None, None
    _, name, tier, field = best
    return name, tier, field


def is_low_rise_blocked(prop_type, title, cluster):
    """Apartments matching the blocklist are dropped. Villas / townhouses always kept."""
    if prop_type in ("villa", "townhouse"):
        return False
    text = f"{title or ''} {cluster or ''}"
    return bool(LOW_RISE_BLOCKLIST.search(text))


def main():
    print(f"=== Dubai Combined Processor — {TODAY} ===\n")

    raw_data = load_raw()
    listings_in = raw_data.get("listings", [])
    print(f"Raw input: {len(listings_in)} total listings")

    devs_data = load_developers()
    tiers = devs_data.get("tiers", {})
    print(f"Loaded {len(devs_data.get('developers', []))} developers across {len(tiers)} tiers")

    out_listings = []
    dropped_low_rise = 0

    for raw in listings_in:
        prop_type = (raw.get("prop_type") or "").lower()
        title     = raw.get("title", "")
        cluster   = raw.get("cluster", "")
        community = raw.get("community", "")
        removed   = bool(raw.get("removed", False))
        # Low-rise heuristic on ACTIVE listings only (keep removed regardless;
        # they get filtered in the dashboard but we want stats on them).
        if not removed and is_low_rise_blocked(prop_type, title, cluster):
            dropped_low_rise += 1
            continue

        price = int(raw.get("price", 0) or 0)
        sqft  = int(raw.get("sqft", 0) or 0)
        psf   = round(price / sqft) if (price > 0 and sqft > 0) else None

        dev_name, dev_tier, dev_match = match_developer(devs_data, community, cluster, title)

        # Build the output record (carry over all relevant fields)
        out_listings.append({
            "uid":             raw.get("uid"),
            "href":            raw.get("href") or raw.get("link") or "",
            "price":           price,
            "beds":            raw.get("beds", 0),
            "baths":           raw.get("baths", 0),
            "sqft":            sqft,
            "psf":             psf,
            "cluster":         cluster,
            "community":       community,
            "title":           title,
            "source":          raw.get("source", ""),
            "listed":          raw.get("listed", ""),
            "isOffPlan":       bool(raw.get("isOffPlan", False)),
            "prop_type":       prop_type,
            "scrape_date":     raw.get("scrape_date", ""),
            "last_seen":       raw.get("last_seen", ""),
            "removed":         removed,
            "removed_date":    raw.get("removed_date"),
            "removed_reason":  raw.get("removed_reason"),
            "hidden":          bool(raw.get("hidden", False)),
            "dev_name":        dev_name,
            "dev_tier":        dev_tier,
            "dev_match_field": dev_match,
        })

    # Sort: newest listed first, scrape_date desc as tiebreak
    def sort_key(l):
        return (l.get("listed") or "", l.get("scrape_date") or "")
    out_listings.sort(key=sort_key, reverse=True)

    active_count   = sum(1 for l in out_listings if not l.get("removed"))
    removed_count  = sum(1 for l in out_listings if l.get("removed"))

    output = {
        "last_updated":  TODAY,
        "total_active":  active_count,
        "total_removed": removed_count,
        "tiers":         tiers,
        "listings":      out_listings,
    }

    atomic_write_json(JSON_OUT, output)
    print(f"✓ Wrote {len(out_listings)} listings → {JSON_OUT}")
    print(f"  Active: {active_count}, Removed: {removed_count}, Low-rise dropped: {dropped_low_rise}")

    if os.path.exists(HTML_OUT):
        with open(HTML_OUT) as f:
            html = f.read()
        _replacement = 'const DATA = ' + json.dumps(output, default=str) + ';'
        new_html = re.sub(
            r'const DATA = \{.*?\};',
            lambda m: _replacement,
            html,
            flags=re.DOTALL,
        )
        # Atomic rewrite of HTML
        tmp = HTML_OUT + ".tmp"
        with open(tmp, "w") as f:
            f.write(new_html)
        os.replace(tmp, HTML_OUT)
        print(f"✓ Updated HTML dashboard → {HTML_OUT}")
    else:
        print(f"WARNING: combined.html not found at {HTML_OUT}")

    # Summary stats
    by_type = {}
    for l in out_listings:
        if l.get("removed"):
            continue
        by_type[l.get("prop_type") or "other"] = by_type.get(l.get("prop_type") or "other", 0) + 1
    by_tier = {}
    for l in out_listings:
        if l.get("removed"):
            continue
        t = l.get("dev_tier")
        if t is not None:
            by_tier[str(t)] = by_tier.get(str(t), 0) + 1

    print(f"\n{'─'*50}")
    print(f"Active by type: {by_type}")
    print(f"Active by dev tier: {by_tier}")
    print("\nDone! ✓")


if __name__ == "__main__":
    main()
