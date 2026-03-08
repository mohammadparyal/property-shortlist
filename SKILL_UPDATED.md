---
name: dubai-deal-scanner
description: Scan Bayut & Property Finder for distress property deals across 8 Dubai communities, with price history tracking, detail page links, and panic sell scoring.
---

## Dubai Deal Scanner — With Price History Tracking

You are a property deal scanner. Scrape listings from Bayut and Property Finder, compare against previous data for price changes, and update the dashboard.

---

## ⚠️ CRITICAL LESSONS LEARNED (March 2026 — Read Before Every Run)

### PF URL Format Changed in 2026 — Old Slug URLs Are Broken

**BROKEN (DO NOT USE):**
```
/en/buy/townhouses/dubai/damac-lagoons/?beds=3-any&price_min=2000000...
```

**WORKING (always use location-ID format):**
```
https://www.propertyfinder.ae/en/search?l={location_id}&c=1&bdr%5B%5D=3&pf=2000000&pt=3000000&ob=pr
```

**URL parameter meanings:**
- `c=1` → **BUY** listings. `c=2` is RENT — this is how Claude initially searched the wrong listings. Always verify `c=1` is in the URL.
- `bdr%5B%5D=3` → decoded: `bdr[]=3` = 3 bedrooms filter
- `pf` / `pt` → price from / price to in AED
- `ob=pr` → sort by **price ascending**. `ob=mr` is "most recent" — always use `ob=pr`

### PF Data Structure Changed in 2026

**BROKEN (old path — returns undefined):**
```javascript
data.props.pageProps.searchResult.properties[]
```

**WORKING (new path):**
```javascript
data.props.pageProps.searchResult.listings[].property
```

Each listing item wraps a `.property` object with: `reference`, `price.value`, `bedrooms`, `bathrooms`, `size.value`, `share_url`, `details_path`, `location.name`, `title`, `listed_date`, `completion_status`, `property_type`.

### Never Use append_community.py for PF When Bayut Data Also Exists

`append_community.py` **replaces** a community's entire data. If you've already saved Bayut listings and then save PF listings with `append_community.py`, you lose all Bayut data.

**Always use `combine_append.py` for PF listings:**
```bash
python3 scripts/combine_append.py < /tmp/community_raw.json
```
It preserves existing Bayut listings and appends fresh PF listings.

### process_deals.py HTML Update — Use Lambda in re.sub

The dashboard HTML update contains a `re.sub()` call that breaks when `json.dumps()` output contains `\u` Unicode escapes. The fix (already applied in process_deals.py):
```python
_replacement = 'const DATA = ' + json.dumps(output, default=str) + ';'
new_html = re.sub(
    r'const DATA = \{.*?\};',
    lambda m: _replacement,   # ← lambda prevents \u escape interpretation
    html,
    flags=re.DOTALL,
)
```
If this bug reappears: change the string replacement to `lambda m: replacement_string`.

---

## Step 0: Load Previous Data

Before scraping, read the EXISTING `dubai_deals.json` to build a price history lookup:

```python
import json, os
prev_map = {}
json_path = "/mnt/dubai-property-shortlist/dubai_deals.json"
if os.path.exists(json_path):
    with open(json_path) as f:
        prev_data = json.load(f)
    for pl in prev_data.get("listings", []):
        uid = pl.get("unique_id")
        if uid:
            prev_map[uid] = pl
```

---

## Step 1: Scrape All 8 Communities

### Property Finder — Location IDs and URLs (Verified March 2026)

All standard communities use this URL pattern. Replace `{id}` and price range as shown:
```
https://www.propertyfinder.ae/en/search?l={id}&c=1&bdr%5B%5D=3&pf=2000000&pt=3000000&ob=pr
```

| Community | Location ID | Special Notes |
|-----------|------------|---------------|
| DAMAC Lagoons | 11559 | Standard |
| DAMAC Islands | 14611 | **4BR ONLY** — use `bdr%5B%5D=4`, range `pf=2000000&pt=4000000` |
| The Valley | 10757 | Standard |
| DAMAC Hills 2 | 125 | **TWO RANGES** — scrape `pf=1000000&pt=2000000` AND `pf=2000000&pt=3000000`, dedupe by uid |
| Villanova | 8780 | Standard |
| DAMAC Hills | 129 | Standard |
| Dubai Hills Estate | 105 | Market now 6M+, expect ~0 results in 2M-3M (keep Bayut data) |
| Tilal Al Ghaf | 9885 | Market now 4.5M+, expect 0 results in 2M-3M (keep Bayut data) |

