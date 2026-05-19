"""IMAP-Adapter fuer Seeking-Alpha-Mails via Gmail.

Setup auf Stefan-Seite (einmalig):
  1. App-Password generieren: myaccount.google.com -> Security -> 2-Step-Verification
     -> App passwords -> "Mail / Other" -> 16-Char-Code
  2. Gmail-Filter anlegen:
       From: noreply@seekingalpha.com OR alerts@seekingalpha.com
       Apply label: nova-sa
  3. ~/.nova_env auf nova-hub:
       export GMAIL_IMAP_HOST=imap.gmail.com
       export GMAIL_IMAP_USER=...@gmail.com
       export GMAIL_IMAP_PASSWORD='xxxx xxxx xxxx xxxx'
       export GMAIL_SA_LABEL=nova-sa

Flow:
  - search() liefert UIDs aller Mails mit Label `nova-sa`
  - fetch() laed pro UID Header + Body, parsed Symbol + Summary
  - move_to_processed() verschiebt die UIDs nach `nova-sa/processed`

Gmail-IMAP-Eigenheiten:
  - Labels sind Folders im IMAP-Sinn. SELECT "nova-sa" funktioniert.
  - MOVE-Command kennt Gmail nicht; wir COPY + EXPUNGE-mark "\\Deleted".
    Faster than full move dance, idempotent enough.
"""

from __future__ import annotations

import email
import email.header
import hashlib
import imaplib
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, Optional

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

log = logging.getLogger(__name__)


# ---------- Datentypen ----------

@dataclass
class ParsedArticle:
    article_id:    str
    ts:            datetime         # UTC
    title:         str
    summary:       str
    url:           Optional[str]
    symbols:       list[tuple[str, str]] = field(default_factory=list)   # (symbol, extracted_from)
    imap_uid:      Optional[str] = None
    raw_subject:   Optional[str] = None
    raw_from:      Optional[str] = None


@dataclass
class ImapConfig:
    host:     str
    user:     str
    password: str
    label:    str = "nova-sa"
    processed_label: str = "nova-sa/processed"

    @classmethod
    def from_env(cls) -> "ImapConfig":
        host = os.environ.get("GMAIL_IMAP_HOST", "imap.gmail.com")
        user = os.environ.get("GMAIL_IMAP_USER")
        pwd  = os.environ.get("GMAIL_IMAP_PASSWORD")
        label = os.environ.get("GMAIL_SA_LABEL", "nova-sa")
        if not user or not pwd:
            raise RuntimeError(
                "GMAIL_IMAP_USER + GMAIL_IMAP_PASSWORD muessen in ~/.nova_env gesetzt sein.\n"
                "Setup: App-Password via myaccount.google.com -> Security -> App passwords.")
        return cls(host=host, user=user, password=pwd, label=label,
                   processed_label=f"{label}/processed")


# ---------- Helpers ----------

# (NYSE:AAPL), (NASDAQ:MSFT), (OTC:XXX), (XETRA:SAP) — Exchange:Ticker
_EXCH_TICKER_RE = re.compile(r"\(\s*([A-Z]+)\s*:\s*([A-Z][A-Z0-9.\-]{0,9})\s*\)")
# Plain (SYMBOL) — 3-5 Zeichen, alphanumerisch, fuer Mails ohne Exchange-Prefix
# (z.B. "Apple Inc. (AAPL) reports Q1"). Restriktiv auf Laenge 3-5 um False
# Positives wie (US), (USA), (II) zu vermeiden — finale Filterung ueber
# ref_instruments-Lookup macht den Rest.
_PLAIN_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{2,4})\)")
# /symbol/AAPL/  oder  /symbol/BRK.B/ in Body-Links
_BODY_URL_SYMBOL_RE = re.compile(r"/symbol/([A-Z][A-Z0-9.\-]{0,9})\b", re.IGNORECASE)
# Canonical Article URL
_URL_RE             = re.compile(r"https?://(?:www\.)?seekingalpha\.com/[a-z]+/[\w\-/?&=]+", re.IGNORECASE)

# Datum-Patterns fuer "erste Zeilen nach dem Datum"-Lokalisierung im Body.
_DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                              # 2026-05-11
    re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b"),                        # 11.05.2026
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b"),  # May 11, 2026
    re.compile(r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b"),    # 11 May 2026
]

