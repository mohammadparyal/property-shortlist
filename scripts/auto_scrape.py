#!/usr/bin/env python3
"""
AUTO SCRAPE — Fully automated Property Finder + Bayut scraper using Playwright.

Usage:
    python scripts/auto_scrape.py                  # Full scan (headless)
    python scripts/auto_scrape.py --pf-only        # Property Finder only
    python scripts/auto_scrape.py --bayut-only     # Bayut only
    python scripts/auto_scrape.py --dry-run        # Scrape but don't publish
    python scripts/auto_scrape.py --visible        # Open real browser (solve CAPTCHA manually)
    python scripts/auto_scrape.py --visible --bayut-only   # Best for first Bayut run

How --visible works:
    1. Opens a real Chrome window you can see and interact with
    2. If Bayut shows CAPTCHA, solve it manually — script waits for you
    3. After solving, cookies are saved to scripts/cookies_bayut.json
    4. Next runs (even headless) reuse those cookies — no CAPTCHA needed
    5. Cookies last 24-48 hours, then solve once more

Requires:
    pip install playwright playwright-stealth
    playwright install chromium
"""

import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime

# ─── PATHS ───────────────────────────────────────────────────────────────────
BASE       = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW_PATH   = os.path.join(BASE, "raw_data.json")
SCRIPTS    = os.path.join(BASE, "scripts")
LOG_DIR    = os.path.join(BASE, "logs")
COOKIES_PF    = os.path.join(SCRIPTS, "cookies_pf.json")
COOKIES_BAYUT = os.path.join(SCRIPTS, "cookies_bayut.json")
TODAY      = datetime.now().strftime("%Y-%m-%d")

os.makedirs(LOG_DIR, exist_ok=True)

# ─── COMMUNITY CONFIGS ──────────────────────────────────────────────────────
# PF: (community_name, location_id, min_beds, price_min, price_max)
PF_COMMUNITIES = [
    ("DAMAC Lagoons",       11559, 3, 2_000_000, 3_000_000),
    ("DAMAC Islands",       14611, 4, 2_000_000, 3_000_000),
    ("The Valley",          10757, 3, 2_000_000, 3_000_000),
    ("DAMAC Hills 2",       125,   3, 1_000_000, 2_000_000),
    ("DAMAC Hills 2",       125,   3, 2_000_000, 3_000_000),
    ("Villanova",           8780,  3, 2_000_000, 3_000_000),
    ("DAMAC Hills",         129,   3, 2_000_000, 3_000_000),
    ("Dubai Hills Estate",  105,   3, 2_000_000, 3_000_000),
    ("Tilal Al Ghaf",       9885,  3, 2_000_000, 3_000_000),
]