**Full working URLs:**
```
DAMAC Lagoons:      https://www.propertyfinder.ae/en/search?l=11559&c=1&bdr%5B%5D=3&pf=2000000&pt=3000000&ob=pr
DAMAC Islands:      https://www.propertyfinder.ae/en/search?l=14611&c=1&bdr%5B%5D=4&pf=2000000&pt=4000000&ob=pr
The Valley:         https://www.propertyfinder.ae/en/search?l=10757&c=1&bdr%5B%5D=3&pf=2000000&pt=3000000&ob=pr
DAMAC Hills 2 (A):  https://www.propertyfinder.ae/en/search?l=125&c=1&bdr%5B%5D=3&pf=1000000&pt=2000000&ob=pr
DAMAC Hills 2 (B):  https://www.propertyfinder.ae/en/search?l=125&c=1&bdr%5B%5D=3&pf=2000000&pt=3000000&ob=pr
Villanova:          https://www.propertyfinder.ae/en/search?l=8780&c=1&bdr%5B%5D=3&pf=2000000&pt=3000000&ob=pr
DAMAC Hills:        https://www.propertyfinder.ae/en/search?l=129&c=1&bdr%5B%5D=3&pf=2000000&pt=3000000&ob=pr
Dubai Hills Estate: https://www.propertyfinder.ae/en/search?l=105&c=1&bdr%5B%5D=3&pf=2000000&pt=3000000&ob=pr
Tilal Al Ghaf:      https://www.propertyfinder.ae/en/search?l=9885&c=1&bdr%5B%5D=3&pf=2000000&pt=3000000&ob=pr
```

#### Property Finder Extractor Script

Run `scripts/pf_extractor.js` via `javascript_tool` after navigating to each URL. Wait 3 seconds after navigation before running. The script is in the repo — copy and paste the full content with the community name changed at the bottom.

#### Reading PF Data — Chunk Method

The javascript_tool truncates output at ~2000 chars. Always read in 900-char slices:
```javascript
// First: check how many chunks
window._numChunks  // → e.g. 9

// Then read each chunk:
window._rawJson.slice(0,900)
window._rawJson.slice(900,1800)
window._rawJson.slice(1800,2700)
// ... continue until slice(n*900) returns only "]" or ends with "}]"
```

#### ⚠️ Chunk Boundary "Missing Quote" Bug

The MCP javascript_tool sometimes **strips the leading `"` character** from a chunk if that chunk begins exactly on a JSON string opening quote.

**How to detect:** If a chunk starts with a key name like `isOffPlan":true` instead of `"isOffPlan":true`, the `"` was stripped.

**Fix in Python assembly:** Manually prepend `"` to that chunk before concatenating:
```python
c3 = '"isOffPlan":true,...'   # Added leading " manually
full_json = c1 + c2 + c3 + ...
```

**Symptom also appears as:** A UID ending mid-string without a closing `"`, or a key appearing without its opening quote. Always `json.loads()` the assembled string to verify before saving — if it throws `JSONDecodeError`, inspect around the reported character position.

#### DAMAC Hills 2 — Dual Range Deduplication

```python
import json

# Load sub-2M results (saved to /tmp/dh2_sub2m.json)
with open('/tmp/dh2_sub2m.json') as f:
    sub2m = json.load(f)

# Load 2M-3M results (assembled from chunks)
range2 = [...]  # assembled from chunks

# Combine with deduplication
seen = set()
combined = []
for listing in sub2m + range2:
    if listing['uid'] not in seen:
        seen.add(listing['uid'])
        combined.append(listing)

print(f"Combined: {len(sub2m)} sub-2M + {len(range2)} 2M-3M = {len(combined)} unique")
with open('/tmp/community_raw.json', 'w') as f:
    json.dump(combined, f)
```

#### Saving PF Data

```bash
# Always use combine_append.py (preserves Bayut data)
python3 scripts/combine_append.py < /tmp/community_raw.json

# NEVER use append_community.py for PF — it wipes Bayut listings
```

---

### Bayut — Verified Working URLs (sort=price_asc, beds_min=3)

