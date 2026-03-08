#!/usr/bin/env python3
"""
Dubai Property Distress Deal Finder
Scrapes Bayut & Property Finder for villas/townhouses in target communities
Filters for potential distress deals near launch prices (2-3M AED range)

Usage:
    python dubai_scraper.py
    python dubai_scraper.py --min-price 2000000 --max-price 3000000
    python dubai_scraper.py --community "dubai hills"
"""

import requests
import json
import csv
import re
import time
import argparse
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urlencode, quote
import os

# ============================================================
# CONFIGURATION - EDIT THESE TO MATCH YOUR CRITERIA
# ============================================================

CONFIG = {
    "min_price": 2_000_000,  # AED
    "max_price": 3_000_000,  # AED
    "min_beds": 3,
    "property_types": ["villa", "townhouse"],
    "preferred_features": ["closed kitchen", "maids room", "parking"],
    "target_communities": [
        # Core targets (your shortlist)
        "Dubai Hills Estate",
        "Arabian Ranches",
        "Arabian Ranches 2",
        "Arabian Ranches 3",
        "Jumeirah Village Circle",
        "Jumeirah Village Triangle",
        "DAMAC Hills",
        "DAMAC Hills 2",
        # Within 10-20km of Miracle Garden
        "Villanova",
        "Mudon",
        "Reem",
        "Town Square",
        "Serena",
        "Dubai Sports City",
        "Victory Heights",
        "Motor City",
        "Al Furjan",
        "The Springs",
        "The Meadows",
        "Emirates Living",
        "Arjan",
        "Al Barsha South",
        "Dubailand",
        "Layan",
        "Nad Al Sheba",
        "Tilal Al Ghaf",
        "The Valley",
    ],
    # Launch price benchmarks (AED) - used to score deals
    # These are approximate 2022-2023 launch/early prices for 3-4BR
    "launch_price_benchmarks": {
        "Dubai Hills Estate": {"3BR": 1_900_000, "4BR": 2_800_000},
        "Arabian Ranches 3": {"3BR": 2_200_000, "4BR": 3_200_000},
        "DAMAC Hills": {"3BR": 1_800_000, "4BR": 2_500_000},
        "DAMAC Hills 2": {"3BR": 1_100_000, "4BR": 1_500_000},
        "Villanova": {"3BR": 1_600_000, "4BR": 2_200_000},
        "Mudon": {"3BR": 1_800_000, "4BR": 2_400_000},
        "Town Square": {"3BR": 1_300_000, "4BR": 1_800_000},
        "JVC": {"3BR": 1_500_000, "4BR": 2_200_000},
        "Serena": {"3BR": 1_400_000, "4BR": 1_900_000},
        "The Valley": {"3BR": 1_530_000, "4BR": 2_100_000},
        "Tilal Al Ghaf": {"3BR": 2_500_000, "4BR": 3_500_000},
        "Al Furjan": {"3BR": 2_000_000, "4BR": 2_800_000},
        "Reem": {"3BR": 1_600_000, "4BR": 2_200_000},
        "Motor City": {"3BR": 2_200_000, "4BR": 3_000_000},
        "Dubai Sports City": {"3BR": 1_800_000, "4BR": 2_500_000},
    },
}

# ============================================================
# BAYUT SCRAPER
# ============================================================

BAYUT_BASE = "https://www.bayut.com"
PF_BASE = "https://www.propertyfinder.ae"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


def scrape_bayut(community, min_price, max_price, min_beds=3, page=1):
    """Scrape Bayut listings for a specific community."""
    listings = []
    slug = community.lower().replace(" ", "-")

    # Bayut URL pattern for villas/townhouses
    urls = [
        f"{BAYUT_BASE}/for-sale/villas/dubai/{slug}/?price_min={min_price}&price_max={max_price}&beds_min={min_beds}&page={page}",
        f"{BAYUT_BASE}/for-sale/townhouses/dubai/{slug}/?price_min={min_price}&price_max={max_price}&beds_min={min_beds}&page={page}",
    ]

    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")

            # Find listing cards
            cards = soup.find_all("article") or soup.find_all("li", class_=re.compile(r"listing|property|card", re.I))

            for card in cards:
                listing = parse_bayut_card(card, community)
                if listing:
                    listings.append(listing)

            time.sleep(1.5)  # Rate limiting
        except Exception as e:
            print(f"  [Bayut] Error scraping {community}: {e}")

    return listings


