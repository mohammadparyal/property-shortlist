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

## Recent Changes (April 2026)
1. Fixed race condition in communities.json (Lock + atomic writes + batch toggle endpoint)
2. Connected scrapers to communities.json (--config flag, load_communities_from_config)
3. Added PF CAPTCHA pause/resume with Continue button
4. Added Bayut CAPTCHA pause/resume with smart positive/negative signal detection
5. Added stale listing cleanup to both villa and apartment scrapers