```
DAMAC Lagoons:      https://www.bayut.com/for-sale/townhouses/dubai/damac-lagoons/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000
DAMAC Islands:      https://www.bayut.com/for-sale/townhouses/dubai/dubailand/damac-islands/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000
                    ⚠️ MUST include dubailand/ — using damac-islands/ alone gives 0 results
The Valley:         https://www.bayut.com/for-sale/townhouses/dubai/the-valley-by-emaar/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000
                    ⚠️ MUST use the-valley-by-emaar not the-valley
DAMAC Hills 2:      https://www.bayut.com/for-sale/townhouses/dubai/damac-hills-2-akoya-by-damac/?sort=price_asc&beds_min=3&price_min=1000000&price_max=2000000
                    AND: price_min=2000000&price_max=3000000
Villanova:          https://www.bayut.com/for-sale/townhouses/dubai/dubailand/villanova/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000
                    ⚠️ MUST include dubailand/
DAMAC Hills:        https://www.bayut.com/for-sale/townhouses/dubai/damac-hills/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000
Dubai Hills Estate: https://www.bayut.com/for-sale/townhouses/dubai/dubai-hills-estate/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000
                    Note: market now 6M+, price filter won't restrict much
Tilal Al Ghaf:      https://www.bayut.com/for-sale/townhouses/dubai/tilal-al-ghaf/?sort=price_asc&beds_min=3&price_min=2000000&price_max=3000000
                    Note: market now 4.5M+
```

⚠️ **Bayut does NOT use `__NEXT_DATA__`** — use DOM extraction via `article` elements.

Always navigate to bayut.com BUY side (never rent). Wait 4 seconds after navigation.

**Bayut extractor script** (in `scripts/bayut_extractor.js`):
```javascript
(function(community) {
  const results = [];
  document.querySelectorAll('article').forEach(a => {
    const link = a.querySelector('a[href*="details-"]');
    const href = link ? link.href : '';
    const idMatch = href.match(/details-(\d+)/);
    if (!idMatch) return;
    const uid = 'bayut-' + idMatch[1];
    const lines = a.innerText.split('\n').map(l=>l.trim()).filter(l=>l&&l!=='|');
    const aedIdx = lines.indexOf('AED');
    const price = aedIdx >= 0 ? parseInt((lines[aedIdx+1]||'').replace(/\D/g,''))||0 : 0;
    const thIdx = lines.findIndex(l=>l==='Townhouse'||l==='Villa');
    const beds = thIdx >= 0 ? parseInt(lines[thIdx+1])||0 : 0;
    const baths = thIdx >= 0 ? parseInt(lines[thIdx+2])||0 : 0;
    const sqftLine = lines.find(l=>l.endsWith('sqft'));
    const sqft = sqftLine ? parseInt(sqftLine.replace(/\D/g,'')) : 0;
    const areaIdx = lines.indexOf('Area:');
    const title = areaIdx >= 0 ? (lines[areaIdx+2]||'') : '';
    const locLine = areaIdx >= 0 ? (lines[areaIdx+3]||'') : '';
    const cluster = locLine.split(',')[0].trim();
    const isOffPlan = lines.some(l=>l==='Off-Plan');
    if (price>0 && beds>=3) results.push({uid,href,price,beds,baths,sqft,title,cluster,community,isOffPlan});
  });
  window._raw = results;
  return JSON.stringify({count:results.length, data:results});
})('DAMAC Lagoons')
```

Bayut output is also truncated — read in slices of 12 listings: `.slice(0,12)`, `.slice(12,24)`.
Listing dates NOT available on search pages — use `""` for new Bayut listings.

**Save Bayut data:**
```bash
python3 scripts/append_community.py '[{...}]'
# append_community.py is fine for Bayut-only saves (replaces community data)
```

---

## Step 2: Generate Unique IDs

Every listing MUST get a `unique_id`:
- Bayut: extract from detail URL → `bayut-{id}` (e.g. `bayut-13708807`)
- PropertyFinder: use reference → `pf-{ref}` (e.g. `pf-MPS-46656`)
- Fallback: `{source}-{community}-{cluster}-{beds}br-{price}`

---

## Step 3: Price History Merge

```python
uid = listing["unique_id"]
if uid in prev_map:
    old = prev_map[uid]
    old_price = old.get("price", 0)
    new_price = listing["price"]
    history = list(old.get("price_history") or [])
    slash = old.get("slash_price") or old_price

    if new_price < old_price:
        history.insert(0, {"date": today, "old_price": old_price, "new_price": new_price, "change": "drop"})
        listing["price_tag"] = "drop"
    elif new_price > old_price:
        history.insert(0, {"date": today, "old_price": old_price, "new_price": new_price, "change": "increase"})
        listing["price_tag"] = "increase"
    else:
        listing["price_tag"] = old.get("price_tag")

    slash = max(slash, new_price, old_price)
    listing["slash_price"] = slash if slash > new_price else None
    listing["price_history"] = history[:10]
else:
    listing["slash_price"] = None
    listing["price_history"] = []
    listing["price_tag"] = None
```

