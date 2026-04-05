#!/usr/bin/env python3
"""
AUTO SCRAPE APARTMENTS — Fully automated Property Finder + Bayut scraper for APARTMENTS.
Mirrors auto_scrape.py but targets low-rise apartment communities.

Usage:
    python scripts/auto_scrape_apartments.py                  # Full scan (headless)
    python scripts/auto_scrape_apartments.py --pf-only        # Property Finder only
    python scripts/auto_scrape_apartments.py --bayut-only     # Bayut only
    python scripts/auto_scrape_apartments.py --dry-run        # Scrape but don't publish
    python scripts/auto_scrape_apartments.py --visible        # Open real browser (solve CAPTCHA manually)
    python scripts/auto_scrape_apartments.py --visible --bayut-only   # Best for first Bayut run

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
RAW_PATH   = os.path.join(BASE, "raw_data_apartments.json")
SCRIPTS    = os.path.join(BASE, "scripts")
LOG_DIR    = os.path.join(BASE, "logs")
COOKIES_PF    = os.path.join(SCRIPTS, "cookies_pf.json")
COOKIES_BAYUT = os.path.join(SCRIPTS, "cookies_bayut.json")
TODAY      = datetime.now().strftime("%Y-%m-%d")

os.makedirs(LOG_DIR, exist_ok=True)

# ─── COMMUNITY CONFIGS ──────────────────────────────────────────────────────
# PF apartments: (community_name, location_id_or_url, min_beds, price_min, price_max)
# c=2 for apartments on Property Finder
PF_COMMUNITIES = [
    # ── Emaar communities (top-tier developer) ──
    ("Dubai Hills Estate",  105,   3, 1_500_000, 2_500_000),
    ("Emaar South",         "https://www.propertyfinder.ae/en/buy/dubai/apartments-for-sale-dubai-south-dubai-world-central-emaar-south.html?beds_min=3&price_min=1500000&price_max=2500000&sort=pa", 3, 1_500_000, 2_500_000),
    ("Mirdif Hills",        "https://www.propertyfinder.ae/en/buy/dubai/apartments-for-sale-mirdif-hills.html?beds_min=3&price_min=1500000&price_max=2500000&sort=pa", 3, 1_500_000, 2_500_000),
    ("Town Square",         131,   3, 1_500_000, 2_500_000),
    # ── Nakheel / Dubai Properties / Nshama ──
    ("Al Furjan",           "https://www.propertyfinder.ae/en/buy/dubai/apartments-for-sale-al-furjan.html?beds_min=3&price_min=1500000&price_max=2500000&sort=pa", 3, 1_500_000, 2_500_000),
    ("Mudon",               8250,  3, 1_500_000, 2_500_000),
    ("Remraam",             "https://www.propertyfinder.ae/en/buy/dubai/apartments-for-sale-remraam.html?beds_min=3&price_min=1500000&price_max=2500000&sort=pa", 3, 1_500_000, 2_500_000),
    # ── DAMAC communities ──
    ("DAMAC Hills 2",       125,   3, 1_500_000, 2_500_000),
    ("DAMAC Hills",         129,   3, 1_500_000, 2_500_000),
    # ── JVC (multiple developers, large low-rise stock) ──
    ("JVC",                 "https://www.propertyfinder.ae/en/buy/dubai/apartments-for-sale-jumeirah-village-circle.html?beds_min=3&price_min=1500000&price_max=2500000&sort=pa", 3, 1_500_000, 2_500_000),
    # ── Motor City (Union Properties) ──
    ("Motor City",          "https://www.propertyfinder.ae/en/buy/dubai/apartments-for-sale-motor-city.html?beds_min=3&price_min=1500000&price_max=2500000&sort=pa", 3, 1_500_000, 2_500_000),
]

# Bayut apartments: (community_name, url)
BAYUT_COMMUNITIES = [
    ("Dubai Hills Estate", "https://www.bayut.com/for-sale/apartments/dubai/dubai-hills-estate/?sort=price_asc&beds_min=3&price_min=1500000&price_max=2500000"),
    ("Emaar South",        "https://www.bayut.com/for-sale/apartments/dubai/dubai-south-dubai-world-central/emaar-south/?sort=price_asc&beds_min=3&price_min=1500000&price_max=2500000"),
    ("Mirdif Hills",       "https://www.bayut.com/for-sale/apartments/dubai/mirdif/mirdif-hills/?sort=price_asc&beds_min=3&price_min=1500000&price_max=2500000"),
    ("Town Square",        "https://www.bayut.com/for-sale/apartments/dubai/town-square/?sort=price_asc&beds_min=3&price_min=1500000&price_max=2500000"),
    ("Al Furjan",          "https://www.bayut.com/for-sale/apartments/dubai/al-furjan/?sort=price_asc&beds_min=3&price_min=1500000&price_max=2500000"),
    ("Mudon",              "https://www.bayut.com/for-sale/apartments/dubai/mudon/?sort=price_asc&beds_min=3&price_min=1500000&price_max=2500000"),
    ("Remraam",            "https://www.bayut.com/for-sale/apartments/dubai/remraam/?sort=price_asc&beds_min=3&price_min=1500000&price_max=2500000"),
    ("DAMAC Hills 2",      "https://www.bayut.com/for-sale/apartments/dubai/damac-hills-2-akoya-by-damac/?sort=price_asc&beds_min=3&price_min=1500000&price_max=2500000"),
    ("DAMAC Hills",        "https://www.bayut.com/for-sale/apartments/dubai/damac-hills/?sort=price_asc&beds_min=3&price_min=1500000&price_max=2500000"),
    ("JVC",                "https://www.bayut.com/for-sale/apartments/dubai/jumeirah-village-circle-jvc/?sort=price_asc&beds_min=3&price_min=1500000&price_max=2500000"),
    ("Motor City",         "https://www.bayut.com/for-sale/apartments/dubai/motor-city/?sort=price_asc&beds_min=3&price_min=1500000&price_max=2500000"),
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
    log(f"raw_data_apartments.json saved — {total} total listings across {len(data['communities'])} communities")


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
            for i, ex in enumerate(existing):
                if ex["uid"] == l["uid"]:
                    existing[i] = l
                    break
    raw_data["communities"][community] = existing
    log(f"  {community}: {len(listings)} scraped, {added} new, {len(existing)} total")


# ─── PROPERTY FINDER SCRAPER (APARTMENTS) ──────────────────────────────────
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
        // Filter for apartments/flats only
        if (type.indexOf('apartment') === -1 && type.indexOf('flat') === -1 && type.indexOf('penthouse') === -1 && type.indexOf('duplex') === -1) continue;

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
    height = await page.evaluate("document.body.scrollHeight")
    current = 0
    while current < height:
        step = random.randint(200, 500)
        current = min(current + step, height)
        await page.evaluate(f"window.scrollTo(0, {current})")
        await asyncio.sleep(random.uniform(0.3, 0.8))
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(random.uniform(0.5, 1))


async def human_mouse(page):
    for _ in range(random.randint(2, 5)):
        x = random.randint(100, 1200)
        y = random.randint(100, 600)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.2, 0.6))


async def warmup_visit(page, domain):
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
    PF uses Cloudflare Turnstile: 'Let's confirm you are human' + 'Begin >' button."""
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
    title = (await page.title()).lower()
    if any(kw in title for kw in ["just a moment", "challenge", "captcha", "verify", "security"]):
        return True
    has_data = await page.evaluate("!!document.getElementById('__NEXT_DATA__')")
    return not has_data


