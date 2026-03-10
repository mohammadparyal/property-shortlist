# Automated Scraping Plan — Property Finder & Bayut

## The Problem With Current Manual Process

Every scan requires:
1. Manually navigating to 10+ URLs in Chrome (9 communities × 2 sources, plus DAMAC Hills 2 dual range)
2. Running JavaScript in the browser console via the MCP extension
3. Reading output in 900-char chunks (because MCP truncates)
4. Assembling JSON in Python manually
5. Watching for boundary quote-stripping bugs and fixing by hand
6. Saving each community one by one

A full scan takes 45–90 minutes of active work. Automation gets it to under 5 minutes, fully unattended.

---

## Why These Sites Are Tricky

### Property Finder
- Uses **Cloudflare** protection (JS challenge, bot fingerprinting)
- Data is **server-rendered** inside `__NEXT_DATA__` JSON in the HTML
- This is good news: once you get the HTML, you get all the data without executing any page JS
- If you can bypass Cloudflare, a simple HTTP request is enough — no real browser needed

### Bayut
- Also uses **Cloudflare** + likely **DataDome** (advanced bot detection)
- Data is **client-side rendered** (React) — you need a real browser to execute JS and render `<article>` elements
- More aggressive bot detection than PF
- Requires full browser simulation with human-like behaviour

---

## Recommended Tool: Playwright (Python)

### Why Playwright over alternatives

| Tool | Reason |
|------|--------|
| **Playwright** ✅ | Modern, fast, Python-native, excellent stealth options, handles both sites |
| Selenium + undetected-chromedriver | Works but slower, older API, harder to maintain |
| Scrapy + Splash | Overkill, poor JS rendering for Bayut |
| Requests / httpx | Too easily blocked by Cloudflare without extra work |
| Puppeteer | JS only, no Python |
| Bright Data / Oxylabs | Paid residential proxies — overkill for this use case |

### Stealth library
Use **`playwright-stealth`** (Python package: `playwright-stealth`). It patches Playwright to:
- Remove the `navigator.webdriver = true` flag that bots expose
- Fake Chrome's plugin list, languages, and hardware fingerprint
- Spoof canvas fingerprint, WebGL, audio context
- Make `window.chrome` look like a real installed Chrome

This is the single most important thing for bypassing Cloudflare and DataDome.

---

## Architecture

```
auto_scrape.py
├── scrape_pf(community, location_id, beds, price_min, price_max)
│   └── Navigate → wait → extract __NEXT_DATA__ → parse listings
│
├── scrape_bayut(community, url)
│   └── Navigate → scroll → wait for articles → extract DOM
│
├── save_community(community, listings)
│   └── combine_append.py logic (Bayut listings + PF listings)
│
└── main()
    ├── For each community: scrape_bayut() → save
    ├── For each community: scrape_pf() → save (DAMAC Hills 2 deduped)
    └── process_deals.py
```

Single script, one command, fully automated.

---

## Human-Like Behaviour Techniques (Critical for Bot Bypass)

These are what make the difference between getting blocked and not:

### 1. Random delays
Never fire requests at machine speed. Insert random sleeps:
- Between page loads: 3–7 seconds (random)
- Before running extraction: 1–3 seconds
- Between communities: 4–10 seconds
```python
import random, asyncio
await asyncio.sleep(random.uniform(3, 7))
```

### 2. Real browser viewport and user-agent
```python
browser = await playwright.chromium.launch(headless=True)
context = await browser.new_context(
    viewport={"width": 1366, "height": 768},
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    locale="en-US",
    timezone_id="Asia/Dubai",
)
```

### 3. Mouse movement before extraction
Bayut specifically watches for mouse events. Before scraping, simulate a few random moves:
```python
await page.mouse.move(random.randint(100, 800), random.randint(100, 600))
await asyncio.sleep(0.5)
await page.mouse.move(random.randint(200, 700), random.randint(200, 500))
```

### 4. Scroll the page naturally
Both sites use lazy loading and also check scroll behaviour:
```python
await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
await asyncio.sleep(1)
await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
await asyncio.sleep(1)
```

### 5. Keep a persistent browser context (cookies)
First visit builds up cookies like a real user. Reuse the same context across all communities rather than a fresh browser per URL.

### 6. Never run headless=False unless debugging
Counterintuitively, `headless=False` (visible browser) is sometimes MORE detectable because the window is 0x0 or offscreen. `headless=True` with stealth is better.

### 7. Handle Cloudflare challenges
If Cloudflare shows a 5-second JS challenge page, wait for it:
```python
# After navigation, check if we hit Cloudflare
if "Just a moment" in await page.title():
    await asyncio.sleep(8)  # Wait for CF challenge to complete
    await page.wait_for_load_state("networkidle")
```

---

## Property Finder — Specific Approach

PF is the easier target. The data lives in `<script id="__NEXT_DATA__">` which is server-rendered, so:

1. Navigate to the search URL
2. Wait for `networkidle` (or wait for `#__NEXT_DATA__` element)
3. Extract `document.getElementById('__NEXT_DATA__').textContent`
4. Parse JSON: `data.props.pageProps.searchResult.listings[].property`
5. No scrolling needed — all page 1 results are in that JSON

