/**
 * PROPERTY FINDER EXTRACTOR — Run this in browser after navigating to a PF search page.
 * Change the community name string at the bottom to match the current page.
 *
 * UPDATED for new PF URL format (March 2026):
 *   Old URLs (BROKEN — slug-based, redirects wrongly):
 *     /en/buy/townhouses/dubai/damac-lagoons/?beds=3-any...
 *   New URLs (WORKING — location-ID-based, buy side, price sort):
 *     /en/search?l={location_id}&c=1&bdr%5B%5D=3&pf=2000000&pt=3000000&ob=pr
 *
 *   IMPORTANT URL PARAMETERS:
 *     c=1         → BUY listings (c=2 is RENT — DO NOT USE c=2)
 *     bdr%5B%5D=3 → 3 bedrooms (decoded: bdr[]=3)
 *     pf/pt       → price from / price to (in AED)
 *     ob=pr       → sort by PRICE ascending (ob=mr sorts by most recent — different!)
 *
 * Location IDs (verified March 2026):
 *   DAMAC Lagoons:      11559
 *   DAMAC Islands:      14611  ⚠️ No 3BR — use bdr%5B%5D=4, range pf=2000000&pt=4000000
 *   The Valley:         10757
 *   DAMAC Hills 2:      125    ⚠️ Must scrape TWO ranges: 1M-2M AND 2M-3M, then dedupe
 *   Villanova:          8780
 *   DAMAC Hills:        129
 *   Dubai Hills Estate: 105    ⚠️ Market now 6M+, almost no results in 2M-3M
 *   Tilal Al Ghaf:      9885   ⚠️ Market now 4.5M+, zero results in 2M-3M
 *
 * SPECIAL CASE — DAMAC Islands (4BR only):
 *   URL: /en/search?l=14611&c=1&bdr%5B%5D=4&pf=2000000&pt=4000000&ob=pr
 *
 * SPECIAL CASE — DAMAC Hills 2 (two price ranges):
 *   Sub-2M:  /en/search?l=125&c=1&bdr%5B%5D=3&pf=1000000&pt=2000000&ob=pr
 *   2M-3M:   /en/search?l=125&c=1&bdr%5B%5D=3&pf=2000000&pt=3000000&ob=pr
 *   Scrape both, combine into one array, dedupe by uid before saving.
 *
 * HOW TO USE:
 *   1. Navigate to the /en/search?l={id}&c=1&bdr%5B%5D=3&pf=...&pt=...&ob=pr URL
 *      (make sure it says "buy" in the page title, not "rent")
 *   2. Wait 3 seconds for page to load
 *   3. Run this entire script via javascript_tool with community name filled in
 *   4. Check window._numChunks — read that many 900-char slices:
 *        window._rawJson.slice(0,900)
 *        window._rawJson.slice(900,1800)
 *        window._rawJson.slice(1800,2700)  ... etc.
 *   5. Assemble all chunks in Python, parse JSON, save to /tmp/community_raw.json
 *   6. Pipe to combine_append.py (NOT append_community.py — that wipes Bayut data!)
 *        python3 scripts/combine_append.py < /tmp/community_raw.json
 *
 * CHUNK ASSEMBLY GOTCHA — boundary quote stripping:
 *   The MCP javascript_tool sometimes strips the LEADING " character from a chunk
 *   if that chunk starts exactly on a JSON string opening quote.
 *   Example: chunk shows  isOffPlan":true  but it should be  "isOffPlan":true
 *   Fix: prepend the missing " manually before concatenating that chunk in Python.
 *
 * OUTPUT stored in window._raw — same schema as bayut_extractor.js:
 *   { uid, href, price, beds, baths, sqft, cluster, title, community, isOffPlan, listed, source }
 *
 * NOTE: PF changed structure in 2026 — data is now in searchResult.listings[].property
 *       (was previously searchResult.properties[])
 * NOTE: 'listed' date from p.listed_date (ISO string e.g. "2026-03-01T11:04:11Z")
 * NOTE: 'uid' format is 'pf-{reference}' (e.g. 'pf-L-14529')
 */
(function(community) {
  const el = document.getElementById('__NEXT_DATA__');
  if (!el) {
    window._raw = [];
    return 'ERROR: __NEXT_DATA__ not found. Make sure you are on a /en/search?l=... page.';
  }

  let data;
  try {
    data = JSON.parse(el.textContent);
  } catch (e) {
    window._raw = [];
    return 'ERROR: Failed to parse __NEXT_DATA__: ' + e.message;
  }

  // New PF structure: searchResult.listings (each item wraps a .property object)
  const allListings = data?.props?.pageProps?.searchResult?.listings;
  const meta        = data?.props?.pageProps?.searchResult?.meta || {};

  if (!Array.isArray(allListings)) {
    window._raw = [];
    const keys = Object.keys(data?.props?.pageProps?.searchResult || {});
    return 'ERROR: searchResult.listings not found. searchResult keys: ' + keys.join(', ');
  }

  const results = [];

  allListings.forEach(item => {
    const p = item?.property;
    if (!p) return;

    // Filter to townhouses and villas only
    const propType = (p.property_type || '').toLowerCase();
    if (propType && !propType.includes('townhouse') && !propType.includes('villa')) return;

    const ref   = p.reference || String(p.id) || '';
    const uid   = 'pf-' + ref;
    const price = parseInt(p.price?.value || 0);
    const beds  = parseInt(p.bedrooms || 0);
    const baths = parseInt(p.bathrooms || 0);
    const sqft  = parseInt(p.size?.value || 0);
    const href  = p.share_url || ('https://www.propertyfinder.ae' + (p.details_path || ''));

    // Cluster: location.name (e.g. "Portofino")
    const cluster = p.location?.name || '';
    const title   = p.title || '';

    // Listed date: listed_date is ISO string e.g. "2026-01-21T11:04:11Z"
    const rawDate = p.listed_date || '';
    const listed  = rawDate ? rawDate.substring(0, 10) : '';

    // Off-plan status
    const status    = (p.completion_status || '').toLowerCase();
    const isOffPlan = status.includes('off') || status.includes('under');

    if (price > 0 && beds >= 3 && ref) {
      results.push({
        uid,
        href,
        price,
        beds,
        baths,
        sqft,
        cluster,
        title,
        community,
        isOffPlan,
        listed,
        source: 'PropertyFinder',
      });
    }
  });

  window._raw     = results;
  window._rawJson = JSON.stringify(results);
  window._numChunks = Math.ceil(window._rawJson.length / 900);

  const totalOnPage  = allListings.length;
  const filtered     = results.length;
  const totalInSearch = meta.total_count || 0;

  return `Community: ${community} | Page: ${totalOnPage} items | Matching (TH/Villa 3BR+): ${filtered} | Total in search: ${totalInSearch} | Chunks: ${window._numChunks} | Read: window._rawJson.slice(0,900)`;
})('DAMAC Lagoons');  // ← CHANGE THIS to current community name
