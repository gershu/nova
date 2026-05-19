"""RSS-News-Source: schliesst yfinance-Coverage-Luecken bei DACH-Stocks.

yfinance.news ist bei deutschen Aktien (SAP/BAS/CBK/EOAN/MUV2/...)
schwach — leere oder null-relevante Ergebnisse. RSS-Feeds von Handelsblatt,
Manager Magazin, DGAP (Ad-hoc-Mitteilungen) etc. fuellen die Luecke.

Modul-Level-Cache (TTL 10 min) verhindert dass jeder alert_explainer-Call
neu fetched. Bei 18 alerts/Tag: 5 RSS-Fetches total statt 90.

Keyword-Match per Symbol UND Company-Name (aus ref_instruments.name) —
fuer DACH ist Name-Match deutlich treffsicherer ('Commerzbank' vs 'CBK').
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

log = logging.getLogger("nova.llm.news_rss")


# Default-Feeds — frei, kein API-Key, gute DACH-Coverage
DEFAULT_FEEDS: list[dict] = [
    {
        "url":       "https://www.handelsblatt.com/contentexport/feed/finanzen",
        "publisher": "Handelsblatt",
        "language":  "de",
    },
    {
        "url":       "https://www.manager-magazin.de/wirtschaft/index.rss",
        "publisher": "Manager Magazin",
        "language":  "de",
    },
    {
        "url":       "https://www.dgap.de/dgap/News/feed/eqs/",
        "publisher": "DGAP",
        "language":  "de",
    },
    {
        "url":       "https://www.boersen-zeitung.de/feed.xml",
        "publisher": "Boersen-Zeitung",
        "language":  "de",
    },
]


# Module-Level-Cache: url -> (fetch_timestamp_monotonic, articles)
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL_SECONDS = 600  # 10 min


def _fetch_feed(url: str, publisher: str, language: str) -> list[dict]:
    """Fetch one RSS feed (with cache). Returns list of normalized article dicts."""
    try:
        import feedparser
    except ImportError:
        log.warning("feedparser nicht installiert — RSS-Augmentation unverfuegbar")
        return []

    now = time.monotonic()
    if url in _CACHE:
        ts, items = _CACHE[url]
        if now - ts < _CACHE_TTL_SECONDS:
            return items

    try:
        d = feedparser.parse(url)
    except Exception as e:  # noqa: BLE001
        log.warning("RSS fetch failed for %s: %s", publisher, e)
        return []

    out: list[dict] = []
    for entry in d.entries[:50]:    # cap auf 50 Items pro Feed
        # Date parsing — feedparser parst published in published_parsed (struct_time)
        ts_str = ""
        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        if pub:
            try:
                ts_str = datetime(*pub[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                pass

        out.append({
            "title":     (entry.get("title") or "").strip(),
            "summary":   (entry.get("summary") or "").strip()[:500],
            "link":      entry.get("link", ""),
            "publisher": publisher,
            "language":  language,
            "ts":        ts_str,
        })

    _CACHE[url] = (now, out)
    return out


def fetch_all_feeds(feeds: list[dict] | None = None) -> list[dict]:
    """Fetch alle Feeds (mit Cache). Returnt zusammengefuehrte Liste."""
    if feeds is None:
        feeds = DEFAULT_FEEDS
    out: list[dict] = []
    for f in feeds:
        try:
            out.extend(_fetch_feed(f["url"], f["publisher"], f["language"]))
        except Exception as e:  # noqa: BLE001
            log.warning("RSS feed exception (%s): %s", f.get("publisher"), e)
            continue
    return out


def _build_keywords(symbol: str | None, name: str | None) -> list[str]:
    """Baut Keyword-Liste — Name-Tokens und Symbol. Returnt in Reihenfolge
    most-specific-first."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(k: str) -> None:
        kl = k.lower().strip()
        if kl and kl not in seen and len(kl) >= 3:
            seen.add(kl)
            out.append(k.strip())

    if name:
        _add(name)
        # Erstes signifikantes Token (z.B. 'SAP SE' -> 'SAP', 'Commerzbank AG' -> 'Commerzbank')
        for token in name.split():
            if token.upper() not in {"THE", "INC", "CORP", "AG", "SE", "NV", "PLC", "LTD", "GROUP", "CLASS", "INC."}:
                _add(token)
                break

    if symbol and not symbol.startswith("IBCID"):    # IBCID-Bonds skip — generisch
        _add(symbol)

    return out


def match_articles_for_symbol(
    articles: list[dict],
    symbol: str | None,
    name: str | None,
    max_n: int = 3,
) -> list[dict]:
    """Filter Artikel die Symbol/Company-Name in title oder summary erwaehnen."""
    keywords = _build_keywords(symbol, name)
    if not keywords:
        return []

    matched: list[dict] = []
    for a in articles:
        haystack = (a.get("title", "") + " " + a.get("summary", "")).lower()
        for kw in keywords:
            if kw.lower() in haystack:
                matched.append({**a, "matched_keyword": kw})
                break

    # Sort by date desc (string-sort funktioniert fuer YYYY-MM-DD format)
    matched.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return matched[:max_n]


def fetch_news_rss(
    symbol: str | None,
    name: str | None = None,
    max_n: int = 3,
    feeds: list[dict] | None = None,
) -> list[dict]:
    """Top-level: alle Feeds laden, Symbol/Name matchen, top-N zurueck.
    Output-Shape kompatibel zu news_yfinance: {ts, title, publisher, link}."""
    articles = fetch_all_feeds(feeds)
    matched = match_articles_for_symbol(articles, symbol, name, max_n=max_n)
    return [
        {
            "ts":        a["ts"],
            "title":     a["title"],
            "publisher": a["publisher"],
            "link":      a["link"],
        }
        for a in matched
    ]