def parse_bayut_card(card, community):
    """Parse a single Bayut listing card."""
    try:
        # Price
        price_el = card.find(string=re.compile(r"AED|[\d,]+")) or card.find(class_=re.compile(r"price", re.I))
        price_text = price_el.get_text() if hasattr(price_el, 'get_text') else str(price_el) if price_el else ""
        price_match = re.search(r"[\d,]+", price_text.replace("AED", "").strip())
        price = int(price_match.group().replace(",", "")) if price_match else 0

        # Title
        title_el = card.find("h2") or card.find(class_=re.compile(r"title", re.I))
        title = title_el.get_text(strip=True) if title_el else "N/A"

        # Link
        link_el = card.find("a", href=True)
        link = BAYUT_BASE + link_el["href"] if link_el and not link_el["href"].startswith("http") else (link_el["href"] if link_el else "")

        # Beds/baths/area
        beds = extract_number(card, r"(\d+)\s*bed")
        baths = extract_number(card, r"(\d+)\s*bath")
        area = extract_number(card, r"([\d,]+)\s*sq")

        if price == 0:
            return None

        return {
            "source": "Bayut",
            "community": community,
            "title": title[:100],
            "price_aed": price,
            "bedrooms": beds,
            "bathrooms": baths,
            "area_sqft": area,
            "link": link,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    except:
        return None


def scrape_propertyfinder(community, min_price, max_price, min_beds=3, page=1):
    """Scrape Property Finder listings."""
    listings = []
    slug = community.lower().replace(" ", "-")

    url = f"{PF_BASE}/en/buy/dubai/{slug}/villas-for-sale.html?bf={min_beds}&pf={min_price}&pt={max_price}&page={page}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return listings
        soup = BeautifulSoup(resp.text, "html.parser")

        cards = soup.find_all("div", class_=re.compile(r"card|listing|property", re.I))

        for card in cards:
            listing = parse_pf_card(card, community)
            if listing:
                listings.append(listing)

        time.sleep(1.5)
    except Exception as e:
        print(f"  [PF] Error scraping {community}: {e}")

    return listings


def parse_pf_card(card, community):
    """Parse a Property Finder listing card."""
    try:
        price_el = card.find(class_=re.compile(r"price", re.I))
        price_text = price_el.get_text() if price_el else ""
        price_match = re.search(r"[\d,]+", price_text.replace("AED", "").strip())
        price = int(price_match.group().replace(",", "")) if price_match else 0

        title_el = card.find("h2") or card.find(class_=re.compile(r"title", re.I))
        title = title_el.get_text(strip=True) if title_el else "N/A"

        link_el = card.find("a", href=True)
        link = PF_BASE + link_el["href"] if link_el and not link_el["href"].startswith("http") else (link_el["href"] if link_el else "")

        beds = extract_number(card, r"(\d+)\s*bed")
        baths = extract_number(card, r"(\d+)\s*bath")
        area = extract_number(card, r"([\d,]+)\s*sq")

        if price == 0:
            return None

        return {
            "source": "PropertyFinder",
            "community": community,
            "title": title[:100],
            "price_aed": price,
            "bedrooms": beds,
            "bathrooms": baths,
            "area_sqft": area,
            "link": link,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    except:
        return None


def extract_number(element, pattern):
    text = element.get_text() if hasattr(element, "get_text") else str(element)
    match = re.search(pattern, text, re.I)
    return int(match.group(1).replace(",", "")) if match else 0


# ============================================================
# DEAL SCORING ENGINE
# ============================================================

def score_deal(listing, config):
    """
    Score a listing as a potential distress deal.
    Higher score = better deal potential.

    Scoring factors:
    - Price vs launch benchmark (0-40 pts)
    - Price per sqft analysis (0-15 pts)
    - Days on market indicator (0-10 pts)
    - Below community average (0-15 pts)
    - Closed kitchen bonus (0-10 pts)
    - Bedroom count bonus (0-10 pts)
    """
    score = 0
    notes = []

    price = listing.get("price_aed", 0)
    beds = listing.get("bedrooms", 0)
    area = listing.get("area_sqft", 0)
    community = listing.get("community", "")
    title = listing.get("title", "").lower()

    if price == 0:
        return 0, "No price data"

    # 1. Price vs Launch Benchmark (biggest weight - 40 pts)
    benchmark_key = f"{beds}BR" if beds >= 3 else "3BR"
    benchmarks = config["launch_price_benchmarks"]

    # Try exact community match, then partial
    launch_price = None
    for bm_community, bm_prices in benchmarks.items():
        if bm_community.lower() in community.lower() or community.lower() in bm_community.lower():
            launch_price = bm_prices.get(benchmark_key, bm_prices.get("3BR"))
            break

    if launch_price:
        price_ratio = price / launch_price
        if price_ratio <= 1.0:
            score += 40
            notes.append(f"AT/BELOW LAUNCH ({price_ratio:.0%} of launch AED {launch_price:,.0f})")
        elif price_ratio <= 1.10:
            score += 30
            notes.append(f"Near launch +{(price_ratio-1)*100:.0f}% (launch AED {launch_price:,.0f})")
        elif price_ratio <= 1.20:
            score += 20
            notes.append(f"+{(price_ratio-1)*100:.0f}% above launch")
        elif price_ratio <= 1.30:
            score += 10
            notes.append(f"+{(price_ratio-1)*100:.0f}% above launch")
        else:
            notes.append(f"+{(price_ratio-1)*100:.0f}% above launch — not distress")

    # 2. Price per sqft (15 pts)
    if area > 0:
        ppsf = price / area
        if ppsf < 800:
            score += 15
            notes.append(f"Excellent PSF: AED {ppsf:.0f}/sqft")
        elif ppsf < 1000:
            score += 10
            notes.append(f"Good PSF: AED {ppsf:.0f}/sqft")
        elif ppsf < 1200:
            score += 5
            notes.append(f"Average PSF: AED {ppsf:.0f}/sqft")

    # 3. Title-based signals (10 pts)
    distress_keywords = ["urgent", "below market", "motivated", "must sell", "distress",
                         "reduced", "price drop", "negotiable", "quick sale", "investor deal",
                         "lowest price", "best deal", "below original", "fire sale"]
    for kw in distress_keywords:
        if kw in title:
            score += 10
            notes.append(f"Distress signal: '{kw}' in listing")
            break

    # 4. Price position in range (15 pts) — lower in budget = better
    budget_position = (price - config["min_price"]) / (config["max_price"] - config["min_price"])
    if budget_position <= 0.3:
        score += 15
        notes.append("Lower third of budget range")
    elif budget_position <= 0.6:
        score += 10
    elif budget_position <= 0.85:
        score += 5

    # 5. Closed kitchen bonus (10 pts)
    if "closed kitchen" in title or "separate kitchen" in title:
        score += 10
        notes.append("Closed kitchen detected")

    # 6. Bedroom bonus (10 pts)
    if beds >= 4:
        score += 10
        notes.append(f"{beds}BR — extra space value")
    elif beds == 3:
        score += 5

    return score, " | ".join(notes) if notes else "Standard listing"


# ============================================================
# MAIN RUNNER
# ============================================================

def run_scraper(config):
    """Run the full scraping and analysis pipeline."""
    all_listings = []
    communities = config["target_communities"]

    print("=" * 70)
    print("  DUBAI DISTRESS DEAL FINDER")
    print(f"  Budget: AED {config['min_price']:,.0f} - {config['max_price']:,.0f}")
    print(f"  Min Beds: {config['min_beds']} | Type: Villas & Townhouses")
    print(f"  Communities: {len(communities)} targets")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    for i, community in enumerate(communities, 1):
        print(f"\n[{i}/{len(communities)}] Scraping: {community}...")

        # Scrape both platforms
        bayut_results = scrape_bayut(community, config["min_price"], config["max_price"], config["min_beds"])
        pf_results = scrape_propertyfinder(community, config["min_price"], config["max_price"], config["min_beds"])

        community_total = len(bayut_results) + len(pf_results)
        print(f"  Found: {len(bayut_results)} (Bayut) + {len(pf_results)} (PF) = {community_total} listings")

        all_listings.extend(bayut_results)
        all_listings.extend(pf_results)

    # Score all listings
    print(f"\n{'=' * 70}")
    print(f"SCORING {len(all_listings)} LISTINGS...")

    for listing in all_listings:
        score, notes = score_deal(listing, config)
        listing["deal_score"] = score
        listing["deal_notes"] = notes

    # Sort by score descending
    all_listings.sort(key=lambda x: x.get("deal_score", 0), reverse=True)

    # Save to CSV
    output_file = f"dubai_deals_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    if all_listings:
        fieldnames = ["deal_score", "deal_notes", "source", "community", "title",
                      "price_aed", "bedrooms", "bathrooms", "area_sqft", "link", "scraped_at"]
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_listings)
        print(f"\nSaved {len(all_listings)} listings to: {output_file}")

    # Print top deals
    print(f"\n{'=' * 70}")
    print("TOP 20 POTENTIAL DISTRESS DEALS:")
    print(f"{'=' * 70}")

    for i, deal in enumerate(all_listings[:20], 1):
        print(f"\n#{i} | Score: {deal['deal_score']}/100 | {deal['community']}")
        print(f"   Price: AED {deal['price_aed']:,.0f} | {deal['bedrooms']}BR | {deal['area_sqft']:,} sqft")
        print(f"   {deal['title'][:80]}")
        print(f"   Notes: {deal['deal_notes']}")
        print(f"   Link: {deal['link']}")

    return all_listings, output_file


def generate_broker_prompt(config):
    """Generate a prompt you can send to brokers/agents to find deals."""
    prompt = f"""
================================================================================
PROPERTY SEARCH BRIEF — DISTRESS/BELOW-MARKET DEALS
Generated: {datetime.now().strftime('%Y-%m-%d')}
================================================================================

BUYER PROFILE:
- Serious buyer, exploring & shortlisting phase
- Looking for: Villas / Townhouses
- Budget: AED {config['min_price']:,.0f} — AED {config['max_price']:,.0f}
- Minimum: {config['min_beds']} Bedrooms
- Must have: Closed kitchen (strong preference)
- Interested in: Distress sales, motivated sellers, below-market prices

TARGET COMMUNITIES (Priority Order):
"""
    for i, c in enumerate(config["target_communities"], 1):
        prompt += f"  {i}. {c}\n"

    prompt += f"""
DEAL CRITERIA — What I'm Looking For:
1. Properties priced at or near original LAUNCH PRICES (2022-2023)
2. Sellers who need to exit quickly (visa issues, relocation, financial pressure)
3. Properties listed significantly below community average (15%+ below)
4. Recently reduced prices (price drops in last 30 days)
5. Properties that have been on market 60+ days (motivated sellers)
6. Off-plan assignments at original or below-original price
7. Developer inventory being cleared at discounts

LAUNCH PRICE BENCHMARKS I'M USING:
"""
    for community, prices in config["launch_price_benchmarks"].items():
        prompt += f"  {community}: 3BR ~AED {prices.get('3BR', 'N/A'):,.0f} | 4BR ~AED {prices.get('4BR', 'N/A'):,.0f}\n"

    prompt += """
RED FLAGS TO AVOID:
- Properties with undisclosed service charges above AED 25/sqft
- Communities with major construction ongoing nearby
- Units facing construction sites or highways
- Properties with title deed issues or pending NOCs

PREFERRED FEATURES:
- Closed kitchen (TOP PRIORITY)
- Maid's room
- Covered parking (2+ spots)
- Corner unit / end unit (for townhouses)
- Upgraded/maintained condition
- Near community center/amenities
- Garden/landscaped area

MARKET CONTEXT (March 2026):
Due to current geopolitical situation (Iran conflict), there may be panic sellers
and motivated exits. I'm specifically looking for these opportunities where the
underlying asset value is strong but the seller needs a quick transaction.

I am NOT a distressed buyer — I am a calculated buyer waiting for the right entry
point. I can move quickly on the right deal.

Please share any listings matching above criteria. For each listing, include:
- Price (and any recent price changes)
- Days on market
- Reason for sale if known
- How price compares to similar sold properties in last 6 months
================================================================================
"""
    return prompt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dubai Distress Deal Finder")
    parser.add_argument("--min-price", type=int, default=CONFIG["min_price"])
    parser.add_argument("--max-price", type=int, default=CONFIG["max_price"])
    parser.add_argument("--min-beds", type=int, default=CONFIG["min_beds"])
    parser.add_argument("--community", type=str, help="Filter to one community")
    parser.add_argument("--broker-prompt", action="store_true", help="Generate broker search brief")
    args = parser.parse_args()

    CONFIG["min_price"] = args.min_price
    CONFIG["max_price"] = args.max_price
    CONFIG["min_beds"] = args.min_beds

    if args.community:
        CONFIG["target_communities"] = [c for c in CONFIG["target_communities"]
                                         if args.community.lower() in c.lower()]

    if args.broker_prompt:
        print(generate_broker_prompt(CONFIG))
    else:
        listings, output_file = run_scraper(CONFIG)

        # Also save the broker prompt
        broker_file = "broker_search_brief.txt"
        with open(broker_file, "w") as f:
            f.write(generate_broker_prompt(CONFIG))
        print(f"\nBroker search brief saved to: {broker_file}")
