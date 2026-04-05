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
    ("DAMAC Islands 2",     17773, 4, 2_000_000, 3_000_000),
    ("The Valley",          10757, 3, 2_000_000, 3_000_000),
    ("DAMAC Hills 2",       125,   3, 1_000_000, 2_000_000),
    ("DAMAC Hills 2",       125,   3, 2_000_000, 3_000_000),
    ("Villanova",           8780,  3, 2_000_000, 3_000_000),
    ("DAMAC Hills",         129,   3, 2_000_000, 3_000_000),
    ("Dubai Hills Estate",  105,   3, 2_000_000, 3_000_000),
    ("Tilal Al Ghaf",       9885,  3, 2_000_000, 3_000_000),
    # ── New communities (Dubai South & developer) ──
    ("Emaar South",         "https://www.propertyfinder.ae/en/buy/dubai/townhouses-for-sale-dubai-south-dubai-world-central-emaar-south.html?beds_min=3&price_min=1500000&price_max=3000000&sort=pa", 3, 1_500_000, 3_000_000),
    ("Arabian Ranches 3",   10393, 3, 2_000_000, 3_000_000),
    ("Town Square",         131,   3, 1_000_000, 2_000_000),
    ("Town Square",         131,   3, 2_000_000, 3_000_000),
    ("Mudon",               8250,  3, 1_500_000, 3_000_000),
    # ── Batch 3: Al Barsha South 2, JVC, JVT ──
    ("Al Barsha South 2",   "https://www.propertyfinder.ae/en/buy/dubai/townhouses-for-sale-al-barsha-al-barsha-south-al-barsha-south-second.html?beds_min=3&price_min=1500000&price_max=3000000&sort=pa", 3, 1_500_000, 3_000_000),
    ("JVC",                 "https://www.propertyfinder.ae/en/buy/dubai/villas-townhouses-for-sale-jumeirah-village-circle.html?beds_min=3&price_min=1000000&price_max=3000000&sort=pa", 3, 1_000_000, 3_000_000),
    ("JVT",                 "https://www.propertyfinder.ae/en/buy/dubai/villas-townhouses-for-sale-jumeirah-village-triangle.html?beds_min=3&price_min=1000000&price_max=3000000&sort=pa", 3, 1_000_000, 3_000_000),
]

