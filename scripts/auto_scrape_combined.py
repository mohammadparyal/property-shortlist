#!/usr/bin/env python3
"""
AUTO SCRAPE COMBINED — Dubai-wide combined scraper (apartments + villas + townhouses).
Mirrors auto_scrape_apartments.py architecture but scrapes a single Dubai-wide search,
incrementally (stops paging when it hits known UIDs or listings older than RECENT_DAYS_WINDOW).

Filters: price <= 2.3M AED, beds 2-7+, sorted newest first.

Usage:
    python scripts/auto_scrape_combined.py                  # Full scan (headless)
    python scripts/auto_scrape_combined.py --pf-only        # Property Finder only
    python scripts/auto_scrape_combined.py --bayut-only     # Bayut only
    python scripts/auto_scrape_combined.py --dry-run        # Scrape but don't process
    python scripts/auto_scrape_combined.py --visible        # Open real browser (solve CAPTCHA manually)

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
from datetime import datetime, timedelta

# ─── PATHS ───────────────────────────────────────────────────────────────────
BASE       = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW_PATH   = os.path.join(BASE, "raw_data_combined.json")
SCRIPTS    = os.path.join(BASE, "scripts")
LOG_DIR    = os.path.join(BASE, "logs")
COOKIES_PF    = os.path.join(SCRIPTS, "cookies_pf.json")
COOKIES_BAYUT = os.path.join(SCRIPTS, "cookies_bayut.json")
TODAY      = datetime.now().strftime("%Y-%m-%d")

os.makedirs(LOG_DIR, exist_ok=True)

# ─── CONSTANTS ───────────────────────────────────────────────────────────────
MAX_PAGES_COLD_START   = 5
RECENT_DAYS_WINDOW     = 3       # Stop paging when listings older than 3 days
SOFT_DELETE_GRACE_DAYS = 7
OLD_LISTING_DAYS       = 30
PRICE_MAX              = 2_300_000
BEDS_MIN               = 2
BEDS_MAX               = 7

# ─── SEARCH URLS ─────────────────────────────────────────────────────────────
# PF: l=1=Dubai, c=1=sale, rt[]=1/2/3=apartment/villa/townhouse, ob=nd=newest first
PF_BASE_URL = (
    "https://www.propertyfinder.ae/en/search?"
    "l=1&c=1"
    "&rt%5B%5D=1&rt%5B%5D=2&rt%5B%5D=3"
    "&bdr%5B%5D=2&bdr%5B%5D=3&bdr%5B%5D=4&bdr%5B%5D=5&bdr%5B%5D=6&bdr%5B%5D=7"
    f"&pt={PRICE_MAX}"
    "&ob=nd"
)

# Bayut Dubai-wide.
# Bedroom filter is path-segment based: '2,3,4,5,6,7,8+-bedroom-property' covers
# 2 through 8+ beds (Bayut groups everything ≥ 8 under "8+"). The query-param
# beds_min / beds_max DO NOT WORK on Bayut for this listings page — confirmed.
# Sort by date_desc (newest first), price_max as query param.
BAYUT_BASE_URL = (
    "https://www.bayut.com/for-sale/2,3,4,5,6,7,8+-bedroom-property/dubai/"
    f"?sort=date_desc&price_max={PRICE_MAX}"
)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ─── RAW DATA ACCUMULATOR (FLAT STRUCTURE) ──────────────────────────────────
def load_raw():
    if os.path.exists(RAW_PATH):
        try:
            with open(RAW_PATH) as f:
                d = json.load(f)
            # Defensive defaults
            if "listings" not in d:
                d["listings"] = []
            return d
        except (json.JSONDecodeError, IOError):
            pass
    return {"listings": [], "last_updated": TODAY, "total_listings": 0}


def save_raw(data):
    data["last_updated"] = TODAY
    data["total_listings"] = len(data.get("listings", []))
    tmp = RAW_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, RAW_PATH)
    log(f"raw_data_combined.json saved — {data['total_listings']} total listings")


def merge_listings(raw_data, fresh_listings):
    """Merge fresh listings into raw_data['listings'] (dedupe by uid).
    Updates last_seen / un-removes / preserves scrape_date for known uids.
    Returns count of new uids added."""
    by_uid = {l["uid"]: l for l in raw_data["listings"]}
    added = 0
    updated = 0
    for nl in fresh_listings:
        uid = nl.get("uid")
        if not uid:
            continue
        if uid in by_uid:
            ex = by_uid[uid]
            # Preserve scrape_date and any user state
            scrape_date = ex.get("scrape_date") or TODAY
            hidden      = ex.get("hidden", False)
            # Refresh fields
            ex.update(nl)
            ex["scrape_date"] = scrape_date
            ex["last_seen"]   = TODAY
            ex["hidden"]      = hidden
            # Re-saw it — un-remove
            ex["removed"]        = False
            ex["removed_date"]   = None
            ex["removed_reason"] = None
            updated += 1
        else:
            nl.setdefault("scrape_date", TODAY)
            nl["last_seen"]      = TODAY
            nl.setdefault("hidden", False)
            nl["removed"]        = False
            nl["removed_date"]   = None
            nl["removed_reason"] = None
            raw_data["listings"].append(nl)
            by_uid[uid] = nl
            added += 1
    log(f"  Merged: {added} new, {updated} updated, {len(raw_data['listings'])} total")
    return added


# ─── PROPERTY FINDER EXTRACTOR ─────────────────────────────────────────────
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

    var rawItems = (searchResult.listings && searchResult.listings.length > 0)
        ? searchResult.listings
        : (searchResult.properties && searchResult.properties.length > 0)
            ? searchResult.properties
            : [];
    var results = [];

    for (var i = 0; i < rawItems.length; i++) {
        var item = rawItems[i];
        var p = item.property || item;

        var rawType = (p.property_type || p.type || '').toLowerCase();
        var prop_type = '';
        if (rawType.indexOf('apartment') !== -1 || rawType.indexOf('flat') !== -1
            || rawType.indexOf('penthouse') !== -1 || rawType.indexOf('duplex') !== -1) {
            prop_type = 'apartment';
        } else if (rawType.indexOf('townhouse') !== -1 || rawType.indexOf('town house') !== -1) {
            prop_type = 'townhouse';
        } else if (rawType.indexOf('villa') !== -1) {
            prop_type = 'villa';
        } else {
            // Skip unsupported types (land, building, etc.)
            continue;
        }

        var priceRaw = p.price || 0;
        var price = (typeof priceRaw === 'object') ? (priceRaw.value || 0) : priceRaw;
        if (price <= 0) continue;

        var bedsRaw = p.bedrooms || p.beds || 0;
        var beds = (typeof bedsRaw === 'string') ? parseInt(bedsRaw) : bedsRaw;
        if (beds < 2) continue;

        var bathsRaw = p.bathrooms || p.baths || 0;
        var baths = (typeof bathsRaw === 'string') ? parseInt(bathsRaw) : bathsRaw;
        var sizeVal = p.size || p.area || 0;
        var sqft = 0;
        if (typeof sizeVal === 'object') {
            sqft = sizeVal.value || 0;
            var unit = (sizeVal.unit || '').toLowerCase();
            if (unit === 'sqm' || unit === 'm²' || unit === 'meter') {
                sqft = Math.round(sqft * 10.764);
            } else {
                sqft = Math.round(sqft);
            }
        } else {
            sqft = Math.round(sizeVal || 0);
        }
        var ref    = (p.reference || p.ref || '') + '';
        var title  = p.title || p.name || '';
        var listed = (p.listed_date || p.added_on || '').slice(0, 10);

        var cluster = '';
        var community = '';
        var locTree = p.location_tree || p.location || [];
        if (Array.isArray(locTree) && locTree.length >= 1) {
            var last = locTree[locTree.length - 1];
            cluster = (last && (last.name || last)) || '';
            // Community = second-to-last node, fallback to last
            if (locTree.length >= 2) {
                var penult = locTree[locTree.length - 2];
                community = (penult && (penult.name || penult)) || '';
            } else {
                community = cluster;
            }
        } else if (locTree && locTree.name) {
            cluster = locTree.name;
            community = locTree.name;
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
            community: community,
            source:    'PropertyFinder',
            listed:    listed,
            isOffPlan: isOffPlan,
            prop_type: prop_type
        });
    }
    return {listings: results, total: rawItems.length, filtered: results.length};
}
"""


