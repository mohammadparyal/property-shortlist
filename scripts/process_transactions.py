#!/usr/bin/env python3
"""
PROCESS TRANSACTIONS — Reads raw_data_transactions.json, applies developer matching,
sorts newest first, computes aggregate stats, writes:
  - transactions_data.json
  - transactions.html (updates the const DATA = {...}; block)

Usage:
    python scripts/process_transactions.py
"""

import json
import os
import re
from datetime import datetime, timedelta
from statistics import median

# ─── PATHS ───────────────────────────────────────────────────────────────────
BASE      = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW_PATH  = os.path.join(BASE, "raw_data_transactions.json")
JSON_OUT  = os.path.join(BASE, "transactions_data.json")
HTML_OUT  = os.path.join(BASE, "transactions.html")
DEVS_PATH = os.path.join(BASE, "scripts", "developers.json")
TODAY     = datetime.now().strftime("%Y-%m-%d")
OLD_DAYS  = 30


def atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def load_raw():
    if not os.path.exists(RAW_PATH):
        print(f"⚠ {RAW_PATH} not found — producing empty deals file")
        return {"transactions": [], "last_updated": TODAY, "total_transactions": 0}
    with open(RAW_PATH) as f:
        return json.load(f)


def load_developers():
    if not os.path.exists(DEVS_PATH):
        print(f"⚠ {DEVS_PATH} not found — skipping developer matching")
        return {"tiers": {}, "developers": []}
    with open(DEVS_PATH) as f:
        return json.load(f)


def match_developer(devs_data, community, cluster, title):
    """Same scoring as process_combined.match_developer."""
    fields = [
        ("cluster",   (cluster   or "").lower(), 3),
        ("community", (community or "").lower(), 2),
        ("title",     (title     or "").lower(), 1),
    ]
    best = None
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
                    break
    if best is None:
        return None, None, None
    _, name, tier, field = best
    return name, tier, field


def main():
    print(f"=== Dubai Transactions Processor — {TODAY} ===\n")

    raw_data = load_raw()
    txs_in = raw_data.get("transactions", [])
    print(f"Raw input: {len(txs_in)} transactions")

    devs_data = load_developers()
    tiers = devs_data.get("tiers", {})
    print(f"Loaded {len(devs_data.get('developers', []))} developers across {len(tiers)} tiers")

    # Defensive 30-day cutoff
    today_date = datetime.strptime(TODAY, "%Y-%m-%d").date()
    cutoff = today_date - timedelta(days=OLD_DAYS)

    out = []
    dropped_old = 0
    for raw in txs_in:
        d = (raw.get("transaction_date") or "")[:10]
        if d:
            try:
                dd = datetime.strptime(d, "%Y-%m-%d").date()
                if dd < cutoff:
                    dropped_old += 1
                    continue
            except (ValueError, TypeError):
                pass

        community = raw.get("community", "") or ""
        building  = raw.get("building", "") or ""
        sub_community = raw.get("sub_community", "") or ""
        status        = raw.get("status", "") or ""

        # ── Normalize legacy/buggy status data ────────────────────────────
        # Older scraper versions sometimes wrote sub_community or community
        # into the status field when no real status badge was present.
        # Repair here so the dashboard shows clean values.
        KNOWN_STATUSES = {"Off-Plan", "Off Plan", "Initial Sale", "Resale", "Sale"}
        if status and status not in KNOWN_STATUSES:
            # status was wrongly populated with community/sub-community text
            status = ""
        if sub_community in KNOWN_STATUSES:
            # Sub-community wrongly carries the status badge — promote it.
            if not status:
                status = sub_community
            sub_community = ""

        # Pass building in cluster slot (most-specific signal); skip title (transactions have none).
        dev_name, dev_tier, dev_match = match_developer(devs_data, community, building, "")

        price = int(raw.get("price") or 0)
        sqft  = int(raw.get("sqft") or 0)
        psf   = round(price / sqft) if (price > 0 and sqft > 0) else None

        out.append({
            "uid":              raw.get("uid"),
            "transaction_date": d,
            "building":         building,
            "community":        community,
            "sub_community":    sub_community,
            "status":           status,
            "prop_type":        (raw.get("prop_type") or "").lower(),
            "beds":             int(raw.get("beds") or 0),
            "sqft":             sqft,
            "plot_sqft":        raw.get("plot_sqft"),
            "price":            price,
            "psf":              psf,
            "scrape_date":      raw.get("scrape_date", ""),
            "source":           raw.get("source", "Bayut Transactions"),
            "dev_name":         dev_name,
            "dev_tier":         dev_tier,
            "dev_match_field":  dev_match,
        })

    # Sort: newest transaction_date first, then scrape_date desc
    out.sort(key=lambda t: ((t.get("transaction_date") or ""),
                             (t.get("scrape_date") or "")), reverse=True)

    # Aggregates
    prices = [t["price"] for t in out if t.get("price")]
    psfs   = [t["psf"]   for t in out if t.get("psf")]
    median_price = int(median(prices)) if prices else 0
    median_psf   = int(median(psfs))   if psfs   else 0

    dev_counts = {}
    for t in out:
        n = t.get("dev_name")
        if n:
            dev_counts[n] = dev_counts.get(n, 0) + 1
    top_developer = max(dev_counts.items(), key=lambda kv: kv[1])[0] if dev_counts else ""

    comm_counts = {}
    for t in out:
        c = t.get("community") or ""
        if c:
            comm_counts[c] = comm_counts.get(c, 0) + 1
    top_community = max(comm_counts.items(), key=lambda kv: kv[1])[0] if comm_counts else ""

    output = {
        "last_updated":   TODAY,
        "total_30d":      len(out),
        "median_price":   median_price,
        "median_psf":     median_psf,
        "top_developer":  top_developer,
        "top_community":  top_community,
        "tiers":          tiers,
        "transactions":   out,
    }

    atomic_write_json(JSON_OUT, output)
    print(f"✓ Wrote {len(out)} transactions → {JSON_OUT}")
    print(f"  Dropped (>30d): {dropped_old}")
    print(f"  Median price: AED {median_price:,}, Median PSF: AED {median_psf:,}/sqft")
    print(f"  Top developer: {top_developer or '—'}, Top community: {top_community or '—'}")

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
        tmp = HTML_OUT + ".tmp"
        with open(tmp, "w") as f:
            f.write(new_html)
        os.replace(tmp, HTML_OUT)
        print(f"✓ Updated HTML dashboard → {HTML_OUT}")
    else:
        print(f"WARNING: transactions.html not found at {HTML_OUT}")

    # Summary
    by_type = {}
    by_status = {}
    for t in out:
        by_type[t.get("prop_type") or "other"] = by_type.get(t.get("prop_type") or "other", 0) + 1
        by_status[t.get("status") or "unknown"] = by_status.get(t.get("status") or "unknown", 0) + 1

    print(f"\n{'─'*50}")
    print(f"By type:   {by_type}")
    print(f"By status: {by_status}")
    print("\nDone! ✓")


if __name__ == "__main__":
    main()