# Bayut: (community_name, url)
BAYUT_COMMUNITIES = [
    ("DAMAC Lagoons",      "https://www.bayut.com/for-sale/townhouses/dubai/damac-lagoons/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("DAMAC Islands",      "https://www.bayut.com/for-sale/townhouses/dubai/dubailand/damac-islands/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("DAMAC Islands 2",    "https://www.bayut.com/for-sale/townhouses/dubai/damac-islands-2/?sort=price_asc&beds_min=4&price_min=2000000&price_max=3000000"),
    ("The Valley",         "https://www.bayut.com/for-sale/townhouses/dubai/the-valley-by-emaar/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("DAMAC Hills 2",      "https://www.bayut.com/for-sale/townhouses/dubai/damac-hills-2-akoya-by-damac/?sort=price_asc&beds_min=3&price_min=1000000&price_max=2000000"),
    ("DAMAC Hills 2",      "https://www.bayut.com/for-sale/townhouses/dubai/damac-hills-2-akoya-by-damac/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("Villanova",          "https://www.bayut.com/for-sale/townhouses/dubai/dubailand/villanova/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("DAMAC Hills",        "https://www.bayut.com/for-sale/townhouses/dubai/damac-hills/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("Dubai Hills Estate", "https://www.bayut.com/for-sale/townhouses/dubai/dubai-hills-estate/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("Tilal Al Ghaf",      "https://www.bayut.com/for-sale/townhouses/dubai/tilal-al-ghaf/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    # ── New communities (Dubai South & developer) ──
    ("Emaar South",        "https://www.bayut.com/for-sale/townhouses/dubai/dubai-south/emaar-south/?sort=price_asc&beds_min=3&price_min=1500000&price_max=3000000"),
    ("Arabian Ranches 3",  "https://www.bayut.com/for-sale/townhouses/dubai/arabian-ranches-3/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("Town Square",        "https://www.bayut.com/for-sale/townhouses/dubai/town-square/?sort=price_asc&beds_min=3&price_min=1000000&price_max=2000000"),
    ("Town Square",        "https://www.bayut.com/for-sale/townhouses/dubai/town-square/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000"),
    ("Mudon",              "https://www.bayut.com/for-sale/townhouses/dubai/mudon/?sort=price_asc&beds_min=3&price_min=1500000&price_max=3000000"),
    # ── Batch 3: Al Barsha South 2, JVC, JVT ──
    ("Al Barsha South 2",  "https://www.bayut.com/for-sale/townhouses/dubai/al-barsha/al-barsha-south/al-barsha-south-2/?sort=price_asc&beds_min=3&price_min=1500000&price_max=3000000"),
    ("JVC",                "https://www.bayut.com/for-sale/villas/dubai/jumeirah-village-circle/?sort=price_asc&beds_min=3&price_min=1000000&price_max=3000000"),
    ("JVC",                "https://www.bayut.com/for-sale/townhouses/dubai/jumeirah-village-circle/?sort=price_asc&beds_min=3&price_min=1000000&price_max=3000000"),
    ("JVT",                "https://www.bayut.com/for-sale/villas/dubai/jumeirah-village-triangle/?sort=price_asc&beds_min=3&price_min=1000000&price_max=3000000"),
    ("JVT",                "https://www.bayut.com/for-sale/townhouses/dubai/jumeirah-village-triangle/?sort=price_asc&beds_min=3&price_min=1000000&price_max=3000000"),
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


async def check_pf_captcha(page):
    """Check if PF is showing a CAPTCHA / human verification page.
    PF uses Cloudflare Turnstile which shows:
      'Let's confirm you are human' + 'Begin >' button
    Returns True if CAPTCHA is detected."""
    # Check page body text for the known challenge phrases
    is_challenge = await page.evaluate("""
        (() => {
            const text = (document.body && document.body.innerText) || '';
            const lc = text.toLowerCase();
            return lc.includes('confirm you are human') ||
                   lc.includes('complete the security check') ||
                   lc.includes('verify you are human') ||
                   lc.includes("let's confirm you are human") ||
                   lc.includes('one more step') ||
                   !!(document.querySelector('iframe[src*="captcha"]') ||
                      document.querySelector('iframe[src*="challenge"]') ||
                      document.querySelector('iframe[src*="turnstile"]'));
        })()
    """)
    if is_challenge:
        return True
    # Fallback: check page title
    title = (await page.title()).lower()
    if any(kw in title for kw in ["just a moment", "challenge", "captcha", "verify", "security"]):
        return True
    # Final check: no __NEXT_DATA__ on a PF page usually means blocked
    has_data = await page.evaluate("!!document.getElementById('__NEXT_DATA__')")
    return not has_data


# Signal file for CAPTCHA pause/resume between scraper and control panel
CAPTCHA_SIGNAL = os.path.join(BASE, ".captcha_signal")


def write_captcha_signal(status, community=""):
    """Write CAPTCHA signal file for server to read."""
    with open(CAPTCHA_SIGNAL, "w") as f:
        json.dump({"status": status, "community": community, "time": time.time()}, f)


def read_captcha_signal():
    """Read CAPTCHA signal file (written by server when user clicks Continue)."""
    try:
        if os.path.exists(CAPTCHA_SIGNAL):
            with open(CAPTCHA_SIGNAL) as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return None


def clear_captcha_signal():
    """Remove the signal file."""
    try:
        if os.path.exists(CAPTCHA_SIGNAL):
            os.remove(CAPTCHA_SIGNAL)
    except OSError:
        pass


async def wait_for_pf_captcha(page, visible_mode, community="", timeout=600):
    """Wait for user to solve PF CAPTCHA in visible mode.
    Pauses scraper until either:
      1. __NEXT_DATA__ appears on the page (CAPTCHA auto-detected as solved)
      2. User clicks 'Continue' in the control panel (signal file)
    Timeout is 10 minutes — plenty of time to solve."""
    if not visible_mode:
        return False

    log(f"  🔒 CAPTCHA:WAITING:{community}")
    log("  👉 Solve the CAPTCHA in the browser window, then click Continue in the control panel")
    log(f"  ⏳ Scraper PAUSED — waiting for you (up to {timeout//60} min)...")

    # Write signal file so server knows we're waiting
    write_captcha_signal("waiting", community)

    start = time.time()
    while time.time() - start < timeout:
        await asyncio.sleep(2)

        # Check 1: Did __NEXT_DATA__ appear? (CAPTCHA solved + page loaded)
        try:
            has_data = await page.evaluate("!!document.getElementById('__NEXT_DATA__')")
            if has_data:
                log("  ✅ CAPTCHA solved! PF page loaded. Continuing...")
                clear_captcha_signal()
                return True
        except Exception:
            pass  # Page might be navigating

        # Check 2: Did user click Continue in the control panel?
        sig = read_captcha_signal()
        if sig and sig.get("status") == "continue":
            log("  ▶ Continue signal received from control panel")
            clear_captcha_signal()
            # Wait a moment for page to settle after CAPTCHA solve
            await asyncio.sleep(2)
            # Check if page is now ready
            try:
                has_data = await page.evaluate("!!document.getElementById('__NEXT_DATA__')")
                if has_data:
                    log("  ✅ Page loaded after CAPTCHA. Continuing...")
                    return True
                else:
                    # CAPTCHA might have been solved but page needs reload
                    log("  ↻ Page not ready yet — reloading...")
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(random.uniform(3, 5))
                    has_data = await page.evaluate("!!document.getElementById('__NEXT_DATA__')")
                    if has_data:
                        log("  ✅ Page loaded after reload. Continuing...")
                        return True
                    else:
                        log("  ⚠ Still no data after reload — CAPTCHA may not be solved yet")
                        write_captcha_signal("waiting", community)
                        continue
            except Exception as e:
                log(f"  ⚠ Error checking page: {e}")
                continue

        # Check 3: Did the page title change? (might have navigated past CAPTCHA)
        try:
            title = (await page.title()).lower()
            if "property" in title or "search" in title or "finder" in title:
                await asyncio.sleep(1)
                has_data = await page.evaluate("!!document.getElementById('__NEXT_DATA__')")
                if has_data:
                    log("  ✅ CAPTCHA solved! PF page loaded. Continuing...")
                    clear_captcha_signal()
                    return True
        except Exception:
            pass

    log("  ✗ Timed out waiting for CAPTCHA solve (10 min)")
    clear_captcha_signal()
    return False


async def scrape_pf(page, community, location_id, beds, price_min, price_max, visible_mode=False, max_retries=3):
    """Scrape Property Finder for one community/price range.
    When CAPTCHA / human verification is detected:
      - In visible mode: waits for you to solve it in the browser
      - In headless mode: retries with fresh navigation"""
    if isinstance(location_id, str) and location_id.startswith("http"):
        url = location_id  # Direct URL override (for communities without numeric ID)
    else:
        url = f"https://www.propertyfinder.ae/en/search?l={location_id}&c=1&bdr%5B%5D={beds}&pf={price_min}&pt={price_max}&ob=pr"
    log(f"  PF: {community}")

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0 and not visible_mode:
                log(f"  ↻ PF {community}: Retry {attempt}/{max_retries} — navigating fresh...")
                try:
                    await page.goto("https://www.propertyfinder.ae/en", wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(random.uniform(3, 6))
                    await human_mouse(page)
                    await human_scroll(page)
                    await asyncio.sleep(random.uniform(2, 4))
                except Exception:
                    pass

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(3, 6))

            # Check for Cloudflare or PF CAPTCHA
            if await check_pf_captcha(page):
                if visible_mode:
                    # Wait for user to solve CAPTCHA in the browser
                    solved = await wait_for_pf_captcha(page, visible_mode, community)
                    if solved:
                        # Page should now have __NEXT_DATA__ — extract it
                        await asyncio.sleep(random.uniform(1, 2))
                    else:
                        # User didn't solve in time
                        if attempt < max_retries:
                            log(f"  ⚠ PF {community}: CAPTCHA not solved, will retry...")
                            continue
                        log(f"  ✗ PF {community}: CAPTCHA not solved after {max_retries} attempts")
                        return []
                else:
                    # Headless mode — just wait and hope
                    log(f"  ⚠ PF {community}: CAPTCHA detected (headless), waiting 15s...")
                    await asyncio.sleep(15)
                    if attempt < max_retries:
                        continue
                    log(f"  ✗ PF {community}: CAPTCHA in headless mode. Try --visible to solve manually")
                    return []

            # Human-like behavior before extraction
            await human_mouse(page)
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # Extract __NEXT_DATA__
            js = PF_EXTRACT_JS.replace("'__COMMUNITY__'", f"'{community}'")
            result = await page.evaluate(js)

            if "error" in result:
                error_msg = result.get("error", "")
                if "No __NEXT_DATA__" in error_msg:
                    if visible_mode:
                        # Even after check, no data — it's a CAPTCHA we missed
                        log(f"  🔒 PF {community}: No __NEXT_DATA__ — CAPTCHA page. Solve it in the browser!")
                        solved = await wait_for_pf_captcha(page, visible_mode, community)
                        if solved:
                            # Re-extract after CAPTCHA solved
                            js = PF_EXTRACT_JS.replace("'__COMMUNITY__'", f"'{community}'")
                            result = await page.evaluate(js)
                            if "error" not in result:
                                listings = result.get("listings", [])
                                log(f"  ✓ PF {community}: {result['filtered']} listings (after CAPTCHA)")
                                return listings
                        if attempt < max_retries:
                            continue
                    elif attempt < max_retries:
                        log(f"  ⚠ PF {community}: {error_msg} — CAPTCHA page, will retry...")
                        await asyncio.sleep(random.uniform(5, 10))
                        continue
                log(f"  ✗ PF {community}: {error_msg} (keys: {result.get('keys', [])})")
                return []

            listings = result.get("listings", [])
            if attempt > 0:
                log(f"  ✓ PF {community}: {result['filtered']} listings (retry {attempt} succeeded)")
            else:
                log(f"  ✓ PF {community}: {result['filtered']} listings (of {result['total']} total properties)")
            return listings

        except Exception as e:
            if attempt < max_retries:
                log(f"  ⚠ PF {community} error: {e} — will retry...")
                await asyncio.sleep(random.uniform(3, 6))
                continue
            log(f"  ✗ PF {community} ERROR: {e}")
            return []

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

async def check_bayut_blocked(page):
    """Check if Bayut is showing a CAPTCHA or block page.

    Strategy:
      1. If the page has normal Bayut content (nav, search, articles) → NOT blocked
      2. If the page has challenge-specific elements (Turnstile iframe) → blocked
      3. If the page title is a known challenge title → blocked
      4. If the page body ONLY has challenge text (very short body) → blocked
    """
    result = await page.evaluate("""
        (() => {
            // Positive signals: normal Bayut page elements
            const hasNav = !!document.querySelector('nav, header, [class*="navbar"], [class*="header"]');
            const hasSearch = !!document.querySelector('input[type="search"], [class*="search"], form[action*="search"]');
            const hasArticles = document.querySelectorAll('article').length > 0;
            const hasFooter = !!document.querySelector('footer, [class*="footer"]');
            const isNormalPage = (hasNav && hasFooter) || hasArticles || hasSearch;

            // Negative signals: challenge page elements
            const hasTurnstile = !!(
                document.querySelector('iframe[src*="captcha"]') ||
                document.querySelector('iframe[src*="challenge"]') ||
                document.querySelector('iframe[src*="turnstile"]') ||
                document.querySelector('#challenge-running') ||
                document.querySelector('#challenge-stage') ||
                document.querySelector('.cf-turnstile')
            );

            const text = (document.body && document.body.innerText) || '';
            const lc = text.toLowerCase();
            const bodyLen = text.length;

            // Challenge-specific phrases (only match in short pages, not in footer/legal text of real pages)
            const hasChallengeText = (
                lc.includes("let's confirm you are human") ||
                lc.includes('verify you are human') ||
                lc.includes('checking if the site connection is secure') ||
                lc.includes('checking your browser')
            );

            // If page looks normal (has nav + footer or articles), it's not blocked
            if (isNormalPage && !hasTurnstile) return false;

            // If we see Turnstile iframe, it's blocked
            if (hasTurnstile) return true;

            // If short page with challenge text, it's blocked
            if (hasChallengeText && bodyLen < 2000) return true;

            // If page title says so
            const title = document.title.toLowerCase();
            if (title.includes('just a moment') || title.includes('attention required') ||
                title === '' || title === 'just a moment...') return true;

            return false;
        })()
    """)
    return result


async def bayut_page_is_ready(page):
    """Check if Bayut page has loaded real content (nav, articles, search, etc.)."""
    try:
        return await page.evaluate("""
            (() => {
                const hasNav = !!document.querySelector('nav, header, [class*="navbar"], [class*="header"]');
                const hasFooter = !!document.querySelector('footer, [class*="footer"]');
                const hasArticles = document.querySelectorAll('article').length > 0;
                const hasSearch = !!document.querySelector('input[type="search"], [class*="search"]');
                const bodyLen = (document.body && document.body.innerText || '').length;
                // Normal Bayut page: has nav+footer, or has articles, or has search + reasonable content
                return (hasNav && hasFooter) || hasArticles || (hasSearch && bodyLen > 3000);
            })()
        """)
    except Exception:
        return False


async def wait_for_bayut_captcha(page, visible_mode, community="", timeout=600):
    """Wait for user to solve Bayut CAPTCHA in visible mode.
    Polls for: 1) normal Bayut content appearing, 2) Continue signal from panel.
    Bayut puts CAPTCHA on homepage — once solved, cookies persist for community pages."""
    if not visible_mode:
        return False

    # Quick check — maybe not actually blocked
    if await bayut_page_is_ready(page):
        return True

    log(f"  🔒 CAPTCHA:WAITING:{community}")
    log("  👉 Solve the Bayut CAPTCHA in the browser window, then click Continue in the control panel")
    log(f"  ⏳ Scraper PAUSED — waiting for you (up to {timeout//60} min)...")

    write_captcha_signal("waiting", community)

    start = time.time()
    while time.time() - start < timeout:
        await asyncio.sleep(2)

        # Check 1: Did normal Bayut content appear? (CAPTCHA solved, page redirected)
        try:
            if await bayut_page_is_ready(page):
                log("  ✅ CAPTCHA solved! Bayut page loaded. Continuing...")
                clear_captcha_signal()
                return True
        except Exception:
            pass

        # Check 2: Did user click Continue in the control panel?
        sig = read_captcha_signal()
        if sig and sig.get("status") == "continue":
            log("  ▶ Continue signal received from control panel")
            clear_captcha_signal()
            await asyncio.sleep(3)
            try:
                if await bayut_page_is_ready(page):
                    log("  ✅ Bayut page loaded. Continuing...")
                    return True
                # Maybe CAPTCHA was solved but page didn't auto-redirect — try navigating
                log("  ↻ Page not ready — navigating to Bayut homepage...")
                await page.goto("https://www.bayut.com/", wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(random.uniform(3, 5))
                if await bayut_page_is_ready(page):
                    log("  ✅ Bayut homepage loaded. Continuing...")
                    return True
                # Still not ready — maybe CAPTCHA wasn't actually solved
                if await check_bayut_blocked(page):
                    log("  ⚠ Still showing CAPTCHA — please solve it and click Continue again")
                    write_captcha_signal("waiting", community)
                    continue
                else:
                    # Not blocked but not ready — give it a moment
                    await asyncio.sleep(3)
                    log("  ✅ Page seems clear. Continuing...")
                    return True
            except Exception as e:
                log(f"  ⚠ Error: {e} — will keep waiting")
                write_captcha_signal("waiting", community)
                continue

    log("  ✗ Timed out waiting for Bayut CAPTCHA solve (10 min)")
    clear_captcha_signal()
    return False


async def scrape_bayut(page, community, url, visible_mode=False, retries=2):
    """Scrape Bayut for one community URL with human-like behavior.
    When CAPTCHA is detected in visible mode, pauses and waits for user to solve."""
    log(f"  Bayut: {community}")

    for attempt in range(retries + 1):
        try:
            if attempt > 0:
                log(f"  Retry {attempt}/{retries} for {community}...")
                await asyncio.sleep(random.uniform(15, 25))

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(4, 8))

            # Check for bot detection / CAPTCHA
            if await check_bayut_blocked(page):
                if visible_mode:
                    # Pause and wait for user to solve in browser + click Continue
                    solved = await wait_for_bayut_captcha(page, visible_mode, community)
                    if not solved:
                        log(f"  ✗ Bayut {community}: CAPTCHA not solved")
                        return []
                else:
                    title = await page.title()
                    log(f"  ⚠ Bot detection: '{title}' — waiting 20s...")
                    await asyncio.sleep(20)
                    if await check_bayut_blocked(page):
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


# ─── CONFIG LOADER ──────────────────────────────────────────────────────────
def load_communities_from_config(config_path, mode="villa"):
    """Load PF and Bayut community lists from communities.json.
    Returns (pf_list, bayut_list) matching the hardcoded format."""
    with open(config_path) as f:
        config = json.load(f)

    pf_list = []
    bayut_list = []

    for comm in config.get(mode, []):
        if not comm.get("enabled", True):
            continue
        name = comm["name"]
        beds = comm.get("beds_min", 3)
        pmin = comm.get("price_min", 1500000)
        pmax = comm.get("price_max", 3000000)

        # Build PF entries
        pf = comm.get("pf")
        if pf:
            ranges = comm.get("pf_ranges", [{"beds_min": beds, "price_min": pmin, "price_max": pmax}])
            for r in ranges:
                r_beds = r.get("beds_min", beds)
                r_pmin = r.get("price_min", pmin)
                r_pmax = r.get("price_max", pmax)
                if pf["type"] == "url":
                    pf_list.append((name, pf["value"], r_beds, r_pmin, r_pmax))
                else:
                    # Pass numeric ID — scrape_pf() builds the URL
                    pf_list.append((name, pf["value"], r_beds, r_pmin, r_pmax))

        # Build Bayut entries
        bayut_entries = comm.get("bayut", [])
        bayut_ranges = comm.get("bayut_ranges")
        if bayut_entries:
            if bayut_ranges:
                for entry in bayut_entries:
                    for r in bayut_ranges:
                        r_beds = r.get("beds_min", beds)
                        r_pmin = r.get("price_min", pmin)
                        r_pmax = r.get("price_max", pmax)
                        url = f"https://www.bayut.com/for-sale/{entry['prop_type']}/dubai/{entry['path']}/?sort=price_asc&beds_min={r_beds}&price_min={r_pmin}&price_max={r_pmax}"
                        bayut_list.append((name, url))
            else:
                for entry in bayut_entries:
                    url = f"https://www.bayut.com/for-sale/{entry['prop_type']}/dubai/{entry['path']}/?sort=price_asc&beds_min={beds}&price_min={pmin}&price_max={pmax}"
                    bayut_list.append((name, url))

    return pf_list, bayut_list


# ─── MAIN ────────────────────────────────────────────────────────────────────
async def main():
    # Parse args (support both set-based and positional for --config path)
    argv = sys.argv[1:]
    pf_only     = "--pf-only" in argv
    bayut_only  = "--bayut-only" in argv
    dry_run     = "--dry-run" in argv
    visible     = "--visible" in argv
    no_process  = "--no-process" in argv

    # Parse --config <path>
    config_path = None
    if "--config" in argv:
        ci = argv.index("--config")
        if ci + 1 < len(argv):
            config_path = argv[ci + 1]

    log(f"═══ Dubai Auto Scraper — {TODAY} ═══")
    mode_str = "PF only" if pf_only else "Bayut only" if bayut_only else "Full scan"
    log(f"Mode: {mode_str}{' (VISIBLE)' if visible else ''}{' (DRY RUN)' if dry_run else ''}")

    # Load communities from config file or use hardcoded defaults
    global PF_COMMUNITIES, BAYUT_COMMUNITIES
    if config_path and os.path.exists(config_path):
        log(f"Loading communities from {os.path.basename(config_path)}")
        pf_from_config, bayut_from_config = load_communities_from_config(config_path, mode="villa")
        if pf_from_config:
            PF_COMMUNITIES = pf_from_config
            log(f"  PF: {len(PF_COMMUNITIES)} entries from config")
        if bayut_from_config:
            BAYUT_COMMUNITIES = bayut_from_config
            log(f"  Bayut: {len(BAYUT_COMMUNITIES)} entries from config")
    else:
        log("Using hardcoded community lists")

    # ── Cleanup: delete logs older than 2 days ──────────────────────────────
    import glob as _glob
    cutoff = time.time() - 2 * 86400
    for old_log in _glob.glob(os.path.join(LOG_DIR, "*.log")):
        try:
            if os.path.getmtime(old_log) < cutoff:
                os.remove(old_log)
                log(f"  Cleaned old log: {os.path.basename(old_log)}")
        except OSError:
            pass

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
                listings = await scrape_pf(page, community, loc_id, beds, pmin, pmax, visible_mode=visible)
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
            if visible and await check_bayut_blocked(page):
                solved = await wait_for_bayut_captcha(page, visible, "Bayut Homepage")
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
                elif not visible and await check_bayut_blocked(page):
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
    if no_process:
        log("\n--no-process flag set — skipping process_deals.py (server will run it)")
    elif not dry_run:
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