# ─── HELPERS (mirrored from auto_scrape_apartments.py) ─────────────────────
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
    log("  ✗ Timed out waiting for CAPTCHA solve (10 min)")
    clear_captcha_signal()
    return False


async def check_bayut_blocked(page):
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


# ─── BAYUT EXTRACTOR (Dubai-wide, all property types) ─────────────────────
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

        // Type detector: Apartment / Townhouse / Villa / Penthouse / Duplex
        const typeKeywords = ['Apartment','Flat','Penthouse','Duplex','Townhouse','Town house','Villa'];
        let thIdx = -1, typeStr = '';
        for (let i=0;i<lines.length;i++) {
            const ln = lines[i];
            if (typeKeywords.indexOf(ln) !== -1) { thIdx = i; typeStr = ln; break; }
        }
        let prop_type = '';
        const tlc = typeStr.toLowerCase();
        if (tlc === 'townhouse' || tlc === 'town house') prop_type = 'townhouse';
        else if (tlc === 'villa') prop_type = 'villa';
        else if (tlc === 'apartment' || tlc === 'flat' || tlc === 'penthouse' || tlc === 'duplex') prop_type = 'apartment';

        const beds  = thIdx >= 0 ? parseInt(lines[thIdx + 1]) || 0 : 0;
        const baths = thIdx >= 0 ? parseInt(lines[thIdx + 2]) || 0 : 0;
        if (beds < 2) continue;

        const sqftLine = lines.find(l => l.endsWith('sqft'));
        const sqft = sqftLine ? parseInt(sqftLine.replace(/\\D/g, '')) : 0;

        const areaIdx = lines.indexOf('Area:');
        const title   = areaIdx >= 0 ? (lines[areaIdx + 2] || '') : '';
        const locLine = areaIdx >= 0 ? (lines[areaIdx + 3] || '') : '';
        const locParts = locLine.split(',').map(s => s.trim()).filter(Boolean);
        const cluster = locParts[0] || '';
        // Community = second part of comma-separated location, fallback to first
        const community = locParts[1] || locParts[0] || '';

        // Fallback prop_type from URL path slug
        if (!prop_type) {
            const path = (href || '').toLowerCase();
            if (path.indexOf('townhouse') !== -1) prop_type = 'townhouse';
            else if (path.indexOf('villa') !== -1) prop_type = 'villa';
            else if (path.indexOf('apartment') !== -1 || path.indexOf('flat') !== -1) prop_type = 'apartment';
        }

        const isOffPlan = lines.some(l => l === 'Off-Plan') ||
                          art.innerText.toLowerCase().includes('off plan') ||
                          art.innerText.toLowerCase().includes('off-plan');

        results.push({
            uid, href,
            price, beds, baths, sqft, cluster, title, community,
            source: 'Bayut', listed: '', isOffPlan,
            prop_type: prop_type
        });
    }
    return {listings: results, articlesFound: articles.length};
}
"""


# ─── BAYUT DATE ENRICHMENT (parallel fetch) ────────────────────────────────
BAYUT_DATE_JS = """
async (urls) => {
    const results = {};
    const concurrency = 5;
    let idx = 0;

    async function fetchOne(url) {
        try {
            const resp = await fetch(url);
            const html = await resp.text();
            const match = html.match(/"datePosted"\\s*:\\s*"([^"]+)"/);
            if (match) {
                results[url] = match[1].slice(0, 10);
            }
        } catch (e) {}
    }

    async function worker() {
        while (idx < urls.length) {
            const i = idx++;
            await fetchOne(urls[i]);
        }
    }

    const workers = [];
    for (let w = 0; w < Math.min(concurrency, urls.length); w++) {
        workers.push(worker());
    }
    await Promise.all(workers);
    return results;
}
"""


async def enrich_bayut_dates(context, listings, raw_data=None):
    """Fetch detail pages to get datePosted for Bayut listings."""
    # Carry over dates from existing raw_data
    if raw_data:
        existing_dates = {}
        for l in raw_data.get("listings", []):
            if l.get("listed") and l.get("uid"):
                existing_dates[l["uid"]] = l["listed"]
        carried = 0
        for l in listings:
            if not l.get("listed") and l.get("uid") in existing_dates:
                l["listed"] = existing_dates[l["uid"]]
                carried += 1
        if carried:
            log(f"  ✓ Carried over {carried} cached dates from existing data")

    to_enrich = [l for l in listings if l.get("source") == "Bayut" and l.get("href") and not l.get("listed")]
    if not to_enrich:
        log("  ✓ All Bayut listings already have dates — skipping enrichment")
        return

    log(f"  Enriching dates for {len(to_enrich)} Bayut listings (parallel fetch)...")
    date_page = await context.new_page()
    try:
        await date_page.goto("https://www.bayut.com/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(random.uniform(1, 2))
    except Exception as e:
        log(f"  Date page warmup failed (non-fatal): {e}")
    all_dates = {}
    batch_size = 30
    urls = [l["href"] for l in to_enrich]
    for i in range(0, len(urls), batch_size):
        batch = urls[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(urls) + batch_size - 1) // batch_size
        try:
            if "bayut.com" not in date_page.url:
                await date_page.goto("https://www.bayut.com/", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1)
            dates = await date_page.evaluate(BAYUT_DATE_JS, batch)
            all_dates.update(dates)
            log(f"    Batch {batch_num}/{total_batches}: got {len(dates)}/{len(batch)} dates")
            await asyncio.sleep(random.uniform(0.5, 1))
        except Exception as e:
            log(f"    Batch {batch_num} error: {e} — re-anchoring...")
            try:
                await date_page.goto("https://www.bayut.com/", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                dates = await date_page.evaluate(BAYUT_DATE_JS, batch)
                all_dates.update(dates)
                log(f"    Batch {batch_num} retry: got {len(dates)} dates")
                await asyncio.sleep(random.uniform(0.5, 1))
            except Exception as e2:
                log(f"    Batch {batch_num} retry failed: {e2}")
    await date_page.close()
    applied = 0
    for l in to_enrich:
        if l.get("href") in all_dates:
            l["listed"] = all_dates[l["href"]]
            applied += 1
    log(f"  ✓ Applied dates to {applied}/{len(to_enrich)} Bayut listings")


# ─── INCREMENTAL PAGE-BY-PAGE SCRAPING ─────────────────────────────────────
def listing_too_old(listing, today_date, window_days):
    """Return True if listing's listed-date is older than window_days days before today."""
    listed = listing.get("listed", "")
    if not listed:
        # No date info — don't treat as stop signal (could be missing for valid reason)
        return False
    try:
        ld = datetime.strptime(listed[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    return (today_date - ld).days > window_days


def pf_url_with_page(base_url, page_num):
    if page_num <= 1:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}page={page_num}"


def bayut_url_with_page(base_url, page_num):
    if page_num <= 1:
        return base_url
    # Bayut paginates via /page-N/ before the query string
    if "?" in base_url:
        path, qs = base_url.split("?", 1)
        if not path.endswith("/"):
            path = path + "/"
        return f"{path}page-{page_num}/?{qs}"
    if not base_url.endswith("/"):
        base_url = base_url + "/"
    return f"{base_url}page-{page_num}/"


async def scrape_pf_dubai_wide(page, previous_uids, visible_mode=False):
    """PF: incremental scrape across pages. Stop when known UID OR listing too old."""
    log("  PF: Dubai (PF) — Dubai-wide")
    today_date = datetime.now().date()
    collected = []
    seen_in_run = set()
    stop_signal = False

    for page_num in range(1, MAX_PAGES_COLD_START + 1):
        if stop_signal:
            break
        url = pf_url_with_page(PF_BASE_URL, page_num)
        log(f"  PF page {page_num}: {url[:90]}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(3, 6))

            if await check_pf_captcha(page):
                if visible_mode:
                    solved = await wait_for_pf_captcha(page, visible_mode, "Dubai (PF)")
                    if not solved:
                        log("  ✗ PF: CAPTCHA not solved — aborting PF scrape")
                        return collected
                else:
                    log("  ⚠ PF: CAPTCHA detected (headless) — try --visible")
                    return collected

            await human_mouse(page)
            await asyncio.sleep(random.uniform(0.5, 1.5))

            result = await page.evaluate(PF_EXTRACT_JS)
            if "error" in result:
                log(f"  ✗ PF page {page_num}: {result.get('error')}")
                if visible_mode:
                    solved = await wait_for_pf_captcha(page, visible_mode, "Dubai (PF)")
                    if solved:
                        result = await page.evaluate(PF_EXTRACT_JS)
                        if "error" in result:
                            return collected
                    else:
                        return collected
                else:
                    return collected

            page_listings = result.get("listings", [])
            if not page_listings:
                log(f"  ✓ PF page {page_num}: 0 listings — end of results")
                break

            log(f"  ✓ PF page {page_num}: {len(page_listings)} listings extracted")

            # Walk listings in order, apply stop conditions
            for l in page_listings:
                uid = l.get("uid")
                if not uid:
                    continue
                if uid in seen_in_run:
                    continue
                if uid in previous_uids:
                    log(f"  ⏹ PF: hit known UID {uid} — stopping pagination")
                    stop_signal = True
                    break
                if listing_too_old(l, today_date, RECENT_DAYS_WINDOW):
                    log(f"  ⏹ PF: listing older than {RECENT_DAYS_WINDOW}d ({l.get('listed')}) — stopping pagination")
                    stop_signal = True
                    break
                collected.append(l)
                seen_in_run.add(uid)

            await asyncio.sleep(random.uniform(2, 4))
        except Exception as e:
            log(f"  ✗ PF page {page_num} ERROR: {e}")
            break

    log(f"  ✓ PF Dubai (PF): collected {len(collected)} new/recent listings")
    return collected


async def scrape_bayut_dubai_wide(page, previous_uids, visible_mode=False):
    """Bayut: incremental scrape across pages. Stop when known UID OR listing too old.
    Note: Bayut card has no listed-date; we only get it via enrichment AFTER collection.
    So during paging we only stop on known UID; date check happens post-enrichment to
    decide if we should keep going (handled implicitly by sort=date_desc + UID overlap)."""
    log("  Bayut: Dubai (Bayut) — Dubai-wide")
    today_date = datetime.now().date()
    collected = []
    seen_in_run = set()
    stop_signal = False

    for page_num in range(1, MAX_PAGES_COLD_START + 1):
        if stop_signal:
            break
        url = bayut_url_with_page(BAYUT_BASE_URL, page_num)
        log(f"  Bayut page {page_num}: {url[:100]}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(4, 8))

            if await check_bayut_blocked(page):
                if visible_mode:
                    solved = await wait_for_bayut_captcha(page, visible_mode, "Dubai (Bayut)")
                    if not solved:
                        log("  ✗ Bayut: CAPTCHA not solved — aborting Bayut scrape")
                        return collected
                else:
                    log("  ⚠ Bayut blocked (headless) — try --visible")
                    return collected

            await human_mouse(page)
            await asyncio.sleep(random.uniform(1, 2))
            await human_scroll(page)
            await asyncio.sleep(random.uniform(1, 2))

            result = await page.evaluate(BAYUT_EXTRACT_JS)
            page_listings = result.get("listings", [])
            if not page_listings:
                log(f"  ✓ Bayut page {page_num}: 0 listings — end of results")
                break

            log(f"  ✓ Bayut page {page_num}: {len(page_listings)} listings (from {result.get('articlesFound', 0)} articles)")

            for l in page_listings:
                uid = l.get("uid")
                if not uid:
                    continue
                if uid in seen_in_run:
                    continue
                if uid in previous_uids:
                    log(f"  ⏹ Bayut: hit known UID {uid} — stopping pagination")
                    stop_signal = True
                    break
                # Bayut card has no date; stop only on known-UID overlap (newest-first sort).
                # If listed got cached from previous data, also enforce age.
                if listing_too_old(l, today_date, RECENT_DAYS_WINDOW):
                    log(f"  ⏹ Bayut: listing older than {RECENT_DAYS_WINDOW}d ({l.get('listed')}) — stopping pagination")
                    stop_signal = True
                    break
                collected.append(l)
                seen_in_run.add(uid)

            await asyncio.sleep(random.uniform(3, 6))
        except Exception as e:
            log(f"  ✗ Bayut page {page_num} ERROR: {e}")
            break

    log(f"  ✓ Bayut Dubai (Bayut): collected {len(collected)} new/recent listings")
    return collected


# ─── CLEANUP PHASES ────────────────────────────────────────────────────────
def soft_delete_sweep(raw_data, scraped_uids):
    """Mark listings as removed if not seen this run AND last_seen is past grace."""
    today_date = datetime.now().date()
    grace_cutoff = today_date - timedelta(days=SOFT_DELETE_GRACE_DAYS)
    soft_deleted = 0
    for l in raw_data["listings"]:
        if l.get("removed"):
            continue
        uid = l.get("uid")
        if uid in scraped_uids:
            continue
        # Wasn't seen this run. Mark removed (the spec says: on first miss, mark stale).
        last_seen = l.get("last_seen") or l.get("scrape_date") or ""
        try:
            ls_date = datetime.strptime(last_seen[:10], "%Y-%m-%d").date() if last_seen else today_date
        except (ValueError, TypeError):
            ls_date = today_date
        # Mark on first miss
        l["removed"]        = True
        l["removed_date"]   = TODAY
        l["removed_reason"] = "stale"
        soft_deleted += 1
    if soft_deleted:
        log(f"  🗑 Soft-deleted {soft_deleted} listings (not in current scrape)")
    return soft_deleted


def thirty_day_cleanup(raw_data):
    """Mark listings older than OLD_LISTING_DAYS as removed (old_30d)."""
    today_date = datetime.now().date()
    cutoff = today_date - timedelta(days=OLD_LISTING_DAYS)
    marked = 0
    for l in raw_data["listings"]:
        if l.get("removed"):
            continue
        listed = l.get("listed", "")
        if not listed:
            continue
        try:
            ld = datetime.strptime(listed[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if ld < cutoff:
            l["removed"]        = True
            l["removed_date"]   = TODAY
            l["removed_reason"] = "old_30d"
            marked += 1
    if marked:
        log(f"  🕒 Marked {marked} listings older than {OLD_LISTING_DAYS}d as removed")
    return marked


def expire_grace(raw_data):
    """Permanently drop listings where removed=True AND removed_date < TODAY - 7 days."""
    today_date = datetime.now().date()
    cutoff = today_date - timedelta(days=SOFT_DELETE_GRACE_DAYS)
    before = len(raw_data["listings"])
    kept = []
    for l in raw_data["listings"]:
        if not l.get("removed"):
            kept.append(l)
            continue
        rd = l.get("removed_date")
        if not rd:
            kept.append(l)
            continue
        try:
            rd_date = datetime.strptime(rd[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            kept.append(l)
            continue
        if rd_date < cutoff:
            # past grace — drop permanently
            continue
        kept.append(l)
    raw_data["listings"] = kept
    dropped = before - len(kept)
    if dropped:
        log(f"  🧹 Permanently dropped {dropped} listings past grace window")
    return dropped


# ─── MAIN ────────────────────────────────────────────────────────────────────
async def main():
    argv = sys.argv[1:]
    pf_only     = "--pf-only" in argv
    bayut_only  = "--bayut-only" in argv
    dry_run     = "--dry-run" in argv
    visible     = "--visible" in argv
    no_process  = "--no-process" in argv

    log(f"═══ Dubai Combined Scraper — {TODAY} ═══")
    mode_str = "PF only" if pf_only else "Bayut only" if bayut_only else "Full scan"
    log(f"Mode: {mode_str}{' (VISIBLE)' if visible else ''}{' (DRY RUN)' if dry_run else ''}")
    log(f"Filters: <= AED {PRICE_MAX:,}, beds {BEDS_MIN}-{BEDS_MAX}+, sorted newest first")
    log(f"Cold-start cap: {MAX_PAGES_COLD_START} pages; recent window: {RECENT_DAYS_WINDOW}d")

    # Cleanup old logs
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
    previous_uids = set(l["uid"] for l in raw_data.get("listings", []) if l.get("uid") and not l.get("removed"))
    log(f"Loaded {len(raw_data.get('listings', []))} existing listings ({len(previous_uids)} active UIDs)")

    start_time = time.time()
    scraped_uids = set()

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
            log("PF: Dubai (PF)")
            await warmup_visit(page, "propertyfinder.ae")
            pf_listings = await scrape_pf_dubai_wide(page, previous_uids, visible_mode=visible)
            if pf_listings:
                merge_listings(raw_data, pf_listings)
                for l in pf_listings:
                    if l.get("uid"):
                        scraped_uids.add(l["uid"])
            log(f"PF done: {len(pf_listings)} listings collected")

        # ── Bayut ────────────────────────────────────────────────────────
        if not pf_only:
            log("\n── Bayut ──")
            log("Bayut: Dubai (Bayut)")
            await warmup_visit(page, "bayut.com")
            if visible and await check_bayut_blocked(page):
                solved = await wait_for_bayut_captcha(page, visible, "Bayut Homepage")
                if not solved:
                    log("  ✗ Could not get past Bayut CAPTCHA — skipping Bayut")
            bayut_listings = await scrape_bayut_dubai_wide(page, previous_uids, visible_mode=visible)
            if bayut_listings:
                # Enrich Bayut dates before merging (for accurate "listed" field)
                await enrich_bayut_dates(context, bayut_listings, raw_data)
                merge_listings(raw_data, bayut_listings)
                for l in bayut_listings:
                    if l.get("uid"):
                        scraped_uids.add(l["uid"])
            log(f"Bayut done: {len(bayut_listings)} listings collected")

        # ── Save cookies ─────────────────────────────────────────────────
        try:
            if not pf_only:
                await context.storage_state(path=COOKIES_BAYUT)
                log(f"Cookies saved → {COOKIES_BAYUT}")
            if not bayut_only:
                await context.storage_state(path=COOKIES_PF)
                log(f"Cookies saved → {COOKIES_PF}")
        except Exception as e:
            log(f"  ⚠ Could not save cookies: {e}")

        await browser.close()

    # ── Cleanup phases ──────────────────────────────────────────────────
    # NOTE: soft_delete_sweep() is intentionally DISABLED here. With the
    # incremental newest-first scraper that early-stops at the first known
    # UID, scraped_uids is by design incomplete — it only contains the new
    # listings at the top of the feed. Marking everything else "stale" was
    # producing false positives on every rerun. The dashboard now shows a
    # "⚠ Nd unseen" warning chip in the UI based on last_seen, so the user
    # can spot-check possible delistings without auto-removal. Real cleanup
    # is purely age-based via thirty_day_cleanup().

    # One-time revive: if a previous (buggy) run marked listings as
    # removed_reason=="stale", revert them — they were false positives.
    revived = 0
    for l in raw_data["listings"]:
        if l.get("removed") and l.get("removed_reason") == "stale":
            l["removed"]        = False
            l["removed_date"]   = None
            l["removed_reason"] = None
            revived += 1
    if revived:
        log(f"  ↺ Revived {revived} listings previously mis-flagged as stale")

    log("\n── 30-day cleanup ──")
    thirty_day_cleanup(raw_data)

    log("\n── Grace expiry ──")
    expire_grace(raw_data)

    # ── Save raw data ────────────────────────────────────────────────────
    save_raw(raw_data)

    elapsed = time.time() - start_time
    log(f"\nScraping complete in {elapsed:.0f}s ({elapsed/60:.1f} min)")

    # ── Run process_combined.py ──────────────────────────────────────────
    if no_process:
        log("\n--no-process flag set — skipping process_combined.py (server will run it)")
    elif not dry_run:
        log("\n── Running process_combined.py ──")
        process_script = os.path.join(SCRIPTS, "process_combined.py")
        if os.path.exists(process_script):
            result = subprocess.run(
                [sys.executable, process_script],
                capture_output=True, text=True, cwd=BASE
            )
            print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)
            if result.returncode == 0:
                log("✓ process_combined.py completed successfully")
            else:
                log(f"✗ process_combined.py failed (exit code {result.returncode})")
        else:
            log(f"✗ process_combined.py not found at {process_script}")
    else:
        log("\nDRY RUN — skipping process_combined.py")

    active = sum(1 for l in raw_data["listings"] if not l.get("removed"))
    removed = sum(1 for l in raw_data["listings"] if l.get("removed"))
    log(f"\n═══ SUMMARY ═══")
    log(f"Total listings: {len(raw_data['listings'])} ({active} active, {removed} removed)")
    log(f"This run: {len(scraped_uids)} UIDs scraped")
    log(f"Time: {elapsed:.0f}s")
    log(f"Output: {RAW_PATH}")
    if not dry_run and not no_process:
        log(f"Published: combined_deals.json + combined.html")


if __name__ == "__main__":
    asyncio.run(main())