**Alternative for PF only:** Once you have valid Cloudflare cookies from one browser session, you can sometimes make plain `httpx` requests with those cookies for subsequent calls. But Playwright is simpler and more reliable.

**All 9 PF URLs (ready to use):**
```python
PF_COMMUNITIES = [
    ("DAMAC Lagoons",       11559, 3, 2_000_000, 3_000_000),
    ("DAMAC Islands",       14611, 4, 2_000_000, 3_000_000),
    ("DAMAC Islands 2",     17773, 4, 2_000_000, 3_000_000),
    ("The Valley",          10757, 3, 2_000_000, 3_000_000),
    ("DAMAC Hills 2",       125,   3, 1_000_000, 2_000_000),  # sub-2M range
    ("DAMAC Hills 2",       125,   3, 2_000_000, 3_000_000),  # 2M-3M range (dedupe after)
    ("Villanova",           8780,  3, 2_000_000, 3_000_000),
    ("DAMAC Hills",         129,   3, 2_000_000, 3_000_000),
    ("Dubai Hills Estate",  105,   3, 2_000_000, 3_000_000),
    ("Tilal Al Ghaf",       9885,  3, 2_000_000, 3_000_000),
]
# URL template:
# https://www.propertyfinder.ae/en/search?l={id}&c=1&bdr%5B%5D={beds}&pf={min}&pt={max}&ob=pr
```

---

## Bayut — Specific Approach

Bayut needs real browser rendering. Approach:

1. Navigate to the Bayut URL
2. Wait 4+ seconds
3. Scroll down slowly (triggers lazy-loaded articles)
4. Wait for `article` elements to appear
5. Extract via `page.evaluate()` running the existing bayut_extractor.js logic
6. No chunks needed — `page.evaluate()` returns the full result directly (not limited by MCP truncation)

**All 9 Bayut URLs (ready to use):**
```python
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
]
```

---

## What To Do If Blocked

Sites evolve their bot detection. In order of severity:

### Level 1 — Soft block (403, redirect to CAPTCHA)
- Add longer random delays between requests
- Rotate User-Agent strings
- Clear cookies and restart browser context

### Level 2 — Cloudflare challenge loops
- Try `playwright-stealth` if not already applied
- Wait longer on challenge page (10–15 seconds)
- Add `--disable-blink-features=AutomationControlled` Chrome flag

### Level 3 — Hard block (DataDome, Kasada)
- Use a **residential proxy** service: Bright Data, Oxylabs, Smartproxy. ~$10–20/month for this volume.
- Route Playwright through the proxy: `context = browser.new_context(proxy={"server": "..."})`
- Residential proxies are IP addresses from real ISPs, so bot detectors treat them as normal users

### Level 4 — Persistent CAPTCHA
- Run Playwright non-headless (`headless=False`) on first run to solve CAPTCHA manually once
- Save cookies (`context.storage_state()`) and reuse for subsequent runs
- Cookies from a human-solved session typically last 24–48 hours

---

## Scheduling

Once the script works, schedule it using the existing scheduled tasks feature in Cowork, or a simple cron job on a server:

```
# cron — runs at 8:00 AM and 8:00 PM Dubai time (UTC+4)
0 4,16 * * * cd /path/to/dubai-property-shortlist && python3 scripts/auto_scrape.py >> logs/scrape.log 2>&1
```

Or use the Cowork scheduled task (8 AM and 8 PM GST):
- Task: run `python3 scripts/auto_scrape.py` from the repo folder
- Schedule: `0 4,16 * * *` (UTC, = 8AM/8PM Dubai)

---

## Packages Needed

```bash
pip install playwright playwright-stealth --break-system-packages
playwright install chromium   # downloads the browser binary
```

That's it — just two packages. Everything else (JSON parsing, file I/O, the existing process_deals.py logic) is already in the repo.

---

## Estimated Time Savings

| Step | Manual (current) | Automated |
|------|-----------------|-----------|
| Navigate to 10 PF URLs | 10 min | 0 (script handles) |
| Run extractor + read chunks | 30 min | 0 (no chunks needed) |
| Assemble JSON + fix boundary bugs | 15 min | 0 (no chunking) |
| Navigate to 9 Bayut URLs | 9 min | 0 (script handles) |
| Read Bayut slices + assemble | 15 min | 0 (full result returned) |
| Run process_deals.py | 1 min | 0 (auto-triggered) |
| **Total** | **~80 min active** | **~5 min unattended** |

---

## Summary Recommendation

1. **Use Playwright + playwright-stealth** — best balance of reliability and simplicity
2. **Property Finder**: navigate → extract `__NEXT_DATA__` → done (no DOM scraping needed)
3. **Bayut**: navigate → scroll → `page.evaluate()` bayut_extractor logic → done
4. **Human-like delays**: random 3–7s between pages, scroll simulation, mouse movement
5. **If blocked**: add residential proxy (Bright Data ~$15/month) — almost guaranteed to work
6. **No chunks, no manual JSON assembly** — `page.evaluate()` returns full result in one call
7. **Schedule with cron** — fully hands-off, runs twice a day

The main risk is Bayut's DataDome protection, which is more aggressive than PF's Cloudflare. If playwright-stealth alone doesn't work for Bayut, a residential proxy will solve it. PF should work reliably with just playwright-stealth.
