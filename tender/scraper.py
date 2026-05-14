#!/usr/bin/env python3
"""
TenderScope — Virtual Tour RFP/Tender Scraper
==============================================
A self-contained scraper with a local web dashboard.

Usage:
    pip install requests beautifulsoup4 flask lxml
    python scraper.py

Then open http://localhost:5000 in your browser.

You can also run headless (no browser):
    python scraper.py --headless --urls "https://rfpmart.com" --keywords "virtual tour"
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse, quote_plus
from threading import Thread, Lock

import requests
from bs4 import BeautifulSoup, Comment

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

VERSION = "1.0.0"
DEFAULT_KEYWORDS = [
    "virtual tour", "360 tour", "360 photography", "3d walkthrough",
    "interactive map", "panorama", "matterport", "virtual reality tour",
    "campus tour", "immersive experience",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

HEADERS = {
    "User-Agent": USER_AGENTS[0],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

# Known tender site patterns for smarter extraction
SITE_PROFILES = {
    "rfpmart.com": {
        "search_url": "https://www.rfpmart.com/search-results.html?q={query}",
        "listing_sel": ".rfp-listing, .rfp-item, table tr",
        "title_sel": "a, .rfp-title, td:nth-child(2)",
        "link_sel": "a[href]",
        "id_pattern": r"[A-Z]+-\d+",
    },
    "tendersontime.com": {
        "search_url": "https://www.tendersontime.com/popular-tenders/{query}-tenders/",
        "listing_sel": ".tender-item, .tender-row, table tr, .list-group-item",
        "title_sel": "a, .tender-title, td:nth-child(2)",
        "link_sel": "a[href]",
        "id_pattern": r"\d{6,}",
    },
    "tenderdetail.com": {
        "search_url": "https://www.tenderdetail.com/Indian-tender/{query}-tenders",
        "listing_sel": ".tender-item, .tenderlist, table tr",
        "title_sel": "a, .tender-desc, td",
        "link_sel": "a[href]",
        "id_pattern": r"\d{6,}",
    },
    "sam.gov": {
        "search_url": "https://sam.gov/search/?keywords={query}&index=opp",
        "listing_sel": ".usa-card, .opportunity-item, table tr",
        "title_sel": "a, .title, h3",
        "link_sel": "a[href]",
        "id_pattern": r"[A-Z0-9-]+",
    },
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("tenderscope")

# ---------------------------------------------------------------------------
# Scraper Engine
# ---------------------------------------------------------------------------

class ScrapeResult:
    """One scraped tender/RFP record."""
    FIELDS = [
        "source", "title", "type", "rfp_id", "location", "organization",
        "description", "budget", "posted_date", "deadline",
        "contact_person", "contact_email", "url", "status",
        "relevance", "scraped_at",
    ]

    def __init__(self, **kwargs):
        for f in self.FIELDS:
            setattr(self, f, kwargs.get(f, ""))
        if not self.scraped_at:
            self.scraped_at = datetime.utcnow().isoformat()

    def to_dict(self):
        return {f: getattr(self, f, "") for f in self.FIELDS}


class TenderScraper:
    """Core scraping logic — no API keys, no external AI, just HTTP + parsing."""

    def __init__(self, keywords=None, timeout=20):
        self.keywords = keywords or DEFAULT_KEYWORDS
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.results: list[ScrapeResult] = []
        self.logs: list[dict] = []
        self._lock = Lock()

    # -- logging --
    def _log(self, msg, level="info"):
        entry = {"time": datetime.now().strftime("%H:%M:%S"), "msg": msg, "level": level}
        with self._lock:
            self.logs.append(entry)
        getattr(log, level, log.info)(msg)

    # -- HTTP helpers --
    def _get(self, url, retries=2):
        """Fetch a URL with retry + rotating User-Agent. Returns (soup, raw_text) or (None, None)."""
        import random
        for attempt in range(retries + 1):
            try:
                self.session.headers["User-Agent"] = random.choice(USER_AGENTS)
                # (connect_timeout, read_timeout) — bounds both phases independently
                resp = self.session.get(url, timeout=(10, self.timeout), allow_redirects=True)
                if resp.status_code == 403 and attempt < retries:
                    wait = 2 * (attempt + 1)
                    self._log(f"Got 403 from {urlparse(url).netloc}, retrying in {wait}s (attempt {attempt+2}/{retries+1})...", "warning")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
                    comment.extract()
                return soup, soup.get_text(separator="\n", strip=True)
            except Exception as e:
                if attempt < retries:
                    time.sleep(1)
                    continue
                self._log(f"HTTP error fetching {url}: {e}", "error")
                return None, None
        return None, None

    # -- keyword matching --
    def _matches_keywords(self, text):
        """Return list of matched keywords in text."""
        text_lower = text.lower()
        return [kw for kw in self.keywords if kw.lower() in text_lower]

    def _relevance(self, matched_keywords, text):
        """Score relevance HIGH / MEDIUM / LOW."""
        if not matched_keywords:
            return "LOW"
        primary = ["virtual tour", "360 tour", "360 photography", "3d walkthrough", "matterport"]
        if any(kw in matched_keywords for kw in primary):
            return "HIGH"
        return "MEDIUM"

    # -- generic page scraper --
    def _extract_tenders_generic(self, url, soup, raw_text):
        """
        Strategy: find <a> tags whose visible text matches keywords,
        then walk up to the nearest container to grab metadata.
        Works across most tender listing sites.
        """
        results = []
        domain = urlparse(url).netloc.replace("www.", "")
        seen_titles = set()

        # First pass: find all links on the page
        all_links = soup.find_all("a", href=True)
        for link in all_links:
            link_text = link.get_text(strip=True)
            if len(link_text) < 15:
                continue  # skip nav/button links

            matched = self._matches_keywords(link_text)
            if not matched:
                continue

            title = link_text[:200]
            if title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())

            href = urljoin(url, link["href"])

            # Walk up to parent container for extra metadata
            container = link
            for _ in range(5):
                if container.parent:
                    container = container.parent
                    container_text = container.get_text(separator=" | ", strip=True)
                    if len(container_text) > len(link_text) + 20:
                        break

            container_text = container.get_text(separator=" | ", strip=True) if container else ""

            # Try to extract metadata from container
            rfp_id = ""
            id_match = re.search(r"(?:ID|No|#)[:\s]*([A-Z0-9][\w-]{3,20})", container_text)
            if id_match:
                rfp_id = id_match.group(1)

            deadline = ""
            date_match = re.search(
                r"[Dd]eadline[:\s]*([A-Z][a-z]+ \d{1,2},?\s*\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})",
                container_text,
            )
            if date_match:
                deadline = date_match.group(1)

            location = ""
            loc_match = re.search(
                r"(?:USA|India|Canada|UK|Australia|New York|California|Texas|Florida"
                r"|Washington|Maryland|Pennsylvania|Illinois|Ohio|Georgia|Virginia"
                r"|New Jersey|Colorado|Oregon|Michigan|Maharashtra|Kerala|Delhi"
                r"|Karnataka|Tamil Nadu|West Bengal|Rajasthan|Gujarat|Punjab"
                r"|Karachi|Lahore|Islamabad|Sindh|Punjab)",
                container_text,
            )
            if loc_match:
                location = loc_match.group(0)

            budget = ""
            budget_match = re.search(
                r"(?:[Bb]udget|[Vv]alue)[:\s]*(?:Up to\s*)?(\$?[\d,]+(?:\.\d+)?\s*(?:USD|INR|CAD|GBP|EUR|Lac|Cr)?)",
                container_text,
            )
            if budget_match:
                budget = budget_match.group(1).strip()

            # Guess type
            rtype = "RFP"
            for label, pattern in [
                ("RFI", r"\bRFI\b"),
                ("RFQ", r"\bRFQ\b"),
                ("EOI", r"\bEOI\b"),
                ("Bid", r"\b[Bb]id\b"),
                ("Tender", r"\b[Tt]ender\b"),
            ]:
                if re.search(pattern, container_text):
                    rtype = label
                    break

            # Guess status
            status = "unknown"
            if re.search(r"[Ee]xpir|[Cc]losed|[Aa]rchiv", container_text):
                status = "expired"
            elif re.search(r"[Aa]ctive|[Oo]pen|[Ll]ive", container_text):
                status = "active"
            elif deadline:
                try:
                    for fmt in ("%B %d, %Y", "%B %d %Y", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d"):
                        try:
                            dl_date = datetime.strptime(deadline.replace(",", "").strip(), fmt)
                            status = "active" if dl_date > datetime.now() else "expired"
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            r = ScrapeResult(
                source=domain,
                title=title,
                type=rtype,
                rfp_id=rfp_id,
                location=location,
                organization="",
                description=container_text[:300] if container_text != title else "",
                budget=budget or "Not disclosed",
                posted_date="",
                deadline=deadline,
                contact_person="Check source",
                contact_email="Check source",
                url=href,
                status=status,
                relevance=self._relevance(matched, title + " " + container_text),
            )
            results.append(r)

        return results

    # -- detail page scraper (follows links for more info) --
    def _scrape_detail_page(self, result: ScrapeResult):
        """Optionally follow the tender URL to grab more metadata."""
        if not result.url or result.url.startswith("#"):
            return
        soup, text = self._get(result.url)
        if not soup or not text:
            return

        # Try to find contact info
        email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        if email_match:
            result.contact_email = email_match.group(0)

        # Try organization
        for sel in ["h1", "h2", ".agency-name", ".org-name", ".authority"]:
            tag = soup.select_one(sel)
            if tag and len(tag.get_text(strip=True)) > 5:
                result.organization = tag.get_text(strip=True)[:120]
                break

        # Try posted date
        posted_match = re.search(
            r"[Pp]osted\s*(?:[Dd]ate)?[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+ \d{1,2},? \d{4})",
            text,
        )
        if posted_match:
            result.posted_date = posted_match.group(1)

        # Try contact person
        contact_match = re.search(
            r"[Cc]ontact(?:\s+[Pp]erson)?[:\s]*([A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)?)",
            text,
        )
        if contact_match:
            result.contact_person = contact_match.group(1)

    # -- Google search fallback (no API key needed) --
    def _search_google(self, query, site=None):
        """
        Use Google search to find tender pages. Returns list of URLs.
        This is a lightweight approach — no API key needed.
        """
        search_q = f'site:{site} {query}' if site else query
        url = f"https://www.google.com/search?q={quote_plus(search_q)}&num=15"
        self._log(f"Google search: {search_q}")
        try:
            resp = self.session.get(url, timeout=self.timeout)
            soup = BeautifulSoup(resp.text, "lxml")
            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/url?q="):
                    real_url = href.split("/url?q=")[1].split("&")[0]
                    if "google.com" not in real_url and "youtube.com" not in real_url:
                        links.append(real_url)
            return links[:10]
        except Exception as e:
            self._log(f"Google search failed: {e}", "error")
            return []

    # -- main scrape orchestrator --
    def scrape_url(self, url, follow_detail=False, max_detail=5):
        """Scrape a single URL for tender listings."""
        self._log(f"Scraping: {url}")
        soup, raw_text = self._get(url)
        if not soup:
            self._log(f"Failed to fetch {url}", "error")
            return []

        results = self._extract_tenders_generic(url, soup, raw_text)
        self._log(f"Found {len(results)} keyword-matched listings on {url}")

        # Save results FIRST so they survive any failure during detail-page follow
        with self._lock:
            self.results.extend(results)

        # Optionally follow detail pages for richer data (mutates result objects in place)
        if follow_detail and results:
            detail_count = min(len(results), max_detail)
            self._log(f"Following {detail_count} detail pages for metadata...")
            for r in results[:detail_count]:
                try:
                    self._scrape_detail_page(r)
                except Exception as e:
                    self._log(f"Detail page failed for {r.url}: {e}", "warning")
                time.sleep(0.5)  # be polite

        return results

    def scrape_with_search(self, site_domain=None):
        """
        Use Google to discover tender pages for each keyword,
        then scrape each discovered page.
        """
        discovered = set()
        for kw in self.keywords:
            query = f"{kw} tender RFP 2025 2026"
            links = self._search_google(query, site=site_domain)
            discovered.update(links)
            time.sleep(1)  # rate limit

        self._log(f"Discovered {len(discovered)} pages via search")
        all_results = []
        for page_url in list(discovered)[:20]:  # cap at 20 pages
            results = self.scrape_url(page_url, follow_detail=True, max_detail=3)
            all_results.extend(results)
            time.sleep(0.5)

        return all_results

    def scrape_all(self, urls, follow_detail=False, use_search=False):
        """Scrape multiple URLs."""
        self._log(f"Starting scrape of {len(urls)} URLs, keywords: {self.keywords}")
        all_results = []

        for url in urls:
            results = self.scrape_url(url, follow_detail=follow_detail)
            all_results.extend(results)
            time.sleep(0.5)

            # If search mode, also discover pages from this domain
            if use_search:
                domain = urlparse(url).netloc.replace("www.", "")
                extra = self.scrape_with_search(site_domain=domain)
                all_results.extend(extra)

        # Deduplicate by title
        seen = set()
        deduped = []
        for r in all_results:
            key = r.title.lower().strip()
            if key not in seen:
                seen.add(key)
                deduped.append(r)

        with self._lock:
            self.results = deduped

        self._log(f"Scrape complete: {len(deduped)} unique results (from {len(all_results)} raw)")
        return deduped

    # -- export --
    def to_csv(self, filepath=None):
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=ScrapeResult.FIELDS)
        writer.writeheader()
        for r in self.results:
            writer.writerow(r.to_dict())
        text = buf.getvalue()
        if filepath:
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                f.write(text)
            self._log(f"CSV saved to {filepath}")
        return text

    def to_json(self, filepath=None):
        data = [r.to_dict() for r in self.results]
        text = json.dumps(data, indent=2)
        if filepath:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(text)
            self._log(f"JSON saved to {filepath}")
        return text


# ---------------------------------------------------------------------------
# Flask Web Dashboard
# ---------------------------------------------------------------------------

def create_app():
    from flask import Flask, request, jsonify, Response

    app = Flask(__name__)
    scraper = TenderScraper()
    scrape_thread = None

    @app.route("/")
    def index():
        return DASHBOARD_HTML

    @app.route("/api/scrape", methods=["POST"])
    def api_scrape():
        nonlocal scrape_thread
        data = request.json or {}
        urls = data.get("urls", [])
        keywords = data.get("keywords", DEFAULT_KEYWORDS)
        follow_detail = data.get("follow_detail", True)
        use_search = data.get("use_search", False)

        if not urls:
            return jsonify({"error": "No URLs provided"}), 400

        scraper.keywords = keywords
        scraper.results = []
        scraper.logs = []

        def run():
            scraper.scrape_all(urls, follow_detail=follow_detail, use_search=use_search)

        scrape_thread = Thread(target=run, daemon=True)
        scrape_thread.start()
        return jsonify({"status": "started", "url_count": len(urls)})

    @app.route("/api/status")
    def api_status():
        running = scrape_thread is not None and scrape_thread.is_alive()
        return jsonify({
            "running": running,
            "result_count": len(scraper.results),
            "logs": scraper.logs[-50:],
            "results": [r.to_dict() for r in scraper.results],
        })

    @app.route("/api/export/csv")
    def api_export_csv():
        csv_text = scraper.to_csv()
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=tenders.csv"},
        )

    @app.route("/api/export/json")
    def api_export_json():
        return Response(
            scraper.to_json(),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=tenders.json"},
        )

    return app


# ---------------------------------------------------------------------------
# Inline HTML Dashboard (served by Flask, no separate files needed)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TenderScope — Virtual Tour RFP Scraper</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=DM+Sans:wght@400;500;600;700&display=swap');

  * { margin:0; padding:0; box-sizing:border-box; }

  :root {
    --bg: #080f0a;
    --surface: #0d1a12;
    --border: #1a2e1f;
    --green: #00ff88;
    --green-dim: #4a6b50;
    --text: #c8e6d0;
    --text-dim: #6b8a72;
    --text-bright: #e8f5eb;
    --red: #ff4444;
    --yellow: #ffaa00;
    --blue: #00aaff;
    --purple: #cc88ff;
    --mono: 'JetBrains Mono', monospace;
    --sans: 'DM Sans', system-ui, sans-serif;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    min-height: 100vh;
  }

  /* grid background */
  body::before {
    content: '';
    position: fixed; inset: 0; z-index: 0; pointer-events: none; opacity: 0.035;
    background-image:
      linear-gradient(rgba(0,255,136,0.5) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,255,136,0.5) 1px, transparent 1px);
    background-size: 48px 48px;
  }

  .container { position: relative; z-index: 1; max-width: 1200px; margin: 0 auto; padding: 20px; }

  /* Header */
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 0; border-bottom: 1px solid var(--border); margin-bottom: 24px;
  }
  .logo { display: flex; align-items: center; gap: 12px; }
  .logo-icon {
    width: 40px; height: 40px; border-radius: 10px;
    background: linear-gradient(135deg, var(--green), #00aa55);
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; color: var(--bg); font-weight: 700;
    box-shadow: 0 0 20px #00ff8830;
  }
  .logo h1 { font-size: 18px; letter-spacing: -0.02em; color: var(--text-bright); }
  .logo p { font-size: 11px; color: var(--green-dim); font-family: var(--mono); letter-spacing: 0.05em; }

  /* Tabs */
  .tabs { display: flex; gap: 6px; }
  .tab {
    padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border);
    background: transparent; color: var(--green-dim); font-size: 13px;
    font-weight: 600; cursor: pointer; font-family: var(--sans); transition: all 0.2s;
  }
  .tab.active { border-color: #00ff8860; background: #00ff8812; color: var(--green); }
  .tab .badge {
    margin-left: 6px; padding: 1px 6px; border-radius: 10px;
    background: #00ff8825; font-size: 10px; color: var(--green);
  }

  /* Layout */
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  @media (max-width: 768px) { .grid-2 { grid-template-columns: 1fr; } }

  /* Cards */
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 20px;
  }
  .card-title {
    font-size: 13px; font-weight: 600; color: var(--green); margin-bottom: 12px;
    font-family: var(--mono); letter-spacing: 0.05em;
  }

  /* Form elements */
  textarea, input[type="text"] {
    width: 100%; background: var(--bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 10px 12px; color: var(--text);
    font-family: var(--mono); font-size: 12px; resize: vertical; outline: none;
  }
  textarea:focus, input:focus { border-color: var(--green-dim); }
  textarea::placeholder, input::placeholder { color: #2a3a2e; }

  /* Keyword pills */
  .pills { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
  .pill {
    padding: 5px 12px; border-radius: 20px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text-dim); font-size: 12px;
    font-family: var(--mono); cursor: pointer; transition: all 0.2s; user-select: none;
  }
  .pill.active { border-color: var(--green); background: #00ff8815; color: var(--green); }
  .pill:hover { border-color: var(--green-dim); }

  /* Quick-add buttons */
  .quick-add { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
  .quick-add span { font-size: 10px; color: var(--green-dim); line-height: 26px; margin-right: 6px; }
  .quick-btn {
    padding: 3px 8px; border-radius: 4px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text-dim); font-size: 10px;
    cursor: pointer; font-family: var(--mono);
  }
  .quick-btn:hover { border-color: var(--green-dim); color: var(--green); }

  /* Buttons */
  .btn-row { display: flex; gap: 10px; margin-top: 16px; }
  .btn-primary {
    flex: 1; padding: 14px 24px; border-radius: 10px; border: none;
    background: linear-gradient(135deg, var(--green), #00cc66);
    color: var(--bg); font-size: 14px; font-weight: 700; cursor: pointer;
    font-family: var(--sans); letter-spacing: 0.02em; transition: all 0.3s;
    box-shadow: 0 0 30px #00ff8825;
  }
  .btn-primary:disabled { background: var(--border); color: var(--green-dim); cursor: not-allowed; box-shadow: none; }
  .btn-secondary {
    padding: 14px 18px; border-radius: 10px; border: 1px solid var(--border);
    background: var(--surface); color: var(--text-dim); font-size: 13px;
    cursor: pointer; font-weight: 600; font-family: var(--sans);
  }
  .btn-export {
    padding: 8px 16px; border-radius: 8px; border: 1px solid #00ff8840;
    background: #00ff8810; color: var(--green); font-size: 12px;
    font-weight: 600; cursor: pointer; font-family: var(--sans);
  }
  .btn-export:disabled { opacity: 0.4; cursor: not-allowed; }

  /* Terminal */
  .terminal {
    background: #0a110d; border: 1px solid var(--border); border-radius: 12px;
    display: flex; flex-direction: column; overflow: hidden;
  }
  .terminal-bar {
    padding: 10px 16px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 8px;
  }
  .terminal-dots { display: flex; gap: 5px; }
  .terminal-dots span { width: 10px; height: 10px; border-radius: 50%; }
  .terminal-bar label { font-size: 11px; color: var(--green-dim); font-family: var(--mono); }
  .terminal-body {
    flex: 1; padding: 16px; overflow-y: auto; max-height: 420px; min-height: 420px;
  }
  .terminal-empty {
    color: #2a3a2e; font-family: var(--mono); font-size: 12px;
    text-align: center; margin-top: 80px;
  }
  .terminal-empty .icon { font-size: 32px; margin-bottom: 12px; opacity: 0.3; }
  .log-line {
    font-family: var(--mono); font-size: 11px; padding: 3px 0; line-height: 1.6;
    padding-left: 10px;
  }
  .log-line .time { color: #2a3a2e; margin-right: 8px; }
  .log-info    { color: var(--text-dim); border-left: 2px solid #6b8a7220; }
  .log-system  { color: var(--green);    border-left: 2px solid #00ff8820; }
  .log-error   { color: var(--red);      border-left: 2px solid #ff444420; }
  .log-warning { color: var(--yellow);   border-left: 2px solid #ffaa0020; }

  /* Results */
  .export-bar {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 16px; padding: 12px 16px; background: var(--surface);
    border-radius: 10px; border: 1px solid var(--border);
  }
  .export-bar .meta { font-family: var(--mono); font-size: 12px; color: var(--green-dim); }
  .export-btns { display: flex; gap: 8px; }

  .result-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px; margin-bottom: 8px; cursor: pointer; transition: all 0.2s;
  }
  .result-card:hover { border-color: #00ff8830; }
  .result-card.expanded { border-color: #00ff8830; }
  .result-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  .result-badges { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }

  .badge-relevance {
    padding: 2px 8px; border-radius: 4px; font-size: 10px;
    font-family: var(--mono); font-weight: 600;
  }
  .badge-HIGH   { background: #00ff8818; border: 1px solid #00ff8840; color: var(--green); }
  .badge-MEDIUM { background: #ffaa0018; border: 1px solid #ffaa0040; color: var(--yellow); }
  .badge-LOW    { background: #ff444418; border: 1px solid #ff444440; color: var(--red); }

  .badge-status {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 10px; font-family: var(--mono); text-transform: uppercase; letter-spacing: 0.1em;
  }
  .badge-status .dot { width: 6px; height: 6px; border-radius: 50%; }
  .status-active  { color: var(--green); }
  .status-active .dot  { background: var(--green); box-shadow: 0 0 6px #00ff8850; }
  .status-expired { color: var(--red); }
  .status-expired .dot { background: var(--red); }
  .status-unknown { color: var(--yellow); }
  .status-unknown .dot { background: var(--yellow); }

  .badge-type {
    font-size: 10px; color: var(--green-dim); font-family: var(--mono);
    background: var(--bg); padding: 2px 6px; border-radius: 4px;
  }

  .result-title { font-size: 14px; font-weight: 600; color: var(--text-bright); margin-bottom: 4px; }
  .result-meta {
    font-size: 12px; color: var(--text-dim); display: flex; gap: 16px; font-family: var(--mono);
  }
  .chevron { color: #2a3a2e; font-size: 16px; transition: transform 0.2s; }
  .chevron.open { transform: rotate(180deg); }

  .result-detail {
    margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border);
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px;
    font-family: var(--mono); font-size: 11px;
  }
  .detail-line { color: var(--text-dim); padding: 2px 0; line-height: 1.6; }
  .detail-line .label { color: var(--green); margin-right: 8px; }
  .detail-line a { color: var(--blue); text-decoration: none; word-break: break-all; }
  .full-width { grid-column: 1 / -1; }

  .empty-state {
    text-align: center; padding: 80px; color: #2a3a2e; font-family: var(--mono);
  }
  .empty-state .icon { font-size: 40px; margin-bottom: 12px; opacity: 0.3; }

  /* Checkbox row */
  .checkbox-row { display: flex; gap: 20px; margin-top: 10px; }
  .checkbox-row label {
    display: flex; align-items: center; gap: 6px;
    font-size: 12px; color: var(--text-dim); cursor: pointer; font-family: var(--mono);
  }
  .checkbox-row input[type="checkbox"] { accent-color: var(--green); }

  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }
  .cursor-blink { animation: blink 1.2s ease-in-out infinite; color: var(--green); font-family: var(--mono); font-size: 11px; }
</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <header>
    <div class="logo">
      <div class="logo-icon">⚡</div>
      <div>
        <h1>TenderScope</h1>
        <p>VIRTUAL TOUR RFP SCRAPER v""" + VERSION + r"""</p>
      </div>
    </div>
    <div class="tabs">
      <button class="tab active" data-tab="scraper" onclick="switchTab('scraper',this)">Scraper</button>
      <button class="tab" data-tab="results" onclick="switchTab('results',this)">Results <span class="badge" id="result-count">0</span></button>
    </div>
  </header>

  <!-- SCRAPER TAB -->
  <div id="tab-scraper">
    <div class="grid-2">
      <!-- Left: Config -->
      <div>
        <!-- URLs -->
        <div class="card" style="margin-bottom:16px">
          <div class="card-title">▸ TARGET URLs</div>
          <textarea id="urls" rows="5"
            placeholder="Paste URLs here, one per line...&#10;&#10;https://www.rfpmart.com/&#10;https://www.tendersontime.com/popular-tenders/virtual-tour-tenders/"></textarea>
          <div class="quick-add">
            <span>Quick add:</span>
            <button class="quick-btn" onclick="addUrl('https://www.rfpmart.com/')">rfpmart.com</button>
            <button class="quick-btn" onclick="addUrl('https://www.tendersontime.com/popular-tenders/virtual-tour-tenders/')">tendersontime.com</button>
            <button class="quick-btn" onclick="addUrl('https://www.tenderdetail.com/Indian-tender/virtual-tour-tenders')">tenderdetail.com</button>
            <button class="quick-btn" onclick="addUrl('https://sam.gov/search/?keywords=virtual+tour&index=opp')">sam.gov</button>
            <button class="quick-btn" onclick="addUrl('https://www.bidnetdirect.com/')">bidnetdirect.com</button>
          </div>
        </div>

        <!-- Keywords -->
        <div class="card" style="margin-bottom:16px">
          <div class="card-title">▸ SEARCH KEYWORDS</div>
          <div class="pills" id="keyword-pills"></div>
          <div style="display:flex;gap:8px">
            <input type="text" id="custom-kw" placeholder="Add custom keyword..." onkeydown="if(event.key==='Enter')addKeyword()">
            <button class="btn-secondary" style="padding:8px 14px;font-size:12px" onclick="addKeyword()">+ Add</button>
          </div>
          <div class="checkbox-row">
            <label><input type="checkbox" id="follow-detail" checked> Follow detail pages</label>
            <label><input type="checkbox" id="use-search"> Google discovery mode</label>
          </div>
        </div>

        <!-- Actions -->
        <div class="btn-row">
          <button class="btn-primary" id="btn-scrape" onclick="startScrape()">⚡ Start Scrape</button>
          <button class="btn-secondary" onclick="clearAll()">🗑 Clear</button>
        </div>
      </div>

      <!-- Right: Terminal -->
      <div class="terminal">
        <div class="terminal-bar">
          <div class="terminal-dots">
            <span style="background:#ff4444"></span>
            <span style="background:#ffaa00"></span>
            <span style="background:#00ff88"></span>
          </div>
          <label>tenderscope — live output</label>
        </div>
        <div class="terminal-body" id="terminal">
          <div class="terminal-empty">
            <div class="icon">⚡</div>
            Waiting for scrape command...<br>Add URLs and keywords, then hit Start
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- RESULTS TAB -->
  <div id="tab-results" style="display:none">
    <div class="export-bar">
      <div class="meta" id="results-meta">0 tenders found</div>
      <div class="export-btns">
        <button class="btn-export" id="btn-csv" onclick="window.location='/massinteract-tender/api/export/csv'" disabled>↓ Download CSV</button>
        <button class="btn-export" id="btn-json" onclick="window.location='/massinteract-tender/api/export/json'" disabled>↓ Download JSON</button>
      </div>
    </div>
    <div id="results-list">
      <div class="empty-state"><div class="icon">📋</div>No results yet. Run a scrape first.</div>
    </div>
  </div>

</div>

<script>
// State
let activeKeywords = ["virtual tour", "360 tour", "360 photography", "3d walkthrough", "matterport"];
const allKeywords = [
  "virtual tour", "360 tour", "360 photography", "virtual reality tour",
  "interactive map", "3d walkthrough", "panorama", "matterport",
  "campus tour", "immersive experience"
];
let polling = null;
let results = [];

// Init
document.addEventListener('DOMContentLoaded', renderPills);

function renderPills() {
  const c = document.getElementById('keyword-pills');
  c.innerHTML = allKeywords.map(kw =>
    `<span class="pill ${activeKeywords.includes(kw)?'active':''}" onclick="toggleKW('${kw}')">${kw}</span>`
  ).join('');
}

function toggleKW(kw) {
  const i = activeKeywords.indexOf(kw);
  if (i >= 0) activeKeywords.splice(i, 1);
  else activeKeywords.push(kw);
  renderPills();
}

function addKeyword() {
  const inp = document.getElementById('custom-kw');
  const kw = inp.value.trim();
  if (kw && !allKeywords.includes(kw)) {
    allKeywords.push(kw);
    activeKeywords.push(kw);
    renderPills();
  }
  inp.value = '';
}

function addUrl(url) {
  const ta = document.getElementById('urls');
  if (!ta.value.includes(url)) {
    ta.value = ta.value ? ta.value.trimEnd() + '\n' + url : url;
  }
}

function switchTab(tab, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-scraper').style.display = tab === 'scraper' ? '' : 'none';
  document.getElementById('tab-results').style.display = tab === 'results' ? '' : 'none';
}

function clearAll() {
  document.getElementById('terminal').innerHTML =
    '<div class="terminal-empty"><div class="icon">⚡</div>Waiting for scrape command...</div>';
  results = [];
  renderResults();
}

// Scrape
async function startScrape() {
  const urls = document.getElementById('urls').value.split('\n').map(u=>u.trim()).filter(u=>u);
  if (!urls.length) return alert('Add at least one URL');
  if (!activeKeywords.length) return alert('Select at least one keyword');

  const btn = document.getElementById('btn-scrape');
  btn.disabled = true; btn.textContent = '⟳ Scraping...';

  document.getElementById('terminal').innerHTML = '';
  results = [];
  renderResults();

  try {
    await fetch('/massinteract-tender/api/scrape', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        urls,
        keywords: activeKeywords,
        follow_detail: document.getElementById('follow-detail').checked,
        use_search: document.getElementById('use-search').checked,
      })
    });
    startPolling();
  } catch(e) {
    appendLog({time:'--:--:--', msg:'Failed to start scrape: '+e.message, level:'error'});
    btn.disabled = false; btn.textContent = '⚡ Start Scrape';
  }
}

function startPolling() {
  if (polling) clearInterval(polling);
  let lastLogCount = 0;
  polling = setInterval(async () => {
    try {
      const resp = await fetch('/massinteract-tender/api/status');
      const data = await resp.json();

      // Append new logs
      const newLogs = data.logs.slice(lastLogCount);
      newLogs.forEach(appendLog);
      lastLogCount = data.logs.length;

      // Update results
      results = data.results || [];
      renderResults();
      document.getElementById('result-count').textContent = results.length;

      if (!data.running) {
        clearInterval(polling);
        polling = null;
        const btn = document.getElementById('btn-scrape');
        btn.disabled = false; btn.textContent = '⚡ Start Scrape';
        appendLog({time: new Date().toLocaleTimeString().slice(0,8), msg: '✓ Scrape finished', level: 'system'});
      }
    } catch(e) {}
  }, 1000);
}

function appendLog(log) {
  const t = document.getElementById('terminal');
  if (t.querySelector('.terminal-empty')) t.innerHTML = '';
  const cls = 'log-' + (log.level || 'info');
  t.insertAdjacentHTML('beforeend',
    `<div class="log-line ${cls}"><span class="time">[${log.time}]</span> ${esc(log.msg)}</div>`
  );
  t.scrollTop = t.scrollHeight;
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// Results rendering
function renderResults() {
  const c = document.getElementById('results-list');
  const high = results.filter(r => (r.relevance||'').toUpperCase() === 'HIGH').length;
  const med = results.filter(r => (r.relevance||'').toUpperCase() === 'MEDIUM').length;

  document.getElementById('results-meta').textContent = `${results.length} tenders found  |  HIGH: ${high}  MED: ${med}`;
  document.getElementById('btn-csv').disabled = !results.length;
  document.getElementById('btn-json').disabled = !results.length;

  if (!results.length) {
    c.innerHTML = '<div class="empty-state"><div class="icon">📋</div>No results yet. Run a scrape first.</div>';
    return;
  }

  c.innerHTML = results.map((r, i) => `
    <div class="result-card" id="rc-${i}" onclick="toggleDetail(${i})">
      <div class="result-header">
        <div style="flex:1">
          <div class="result-badges">
            <span class="badge-relevance badge-${(r.relevance||'MEDIUM').toUpperCase()}">${(r.relevance||'MEDIUM').toUpperCase()}</span>
            <span class="badge-status status-${r.status||'unknown'}"><span class="dot"></span>${r.status||'unknown'}</span>
            <span class="badge-type">${r.type||'RFP'}</span>
          </div>
          <div class="result-title">${esc(r.title||'Untitled')}</div>
          <div class="result-meta">
            <span>📍 ${esc(r.location||'N/A')}</span>
            <span>🏢 ${esc(r.organization||'N/A')}</span>
            <span>🗓 ${esc(r.deadline||'N/A')}</span>
          </div>
        </div>
        <span class="chevron" id="chev-${i}">▾</span>
      </div>
      <div class="result-detail" id="det-${i}" style="display:none">
        <div class="detail-line"><span class="label">ID:</span>${esc(r.rfp_id||'N/A')}</div>
        <div class="detail-line"><span class="label">Source:</span>${esc(r.source||'N/A')}</div>
        <div class="detail-line"><span class="label">Budget:</span>${esc(r.budget||'Not disclosed')}</div>
        <div class="detail-line"><span class="label">Posted:</span>${esc(r.posted_date||'N/A')}</div>
        <div class="detail-line"><span class="label">Contact:</span>${esc(r.contact_person||'Check source')}</div>
        <div class="detail-line"><span class="label">Email:</span>${esc(r.contact_email||'Check source')}</div>
        <div class="detail-line full-width"><span class="label">Desc:</span>${esc(r.description||'N/A')}</div>
        ${r.url ? `<div class="detail-line full-width"><span class="label">Link:</span><a href="${esc(r.url)}" target="_blank" onclick="event.stopPropagation()">${esc(r.url)}</a></div>` : ''}
      </div>
    </div>
  `).join('');
}

function toggleDetail(i) {
  const det = document.getElementById('det-'+i);
  const chev = document.getElementById('chev-'+i);
  const card = document.getElementById('rc-'+i);
  const showing = det.style.display !== 'none';
  det.style.display = showing ? 'none' : 'grid';
  chev.classList.toggle('open', !showing);
  card.classList.toggle('expanded', !showing);
}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="TenderScope — Virtual Tour RFP Scraper")
    parser.add_argument("--headless", action="store_true", help="Run without web UI, scrape and export immediately")
    parser.add_argument("--urls", nargs="+", help="URLs to scrape (headless mode)")
    parser.add_argument("--keywords", nargs="+", default=DEFAULT_KEYWORDS, help="Keywords to match")
    parser.add_argument("--output", default="tenders.csv", help="Output file path (csv or json)")
    parser.add_argument("--follow", action="store_true", help="Follow detail pages for richer metadata")
    parser.add_argument("--search", action="store_true", help="Also use Google discovery to find extra pages")
    parser.add_argument("--port", type=int, default=5000, help="Web UI port (default 5000)")
    args = parser.parse_args()

    if args.headless:
        if not args.urls:
            print("Error: --urls required in headless mode")
            sys.exit(1)
        scraper = TenderScraper(keywords=args.keywords)
        scraper.scrape_all(args.urls, follow_detail=args.follow, use_search=args.search)

        if args.output.endswith(".json"):
            scraper.to_json(args.output)
        else:
            scraper.to_csv(args.output)

        print(f"\nDone! {len(scraper.results)} tenders saved to {args.output}")
    else:
        app = create_app()
        print(f"\n{'='*56}")
        print(f"  ⚡ TenderScope v{VERSION}")
        print(f"  Open http://localhost:{args.port} in your browser")
        print(f"{'='*56}\n")
        app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
