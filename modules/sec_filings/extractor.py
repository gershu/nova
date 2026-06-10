"""sec-api.io Extractor-Wrapper — Textsektionen aus 10-K/10-Q ziehen.

Zwei Funktionen:
  - find_filing_url(accession_no): via Query-API die linkToFilingDetails
    (primary HTML doc) zurueckholen, die der Extractor als Input braucht.
  - fetch_section(filing_url, item_id): Extractor-Endpoint aufrufen und
    den Sektion-Text als String zurueckliefern.

Endpunkt: https://api.sec-api.io/extractor
  GET-Parameter: url, item, type (text|html), token

Bekannte Items:
  10-K: 1 (Business), 1A (Risk Factors), 1B, 2, 3, 4, 5, 6, 7 (MD&A),
        7A, 8, 9, 9A, 9B, 10, 11, 12, 13, 14, 15
  10-Q:  part1item1, part1item2 (MD&A), part1item3, part1item4,
         part2item1, part2item1a, part2item2, ...

Fuer den Screener brauchen wir typischerweise: 10-K Item 1, 1A, 7.
"""

from __future__ import annotations

import re

import requests

from .client import QUERY_URL, SecApiError, _api_key


EXTRACTOR_URL = "https://api.sec-api.io/extractor"

# Mitarbeiterzahl aus 10-K Item 1 (Business). dei:EntityNumberOfEmployees
# ist meist NICHT XBRL-getaggt -> Textextraktion.
_EMP_RE = re.compile(
    r"([\d][\d,]{2,})\s+(?:full[\s-]?time\s+|regular\s+|total\s+|"
    r"approximately\s+)?(?:employees|persons|people|full[\s-]?time)",
    re.IGNORECASE)


def parse_employees(text: str):
    """Erste plausible Mitarbeiterzahl (>= 50) aus einem Textabschnitt."""
    if not text:
        return None
    for m in _EMP_RE.finditer(text):
        try:
            v = float(m.group(1).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if v >= 50:
            return v
    return None


def fetch_employees_from_filing(accession_no: str):
    """Mitarbeiterzahl aus 10-K Item 1 (Business) via Extractor. None bei
    Fehlschlag (2 API-Calls: Query + Extractor)."""
    if not accession_no:
        return None
    try:
        url = find_filing_url(accession_no)
        if not url:
            return None
        text = fetch_section(url, "1")
    except SecApiError:
        return None
    return parse_employees(text)


def fetch_mdna_from_filing(accession_no: str, form_type: str | None = None,
                           *, max_chars: int = 12000) -> str:
    """MD&A-Sektion (Management's Discussion & Analysis) als Plain-Text.

    10-K -> Item 7, 10-Q -> part1item2. Liefert '' bei Fehlschlag oder leerer
    Sektion (2 API-Calls: Query + Extractor). max_chars begrenzt die Laenge
    fuer LLM-Prompts.
    """
    if not accession_no:
        return ""
    item = "part1item2" if (form_type or "").upper().startswith("10-Q") else "7"
    try:
        url = find_filing_url(accession_no)
        if not url:
            return ""
        text = fetch_section(url, item)
    except SecApiError:
        return ""
    return (text or "").strip()[:max_chars]


def find_filing_url(accession_no: str) -> str | None:
    """linkToFilingDetails (primary HTM doc) fuer eine Accession-No.

    Der Extractor braucht die HTML-URL, nicht die Accession; ein zusaetzlicher
    Query-API-Call kostet wenig (1 API-Hit). Returns None, wenn das Filing
    nicht gefunden wird.
    """
    if not _api_key():           # Pfad A: sec-api stillgelegt
        return None
    payload = {
        "query": f'accessionNo:"{accession_no}"',
        "from":  "0",
        "size":  "1",
    }
    try:
        resp = requests.post(
            QUERY_URL, json=payload,
            headers={"Authorization": _api_key()}, timeout=20)
    except requests.RequestException as e:
        raise SecApiError(f"Query-API-Request fehlgeschlagen: {e}") from e
    if resp.status_code != 200:
        raise SecApiError(
            f"Query-API HTTP {resp.status_code}: {resp.text[:200]}")
    filings = (resp.json() or {}).get("filings", [])
    if not filings:
        return None
    return filings[0].get("linkToFilingDetails")


def fetch_section(filing_url: str, item: str,
                   *, return_html: bool = False) -> str:
    """Textsektion eines Filings via Extractor-API.

    item: '1', '1A', '7' fuer 10-K; 'part1item2' etc. fuer 10-Q.
    return_html=False liefert Plain-Text (default — fuer LLM-Prompts ideal).
    Returns einen leeren String, wenn die Sektion leer ist; raises bei
    HTTP-Fehlern.
    """
    params = {
        "url":   filing_url,
        "item":  item,
        "type":  "html" if return_html else "text",
        "token": _api_key(),
    }
    try:
        resp = requests.get(EXTRACTOR_URL, params=params, timeout=40)
    except requests.RequestException as e:
        raise SecApiError(
            f"Extractor-Request fehlgeschlagen: {e}") from e
    if resp.status_code != 200:
        raise SecApiError(
            f"Extractor HTTP {resp.status_code} (item={item}): "
            f"{resp.text[:200]}")
    return resp.text or ""
