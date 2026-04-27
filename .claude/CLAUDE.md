# Dubai Distress Deal Tracker — Project Context

## What This Is
A real-time property deal tracker for Dubai. Scrapes Property Finder + Bayut for villas and apartments, scores each listing against developer launch prices, and publishes dashboards on GitHub Pages.

## File Map

### Core
- `server.py` — Flask + Flask-SocketIO control panel (port 5000). Manages scraper subprocesses, WebSocket progress, CAPTCHA pause/resume, community config.
- `scraper_panel.html` — Web UI for the control panel. Start/stop scrapers, toggle communities, CAPTCHA Continue button.
- `communities.json` — Central config for all communities (16 villa, 11 apartment). Each entry has: name, enabled, beds_min, price ranges, PF config, Bayut config.

### Scrapers
- `scripts/auto_scrape.py` — Villa/townhouse scraper (PF + Bayut). Playwright + stealth.
- `scripts/auto_scrape_apartments.py` — Apartment scraper (same architecture).
- Both support: `--config <path>`, `--no-process`, `--visible`, `--pf-only`, `--bayut-only`, `--dry-run`

### Post-Processing
- `scripts/process_deals.py` — Scores villa deals → `dubai_deals.json` + `index.html`
- `scripts/process_apartments.py` — Scores apartment deals → `apartments_deals.json` + `apartments.html`

### Data
- `raw_data.json` — Villa raw scraped data
- `raw_data_apartments.json` — Apartment raw scraped data
- `dubai_deals.json` — Processed villa deals (for dashboard)
- `apartments_deals.json` — Processed apartment deals (for dashboard)
- `.captcha_signal` — Runtime signal file (JSON) for CAPTCHA pause/resume between scraper subprocess and server

### Dashboards (GitHub Pages)
- `index.html` — Villa dashboard
- `apartments.html` — Apartment dashboard

## Key Mechanisms

### CAPTCHA Handling
- **Signal file** (`.captcha_signal`): scraper writes `{status:"waiting", community:"..."}`, server's `/api/captcha/continue` writes `{status:"continue"}`, scraper clears after resuming.
- **PF**: CAPTCHA appears on community listing pages. Detected by absence of `__NEXT_DATA__` + Turnstile iframe/challenge text.
- **Bayut**: CAPTCHA appears on homepage first visit only. Detected using positive signals (nav, footer, articles = normal) vs negative signals (Turnstile iframe, challenge text on short page).
- **UI**: Orange alert with "I've solved it — Continue" button. Server parses stdout for `CAPTCHA:WAITING:` prefix.

### Stale Listing Cleanup
Both scrapers track UIDs found per community (`scraped_uids` dict). After all scraping, `cleanup_stale_listings()` removes listings not found in current run. Only cleans communities that were successfully scraped — preserves data if scrape failed.

### Config Loading
`load_communities_from_config(config_path, mode)` reads `communities.json` and builds PF/Bayut community tuples. Respects `enabled` field. Server passes `--config communities.json --no-process` when launching scrapers.

### Process Safety
- `threading.Lock()` (config_lock) for concurrent communities.json access
- Atomic writes: temp file + `os.replace()`
- Process group kill: `os.setsid` + `os.killpg` to cleanly stop Playwright + browser
- Unbuffered stdout: `python -u` flag for real-time progress streaming

## How to Run
```bash
cd /mnt/dubai-property-shortlist
python server.py
# Open http://localhost:5000/scraper_panel.html
```

### PF Data Extraction
- PF's `__NEXT_DATA__` uses nested structure: `searchResult.listings[].property` (not flat)
- `searchResult.properties` exists but is empty `[]` (truthy!) — must prefer `searchResult.listings`
- Size field is `{value: 1550, unit: "sqft"}` — already in sqft, do NOT multiply by 10.764
- Price field is `{value: 2600000, currency: "AED", ...}` — extract `.value`

### Bayut Date Enrichment
- Bayut listing cards don't include posted date — must fetch each detail page
- Uses in-browser `fetch()` with 5 parallel workers per batch (30 URLs/batch)
- Caches dates: carries over dates from existing raw_data to avoid re-fetching known listings
- Only enriches NEW listings that don't have a date yet

### Dashboard Filters
- Both dashboards have: community buttons, bed filter dropdown (3/4/5+), size filter dropdown
- Filters reset to "All" when community selection changes
- index.html uses `getFiltered()` + `renderTable()` + `applyFilters()`
- apartments.html uses `gF()` + `rT()` (minified names)

## How to Run
```bash
cd /mnt/dubai-property-shortlist
python server.py
# Open http://localhost:5000/scraper_panel.html
```

## Recent Changes (April 2026)
1. Fixed race condition in communities.json (Lock + atomic writes + batch toggle endpoint)
2. Connected scrapers to communities.json (--config flag, load_communities_from_config)
3. Added PF CAPTCHA pause/resume with Continue button
4. Added Bayut CAPTCHA pause/resume with smart positive/negative signal detection
5. Added stale listing cleanup to both villa and apartment scrapers
6. Fixed SQFT/PSF parsing: PF size is already in sqft (was incorrectly ×10.764). Fixed nested `item.property` unwrapping in PF extractor
7. Added bed/size filter dropdowns to both dashboards (reset on community change)
8. Sped up Bayut date enrichment: parallel fetch (5 workers), larger batches (30), date caching from raw_data

## Pending / Known Issues
- End-to-end test of full scraper flow still needed after all recent changes
- Dashboard GitHub Pages deployment needed after sqft fix + filter addition
