#!/usr/bin/env python3
"""
AUTO SCRAPE TRANSACTIONS — Dubai-wide Bayut transactions scraper.

Scrapes the Bayut "property-market-analysis/transactions" page (≤ AED 2.3M, 2-7+ beds).
Page has NO __NEXT_DATA__; we DOM-scrape the transaction table directly.

Usage:
    python scripts/auto_scrape_transactions.py
    python scripts/auto_scrape_transactions.py --visible
    python scripts/auto_scrape_transactions.py --no-process
    python scripts/auto_scrape_transactions.py --dry-run

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
RAW_PATH   = os.path.join(BASE, "raw_data_transactions.json")
SCRIPTS    = os.path.join(BASE, "scripts")
LOG_DIR    = os.path.join(BASE, "logs")
COOKIES_BAYUT = os.path.join(SCRIPTS, "cookies_bayut.json")
TODAY      = datetime.now().strftime("%Y-%m-%d")

os.makedirs(LOG_DIR, exist_ok=True)

# ─── CONSTANTS ───────────────────────────────────────────────────────────────
MAX_PAGES_COLD_START = 5
RECENT_DAYS_WINDOW   = 7      # transactions have DLD reporting lag
OLD_LISTING_DAYS     = 30     # hard cleanup
PRICE_MAX            = 2_300_000

BAYUT_TX_URL = (
    "https://www.bayut.com/property-market-analysis/transactions/sale/"
    "2,3,4,5,6,7,8+-bedroom-property/"
    f"?price_max={PRICE_MAX}"
)

CAPTCHA_SIGNAL = os.path.join(BASE, ".captcha_signal")


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ─── RAW DATA I/O ──────────────────────────────────────────────────────────
def load_raw():
    if os.path.exists(RAW_PATH):
        try:
            with open(RAW_PATH) as f:
                d = json.load(f)
            if "transactions" not in d:
                d["transactions"] = []
            return d
        except (json.JSONDecodeError, IOError):
            pass
    return {"transactions": [], "last_updated": TODAY, "total_transactions": 0}


def save_raw(data):
    data["last_updated"] = TODAY
    data["total_transactions"] = len(data.get("transactions", []))
    tmp = RAW_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, RAW_PATH)
    log(f"raw_data_transactions.json saved — {data['total_transactions']} total transactions")


# ─── BROWSER HELPERS ──────────────────────────────────────────────────────
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


# ─── CAPTCHA SIGNAL FILE ──────────────────────────────────────────────────
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


# ─── BAYUT-PAGE READINESS / CAPTCHA DETECTION ─────────────────────────────
async def check_bayut_blocked(page):
    return await page.evaluate("""
        (() => {
            const hasNav = !!document.querySelector('nav, header, [class*="navbar"], [class*="header"]');
            const hasFooter = !!document.querySelector('footer, [class*="footer"]');
            const hasArticles = document.querySelectorAll('article').length > 0;
            const hasSearch = !!document.querySelector('input[type="search"], [class*="search"], form[action*="search"]');
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


async def bayut_page_is_ready(page):
    try:
        return await page.evaluate("""
            (() => {
                const hasNav = !!document.querySelector('nav, header, [class*="navbar"], [class*="header"]');
                const hasFooter = !!document.querySelector('footer, [class*="footer"]');
                const hasTxRows = document.querySelectorAll('tr[aria-label="Transaction"]').length > 0;
                const hasArticles = document.querySelectorAll('article').length > 0;
                const bodyLen = (document.body && document.body.innerText || '').length;
                return hasTxRows || ((hasNav && hasFooter) || hasArticles) || bodyLen > 5000;
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


# ─── DOM EXTRACTOR (pure DOM, no class deps) ──────────────────────────────
TX_EXTRACT_JS = r"""
() => {
    const rows = document.querySelectorAll('tr[aria-label="Transaction"][data-transaction-id]');
    const out = [];
    for (const row of rows) {
        const cells = row.children;
        if (cells.length < 7) continue;

        const dateText = (cells[0].textContent || '').trim();

        const locTd = cells[1];
        const spanTexts = Array.from(locTd.querySelectorAll('span'))
            .map(s => (s.textContent || '').trim())
            .filter(Boolean);
        // De-dup adjacent duplicates (status badge often double-wraps in nested spans)
        const deduped = [];
        spanTexts.forEach(t => {
            if (deduped.length === 0 || deduped[deduped.length - 1] !== t) deduped.push(t);
        });
        // Identify status by matching against known badge values. If the only badges
        // we see are community / sub-community names, status stays empty rather than
        // mis-pulling the last span (the previous bug).
        const KNOWN_STATUSES = ['Off-Plan', 'Off Plan', 'Initial Sale', 'Resale', 'Sale'];
        let status = '';
        const remaining = [];
        for (const t of deduped) {
            if (status === '' && KNOWN_STATUSES.indexOf(t) !== -1) {
                status = t;          // first match wins; don't push to remaining
            } else {
                remaining.push(t);
            }
        }
        const community    = remaining[0] || '';
        const subCommunity = remaining[1] || '';
        // Building name = whatever leading text is left after subtracting all spans
        let building = locTd.textContent || '';
        deduped.forEach(s => { building = building.split(s).join(''); });
        building = building.trim();

        const price = parseInt((cells[2].textContent || '').replace(/[^0-9]/g, ''), 10) || 0;
        const prop_type = (cells[3].textContent || '').trim().toLowerCase();
        const beds = parseInt((cells[4].textContent || '').trim(), 10) || 0;
        const sqft = parseInt((cells[5].textContent || '').replace(/[^0-9]/g, ''), 10) || 0;
        const plotText = (cells[6].textContent || '').trim();
        const plot_sqft = (plotText === '-' || !plotText) ? null
            : (parseInt(plotText.replace(/[^0-9]/g, ''), 10) || null);

        out.push({
            txId: row.getAttribute('data-transaction-id'),
            dateText: dateText,
            building: building,
            community: community,
            sub_community: subCommunity,
            status: status,
            price: price,
            prop_type: prop_type,
            beds: beds,
            sqft: sqft,
            plot_sqft: plot_sqft
        });
    }
    return { transactions: out, count: rows.length };
}
"""


# ─── PAGINATION URL ───────────────────────────────────────────────────────
def bayut_url_with_page(base_url, page_num):
    if page_num <= 1:
        return base_url
    if "?" in base_url:
        path, qs = base_url.split("?", 1)
        if not path.endswith("/"):
            path = path + "/"
        return f"{path}page-{page_num}/?{qs}"
    if not base_url.endswith("/"):
        base_url = base_url + "/"
    return f"{base_url}page-{page_num}/"


# ─── DATE PARSING ─────────────────────────────────────────────────────────
def parse_tx_date(s):
    """Convert '28 Apr 2026' → '2026-04-28'. Return None on failure."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%d %b %Y").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def tx_too_old(iso_date, today_date, window_days):
    if not iso_date:
        return False
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    return (today_date - d).days > window_days


# ─── INCREMENTAL SCRAPE LOOP ──────────────────────────────────────────────
async def scrape_bayut_transactions(page, previous_uids, visible_mode=False):
    log("  Bayut: Dubai Transactions")
    today_date = datetime.now().date()
    collected = []
    seen_in_run = set()
    stop_signal = False

    for page_num in range(1, MAX_PAGES_COLD_START + 1):
        if stop_signal:
            break
        url = bayut_url_with_page(BAYUT_TX_URL, page_num)
        log(f"  Bayut TX page {page_num}: {url[:110]}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(4, 8))

            if await check_bayut_blocked(page):
                if visible_mode:
                    solved = await wait_for_bayut_captcha(page, visible_mode, "Dubai Transactions")
                    if not solved:
                        log("  ✗ Bayut TX: CAPTCHA not solved — aborting")
                        return collected
                else:
                    log("  ⚠ Bayut TX blocked (headless) — try --visible")
                    return collected

            await human_mouse(page)
            await asyncio.sleep(random.uniform(1, 2))
            await human_scroll(page)
            await asyncio.sleep(random.uniform(1, 2))

            result = await page.evaluate(TX_EXTRACT_JS)
            page_txs = result.get("transactions", [])
            if not page_txs:
                log(f"  ✓ Bayut TX page {page_num}: 0 rows — end of results")
                break

            log(f"  ✓ Bayut TX page {page_num}: {len(page_txs)} rows extracted")

            for t in page_txs:
                tx_id = t.get("txId")
                if not tx_id:
                    continue
                uid = "tx-" + str(tx_id)
                if uid in seen_in_run:
                    continue

                iso_date = parse_tx_date(t.get("dateText", ""))
                if not iso_date:
                    # skip rows we can't date
                    continue

                if uid in previous_uids:
                    log(f"  ⏹ Bayut TX: hit known UID {uid} — stopping pagination")
                    stop_signal = True
                    break
                if tx_too_old(iso_date, today_date, RECENT_DAYS_WINDOW):
                    log(f"  ⏹ Bayut TX: row older than {RECENT_DAYS_WINDOW}d ({iso_date}) — stopping pagination")
                    stop_signal = True
                    break

                price = int(t.get("price") or 0)
                sqft  = int(t.get("sqft") or 0)
                psf   = round(price / sqft) if (price > 0 and sqft > 0) else None
                pt = (t.get("prop_type") or "").lower()
                if pt not in ("apartment", "villa", "townhouse"):
                    # tolerate unknown types but normalize
                    pt = pt or ""

                rec = {
                    "uid":              uid,
                    "transaction_date": iso_date,
                    "building":         t.get("building") or "",
                    "community":        t.get("community") or "",
                    "sub_community":    t.get("sub_community") or "",
                    "status":           t.get("status") or "",
                    "prop_type":        pt,
                    "beds":             int(t.get("beds") or 0),
                    "sqft":             sqft,
                    "plot_sqft":        t.get("plot_sqft"),
                    "price":            price,
                    "psf":              psf,
                    "scrape_date":      TODAY,
                    "source":           "Bayut Transactions",
                }
                collected.append(rec)
                seen_in_run.add(uid)

            await asyncio.sleep(random.uniform(3, 6))
        except Exception as e:
            log(f"  ✗ Bayut TX page {page_num} ERROR: {e}")
            break

    log(f"  ✓ Bayut Dubai Transactions: collected {len(collected)} new/recent transactions")
    return collected


# ─── MERGE / DEDUPE / CLEANUP ────────────────────────────────────────────
def merge_transactions(raw_data, fresh):
    """Append fresh into raw_data['transactions']; latest copy of duplicate uids wins."""
    by_uid = {t["uid"]: t for t in raw_data.get("transactions", []) if t.get("uid")}
    added = 0
    updated = 0
    for nt in fresh:
        uid = nt.get("uid")
        if not uid:
            continue
        if uid in by_uid:
            ex = by_uid[uid]
            ex.update(nt)
            updated += 1
        else:
            by_uid[uid] = nt
            added += 1
    raw_data["transactions"] = list(by_uid.values())
    log(f"  Merged: {added} new, {updated} updated, {len(raw_data['transactions'])} total")
    return added


def thirty_day_cleanup(raw_data):
    """Hard-drop transactions older than OLD_LISTING_DAYS days."""
    today_date = datetime.now().date()
    cutoff = today_date - timedelta(days=OLD_LISTING_DAYS)
    before = len(raw_data.get("transactions", []))
    kept = []
    for t in raw_data.get("transactions", []):
        d = t.get("transaction_date", "")
        if not d:
            kept.append(t)
            continue
        try:
            dd = datetime.strptime(d, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            kept.append(t)
            continue
        if dd < cutoff:
            continue
        kept.append(t)
    raw_data["transactions"] = kept
    dropped = before - len(kept)
    if dropped:
        log(f"  🕒 Dropped {dropped} transactions older than {OLD_LISTING_DAYS}d")
    return dropped


# ─── MAIN ────────────────────────────────────────────────────────────────
async def main():
    argv = sys.argv[1:]
    visible    = "--visible" in argv
    no_process = "--no-process" in argv
    dry_run    = "--dry-run" in argv

    log(f"═══ Dubai Transactions Scraper — {TODAY} ═══")
    log(f"Mode: Bayut Transactions{' (VISIBLE)' if visible else ''}{' (DRY RUN)' if dry_run else ''}")
    log(f"Filters: <= AED {PRICE_MAX:,}, beds 2-7+")
    log(f"Cold-start cap: {MAX_PAGES_COLD_START} pages; recent window: {RECENT_DAYS_WINDOW}d")

    # Cleanup old logs (>2 days)
    import glob as _glob
    cutoff = time.time() - 2 * 86400
    for old_log in _glob.glob(os.path.join(LOG_DIR, "*.log")):
        try:
            if os.path.getmtime(old_log) < cutoff:
                os.remove(old_log)
        except OSError:
            pass

    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    stealth = Stealth()

    raw_data = load_raw()
    previous_uids = set(t["uid"] for t in raw_data.get("transactions", []) if t.get("uid"))
    log(f"Loaded {len(raw_data.get('transactions', []))} existing transactions ({len(previous_uids)} UIDs)")

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

        storage_state = COOKIES_BAYUT if os.path.exists(COOKIES_BAYUT) else None
        if storage_state:
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

        log("\n── Bayut Transactions ──")
        await warmup_visit(page, "bayut.com")
        if visible and await check_bayut_blocked(page):
            solved = await wait_for_bayut_captcha(page, visible, "Bayut Homepage")
            if not solved:
                log("  ✗ Could not get past Bayut CAPTCHA — skipping")

        fresh = await scrape_bayut_transactions(page, previous_uids, visible_mode=visible)
        if fresh:
            merge_transactions(raw_data, fresh)

        # Save cookies
        try:
            await context.storage_state(path=COOKIES_BAYUT)
            log(f"Cookies saved → {COOKIES_BAYUT}")
        except Exception as e:
            log(f"  ⚠ Could not save cookies: {e}")

        await browser.close()

    # ── 30-day hard cleanup ──
    log("\n── 30-day cleanup ──")
    thirty_day_cleanup(raw_data)

    # ── Save ──
    save_raw(raw_data)

    elapsed = time.time() - start_time
    log(f"\nScraping complete in {elapsed:.0f}s ({elapsed/60:.1f} min)")

    # ── Run process_transactions.py ──
    if no_process:
        log("\n--no-process flag set — skipping process_transactions.py (server will run it)")
    elif not dry_run:
        log("\n── Running process_transactions.py ──")
        process_script = os.path.join(SCRIPTS, "process_transactions.py")
        if os.path.exists(process_script):
            result = subprocess.run(
                [sys.executable, process_script],
                capture_output=True, text=True, cwd=BASE
            )
            print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)
            if result.returncode == 0:
                log("✓ process_transactions.py completed successfully")
            else:
                log(f"✗ process_transactions.py failed (exit code {result.returncode})")
        else:
            log(f"✗ process_transactions.py not found at {process_script}")
    else:
        log("\nDRY RUN — skipping process_transactions.py")

    total = len(raw_data.get("transactions", []))
    log(f"\n═══ SUMMARY ═══")
    log(f"✓ Bayut Dubai Transactions: {total} transactions")
    log(f"Time: {elapsed:.0f}s")
    log(f"Output: {RAW_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
