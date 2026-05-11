#!/usr/bin/env python3
"""
Hantavirus Tracker — Auto-updater
Runs every 2h via GitHub Actions. Scrapes hantavirus.live and
hantavirusmap.com, then rewrites data.json in-place.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Run: pip install requests beautifulsoup4")
    sys.exit(1)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; HantavirusTracker/1.0; "
        "+https://github.com/YOUR_USERNAME/hantavirus-tracker)"
    )
}
DATA_FILE = Path(__file__).parent / "data.json"


# ── helpers ───────────────────────────────────────────────────────────────────

def fetch(url: str) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  WARN: could not fetch {url} — {e}")
        return None


def meta(soup: BeautifulSoup, name: str) -> str:
    tag = soup.find("meta", {"name": name}) or soup.find("meta", {"property": name})
    return (tag or {}).get("content", "")


# ── source 1: hantavirus.live ─────────────────────────────────────────────────

def scrape_hantavirus_live() -> dict:
    """
    The meta-description of hantavirus.live is updated hourly and contains
    the headline numbers, e.g.:
      '3 deaths, 12 cases (7 confirmed). ... MV Hondius departed Tenerife...'
    """
    html = fetch("https://hantavirus.live/")
    if not html:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    desc = meta(soup, "description") or meta(soup, "og:description") or ""
    print(f"  hantavirus.live description: {desc[:120]}")

    result = {}

    m = re.search(r"(\d+)\s+deaths?", desc, re.I)
    if m:
        result["deaths"] = int(m.group(1))

    m = re.search(r"(\d+)\s+cases?\s*\((\d+)\s+confirmed\)", desc, re.I)
    if m:
        result["cases"]     = int(m.group(1))
        result["confirmed"] = int(m.group(2))
    else:
        m = re.search(r"(\d+)\s+cases?", desc, re.I)
        if m:
            result["cases"] = int(m.group(1))

    # ship status hint
    if "rotterdam" in desc.lower():
        result["shipStatus"] = "En route Rotterdam"
    elif "tenerife" in desc.lower():
        result["shipStatus"] = "At Tenerife — disembarkation"

    return result


# ── source 2: hantavirusmap.com ───────────────────────────────────────────────

COUNTRY_CODES = {
    "ES": ("🇪🇸", "Spain"),
    "AR": ("🇦🇷", "Argentina"),
    "US": ("🇺🇸", "United States"),
    "GB": ("🇬🇧", "United Kingdom"),
    "DE": ("🇩🇪", "Germany"),
    "NL": ("🇳🇱", "Netherlands"),
    "CA": ("🇨🇦", "Canada"),
    "IT": ("🇮🇹", "Italy"),
    "IN": ("🇮🇳", "India"),
    "ZA": ("🇿🇦", "South Africa"),
    "CV": ("🇨🇻", "Cape Verde"),
    "FR": ("🇫🇷", "France"),
    "NO": ("🇳🇴", "Norway"),
    "CH": ("🇨🇭", "Switzerland"),
    "BE": ("🇧🇪", "Belgium"),
    "GR": ("🇬🇷", "Greece"),
    "IE": ("🇮🇪", "Ireland"),
    "PT": ("🇵🇹", "Portugal"),
    "PL": ("🇵🇱", "Poland"),
}


def scrape_hantavirusmap() -> dict:
    """
    Scrape country signal counts from the outbreak page.
    The page renders country abbreviations followed by signal counts, e.g.:
      'SpainES286\nArgentinaAR26\nUSAUS23...'
    """
    html = fetch("https://hantavirusmap.com/outbreaks/mv-hondius-2026")
    if not html:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    signals_by_country = {}
    # pattern: two-letter ISO code followed by digits, e.g. ES286, AR26
    for code, (flag, name) in COUNTRY_CODES.items():
        pattern = rf"\b{re.escape(code)}\s*(\d+)\b"
        m = re.search(pattern, text)
        if m:
            signals_by_country[code] = {
                "flag": flag,
                "name": name,
                "signals": int(m.group(1)),
            }

    print(f"  hantavirusmap signals found: {len(signals_by_country)} countries")
    return {"signals": signals_by_country}


# ── merge & write ─────────────────────────────────────────────────────────────

def load_existing() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def merge(existing: dict, live: dict, mapdata: dict) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Update top-level outbreak numbers if we got fresh data
    ob = existing.get("outbreak", {})
    if "cases" in live:
        ob["cases"]     = live["cases"]
    if "confirmed" in live:
        ob["confirmed"] = live["confirmed"]
    if "deaths" in live:
        ob["deaths"]    = live["deaths"]
    if "shipStatus" in live:
        ob["shipStatus"] = live["shipStatus"]

    # Derive CFR
    if ob.get("cases") and ob.get("deaths"):
        ob["cfr"] = round(ob["deaths"] / ob["cases"] * 100, 1)

    existing["outbreak"] = ob

    # Merge signal counts into country rows
    signals = mapdata.get("signals", {})
    if signals:
        country_map = {c["name"]: c for c in existing.get("countries", [])}
        for code, sig in signals.items():
            name = sig["name"]
            if name in country_map:
                country_map[name]["signals"] = sig["signals"]
            # If not in existing list, we don't auto-add to avoid noise
        existing["countries"] = list(country_map.values())

    # Stamp
    existing.setdefault("meta", {})
    existing["meta"]["lastUpdated"] = now
    existing["meta"]["source"] = (
        "WHO DON600 (8 May 2026) + hantavirus.live (live) + hantavirusmap.com (live)"
    )

    return existing


def main():
    print("── Hantavirus Tracker Scraper ──────────────────")
    print(f"  Running at {datetime.now(timezone.utc).isoformat()}")

    print("\n[1/2] Scraping hantavirus.live …")
    live = scrape_hantavirus_live()
    print(f"  → {live}")

    print("\n[2/2] Scraping hantavirusmap.com …")
    mapdata = scrape_hantavirusmap()

    print("\n[merge] Loading existing data.json …")
    existing = load_existing()

    merged = merge(existing, live, mapdata)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"\n✓  data.json updated — {merged['outbreak'].get('cases')} cases, "
          f"{merged['outbreak'].get('deaths')} deaths")


if __name__ == "__main__":
    main()
