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
    "DAMAC Islands 2":    {4: 2_750_000, 5: 3_350_000},
    "The Valley":         {3: 1_530_000, 4: 2_100_000},
    "DAMAC Hills 2":      {3:   800_000, 4: 1_200_000},
    "Villanova":          {3: 1_445_000, 4: 2_000_000},
    "DAMAC Hills":        {3: 1_500_000, 4: 2_200_000},
    "Dubai Hills Estate": {3: 2_100_000, 4: 2_600_000},
    "Tilal Al Ghaf":      {3: 1_260_000, 4: 1_800_000},
    # ── New communities ──
    "Emaar South":        {3: 1_750_000, 4: 2_400_000},
    "Arabian Ranches 3":  {3: 2_000_000, 4: 2_580_000},
    "Town Square":        {3: 1_300_000, 4: 1_500_000},
    "Mudon":              {3: 1_900_000, 4: 2_400_000},
    # ── Batch 3 ──
    "Al Barsha South 2":  {3: 2_300_000, 4: 2_800_000},
    "JVC":                {3: 1_200_000, 4: 1_600_000},
    "JVT":                {3: 1_300_000, 4: 1_700_000},
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
    "Closed Kitchen":   r"closed kitchen|enclosed kitchen",
    "Ground Bedroom":   r"ground (floor )?bed|ground (floor )?room|bed(room)? (on |at )?ground|maid.?s.*(ground|down)|downstairs bed",
}

PANIC_START = datetime(2026, 3, 1)
MAX_PRICE   = 3_000_000   # Skip listings above this price

# ─── COMMUNITY NOTES (auto-applied to every listing in that community) ────────
COMMUNITY_NOTES = {
    "DAMAC Hills 2": "Near Al Minhad Air Base",
}


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