CAPTCHA_SIGNAL = os.path.join(BASE, ".captcha_signal")


def write_captcha_signal(status, community=""):
    with open(CAPTCHA_SIGNAL, "w") as f:
        json.dump({"status": status, "community": community, "time": time.time()}, f)


def read_captcha_signal():
    try:
        if os.path.exists(CAPTCHA_SIGNAL):
            with open(CAPTCHA_SIGNAL) as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return None


def clear_captcha_signal():
    try:
        if os.path.exists(CAPTCHA_SIGNAL):
            os.remove(CAPTCHA_SIGNAL)
    except OSError:
        pass


async def wait_for_pf_captcha(page, visible_mode, community="", timeout=600):
    """Wait for user to solve PF CAPTCHA in visible mode.
    Pauses until __NEXT_DATA__ appears or user clicks Continue in control panel."""
    if not visible_mode:
        return False
    log(f"  🔒 CAPTCHA:WAITING:{community}")
    log("  👉 Solve the CAPTCHA in the browser window, then click Continue in the control panel")
    log(f"  ⏳ Scraper PAUSED — waiting for you (up to {timeout//60} min)...")
    write_captcha_signal("waiting", community)
    start = time.time()
    while time.time() - start < timeout:
        await asyncio.sleep(2)
        try:
            has_data = await page.evaluate("!!document.getElementById('__NEXT_DATA__')")
            if has_data:
                log("  ✅ CAPTCHA solved! PF page loaded. Continuing...")
                clear_captcha_signal()
                return True
        except Exception:
            pass
        sig = read_captcha_signal()
        if sig and sig.get("status") == "continue":
            log("  ▶ Continue signal received from control panel")
            clear_captcha_signal()
            await asyncio.sleep(2)
            try:
                has_data = await page.evaluate("!!document.getElementById('__NEXT_DATA__')")
                if has_data:
                    log("  ✅ Page loaded after CAPTCHA. Continuing...")
                    return True
                else:
                    log("  ↻ Page not ready yet — reloading...")
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(random.uniform(3, 5))
                    has_data = await page.evaluate("!!document.getElementById('__NEXT_DATA__')")
                    if has_data:
                        log("  ✅ Page loaded after reload. Continuing...")
                        return True
                    else:
                        log("  ⚠ Still no data — CAPTCHA may not be solved yet")
                        write_captcha_signal("waiting", community)
                        continue
            except Exception as e:
                log(f"  ⚠ Error checking page: {e}")
                continue
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
    """Scrape Property Finder for apartments in one community.
    Detects CAPTCHA and waits for human solve in visible mode."""
    if isinstance(location_id, str) and location_id.startswith("http"):
        url = location_id
    else:
        url = f"https://www.propertyfinder.ae/en/search?l={location_id}&c=2&bdr%5B%5D={beds}&pf={price_min}&pt={price_max}&ob=pr"
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

            # Check for CAPTCHA / human verification
            if await check_pf_captcha(page):
                if visible_mode:
                    solved = await wait_for_pf_captcha(page, visible_mode, community)
                    if solved:
                        await asyncio.sleep(random.uniform(1, 2))
                    else:
                        if attempt < max_retries:
                            log(f"  ⚠ PF {community}: CAPTCHA not solved, will retry...")
                            continue
                        log(f"  ✗ PF {community}: CAPTCHA not solved after {max_retries} attempts")
                        return []
                else:
                    log(f"  ⚠ PF {community}: CAPTCHA detected (headless), waiting 15s...")
                    await asyncio.sleep(15)
                    if attempt < max_retries:
                        continue
                    log(f"  ✗ PF {community}: CAPTCHA in headless mode. Try --visible to solve manually")
                    return []

            await human_mouse(page)
            await asyncio.sleep(random.uniform(0.5, 1.5))

            js = PF_EXTRACT_JS.replace("'__COMMUNITY__'", f"'{community}'")
            result = await page.evaluate(js)

            if "error" in result:
                error_msg = result.get("error", "")
                if "No __NEXT_DATA__" in error_msg:
                    if visible_mode:
                        log(f"  🔒 PF {community}: No __NEXT_DATA__ — CAPTCHA page. Solve it in the browser!")
                        solved = await wait_for_pf_captcha(page, visible_mode, community)
                        if solved:
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