# False-Positive-Filter fuer Plain-Paren-Matching: Exchange-Codes und
# generische Acronyme die in SA-Newsletter oft in Klammern erscheinen.
_NON_TICKER_PAREN = {
    "NYSE", "NASDAQ", "OTC", "AMEX", "ARCA", "XETRA", "FWB", "ASX",
    "LSE", "TSX", "HKEX", "TSE", "BSE", "USD", "EUR", "GBP", "JPY",
    "CEO", "CFO", "COO", "CTO", "ETF", "REIT", "IPO", "M&A",
    "USA", "EU", "UK", "AI", "ML",
}

# Window-Groesse fuer Plain-Paren-Suche im Body — wir scannen die ersten
# N chars NACH dem ersten Datum-Match. SA-Mail-Templates haben das Symbol
# typischerweise in den ersten 2-3 Absaetzen.
_BODY_PAREN_WINDOW_CHARS = 2500


def _decode_header(s: Optional[str]) -> str:
    if not s:
        return ""
    parts = email.header.decode_header(s)
    out = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(enc or "utf-8", errors="replace"))
            except (LookupError, TypeError):
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out).strip()


def _strip_html(html: str, max_chars: int = 1200) -> str:
    """HTML -> plaintext Summary. Mit BS4 sauber; sonst Regex-Fallback."""
    if _BS4_AVAILABLE:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "head"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
    else:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&nbsp;|&amp;|&quot;|&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _extract_body(msg: email.message.Message) -> tuple[str, str]:
    """Returns (html_body, text_body). Bevorzugt HTML."""
    html_body, text_body = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html" and not html_body:
                payload = part.get_payload(decode=True)
                if payload:
                    html_body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            elif ct == "text/plain" and not text_body:
                payload = part.get_payload(decode=True)
                if payload:
                    text_body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = body
            else:
                text_body = body
    return html_body, text_body


def _extract_url(html_body: str, text_body: str) -> Optional[str]:
    """Canonical SA-URL aus Mail body."""
    for source in (html_body, text_body):
        if not source:
            continue
        m = _URL_RE.search(source)
        if m:
            # Tracking-Params abschneiden
            url = m.group(0)
            url = url.split("?")[0].rstrip(",.;)\"'")
            return url
    return None


def _find_first_date_pos(text: str) -> Optional[int]:
    """Position des ersten Datum-Patterns im Text, oder None."""
    earliest: Optional[int] = None
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m and (earliest is None or m.start() < earliest):
            earliest = m.start()
    return earliest


def _extract_symbols(subject: str, body_text: str) -> list[tuple[str, str]]:
    """(symbol, extracted_from) tuples — dedup'd, priority subject > body-URL > body-paren.

    Drei Extraktions-Stufen:
      1. (EXCH:TICKER) — Subject + Body, highest confidence
      2. /symbol/TICKER/ — Body href-Pfade (raw HTML)
      3. (TICKER) plain — Subject + Body-Window nach erstem Datum,
         3-5 Zeichen Restriktion + Acronym-Blacklist gegen False Positives
    """
    found: dict[str, str] = {}

    # 1. (EXCH:TICKER) — sicherstes Pattern, gleicher Pattern fuer subject + body
    for source_label, text in (("subject", subject), ("body", body_text)):
        for m in _EXCH_TICKER_RE.finditer(text):
            sym = m.group(2).upper()
            found.setdefault(sym, source_label)

    # 2. /symbol/TICKER/ in body (raw HTML hrefs)
    for m in _BODY_URL_SYMBOL_RE.finditer(body_text):
        sym = m.group(1).upper()
        found.setdefault(sym, "body")

    # 3. (TICKER) plain — Subject IMMER, Body NUR nach erstem Datum-Pattern.
    # Restriktion: 3-5 Zeichen + nicht in NON_TICKER_PAREN-Blacklist. Finale
    # False-Positive-Filterung passiert via ref_instruments-Lookup im persister.
    for m in _PLAIN_TICKER_RE.finditer(subject):
        sym = m.group(1).upper()
        if sym in _NON_TICKER_PAREN:
            continue
        found.setdefault(sym, "subject_paren")

    if body_text:
        date_pos = _find_first_date_pos(body_text)
        # Wenn kein Datum gefunden: scanne body-anfang trotzdem (defensiv).
        scan_start = date_pos if date_pos is not None else 0
        window = body_text[scan_start : scan_start + _BODY_PAREN_WINDOW_CHARS]
        for m in _PLAIN_TICKER_RE.finditer(window):
            sym = m.group(1).upper()
            if sym in _NON_TICKER_PAREN:
                continue
            found.setdefault(sym, "body_paren")

    return [(s, src) for s, src in found.items()]


def _article_id(msg_id: Optional[str], uid: str) -> str:
    """Stable hash: Message-ID wenn vorhanden, sonst UID."""
    key = (msg_id or f"uid:{uid}").strip()
    return "sa_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


