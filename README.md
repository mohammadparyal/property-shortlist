# Dubai Distress Deal Tracker

Real-time dashboard tracking villa and townhouse deals across Dubai's top communities, scored against original launch prices to help investors spot undervalued properties.

**Live Dashboard:** [mohammadparyal.github.io/property-shortlist](https://mohammadparyal.github.io/property-shortlist/)

## What This Does

This tracker scrapes listings from **Property Finder** and **Bayut** every 12 hours and scores each deal based on how close it is to the original developer launch price. It also monitors for **panic selling** triggered by geopolitical events (US-Iran conflict, March 2026), flagging listings posted during periods of market uncertainty.

### Key Features

- **Deal Scoring (0-100)** — Each listing is scored against verified launch price benchmarks. Higher score = better deal relative to what the developer originally charged.
- **Panic Sell Monitor** — Listings posted after March 1, 2026 are flagged with a PANIC tag and receive bonus scoring, as sellers may be exiting positions at a discount.
- **Multi-Source Data** — Aggregates from both Property Finder and Bayut to ensure comprehensive coverage.
- **Direct Listing Links** — Click through to the original listing on the source platform.
- **Google Sheets Export** — Open the data directly in Google Sheets from the dashboard.
- **Light / Dark Theme** — Toggle between themes with the button in the top-right corner.

## Communities Tracked

| Community | Type | Launch Benchmark (3BR) | Launch Benchmark (4BR) |
|-----------|------|----------------------|----------------------|
| DAMAC Lagoons | Off-plan | AED 1,400,000 | AED 1,700,000 |
| DAMAC Islands | Off-plan | — | AED 2,250,000 |
| DAMAC Islands 2 | Off-plan (2029) | — | AED 2,750,000 |
| The Valley | Off-plan / Ready | AED 1,530,000 | AED 2,100,000 |
| DAMAC Hills 2 | Ready / Off-plan | AED 800,000 | AED 1,200,000 |
| Villanova | Ready | AED 1,445,000 | AED 2,000,000 |
| DAMAC Hills | Off-plan | AED 1,500,000 | AED 2,200,000 |
| Dubai Hills Estate | Off-plan | AED 2,100,000 | AED 2,600,000 |
| Tilal Al Ghaf | Off-plan / Ready | AED 1,260,000 | AED 1,800,000 |

## How Scoring Works

1. **Base Score** — Determined by how far above launch price the listing sits:
   - At or below launch = 95
   - Up to 5% above = 85
   - Up to 15% above = 75
   - Up to 25% above = 65
   - Up to 40% above = 55
   - Up to 60% above = 45
   - Above 60% = 35

2. **Distress Signal Bonus (+3 each)** — Keywords like "motivated seller", "urgent sale", "below OP", "investor deal", "quick exit", "assignment", "price dropped".

3. **Panic Period Bonus (+5)** — Listings posted after March 1, 2026 (US-Iran conflict onset) get an additional boost as they may represent panic selling.

## Files

| File | Description |
|------|-------------|
| `index.html` | Interactive dashboard with embedded data, charts, filters, and sorting |
| `dubai_deals.json` | Raw JSON data for all tracked listings |
| `Dubai_Deal_Tracker.xlsx` | Excel spreadsheet with Deal Tracker, Summary, and Launch Benchmarks sheets |
| `broker_search_brief.md` | Briefing document for real estate brokers with search criteria |

## Data Updates

Data is refreshed automatically every 12 hours (8:00 AM and 8:00 PM GST) via a scheduled scraping task. The dashboard displays the last update timestamp.

## Disclaimer

This tool is for informational purposes only. Always conduct your own due diligence before making any property investment decisions. Listing data is scraped from public sources and may not reflect the most current availability or pricing. Launch price benchmarks are approximate and sourced from multiple public references.

## License

MIT