# ─── BAYUT SCRAPER (APARTMENTS) ────────────────────────────────────────────
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

        const aedIdx = lines.indexOf('AED');
        const price = aedIdx >= 0 ? parseInt((lines[aedIdx + 1] || '').replace(/\\D/g, '')) || 0 : 0;
        if (price <= 0) continue;

        // Look for Apartment/Flat/Penthouse/Duplex
        const thIdx = lines.findIndex(l => l === 'Apartment' || l === 'Flat' || l === 'Penthouse' || l === 'Duplex');
        const beds  = thIdx >= 0 ? parseInt(lines[thIdx + 1]) || 0 : 0;
        const baths = thIdx >= 0 ? parseInt(lines[thIdx + 2]) || 0 : 0;
        if (beds < 3) continue;

        const sqftLine = lines.find(l => l.endsWith('sqft'));
        const sqft = sqftLine ? parseInt(sqftLine.replace(/\\D/g, '')) : 0;

        const areaIdx = lines.indexOf('Area:');
        const title   = areaIdx >= 0 ? (lines[areaIdx + 2] || '') : '';
        const locLine = areaIdx >= 0 ? (lines[areaIdx + 3] || '') : '';
        const cluster = locLine.split(',')[0].trim();

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
    Uses positive signals (normal page elements) to avoid false positives."""
    result = await page.evaluate("""
        (() => {
            const hasNav = !!document.querySelector('nav, header, [class*="navbar"], [class*="header"]');
            const hasSearch = !!document.querySelector('input[type="search"], [class*="search"], form[action*="search"]');
            const hasArticles = document.querySelectorAll('article').length > 0;
            const hasFooter = !!document.querySelector('footer, [class*="footer"]');
            const isNormalPage = (hasNav && hasFooter) || hasArticles || hasSearch;

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

            const hasChallengeText = (
                lc.includes("let's confirm you are human") ||
                lc.includes('verify you are human') ||
                lc.includes('checking if the site connection is secure') ||
                lc.includes('checking your browser')
            );

            if (isNormalPage && !hasTurnstile) return false;
            if (hasTurnstile) return true;
            if (hasChallengeText && bodyLen < 2000) return true;

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
    if await bayut_page_is_ready(page):
        return True

    log(f"  🔒 CAPTCHA:WAITING:{community}")
    log("  👉 Solve the Bayut CAPTCHA in the browser window, then click Continue in the control panel")
    log(f"  ⏳ Scraper PAUSED — waiting for you (up to {timeout//60} min)...")
    write_captcha_signal("waiting", community)

    start = time.time()
    while time.time() - start < timeout:
        await asyncio.sleep(2)
        try:
            if await bayut_page_is_ready(page):
                log("  ✅ CAPTCHA solved! Bayut page loaded. Continuing...")
                clear_captcha_signal()
                return True
        except Exception:
            pass
        sig = read_captcha_signal()
        if sig and sig.get("status") == "continue":
            log("  ▶ Continue signal received from control panel")
            clear_captcha_signal()
            await asyncio.sleep(3)
            try:
                if await bayut_page_is_ready(page):
                    log("  ✅ Bayut page loaded. Continuing...")
                    return True
                log("  ↻ Page not ready — navigating to Bayut homepage...")
                await page.goto("https://www.bayut.com/", wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(random.uniform(3, 5))
                if await bayut_page_is_ready(page):
                    log("  ✅ Bayut homepage loaded. Continuing...")
                    return True
                if await check_bayut_blocked(page):
                    log("  ⚠ Still showing CAPTCHA — please solve it and click Continue again")
                    write_captcha_signal("waiting", community)
                    continue
                else:
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
            if await check_bayut_blocked(page):
                if visible_mode:
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
            await human_mouse(page)
            await asyncio.sleep(random.uniform(1, 2))
            await human_scroll(page)
            await asyncio.sleep(random.uniform(1, 2))
            await human_mouse(page)
            await asyncio.sleep(random.uniform(0.5, 1))
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
        } catch (e) {}
        await new Promise(r => setTimeout(r, 500));
    }
    return results;
}
"""

async def enrich_bayut_dates(context, listings):
    to_enrich = [l for l in listings if l.get("source") == "Bayut" and l.get("href") and not l.get("listed")]
    if not to_enrich:
        return
    log(f"  Enriching dates for {len(to_enrich)} Bayut listings...")
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
                dates = await date_page.evaluate(BAYUT_DATE_JS, batch)
                all_dates.update(dates)
                log(f"    Batch {batch_num} retry: got {len(dates)} dates")
                await asyncio.sleep(random.uniform(1, 2))
            except Exception as e2:
                log(f"    Batch {batch_num} retry failed: {e2}")
    await date_page.close()
    applied = 0
    for l in to_enrich:
        if l.get("href") in all_dates:
            l["listed"] = all_dates[l["href"]]
            applied += 1
    log(f"  ✓ Applied dates to {applied}/{len(to_enrich)} Bayut listings")


# ─── CONFIG LOADER ──────────────────────────────────────────────────────────
def load_communities_from_config(config_path, mode="apartment"):
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
        pmax = comm.get("price_max", 2500000)

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

    log(f"═══ Dubai Apartment Scraper — {TODAY} ═══")
    mode_str = "PF only" if pf_only else "Bayut only" if bayut_only else "Full scan"
    log(f"Mode: {mode_str}{' (VISIBLE)' if visible else ''}{' (DRY RUN)' if dry_run else ''}")

    # Load communities from config file or use hardcoded defaults
    global PF_COMMUNITIES, BAYUT_COMMUNITIES
    if config_path and os.path.exists(config_path):
        log(f"Loading communities from {os.path.basename(config_path)}")
        pf_from_config, bayut_from_config = load_communities_from_config(config_path, mode="apartment")
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

    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    stealth = Stealth()

    raw_data = load_raw()
    start_time = time.time()

    async with async_playwright() as pw:
        use_headless = not visible
        browser = await pw.chromium.launch(
            headless=use_headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

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
            log("\n── Property Finder (Apartments) ──")
            await warmup_visit(page, "propertyfinder.ae")
            pf_total = 0
            for idx, (community, loc_id, beds, pmin, pmax) in enumerate(PF_COMMUNITIES):
                listings = await scrape_pf(page, community, loc_id, beds, pmin, pmax, visible_mode=visible)
                if listings:
                    append_community(raw_data, community, listings)
                    pf_total += len(listings)
                delay = random.uniform(5, 10)
                log(f"  Waiting {delay:.0f}s before next...")
                await asyncio.sleep(delay)
            log(f"PF done: {pf_total} apartment listings scraped")

        # ── Bayut ────────────────────────────────────────────────────────
        if not pf_only:
            log("\n── Bayut (Apartments) ──")
            await warmup_visit(page, "bayut.com")
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
                    log("  ⚠ Bayut is blocking us — stopping Bayut scraping")
                    log("  💡 Tip: run with --visible to solve CAPTCHA manually")
                    bayut_blocked = True
                delay = random.uniform(8, 15)
                log(f"  Waiting {delay:.0f}s before next...")
                await asyncio.sleep(delay)
            if all_bayut:
                await enrich_bayut_dates(context, all_bayut)
                for community in set(l["community"] for l in all_bayut):
                    comm_listings = [l for l in all_bayut if l["community"] == community]
                    if comm_listings:
                        append_community(raw_data, community, comm_listings)
            log(f"Bayut done: {bayut_total} apartment listings scraped")

        # ── Save cookies ─────────────────────────────────────────────────
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

    # ── Run process_apartments.py ────────────────────────────────────────
    if no_process:
        log("\n--no-process flag set — skipping process_apartments.py (server will run it)")
    elif not dry_run:
        log("\n── Running process_apartments.py ──")
        process_script = os.path.join(SCRIPTS, "process_apartments.py")
        if os.path.exists(process_script):
            result = subprocess.run(
                [sys.executable, process_script],
                capture_output=True, text=True, cwd=BASE
            )
            print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)
            if result.returncode == 0:
                log("✓ process_apartments.py completed successfully")
            else:
                log(f"✗ process_apartments.py failed (exit code {result.returncode})")
        else:
            log(f"✗ process_apartments.py not found at {process_script}")
    else:
        log("\nDRY RUN — skipping process_apartments.py")

    total_listings = sum(len(v) for v in raw_data["communities"].values())
    log(f"\n═══ SUMMARY ═══")
    log(f"Communities: {len(raw_data['communities'])}")
    log(f"Total listings: {total_listings}")
    log(f"Time: {elapsed:.0f}s")
    log(f"Output: {RAW_PATH}")
    if not dry_run:
        log(f"Published: apartments_deals.json + apartments.html")


if __name__ == "__main__":
    asyncio.run(main())