# ---------- IMAP-Client ----------

class GmailSAClient:
    """Liest Seeking-Alpha-Mails aus Gmail via IMAP."""

    def __init__(self, cfg: ImapConfig) -> None:
        self.cfg = cfg
        self._imap: Optional[imaplib.IMAP4_SSL] = None

    def __enter__(self) -> "GmailSAClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        try:
            self._imap = imaplib.IMAP4_SSL(self.cfg.host, 993)
            self._imap.login(self.cfg.user, self.cfg.password)
        except imaplib.IMAP4.error as e:
            raise ConnectionError(
                f"Gmail-IMAP-Login fehlgeschlagen: {e}\n"
                f"  user={self.cfg.user}\n"
                f"  hints: App-Password noch gueltig? 2-Step-Verification aktiv?"
            ) from e

    def close(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            except Exception:  # noqa: BLE001
                pass
            self._imap = None

    def _select(self, label: str) -> None:
        # Gmail-Labels mit Spaces/Slashes muessen gequoted werden
        status, _ = self._imap.select(f'"{label}"', readonly=False)
        if status != "OK":
            raise RuntimeError(f"IMAP SELECT '{label}' fehlgeschlagen: {status}")

    def list_uids(self) -> list[str]:
        """Alle UIDs im nova-sa Label."""
        self._select(self.cfg.label)
        status, data = self._imap.uid("search", None, "ALL")
        if status != "OK":
            return []
        uids = data[0].split() if data and data[0] else []
        return [u.decode() if isinstance(u, bytes) else u for u in uids]

    def fetch_article(self, uid: str) -> Optional[ParsedArticle]:
        """Fetched + parsed eine Mail. Returnt None bei Parse-Error."""
        status, data = self._imap.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK" or not data or not data[0]:
            return None
        raw = data[0][1]
        if isinstance(raw, str):
            raw = raw.encode()
        msg = email.message_from_bytes(raw)

        subject = _decode_header(msg.get("Subject"))
        from_addr = _decode_header(msg.get("From"))
        msg_id = (msg.get("Message-ID") or "").strip()

        try:
            date_str = msg.get("Date")
            ts = parsedate_to_datetime(date_str) if date_str else datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts = ts.astimezone(timezone.utc)
        except (TypeError, ValueError):
            ts = datetime.now(timezone.utc)

        html_body, text_body = _extract_body(msg)
        # Wichtig: Symbol-Regex auf RAW HTML laufen lassen — BS4 strippt
        # href-Attribute, und SA encodet Tickers oft NUR im /symbol/XXX/ Pfad,
        # nicht im sichtbaren Text. Raw HTML enthaelt die hrefs.
        body_for_symbols = html_body if html_body else text_body
        summary = _strip_html(html_body, max_chars=1200) if html_body else (text_body[:1200] if text_body else "")
        url = _extract_url(html_body, text_body)
        symbols = _extract_symbols(subject, body_for_symbols)

        return ParsedArticle(
            article_id  = _article_id(msg_id, uid),
            ts          = ts,
            title       = subject,
            summary     = summary,
            url         = url,
            symbols     = symbols,
            imap_uid    = uid,
            raw_subject = subject,
            raw_from    = from_addr,
        )

    def move_to_processed(self, uids: Iterable[str]) -> int:
        """COPY + mark \\Deleted + EXPUNGE — Gmail-konformes 'Move'.

        Pre-condition: aktuelles SELECT ist self.cfg.label.
        """
        uid_list = list(uids)
        if not uid_list:
            return 0
        # Sicherstellen dass Ziel-Label existiert. Gmail erstellt Sub-Labels
        # via Slash-Notation; CREATE ist idempotent.
        self._imap.create(f'"{self.cfg.processed_label}"')

        n = 0
        # Batch in 50er-Gruppen — Gmail droppt sehr lange UID-Listen
        for i in range(0, len(uid_list), 50):
            batch = ",".join(uid_list[i:i+50])
            status, _ = self._imap.uid("copy", batch, f'"{self.cfg.processed_label}"')
            if status != "OK":
                log.warning(f"COPY batch failed: {status}")
                continue
            status, _ = self._imap.uid("store", batch, "+FLAGS", "(\\Deleted)")
            if status != "OK":
                log.warning(f"STORE \\Deleted failed: {status}")
                continue
            n += len(uid_list[i:i+50])
        # EXPUNGE: leert die nova-sa Mailbox von den \\Deleted-markierten
        self._imap.expunge()
        return n
