#!/usr/bin/env python3
"""
PROCESS DEALS — Reads raw_data.json (scraped by bayut_extractor.js),
merges price history, scores each listing, and writes:
  - dubai_deals.json
  - index.html (updates the const DATA = {...}; block)

Usage:
    python process_deals.py

No arguments needed — it reads from ../raw_data.json automatically.
Run this AFTER all communities have been appended via append_community.py.
"""

import json, re, os
from datetime import datetime

# ─── PATHS ───────────────────────────────────────────────────────────────────
BASE     = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW_PATH = os.path.join(BASE, "raw_data.json")
JSON_OUT = os.path.join(BASE, "dubai_deals.json")
HTML_OUT = os.path.join(BASE, "index.html")
TODAY    = datetime.now().strftime("%Y-%m-%d")

# ─── LAUNCH BENCHMARKS (AED at launch — used for % vs launch scoring) ────────
LAUNCH = {
    "DAMAC Lagoons":      {3: 1_400_000, 4: 1_700_000},
    "DAMAC Islands":      {3: 1_800_000, 4: 2_250_000},
    "The Valley":         {3: 1_530_000, 4: 2_100_000},
    "DAMAC Hills 2":      {3:   800_000, 4: 1_200_000},
    "Villanova":          {3: 1_445_000, 4: 2_000_000},
    "DAMAC Hills":        {3: 1_500_000, 4: 2_200_000},
    "Dubai Hills Estate": {3: 2_100_000, 4: 2_600_000},
    "Tilal Al Ghaf":      {3: 1_260_000, 4: 1_800_000},
}

# ─── DISTRESS SIGNAL PATTERNS ─────────────────────────────────────────────────
SIGNAL_PATTERNS = {
    "Motivated Seller": r"motivated|must sell|urgent|distress",
    "Below Launch":     r"below (op|launch|market|original price)|below market price",
    "Investor Deal":    r"investor deal|investment deal|high roi",
    "Genuine Resale":   r"genuine (resale|seller|sale)",
    "Payment Plan":     r"payment plan|\d+/\d+|\d+% (monthly|pp)",
    "Handover Soon":    r"handover (soon|q[1-4] 202[5-7]|ready)",
    "Price Dropped":    r"price drop(ped)?",
    "Quick Exit":       r"quick (exit|sale|close)",
    "Single Row":       r"single row",
    "No Agent Fee":     r"no (agent|commission|agents)",
    "Assignment Sale":  r"assignment|transfer at \d+%",
}

PANIC_START = datetime(2026, 3, 1)


def detect_signals(title: str) -> list[str]:
    found = []
    t = title.lower()
    for label, pattern in SIGNAL_PATTERNS.items():
        if re.search(pattern, t):
            found.append(label)
    return found


def calc_score(price: int, beds: int, community: str, title: str, listed_date: str):
    launch_map   = LAUNCH.get(community, {})
    launch_price = launch_map.get(beds, launch_map.get(3, 1_000_000))
    pct          = round((price - launch_price) / launch_price * 100, 1)
    base         = max(0, round(90 - pct * 0.88))
    signals      = detect_signals(title)
    signal_bonus = len(signals) * 3
    try:
        listed_dt = datetime.strptime(listed_date, "%Y-%m-%d") if listed_date else None
        panic     = bool(listed_dt and listed_dt >= PANIC_START)
    except Exception:
        panic = False
    panic_bonus = 5 if panic else 0
    deal_score  = base + signal_bonus + panic_bonus
    return deal_score, pct, launch_price, signals, panic


def load_previous() -> tuple[dict, list]:
    """Load existing dubai_deals.json and return:
    - prev_map: uid → listing (for price history / slash price tracking)
    - pf_carry: PF listings NOT re-scraped this run (carried forward if PF not in raw_data)
    """
    prev_map    = {}
    pf_carry    = []
    if os.path.exists(JSON_OUT):
        with open(JSON_OUT) as f:
            prev_data = json.load(f)
        for pl in prev_data.get("listings", []):
            uid = pl.get("unique_id")
            if uid:
                prev_map[uid] = pl
        pf_carry = [l for l in prev_data["listings"] if l.get("unique_id", "").startswith("pf-")]
    return prev_map, pf_carry