---

## Step 4: Deal Scoring

**Launch benchmarks:**
| Community | 3BR | 4BR |
|-----------|-----|-----|
| DAMAC Lagoons | 1,400,000 | 1,700,000 |
| DAMAC Islands | — | 2,250,000 |
| The Valley | 1,530,000 | 2,100,000 |
| DAMAC Hills 2 | 800,000 | 1,200,000 |
| Villanova | 1,445,000 | 2,000,000 |
| DAMAC Hills | 1,500,000 | 2,200,000 |
| Dubai Hills Estate | 2,100,000 | 2,600,000 |
| Tilal Al Ghaf | 1,260,000 | 1,800,000 |

```python
score = base + signal_bonus + panic_bonus
# base = max(0, round(90 - pct_vs_launch * 0.88))
# pct_vs_launch = (price - launch) / launch * 100
# signal_bonus = 3 × count of distress keywords in title
# panic_bonus = 5 if listed >= 2026-03-01, else 0
# Distress keywords: motivated|must sell|urgent|distress|investor deal|
#                    below (op|launch|market)|genuine resale|payment plan|
#                    price drop|quick exit|handover soon
```

---

## Step 5: Output JSON Schema

Each listing MUST use these EXACT field names:

```json
{
  "community": "DAMAC Lagoons",
  "cluster": "Nice",
  "price": 2500000,
  "beds": 4,
  "baths": 3,
  "sqft": 2277,
  "title": "Motivated Seller | Urgent Sale",
  "source": "PropertyFinder",
  "listed": "2026-03-03",
  "status": "off_plan",
  "signals": "Motivated Seller, Urgent Sale",
  "ref": "MPS-46656",
  "link": "https://www.propertyfinder.ae/...",
  "unique_id": "pf-MPS-46656",
  "note": "",
  "panic_period": true,
  "deal_score": 56,
  "pct_vs_launch": 47.1,
  "launch_price": 1700000,
  "psf": 1098,
  "slash_price": 2600000,
  "price_history": [{"date": "2026-03-09", "old_price": 2600000, "new_price": 2500000, "change": "drop"}],
  "price_tag": "drop"
}
```

❌ WRONG: `price_aed`, `bedrooms`, `bathrooms`, `size_sqft`, `price_psf`, `listed_date`
✅ CORRECT: `price`, `beds`, `baths`, `sqft`, `psf`, `listed`

---

## Step 6: Write Output Files

```bash
# Just run the process script — it handles both JSON and HTML:
python3 scripts/process_deals.py
```

Or manually:
```python
# 1. Write JSON
with open(JSON_OUT, "w") as f:
    json.dump(output, f, indent=2, default=str)

# 2. Update HTML dashboard — MUST use lambda to avoid \u escape bug
import re
with open(HTML_OUT) as f:
    html = f.read()
_replacement = 'const DATA = ' + json.dumps(output, default=str) + ';'
new_html = re.sub(
    r'const DATA = \{.*?\};',
    lambda m: _replacement,   # ← lambda is essential here
    html,
    flags=re.DOTALL,
)
with open(HTML_OUT, "w") as f:
    f.write(new_html)
```

---

## Step 7: Verify

- Check all listings have `unique_id`, `price_history`, `slash_price`, `price_tag`
- Check no null prices or deal_scores
- Count and report price drops/increases detected
- Verify community counts add up to total

---

## Full Run Order

```
1. Reset (optional):     python3 scripts/append_community.py --reset

2. Bayut scraping (8 communities):
   - Navigate to each Bayut URL
   - Run bayut_extractor.js
   - Read window._raw in slices of 12
   - Save: python3 scripts/append_community.py '[{...}]'

3. PF scraping (8 communities):
   - Navigate to each PF URL (verify c=1, ob=pr in URL)
   - Run pf_extractor.js (from scripts/pf_extractor.js)
   - Read window._numChunks → read that many .slice(n*900,(n+1)*900) chunks
   - Assemble in Python (watch for boundary " stripping bug)
   - json.loads() to verify, save to /tmp/community_raw.json
   - Save: python3 scripts/combine_append.py < /tmp/community_raw.json

4. Special: DAMAC Islands → use 4BR filter + wider price range
5. Special: DAMAC Hills 2 → scrape two price ranges, dedupe, then combine_append
6. Skip PF for Dubai Hills Estate / Tilal Al Ghaf (no results in 2M-3M range)

7. Process:    python3 scripts/process_deals.py
8. Verify:     check output counts and top deals
```
