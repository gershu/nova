"""yfinance.news shared helper.

Wird genutzt von probe_alert.py + alert_explainer/__main__.py.

yfinance.news ist intermittierend leer (Rate-Limit-aehnliches Verhalten bei
sequenziellen Calls). Helper hat 1 Retry mit Sleep on empty result.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone


def fetch_news_yfinance(
    symbol: str,
    max_n: int = 3,
    retry_on_empty: bool = True,
    name: str | None = None,
    augment_with_rss: bool = True,
    augment_threshold: int = 2,
) -> list[dict]:
    """Holt yfinance.news fuer Symbol. Returnt Liste von Dicts mit
    {ts, title, publisher, link}. Leer bei Fail oder wenn Symbol unbekannt.

    NEU (Lean-RSS-Augment):
      Wenn augment_with_rss=True (Default) und yfinance < augment_threshold
      Items liefert (DACH-Stocks typisch), wird mit RSS-Feeds (Handelsblatt,
      Manager Magazin, DGAP) supplementiert. Match per Company-Name (besser
      als Symbol-Match fuer DACH).

      Fuer Symbol-only Calls (kein name): RSS-Match nur per Symbol —
      weniger treffsicher aber besser als nichts.
    """
    items = _do_fetch(symbol, max_n)
    if not items and retry_on_empty:
        time.sleep(0.5)
        items = _do_fetch(symbol, max_n)

    # RSS-Augmentation wenn yfinance unter threshold
    if augment_with_rss and len(items) < augment_threshold:
        try:
            from .news_rss import fetch_news_rss
            need = max_n - len(items)
            rss_items = fetch_news_rss(symbol, name, max_n=need)
            seen_urls = {x.get("link") for x in items if x.get("link")}
            for r in rss_items:
                if r.get("link") and r.get("link") not in seen_urls:
                    items.append(r)
                    seen_urls.add(r.get("link"))
                if len(items) >= max_n:
                    break
        except ImportError:
            pass    # feedparser fehlt — silently no augment

    return items


def _do_fetch(symbol: str, max_n: int) -> list[dict]:
    try:
        import yfinance as yf
    except ImportError:
        return []
    try:
        t = yf.Ticker(symbol)
        news = t.news or []
    except Exception:  # noqa: BLE001
        return []

    out = []
    for item in news[:max_n]:
        # yfinance returns content-wrapped (newer) oder flat (aeltere Versionen)
        content = item.get("content", item)
        title = content.get("title") or item.get("title", "")

        # Publisher: provider.displayName (content-wrapped) oder publisher (flat)
        publisher = ""
        prov = content.get("provider")
        if isinstance(prov, dict):
            publisher = prov.get("displayName", "")
        if not publisher:
            publisher = item.get("publisher", "") or ""

        # Timestamp: pubDate (ISO string) oder providerPublishTime (epoch)
        ts_raw = content.get("pubDate") or item.get("providerPublishTime")
        if isinstance(ts_raw, (int, float)):
            ts_str = datetime.fromtimestamp(ts_raw, tz=timezone.utc).strftime("%Y-%m-%d")
        elif isinstance(ts_raw, str):
            ts_str = ts_raw[:10]
        else:
            ts_str = "?"

        # Link: canonicalUrl.url (content-wrapped) oder link (flat)
        link = ""
        canon = content.get("canonicalUrl")
        if isinstance(canon, dict):
            link = canon.get("url", "")
        if not link:
            link = item.get("link", "") or ""

        out.append({
            "ts":        ts_str,
            "title":     title,
            "publisher": publisher,
            "link":      link,
        })
    return out


def render_news_block(news: list[dict]) -> str:
    """Markdown-aehnlicher Block fuer den LLM-Prompt."""
    if not news:
        return "  (keine Nachrichten verfuegbar)"
    lines = []
    for n in news:
        bits = [f"  [{n['ts']}]"]
        if n["publisher"]:
            bits.append(f"({n['publisher']})")
        bits.append(n["title"])
        lines.append(" ".join(bits))
    return "\n".join(lines)
