/**
 * BAYUT EXTRACTOR — Run this in browser after navigating to a Bayut search page.
 * Change the community name string at the bottom to match the current page.
 *
 * HOW TO USE:
 *   1. Navigate to Bayut URL (wait 4 seconds)
 *   2. Run this entire script via javascript_tool with community name filled in
 *   3. Read results in slices: window._raw.slice(0,12), window._raw.slice(12,24) etc.
 *   4. Pass each slice to append_community.py
 *
 * OUTPUT stored in window._raw — array of objects:
 *   { uid, href, price, beds, baths, sqft, cluster, title, community, isOffPlan }
 */
(function(community) {
  const results = [];
  document.querySelectorAll('article').forEach(a => {
    const link = a.querySelector('a[href*="details-"]');
    const href = link ? link.href : '';
    const idMatch = href.match(/details-(\d+)/);
    if (!idMatch) return;

    const uid = 'bayut-' + idMatch[1];
    const lines = a.innerText.split('\n').map(l => l.trim()).filter(l => l && l !== '|');

    // Price: line after "AED"
    const aedIdx = lines.indexOf('AED');
    const price = aedIdx >= 0 ? parseInt((lines[aedIdx + 1] || '').replace(/\D/g, '')) || 0 : 0;

    // Beds/baths: lines after "Townhouse" or "Villa"
    const thIdx = lines.findIndex(l => l === 'Townhouse' || l === 'Villa');
    const beds  = thIdx >= 0 ? parseInt(lines[thIdx + 1]) || 0 : 0;
    const baths = thIdx >= 0 ? parseInt(lines[thIdx + 2]) || 0 : 0;

    // Sqft: line ending in "sqft"
    const sqftLine = lines.find(l => l.endsWith('sqft'));
    const sqft = sqftLine ? parseInt(sqftLine.replace(/\D/g, '')) : 0;

    // Title and cluster: relative to "Area:" marker
    const areaIdx = lines.indexOf('Area:');
    const title   = areaIdx >= 0 ? (lines[areaIdx + 2] || '') : '';
    const locLine = areaIdx >= 0 ? (lines[areaIdx + 3] || '') : '';
    const cluster = locLine.split(',')[0].trim();

    const isOffPlan = lines.some(l => l === 'Off-Plan');

    if (price > 0 && beds >= 3) {
      results.push({ uid, href, price, beds, baths, sqft, cluster, title, community, isOffPlan });
    }
  });

  window._raw = results;
  return `Community: ${community} | Found: ${results.length} listings | Read with: window._raw.slice(0,12)`;
})('DAMAC Lagoons');  // ← CHANGE THIS to current community name