def load_previous() -> dict:
    """Load existing dubai_deals.json and return prev_map: uid → listing.
    Used ONLY for price history / slash price tracking.
    Listings not found in current scrape are dropped (sold/delisted).
    """
    prev_map = {}
    if os.path.exists(JSON_OUT):
        with open(JSON_OUT) as f:
            prev_data = json.load(f)
        for pl in prev_data.get("listings", []):
            uid = pl.get("unique_id")
            if uid:
                prev_map[uid] = pl
    return prev_map


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
    fresh_drop = False

    if old_price and old_price != price:
        change = "drop" if price < old_price else "increase"
        history.insert(0, {"date": TODAY, "old_price": old_price, "new_price": price, "change": change})
        price_tag = change
        if change == "drop":
            price_drops.append(uid)
            fresh_drop = True
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

    # ── Price drop bonus ──────────────────────────────────────────────────────
    # Fresh drop detected this run → big boost; prior drop in history → smaller boost.
    # Also auto-inject "Price Dropped" signal so it shows in the UI regardless of title wording.
    has_history_drop = any(h.get("change") == "drop" for h in history)
    if fresh_drop:
        deal_score += 10
        if "Price Dropped" not in signals:
            signals = ["Price Dropped"] + signals
    elif has_history_drop:
        deal_score += 5
        if "Price Dropped" not in signals:
            signals = ["Price Dropped"] + signals

    # Auto-apply community notes (e.g. airbase warning)
    note = prev.get("note", "") or COMMUNITY_NOTES.get(community, "")

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
        "note":          note,
        "last_seen":     TODAY,
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

    prev_map = load_previous()
    print(f"Previous data: {len(prev_map)} listings loaded for price history")

    raw_data    = load_raw()
    communities = raw_data.get("communities", {})
    print(f"Raw input: {len(communities)} communities, {raw_data.get('total_listings',0)} total listings")

    # Detect which sources were scraped this run
    scraped_sources = set()
    for listings in communities.values():
        for l in listings:
            scraped_sources.add(l.get("source", "Bayut" if l.get("uid","").startswith("bayut-") else "PropertyFinder"))
    print(f"Sources scraped this run: {scraped_sources}")

    new_listings  = []
    seen_uids     = set()
    price_drops   = []
    price_incrs   = []
    errors        = []
    skipped_price = 0

    # ── Process ONLY listings found in current scrape ────────────────────────
    # If a listing is not in raw_data, it's considered sold/delisted and dropped.
    # Price history is still preserved via prev_map for returning listings.
    for community, listings in communities.items():
        for raw in listings:
            uid = raw.get("uid")
            if not uid or uid in seen_uids:
                continue
            # Price cap filter
            raw_price = int(raw.get("price", 0))
            if raw_price > MAX_PRICE:
                skipped_price += 1
                continue
            seen_uids.add(uid)
            try:
                listing = build_listing(raw, prev_map, price_drops, price_incrs)
                new_listings.append(listing)
            except Exception as e:
                errors.append(f"ERROR {uid}: {e}")

    # Count sources
    pf_count    = sum(1 for l in new_listings if l.get("source") == "PropertyFinder")
    bayut_count = sum(1 for l in new_listings if "bayut" in l.get("unique_id", ""))
    print(f"Fresh listings: {pf_count} PF + {bayut_count} Bayut = {len(new_listings)} total")

    # Track how many previous listings were dropped (sold/delisted)
    prev_uids   = set(prev_map.keys())
    dropped     = prev_uids - seen_uids
    if dropped:
        print(f"Dropped {len(dropped)} listings not found in current scrape (sold/delisted)")

    # ── Smart deduplication ────────────────────────────────────────────────────
    # Agents re-list the same property many times.  Two listings are considered
    # the "same property" when they share community + beds + similar sqft (±5%)
    # + similar price (±3%).  Among duplicates we keep ONLY the one with the
    # most recent last_seen (tie-break: most recent listed, then highest score).
    pre_dedup = len(new_listings)

    def _dedup_key(l):
        """Coarse bucket: community + beds + rounded sqft + rounded price."""
        sqft = l.get("sqft") or 0
        price = l.get("price") or 0
        # Round sqft to nearest 50 and price to nearest 25k for fuzzy match
        return (
            l.get("community", ""),
            l.get("beds", 0),
            round(sqft / 50) * 50 if sqft else 0,
            round(price / 25_000) * 25_000 if price else 0,
        )

    def _pick_best(group):
        """From a group of duplicate listings, pick the best one to keep."""
        # Prefer: most recent last_seen → most recent listed → highest score
        return max(group, key=lambda l: (
            l.get("last_seen", ""),
            l.get("listed", ""),
            l.get("deal_score", 0),
        ))

    buckets: dict[tuple, list] = {}
    for l in new_listings:
        key = _dedup_key(l)
        buckets.setdefault(key, []).append(l)

    deduped = []
    for key, group in buckets.items():
        best = _pick_best(group)
        # Merge price history from all duplicates into the kept listing
        if len(group) > 1:
            all_history = []
            for l in group:
                all_history.extend(l.get("price_history") or [])
            # Deduplicate history entries by (date, old_price, new_price)
            seen_hist = set()
            merged_history = []
            for h in sorted(all_history, key=lambda x: x.get("date", ""), reverse=True):
                hk = (h.get("date"), h.get("old_price"), h.get("new_price"))
                if hk not in seen_hist:
                    seen_hist.add(hk)
                    merged_history.append(h)
            best["price_history"] = merged_history[:10]
            # If any duplicate had a slash_price, keep the highest
            slash_prices = [l.get("slash_price") for l in group if l.get("slash_price")]
            if slash_prices:
                best["slash_price"] = max(slash_prices)
        deduped.append(best)

    new_listings = deduped
    removed_dupes = pre_dedup - len(new_listings)
    if removed_dupes:
        print(f"Dedup: removed {removed_dupes} duplicate listings ({pre_dedup} → {len(new_listings)})")

    # ── Apply community notes ────────────────────────────────────────────────
    for l in new_listings:
        if not l.get("note") and l.get("community") in COMMUNITY_NOTES:
            l["note"] = COMMUNITY_NOTES[l["community"]]

    if skipped_price:
        print(f"Skipped {skipped_price} listings above AED {MAX_PRICE:,} price cap")

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