def load_raw() -> dict:
    """Load raw_data.json produced by append_community.py."""
    if not os.path.exists(RAW_PATH):
        raise FileNotFoundError(
            f"raw_data.json not found at {RAW_PATH}\n"
            "Run bayut_extractor.js in the browser for each community,\n"
            "then call append_community.py to collect results."
        )
    with open(RAW_PATH) as f:
        return json.load(f)


def build_listing(raw: dict, prev_map: dict, price_drops: list, price_increases: list) -> dict:
    """Convert a raw scraped listing dict into a full scored listing.
    Works for both Bayut (uid=bayut-xxx) and PropertyFinder (uid=pf-xxx) raw data.
    """
    uid       = raw["uid"]
    price     = int(raw.get("price", 0))
    beds      = int(raw.get("beds", 0))
    baths     = int(raw.get("baths", 0))
    sqft      = int(raw.get("sqft", 0))
    cluster   = raw.get("cluster", "")
    title     = raw.get("title", "")
    community = raw.get("community", "")
    is_off    = bool(raw.get("isOffPlan", False))
    source    = raw.get("source", "Bayut" if uid.startswith("bayut-") else "PropertyFinder")

    # Build fallback href based on source
    if uid.startswith("bayut-"):
        fallback_href = f"https://www.bayut.com/property/details-{uid.replace('bayut-','')}.html"
        ref           = uid.replace("bayut-", "")
    else:
        ref           = uid.replace("pf-", "")
        fallback_href = f"https://www.propertyfinder.ae/en/plp/{ref}"
    href = raw.get("href") or fallback_href

    prev      = prev_map.get(uid, {})
    old_price = prev.get("price", 0)
    history   = list(prev.get("price_history") or [])
    slash     = prev.get("slash_price") or old_price or price
    price_tag = None

    if old_price and old_price != price:
        change = "drop" if price < old_price else "increase"
        history.insert(0, {"date": TODAY, "old_price": old_price, "new_price": price, "change": change})
        price_tag = change
        if change == "drop":
            price_drops.append(uid)
        else:
            price_increases.append(uid)
    else:
        price_tag = prev.get("price_tag")

    slash_val   = max(slash, price, old_price)
    slash_price = slash_val if slash_val > price else None

    # PF provides listed date directly in raw data; Bayut does not — fall back to prev
    listed  = raw.get("listed") or prev.get("listed", "")
    status  = "off_plan" if is_off else "ready"
    deal_score, pct_vs_launch, launch_price, signals, panic = calc_score(price, beds, community, title, listed)
    psf     = round(price / sqft) if sqft > 0 else 0

    return {
        "community":     community,
        "cluster":       cluster or prev.get("cluster", ""),
        "price":         price,
        "beds":          beds,
        "baths":         baths,
        "sqft":          sqft,
        "title":         title,
        "source":        source,
        "listed":        listed,
        "status":        status,
        "signals":       ", ".join(signals),
        "ref":           ref,
        "link":          href,
        "unique_id":     uid,
        "note":          prev.get("note", ""),
        "panic_period":  panic,
        "deal_score":    deal_score,
        "pct_vs_launch": pct_vs_launch,
        "launch_price":  launch_price,
        "psf":           psf,
        "slash_price":   slash_price,
        "price_history": history[:10],
        "price_tag":     price_tag,
    }


