# ⚡ TenderScope — Virtual Tour RFP/Tender Scraper

A self-contained Python scraper that finds virtual tour tenders and RFPs from
any website. Runs locally on your machine — no API keys, no cloud services,
no subscriptions needed.

## What it does

- **Scrapes any tender/RFP website** you point it at (RFPMart, TendersOnTime,
  TenderDetail, SAM.gov, BidNet, or any other site)
- **Keyword matching** against your virtual tour business terms
- **Follows detail pages** to extract contact names, emails, budgets, deadlines
- **Google discovery mode** finds additional pages you didn't know about
- **Relevance scoring** (HIGH / MEDIUM / LOW) based on keyword matches
- **Exports to CSV or JSON** for your pipeline

## Quick Start

```bash
# 1. Install dependencies (Python 3.9+ required)
pip install -r requirements.txt

# 2. Launch the web dashboard
python scraper.py

# 3. Open http://localhost:5000 in your browser
```

## Usage Modes

### Web Dashboard (recommended)

```bash
python scraper.py                   # default port 5000
python scraper.py --port 8080       # custom port
```

Then open the URL in your browser. You get:
- Paste URLs, pick keywords, hit "Start Scrape"
- Live terminal showing progress
- Results tab with expandable cards
- One-click CSV / JSON download

### Headless CLI (for automation / cron)

```bash
# Basic scrape
python scraper.py --headless \
  --urls "https://www.rfpmart.com/" \
         "https://www.tendersontime.com/popular-tenders/virtual-tour-tenders/" \
  --output tenders.csv

# With detail-page following + Google discovery
python scraper.py --headless \
  --urls "https://www.rfpmart.com/" \
  --keywords "virtual tour" "360 photography" "matterport" \
  --follow --search \
  --output tenders.json

# Custom keywords
python scraper.py --headless \
  --urls "https://sam.gov/search/?keywords=virtual+tour&index=opp" \
  --keywords "virtual tour" "campus tour" "interactive map" \
  --follow \
  --output government_tenders.csv
```

### CLI flags

| Flag | Description |
|------|-------------|
| `--headless` | Skip the web UI, scrape and export immediately |
| `--urls URL [URL ...]` | One or more URLs to scrape |
| `--keywords KW [KW ...]` | Keywords to match (defaults to 10 virtual-tour terms) |
| `--output FILE` | Output path — `.csv` or `.json` |
| `--follow` | Follow each tender's detail page for richer metadata |
| `--search` | Also run Google searches to discover extra pages per domain |
| `--port N` | Web UI port (default 5000) |

## How the scraping works

1. **Fetches each URL** with a standard browser User-Agent
2. **Parses all links** on the page using BeautifulSoup
3. **Keyword-matches** link text against your terms
4. **Walks up the DOM** from each matched link to find the nearest container
   with metadata (deadline, budget, ID, location)
5. **Regex extraction** pulls dates, budgets, RFP IDs, locations, contact emails
6. **Optionally follows** each tender's detail URL for more data
7. **Deduplicates** by title and exports

No headless browser needed — it's fast and lightweight.

## Scheduling recurring scrapes

### Linux / macOS (cron)

```bash
# Run every morning at 8am
crontab -e
0 8 * * * cd /path/to/tender_scraper && python scraper.py --headless --urls "https://www.rfpmart.com/" --follow --output /path/to/output/tenders_$(date +\%Y\%m\%d).csv
```

### Windows (Task Scheduler)

Create a `.bat` file:
```bat
cd C:\path\to\tender_scraper
python scraper.py --headless --urls "https://www.rfpmart.com/" --follow --output tenders_%date:~-4%-%date:~4,2%-%date:~7,2%.csv
```

## Limitations

- **Paywall content**: Sites like RFPMart hide full details behind paid
  subscriptions. The scraper extracts everything publicly visible (titles,
  deadlines, partial descriptions, URLs) but can't get behind login walls.
- **JavaScript-rendered sites**: Some sites load content via JS. This scraper
  uses plain HTTP requests, not a headless browser, so purely JS-rendered
  listings may be missed. For those, consider adding Playwright/Selenium.
- **Rate limiting**: The scraper adds polite delays (0.5–1s) between requests.
  If a site blocks you, increase the delay or reduce the URL count.

## Adding new tender sites

Edit the `SITE_PROFILES` dict in `scraper.py` to add site-specific selectors:

```python
SITE_PROFILES["newtendersite.com"] = {
    "search_url": "https://newtendersite.com/search?q={query}",
    "listing_sel": ".tender-card",
    "title_sel": "h3 a",
    "link_sel": "a[href]",
    "id_pattern": r"TENDER-\d+",
}
```

## Project structure

```
tender_scraper/
├── scraper.py          # Everything: scraper engine + Flask app + HTML dashboard
├── requirements.txt    # Python dependencies
└── README.md           # This file
```

Single file by design — copy `scraper.py` anywhere and it works.