# Bayut: (community_name, url)
BAYUT_COMMUNITIES = [
    ("DAMAC Lagoons",      "https://www.bayut.com/for-sale/townhouses/dubai/damac-lagoons/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("DAMAC Islands",      "https://www.bayut.com/for-sale/townhouses/dubai/dubailand/damac-islands/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("The Valley",         "https://www.bayut.com/for-sale/townhouses/dubai/the-valley-by-emaar/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("DAMAC Hills 2",      "https://www.bayut.com/for-sale/townhouses/dubai/damac-hills-2-akoya-by-damac/?sort=price_asc&beds_min=3&price_min=1000000&price_max=2000000"),
    ("DAMAC Hills 2",      "https://www.bayut.com/for-sale/townhouses/dubai/damac-hills-2-akoya-by-damac/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("Villanova",          "https://www.bayut.com/for-sale/townhouses/dubai/dubailand/villanova/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("DAMAC Hills",        "https://www.bayut.com/for-sale/townhouses/dubai/damac-hills/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("Dubai Hills Estate", "https://www.bayut.com/for-sale/townhouses/dubai/dubai-hills-estate/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("Tilal Al Ghaf",      "https://www.bayut.com/for-sale/townhouses/dubai/tilal-al-ghaf/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ─── RAW DATA ACCUMULATOR ───────────────────────────────────────────────────
def load_raw():
    if os.path.exists(RAW_PATH):
        with open(RAW_PATH) as f:
            return json.load(f)
    return {"communities": {}, "total_listings": 0, "last_updated": TODAY}


def save_raw(data):
    data["last_updated"] = TODAY
    total = sum(len(v) for v in data["communities"].values())
    data["total_listings"] = total
    with open(RAW_PATH, "w") as f:
        json.dump(data, f, indent=2)
    log(f"raw_data.json saved — {total} total listings across {len(data['communities'])} communities")


def append_community(raw_data, community, listings):
    """Merge listings into raw_data under the community key (dedupe by uid)."""
    existing = raw_data["communities"].get(community, [])
    seen = {l["uid"] for l in existing}
    added = 0
    for l in listings:
        if l["uid"] not in seen:
            existing.append(l)
            seen.add(l["uid"])
            added += 1
        else:
            # Update existing with new data
            for i, ex in enumerate(existing):
                if ex["uid"] == l["uid"]:
                    existing[i] = l
                    break
    raw_data["communities"][community] = existing
    log(f"  {community}: {len(listings)} scraped, {added} new, {len(existing)} total")


# ─── PROPERTY FINDER SCRAPER ────────────────────────────────────────────────
PF_EXTRACT_JS = """
() => {
    var el = document.getElementById('__NEXT_DATA__');
    if (!el) return {error: 'No __NEXT_DATA__ found', keys: []};

    var data = JSON.parse(el.textContent);
    var pp = data && data.props && data.props.pageProps;
    var searchResult = pp && pp.searchResult;
    if (!searchResult) {
        return {error: 'No searchResult in pageProps', keys: Object.keys(pp || {})};
    }

    var properties = searchResult.properties || searchResult.listings || [];
    var results = [];

    for (var i = 0; i < properties.length; i++) {
        var p = properties[i];
        var type = (p.property_type || p.type || '').toLowerCase();
        if (type.indexOf('townhouse') === -1 && type.indexOf('villa') === -1) continue;

        // Price can be object {value, currency} or flat number
        var priceRaw = p.price || 0;
        var price = (typeof priceRaw === 'object') ? (priceRaw.value || 0) : priceRaw;
        if (price <= 0) continue;

        var bedsRaw = p.bedrooms || p.beds || 0;
        var beds = (typeof bedsRaw === 'string') ? parseInt(bedsRaw) : bedsRaw;
        if (beds < 3) continue;

        var bathsRaw = p.bathrooms || p.baths || 0;
        var baths = (typeof bathsRaw === 'string') ? parseInt(bathsRaw) : bathsRaw;
        var sizeVal = p.size || p.area || 0;
        var sqft = (typeof sizeVal === 'object') ? (sizeVal.value || 0) : (sizeVal || 0);
        sqft = sqft > 0 ? Math.round(sqft * 10.764) : 0;
        var ref    = (p.reference || p.ref || '') + '';
        var title  = p.title || p.name || '';
        var listed = (p.listed_date || p.added_on || '').slice(0, 10);

        // Location tree: array of {name, slug, ...} or object
        var cluster = '';
        var locTree = p.location_tree || p.location || [];
        if (Array.isArray(locTree) && locTree.length >= 3) {
            cluster = locTree[locTree.length - 1].name || locTree[locTree.length - 1] || '';
        } else if (locTree && locTree.name) {
            cluster = locTree.name;
        }

        var href = p.share_url || p.details_path || ('https://www.propertyfinder.ae/en/plp/' + ref);
        if (href && href.indexOf('http') !== 0) href = 'https://www.propertyfinder.ae' + href;

        var isOffPlan = (p.offering_type || '').toLowerCase().indexOf('off') >= 0 ||
                        (p.completion_status || '').toLowerCase().indexOf('off') >= 0 ||
                        title.toLowerCase().indexOf('off plan') >= 0 ||
                        title.toLowerCase().indexOf('off-plan') >= 0;

        results.push({
            uid:       'pf-' + ref,
            href:      href,
            price:     price,
            beds:      beds,
            baths:     baths,
            sqft:      sqft,
            cluster:   cluster,
            title:     title,
            community: '__COMMUNITY__',
            source:    'PropertyFinder',
            listed:    listed,
            isOffPlan: isOffPlan
        });
    }
    return {listings: results, total: properties.length, filtered: results.length};
}
"""

async def human_scroll(page):
    """Scroll page like a human — gradual, with random pauses."""
    height = await page.evaluate("document.body.scrollHeight")
    current = 0
    while current < height:
        step = random.randint(200, 500)
        current = min(current + step, height)
        await page.evaluate(f"window.scrollTo(0, {current})")
        await asyncio.sleep(random.uniform(0.3, 0.8))
    # Scroll back to top
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(random.uniform(0.5, 1))


async def human_mouse(page):
    """Move mouse randomly to simulate human presence."""
    for _ in range(random.randint(2, 5)):
        x = random.randint(100, 1200)
        y = random.randint(100, 600)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.2, 0.6))


async def warmup_visit(page, domain):
    """Visit homepage first to build cookies like a real user."""
    log(f"  Warming up on {domain}...")
    try:
        await page.goto(f"https://www.{domain}/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(random.uniform(2, 4))
        await human_mouse(page)
        await human_scroll(page)
        await asyncio.sleep(random.uniform(1, 3))
        title = await page.title()
        if "just a moment" in title.lower():
            log(f"  ⚠ CF challenge on warmup, waiting...")
            await asyncio.sleep(10)
        log(f"  Warmup done: {title[:50]}")
    except Exception as e:
        log(f"  Warmup failed (non-fatal): {e}")


async def scrape_pf(page, community, location_id, beds, price_min, price_max):
    """Scrape Property Finder for one community/price range."""
    url = f"https://www.propertyfinder.ae/en/search?l={location_id}&c=1&bdr%5B%5D={beds}&pf={price_min}&pt={price_max}&ob=pr"
    log(f"  PF: {community}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(3, 6))

        # Check for Cloudflare challenge
        title = await page.title()
        if "just a moment" in title.lower() or "challenge" in title.lower():
            log(f"  ⚠ Cloudflare challenge detected, waiting 12s...")
            await asyncio.sleep(12)
            await page.wait_for_load_state("networkidle", timeout=15000)

        # Human-like behavior before extraction
        await human_mouse(page)
        await asyncio.sleep(random.uniform(0.5, 1.5))

        # Extract __NEXT_DATA__
        js = PF_EXTRACT_JS.replace("'__COMMUNITY__'", f"'{community}'")
        result = await page.evaluate(js)

        if "error" in result:
            log(f"  ✗ PF {community}: {result['error']} (keys: {result.get('keys', [])})")
            return []

        listings = result.get("listings", [])
        log(f"  ✓ PF {community}: {result['filtered']} listings (of {result['total']} total properties)")
        return listings

    except Exception as e:
        log(f"  ✗ PF {community} ERROR: {e}")
        return []


# ─── BAYUT SCRAPER ───────────────────────────────────────────────────────────
BAYUT_EXTRACT_JS = """
() => {
    const articles = document.querySelectorAll('article');
    const results = [];

    for (const art of articles) {
        const link = art.querySelector('a[href*="details-"]');
        if (!link) continue;

        const href = link.href || ('https://www.bayut.com' + link.getAttribute('href'));
        const idMatch = href.match(/details-(\\d+)/);
        if (!idMatch) continue;

        const uid = 'bayut-' + idMatch[1];
        const lines = art.innerText.split('\\n').map(l => l.trim()).filter(l => l && l !== '|');

        // Price: "AED" is on its own line, value is on the next line
        const aedIdx = lines.indexOf('AED');
        const price = aedIdx >= 0 ? parseInt((lines[aedIdx + 1] || '').replace(/\\D/g, '')) || 0 : 0;
        if (price <= 0) continue;

        // Beds/baths: line after "Townhouse" or "Villa"
        const thIdx = lines.findIndex(l => l === 'Townhouse' || l === 'Villa');
        const beds  = thIdx >= 0 ? parseInt(lines[thIdx + 1]) || 0 : 0;
        const baths = thIdx >= 0 ? parseInt(lines[thIdx + 2]) || 0 : 0;
        if (beds < 3) continue;

        // Sqft: line ending in "sqft"
        const sqftLine = lines.find(l => l.endsWith('sqft'));
        const sqft = sqftLine ? parseInt(sqftLine.replace(/\\D/g, '')) : 0;

        // Title and cluster: relative to "Area:" marker
        const areaIdx = lines.indexOf('Area:');
        const title   = areaIdx >= 0 ? (lines[areaIdx + 2] || '') : '';
        const locLine = areaIdx >= 0 ? (lines[areaIdx + 3] || '') : '';
        const cluster = locLine.split(',')[0].trim();

        // Off-plan
        const isOffPlan = lines.some(l => l === 'Off-Plan') ||
                          art.innerText.toLowerCase().includes('off plan') ||
                          art.innerText.toLowerCase().includes('off-plan');

        results.push({
            uid, href,
            price, beds, baths, sqft, cluster, title,
            community: '__COMMUNITY__',
            source: 'Bayut', listed: '', isOffPlan
        });
    }
    return {listings: results, articlesFound: articles.length};
}
"""

async def check_blocked(page):
    """Check if the page is showing a CAPTCHA or block page."""
    title = (await page.title()).lower()
    blocked_keywords = ["captcha", "just a moment", "blocked", "security", "access denied", "verify"]
    return any(kw in title for kw in blocked_keywords)


async def wait_for_human_captcha(page, visible_mode, timeout=120):
    """If in visible mode, wait for the user to solve CAPTCHA manually."""
    if not visible_mode:
        return False

    if not await check_blocked(page):
        return True  # Not blocked, good to go

    log("  🔒 CAPTCHA detected! Solve it in the browser window...")
    log(f"  ⏳ Waiting up to {timeout}s for you to solve it...")

    start = time.time()
    while time.time() - start < timeout:
        await asyncio.sleep(2)
        if not await check_blocked(page):
            log("  ✅ CAPTCHA solved! Continuing...")
            return True

    log("  ✗ Timed out waiting for CAPTCHA solve")
    return False


async def scrape_bayut(page, community, url, visible_mode=False, retries=2):
    """Scrape Bayut for one community URL with human-like behavior."""
    log(f"  Bayut: {community}")

    for attempt in range(retries + 1):
        try:
            if attempt > 0:
                log(f"  Retry {attempt}/{retries} for {community}...")
                await asyncio.sleep(random.uniform(15, 25))

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(4, 8))

            # Check for bot detection
            if await check_blocked(page):
                if visible_mode:
                    # In visible mode, let user solve CAPTCHA
                    solved = await wait_for_human_captcha(page, visible_mode)
                    if not solved:
                        log(f"  ✗ Bayut {community}: CAPTCHA not solved")
                        return []
                else:
                    title = await page.title()
                    log(f"  ⚠ Bot detection: '{title}' — waiting 20s...")
                    await asyncio.sleep(20)
                    if await check_blocked(page):
                        if attempt < retries:
                            continue
                        log(f"  ✗ Bayut {community}: blocked after {retries} retries (try --visible mode)")
                        return []

            # Human-like mouse movements
            await human_mouse(page)
            await asyncio.sleep(random.uniform(1, 2))

            # Scroll gradually like a human browsing listings
            await human_scroll(page)
            await asyncio.sleep(random.uniform(1, 2))

            # One more mouse move after scrolling
            await human_mouse(page)
            await asyncio.sleep(random.uniform(0.5, 1))

            # Extract listings
            js = BAYUT_EXTRACT_JS.replace("'__COMMUNITY__'", f"'{community}'")
            result = await page.evaluate(js)

            listings = result.get("listings", [])
            log(f"  ✓ Bayut {community}: {len(listings)} listings (from {result['articlesFound']} articles)")
            return listings

        except Exception as e:
            log(f"  ✗ Bayut {community} ERROR (attempt {attempt+1}): {e}")
            if attempt >= retries:
                return []

    return []


# ─── BAYUT DATE ENRICHMENT ──────────────────────────────────────────────────
BAYUT_DATE_JS = """
async (urls) => {
    const results = {};
    for (const url of urls) {
        try {
            const resp = await fetch(url);
            const html = await resp.text();
            const match = html.match(/"datePosted"\\s*:\\s*"([^"]+)"/);
            if (match) {
                results[url] = match[1].slice(0, 10);
            }
        } catch (e) {
            // skip failed fetches
        }
        // Small delay between fetches
        await new Promise(r => setTimeout(r, 500));
    }
    return results;
}
"""

async def enrich_bayut_dates(context, listings):
    """Fetch detail pages to get datePosted for Bayut listings.

    Uses a dedicated page so navigations/errors don't destroy the main scrape context.
    Automatically re-navigates back to bayut.com if the context gets wiped between batches.
    """
    to_enrich = [l for l in listings if l.get("source") == "Bayut" and l.get("href") and not l.get("listed")]
    if not to_enrich:
        return

    log(f"  Enriching dates for {len(to_enrich)} Bayut listings...")

    # Dedicated page — isolated from the main scraping page
    date_page = await context.new_page()
    try:
        await date_page.goto("https://www.bayut.com/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(random.uniform(1, 2))
    except Exception as e:
        log(f"  Date page warmup failed (non-fatal): {e}")

    all_dates = {}
    batch_size = 10
    urls = [l["href"] for l in to_enrich]

    for i in range(0, len(urls), batch_size):
        batch = urls[i:i+batch_size]
        batch_num = i // batch_size + 1
        try:
            # Re-anchor to bayut.com if we drifted (e.g. after a redirect)
            if "bayut.com" not in date_page.url:
                await date_page.goto("https://www.bayut.com/", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1)
            dates = await date_page.evaluate(BAYUT_DATE_JS, batch)
            all_dates.update(dates)
            log(f"    Batch {batch_num}: got {len(dates)} dates")
            await asyncio.sleep(random.uniform(1, 2))
        except Exception as e:
            log(f"    Batch {batch_num} error: {e} — re-anchoring...")
            try:
                await date_page.goto("https://www.bayut.com/", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                # Retry the batch once after re-anchoring
                dates = await date_page.evaluate(BAYUT_DATE_JS, batch)
                all_dates.update(dates)
                log(f"    Batch {batch_num} retry: got {len(dates)} dates")
                await asyncio.sleep(random.uniform(1, 2))
            except Exception as e2:
                log(f"    Batch {batch_num} retry failed: {e2}")

    await date_page.close()

    # Apply dates back to the original listing dicts
    applied = 0
    for l in to_enrich:
        if l.get("href") in all_dates:
            l["listed"] = all_dates[l["href"]]
            applied += 1

    log(f"  ✓ Applied dates to {applied}/{len(to_enrich)} Bayut listings")


# ─── MAIN ────────────────────────────────────────────────────────────────────
async def main():
    args        = set(sys.argv[1:])
    pf_only     = "--pf-only" in args
    bayut_only  = "--bayut-only" in args
    dry_run     = "--dry-run" in args
    visible     = "--visible" in args

    log(f"═══ Dubai Auto Scraper — {TODAY} ═══")
    mode_str = "PF only" if pf_only else "Bayut only" if bayut_only else "Full scan"
    log(f"Mode: {mode_str}{' (VISIBLE)' if visible else ''}{' (DRY RUN)' if dry_run else ''}")

    # Import here so missing packages give clear error
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    stealth = Stealth()

    raw_data = load_raw()
    start_time = time.time()

    async with async_playwright() as pw:
        # Visible mode: open real browser window. Headless mode: invisible
        use_headless = not visible
        browser = await pw.chromium.launch(
            headless=use_headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        # Load saved cookies if they exist (from a previous --visible session)
        cookie_file = COOKIES_BAYUT if bayut_only else COOKIES_PF
        storage_state = None
        if not bayut_only and os.path.exists(COOKIES_PF):
            storage_state = COOKIES_PF
            log(f"Loading saved PF cookies from {COOKIES_PF}")
        elif bayut_only and os.path.exists(COOKIES_BAYUT):
            storage_state = COOKIES_BAYUT
            log(f"Loading saved Bayut cookies from {COOKIES_BAYUT}")

        context_opts = dict(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="Asia/Dubai",
        )
        if storage_state:
            context_opts["storage_state"] = storage_state

        context = await browser.new_context(**context_opts)

        page = await context.new_page()
        await stealth.apply_stealth_async(page)

        # ── Property Finder ──────────────────────────────────────────────
        if not bayut_only:
            log("\n── Property Finder ──")
            # Warmup: visit PF homepage first to build cookies
            await warmup_visit(page, "propertyfinder.ae")

            pf_total = 0
            for idx, (community, loc_id, beds, pmin, pmax) in enumerate(PF_COMMUNITIES):
                listings = await scrape_pf(page, community, loc_id, beds, pmin, pmax)
                if listings:
                    append_community(raw_data, community, listings)
                    pf_total += len(listings)
                # Longer random delay between communities (human-like browsing pace)
                delay = random.uniform(5, 10)
                log(f"  Waiting {delay:.0f}s before next...")
                await asyncio.sleep(delay)
            log(f"PF done: {pf_total} listings scraped")

        # ── Bayut ────────────────────────────────────────────────────────
        if not pf_only:
            log("\n── Bayut ──")
            # Warmup: visit Bayut homepage first
            await warmup_visit(page, "bayut.com")

            # In visible mode, if warmup hit CAPTCHA, let user solve it
            if visible and await check_blocked(page):
                solved = await wait_for_human_captcha(page, visible)
                if not solved:
                    log("  ✗ Could not get past Bayut CAPTCHA — skipping Bayut")

            bayut_total = 0
            all_bayut = []
            bayut_blocked = False
            for idx, (community, url) in enumerate(BAYUT_COMMUNITIES):
                if bayut_blocked:
                    break
                listings = await scrape_bayut(page, community, url, visible_mode=visible)
                if listings:
                    all_bayut.extend(listings)
                    append_community(raw_data, community, listings)
                    bayut_total += len(listings)
                elif not visible and await check_blocked(page):
                    # In headless mode, if blocked, stop trying remaining communities
                    log("  ⚠ Bayut is blocking us — stopping Bayut scraping")
                    log("  💡 Tip: run with --visible to solve CAPTCHA manually")
                    bayut_blocked = True
                # Longer delays for Bayut (more aggressive bot detection)
                delay = random.uniform(8, 15)
                log(f"  Waiting {delay:.0f}s before next...")
                await asyncio.sleep(delay)

            # Enrich Bayut listings with dates from detail pages
            if all_bayut:
                await enrich_bayut_dates(context, all_bayut)
                # Re-save with dates
                for community in set(l["community"] for l in all_bayut):
                    comm_listings = [l for l in all_bayut if l["community"] == community]
                    if comm_listings:
                        append_community(raw_data, community, comm_listings)

            log(f"Bayut done: {bayut_total} listings scraped")

        # ── Save cookies for future runs ─────────────────────────────────
        if not pf_only:
            await context.storage_state(path=COOKIES_BAYUT)
            log(f"Cookies saved → {COOKIES_BAYUT}")
        if not bayut_only:
            await context.storage_state(path=COOKIES_PF)
            log(f"Cookies saved → {COOKIES_PF}")

        await browser.close()

    # ── Save raw data ────────────────────────────────────────────────────
    save_raw(raw_data)

    elapsed = time.time() - start_time
    log(f"\nScraping complete in {elapsed:.0f}s ({elapsed/60:.1f} min)")

    # ── Run process_deals.py ─────────────────────────────────────────────
    if not dry_run:
        log("\n── Running process_deals.py ──")
        process_script = os.path.join(SCRIPTS, "process_deals.py")
        if os.path.exists(process_script):
            result = subprocess.run(
                [sys.executable, process_script],
                capture_output=True, text=True, cwd=BASE
            )
            print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)
            if result.returncode == 0:
                log("✓ process_deals.py completed successfully")
            else:
                log(f"✗ process_deals.py failed (exit code {result.returncode})")
        else:
            log(f"✗ process_deals.py not found at {process_script}")
    else:
        log("\nDRY RUN — skipping process_deals.py (raw_data.json saved for inspection)")

    total_listings = sum(len(v) for v in raw_data["communities"].values())
    log(f"\n═══ SUMMARY ═══")
    log(f"Communities: {len(raw_data['communities'])}")
    log(f"Total listings: {total_listings}")
    log(f"Time: {elapsed:.0f}s")
    log(f"Output: {RAW_PATH}")
    if not dry_run:
        log(f"Published: dubai_deals.json + index.html")


if __name__ == "__main__":
    asyncio.run(main())