def main():
    print(f"=== Dubai Deal Processor — {TODAY} ===\n")

    prev_map, pf_carry = load_previous()
    print(f"Previous data: {len(prev_map)} listings loaded for price history")

    raw_data    = load_raw()
    communities = raw_data.get("communities", {})
    print(f"Raw input: {len(communities)} communities, {raw_data.get('total_listings',0)} total listings")

    # Detect which sources were scraped this run
    scraped_sources = set()
    for listings in communities.values():
        for l in listings:
            scraped_sources.add(l.get("source", "Bayut" if l.get("uid","").startswith("bayut-") else "PropertyFinder"))
    print(f"Sources scraped this run: {scraped_sources}\n")

    new_listings  = []
    seen_uids     = set()
    price_drops   = []
    price_incrs   = []
    errors        = []

    # ── Process all scraped listings (Bayut + PF both go through same pipeline) ──
    for community, listings in communities.items():
        for raw in listings:
            uid = raw.get("uid")
            if not uid or uid in seen_uids:
                continue
            seen_uids.add(uid)
            try:
                listing = build_listing(raw, prev_map, price_drops, price_incrs)
                new_listings.append(listing)
            except Exception as e:
                errors.append(f"ERROR {uid}: {e}")

    # ── Carry forward PF listings ONLY if PF was NOT scraped this run ────────
    if "PropertyFinder" not in scraped_sources:
        print(f"PF not scraped this run — carrying forward {len(pf_carry)} previous PF listings (re-scored)")
        for pf in pf_carry:
            uid = pf.get("unique_id")
            if not uid or uid in seen_uids:
                continue
            seen_uids.add(uid)
            price     = pf.get("price", 0)
            beds      = pf.get("beds", 3)
            community = pf.get("community", "")
            title     = pf.get("title", "")
            listed    = pf.get("listed", "")
            deal_score, pct_vs_launch, launch_price, signals, panic = calc_score(price, beds, community, title, listed)
            pf_copy = dict(pf)
            pf_copy["deal_score"]    = deal_score
            pf_copy["pct_vs_launch"] = pct_vs_launch
            pf_copy["panic_period"]  = panic
            if not pf.get("signals"):
                pf_copy["signals"] = ", ".join(signals)
            pf_copy.setdefault("note", "")
            new_listings.append(pf_copy)
    else:
        print(f"PF scraped this run — {sum(1 for l in new_listings if l.get('source')=='PropertyFinder')} fresh PF listings included")

    # ── Sort by score ─────────────────────────────────────────────────────────
    new_listings.sort(key=lambda x: x["deal_score"], reverse=True)

    # ── Build output ──────────────────────────────────────────────────────────
    output = {
        "last_updated":   TODAY,
        "total":          len(new_listings),
        "price_drops":    len(price_drops),
        "price_increases":len(price_incrs),
        "listings":       new_listings,
    }

    # ── Write JSON ────────────────────────────────────────────────────────────
    with open(JSON_OUT, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"✓ Wrote {len(new_listings)} listings → {JSON_OUT}")

    # ── Update HTML dashboard ─────────────────────────────────────────────────
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
        with open(HTML_OUT, "w") as f:
            f.write(new_html)
        print(f"✓ Updated HTML dashboard → {HTML_OUT}")
    else:
        print(f"WARNING: index.html not found at {HTML_OUT}")

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"Total listings:    {len(new_listings)}")
    print(f"Price drops:       {len(price_drops)}")
    print(f"Price increases:   {len(price_incrs)}")
    panic_count = sum(1 for l in new_listings if l.get("panic_period"))
    print(f"Panic period:      {panic_count}")
    scores = [l["deal_score"] for l in new_listings]
    print(f"Score range:       {min(scores)} – {max(scores)}")
    print(f"Errors:            {len(errors)}")
    for e in errors:
        print(f"  {e}")
    print(f"\nTop 5 deals:")
    for l in new_listings[:5]:
        print(f"  [{l['deal_score']:>2}] {l['unique_id']:<40} {l['community']} {l['beds']}BR  AED {l['price']:>10,}  pct={l['pct_vs_launch']}%")
    print(f"\n{'─'*50}")
    if price_drops:
        print(f"Price drops: {price_drops}")
    if price_incrs:
        print(f"Price increases: {price_incrs}")
    print("\nDone! ✓")


if __name__ == "__main__":
    main()
