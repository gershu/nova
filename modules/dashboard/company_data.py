"""Quellenagnostische Unternehmens-Datenschicht (Phase 1 der Dashboard-
Zusammenfuehrung Thesis-Cockpit + Ad-Hoc).

Ziel: EINE Schnittstelle fuer alle Unternehmens-Kennzahlen, die automatisch
die beste Quelle waehlt:

  - Ist der Ticker im Universum (ref_instruments) UND in der DB persistiert
    (z.B. ref_income_statement) -> persistierte DuckDB-Tabellen (schnell).
  - Sonst -> on-Demand via sec-api.io (modules.sec_filings.client).

Die Rueckgabe-Shapes sind quellenidentisch, damit die spaetere 6-Fragen-View
quellenagnostisch bleibt. Jede Kennzahl existiert damit nur einmal.

Reines Modul (kein Streamlit) -> in der View mit st.cache_data wrappen,
isoliert testbar. Faellt bei jedem DB-Fehler sauber auf on-Demand zurueck.

GuV-Zeile (unified):
  period_end, form_type, currency, accession_no, filed_at, revenue,
  cost_of_revenue, gross_profit, rd_expense, sga_expense, operating_expense,
  operating_income, other_income, pretax_income, tax_expense, net_income
"""

from __future__ import annotations

from dataclasses import dataclass, field

from modules.sec_filings import client as _sec

# --- DB-Zugriff defensiv importieren (Sandbox/CLI hat evtl. keine DB) ------
try:
    from modules.dashboard.db import run_query as _run_query
except Exception:  # noqa: BLE001
    _run_query = None


_INCOME_COLS = [
    "revenue", "cost_of_revenue", "gross_profit", "rd_expense",
    "sga_expense", "operating_expense", "operating_income", "other_income",
    "pretax_income", "tax_expense", "net_income",
]


def _forms_for(period: str) -> tuple[str, ...]:
    """Periodenwahl -> EDGAR-Formtypen. 'quarterly'->10-Q, sonst 10-K."""
    return ("10-Q",) if period == "quarterly" else ("10-K",)


def _is_period_form(form_type, period: str) -> bool:
    """Passt ein form_type zur gewuenschten Darstellung (annual/quarterly)?"""
    ft = (form_type or "").upper()
    return ft.startswith("10-Q") if period == "quarterly" \
        else ft.startswith("10-K")


@dataclass
class Source:
    """Aufloesung Ticker -> Quelle/Metadaten fuer die View (inkl. Badge)."""
    ticker: str
    ref_instrument_id: str | None = None
    currency: str = "USD"
    name: str | None = None
    in_universe: bool = False
    income_source: str = "on-demand"      # 'db' | 'on-demand'

    @property
    def badge(self) -> str:
        return "DB" if self.income_source == "db" else "on-Demand"


def _db_query(sql: str, params: tuple):
    """run_query mit Schutz: None/Exception -> None (Fallback on-Demand)."""
    if _run_query is None:
        return None
    try:
        return _run_query(sql, params)
    except Exception:  # noqa: BLE001
        return None


def resolve(ticker: str) -> Source:
    """Ticker -> Source. Bevorzugt Universums-Wert mit persistierter GuV."""
    t = (ticker or "").strip().upper()
    src = Source(ticker=t)
    if not t:
        return src
    df = _db_query(
        "SELECT ref_instrument_id, symbol, currency, name "
        "FROM ref_instruments "
        "WHERE upper(symbol) = ? AND active "
        "ORDER BY (currency = 'USD') DESC, ref_instrument_id",
        (t,))
    if df is None or df.empty:
        return src
    row = df.iloc[0]
    src.ref_instrument_id = row["ref_instrument_id"]
    src.currency = row["currency"] or "USD"
    src.name = row["name"]
    src.in_universe = True
    cnt = _db_query(
        "SELECT COUNT(*) AS c FROM ref_income_statement "
        "WHERE ref_instrument_id = ?", (src.ref_instrument_id,))
    if cnt is not None and not cnt.empty and int(cnt["c"].iloc[0]) > 0:
        src.income_source = "db"
    return src


def _row_from_db(r) -> dict:
    d = {"period_end": str(r["period_end"])[:10],
         "form_type": r.get("form_type"),
         "currency": r.get("currency") or "USD",
         "accession_no": r.get("accession_no"),
         "filed_at": str(r["filed_at"])[:10] if r.get("filed_at") else None}
    for c in _INCOME_COLS:
        v = r.get(c)
        d[c] = None if v is None else (float(v) if _is_num(v) else None)
    return d


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _row_from_income(inc) -> dict:
    d = {"period_end": str(inc.period_end)[:10], "form_type": inc.form_type,
         "currency": inc.currency or "USD", "accession_no": inc.accession_no,
         "filed_at": str(inc.filed_at)[:10] if inc.filed_at else None}
    for c in _INCOME_COLS:
        d[c] = getattr(inc, c, None)
    return d


def income_history(ticker: str, *, n_years: int = 12,
                   period: str = "annual",
                   src: Source | None = None) -> dict:
    """GuV-Historie (unified). Returns {source, rows: [unified-dict, …]}.

    period='annual'   -> Jahres-GuV (10-K).
    period='quarterly'-> Quartals-GuV (10-Q; _pick waehlt die diskrete
                         3-Monats-Dauer, kein Year-to-Date).
    DB-Pfad liefert die persistierte Historie (10-Q + 10-K) und wird auf die
    gewuenschte Periode gefiltert; on-Demand zieht die passenden Filings.
    """
    src = src or resolve(ticker)
    if src.income_source == "db" and src.ref_instrument_id:
        df = _db_query(
            "SELECT period_end, form_type, currency, accession_no, filed_at, "
            + ", ".join(_INCOME_COLS) +
            " FROM ref_income_statement WHERE ref_instrument_id = ? "
            "ORDER BY period_end", (src.ref_instrument_id,))
        if df is not None and not df.empty:
            rows = [_row_from_db(r) for _, r in df.iterrows()]
            sel = [r for r in rows if _is_period_form(r.get("form_type"),
                                                      period)]
            if sel:
                return {"source": "db", "rows": sel[-(n_years):]}
    # Fallback / Nicht-Universum: on-Demand
    rows = []
    for f in _sec.find_filings(src.ticker, n=n_years, forms=_forms_for(period)):
        inc = _sec.fetch_income_from_filing(f)
        if inc is not None:
            rows.append(_row_from_income(inc))
    rows.sort(key=lambda d: d["period_end"])
    return {"source": "on-demand", "rows": rows}


def year_metrics(ticker: str, *, n_years: int = 10, period: str = "annual",
                 src: Source | None = None) -> dict:
    """Komplette Perioden-Metriken je Filing (GuV+Bilanz+Cashflow).

    period='annual' -> 10-K, period='quarterly' -> 10-Q. Bilanz/Cashflow sind
    NICHT persistiert -> immer on-Demand. Returns {source, rows:[dict]}.
    Hinweis: Cashflow-Posten im 10-Q sind i.d.R. Year-to-Date.
    """
    src = src or resolve(ticker)
    rows = []
    for f in _sec.find_filings(src.ticker, n=n_years, forms=_forms_for(period)):
        d = _sec.fetch_year_metrics_from_filing(f)
        if d is not None:
            rows.append(d)
    rows.sort(key=lambda d: d.get("period_end") or "")
    return {"source": "on-demand", "rows": rows}


def ppe_series(ticker: str, *, src: Source | None = None) -> dict:
    """PP&E-Zeitreihe (us-gaap Net, Fallback Gross) via company-concept."""
    cik = _cik(ticker, src)
    m = _sec.fetch_concept_series(cik, "us-gaap",
                                  "PropertyPlantAndEquipmentNet")
    if not m:
        m = _sec.fetch_concept_series(cik, "us-gaap",
                                      "PropertyPlantAndEquipmentGross")
    return m


def employee_map(ticker: str, *, src: Source | None = None) -> dict:
    """Mitarbeiter-Zeitreihe (dei company-concept) {iso: anzahl}."""
    return _sec.fetch_employee_counts_detail(_cik(ticker, src)).get("map") \
        or {}


def employee_from_text(accession_no: str):
    """Mitarbeiterzahl aus 10-K Item 1 (Textextraktion), Fallback."""
    try:
        from modules.sec_filings.extractor import fetch_employees_from_filing
        return fetch_employees_from_filing(accession_no)
    except Exception:  # noqa: BLE001
        return None


def balance(ticker: str, *, src: Source | None = None):
    """Juengste Bilanz (BalanceSheet-Dataclass) — on-Demand. None wenn keine."""
    src = src or resolve(ticker)
    f = _sec.find_latest_filing(src.ticker)
    return _sec.fetch_balance_sheet_from_filing(f) if f else None


def balance_history(ticker: str, *, n_years: int = 6, period: str = "annual",
                    src: Source | None = None) -> list:
    """Bilanz-Historie (letzte N Filings) als BalanceSheet-Liste — on-Demand.

    period='annual' -> 10-K, 'quarterly' -> 10-Q (Bilanz = Stichtagswerte,
    daher in beiden Faellen korrekt).
    """
    src = src or resolve(ticker)
    out = []
    for f in _sec.find_filings(src.ticker, n=n_years, forms=_forms_for(period)):
        bs = _sec.fetch_balance_sheet_from_filing(f)
        if bs is not None:
            out.append(bs)
    out.sort(key=lambda b: b.period_end or "")
    return out


def sbc_latest(ticker: str, *, src: Source | None = None) -> dict | None:
    """SBC + Kontext des juengsten 10-K (on-Demand). None wenn keins."""
    src = src or resolve(ticker)
    fil = _sec.find_filings(src.ticker, n=1, forms=("10-K",))
    return _sec.fetch_sbc_from_filing(fil[0]) if fil else None


def sbc_history(ticker: str, *, n_years: int = 6, period: str = "annual",
                src: Source | None = None) -> list:
    """SBC + Kontext je Filing (letzte N) — on-Demand.

    period='annual' -> 10-K, 'quarterly' -> 10-Q (SBC/CFO im 10-Q sind
    i.d.R. Year-to-Date).
    """
    src = src or resolve(ticker)
    out = []
    for f in _sec.find_filings(src.ticker, n=n_years, forms=_forms_for(period)):
        d = _sec.fetch_sbc_from_filing(f)
        if d is not None:
            out.append(d)
    out.sort(key=lambda d: d.get("period_end") or "")
    return out


def earnings_history(ticker: str, *, n_years: int = 8, period: str = "annual",
                     src: Source | None = None) -> list:
    """Gewinnruecklagen/EPS/Equity/FCF/EV-Bausteine je Filing — on-Demand.

    period='annual' -> 10-K, 'quarterly' -> 10-Q (FCF/CFO im 10-Q sind
    i.d.R. Year-to-Date; Bilanzposten sind Stichtagswerte).
    """
    src = src or resolve(ticker)
    out = []
    for f in _sec.find_filings(src.ticker, n=n_years, forms=_forms_for(period)):
        d = _sec.fetch_earnings_history_from_filing(f)
        if d is not None:
            out.append(d)
    out.sort(key=lambda d: d.get("period_end") or "")
    return out


def earnings_nongaap(ticker: str, *, src: Source | None = None) -> dict:
    """Add-back-Kategorien aus dem juengsten Earnings-8-K-Exhibit.

    Returns {categories|None, mentions, adds_back_sbc, amounts, filed_at,
    link, error}.
    """
    src = src or resolve(ticker)
    try:
        ex = _sec.find_earnings_exhibits(src.ticker, n=1)
        if not ex or not ex[0].get("exhibit_url"):
            return {"categories": None, "error": "kein Earnings-Exhibit"}
        text = _sec.fetch_exhibit_text(ex[0]["exhibit_url"])
        ana = _sec.analyze_non_gaap(text)
        return {"categories": ana["categories"], "mentions": ana["mentions"],
                "adds_back_sbc": ana["adds_back_sbc"],
                "amounts": ana.get("amounts"),
                "filed_at": ex[0].get("filed_at"),
                "link": ex[0].get("link"), "error": None}
    except Exception as e:  # noqa: BLE001
        return {"categories": None, "error": f"{e.__class__.__name__}: {e}"}


# ---- Management-Quellen (on-Demand, einheitliche Oberflaeche) ----

def _cik(ticker: str, src: Source | None = None):
    src = src or resolve(ticker)
    try:
        return _sec.get_issuer_cik(src.ticker)
    except Exception:  # noqa: BLE001
        return None


def insider_tx(ticker: str, *, src: Source | None = None) -> list:
    src = src or resolve(ticker)
    return _sec.fetch_insider_transactions(src.ticker, n=300,
                                           issuer_cik=_cik(ticker, src))


def first_filing(ticker: str, owner: str, owner_cik=None,
                 *, src: Source | None = None):
    src = src or resolve(ticker)
    try:
        return _sec.fetch_insider_first_filing(src.ticker, owner, owner_cik,
                                               issuer_cik=_cik(ticker, src))
    except Exception:  # noqa: BLE001
        return None


def mgmt_changes(ticker: str, *, src: Source | None = None) -> list:
    src = src or resolve(ticker)
    try:
        return _sec.fetch_mgmt_changes(src.ticker, n=50)
    except Exception:  # noqa: BLE001
        return []


def beneficial(ticker: str, *, src: Source | None = None) -> dict:
    src = src or resolve(ticker)
    try:
        return _sec.fetch_beneficial_ownership_detail(src.ticker)
    except Exception as e:  # noqa: BLE001
        return {"group_pct": None, "error": f"{e.__class__.__name__}: {e}"}


def institutional(ticker: str, *, src: Source | None = None) -> dict:
    src = src or resolve(ticker)
    try:
        return _sec.fetch_institutional_holdings(src.ticker, n=50)
    except Exception as e:  # noqa: BLE001
        return {"holdings": [], "error": f"{e.__class__.__name__}: {e}"}


def revenue_segments(ticker: str, *, src: Source | None = None) -> dict:
    """Umsatz-Segmente (unified): {source, rows:[{period_end, axis, member,
    member_label, value}, …]}. DB bevorzugt, sonst on-Demand (juengstes 10-K).
    """
    src = src or resolve(ticker)
    if src.in_universe and src.ref_instrument_id:
        df = _db_query(
            "SELECT period_end, axis, member, member_label, value "
            "FROM ref_revenue_segments WHERE ref_instrument_id = ? "
            "ORDER BY period_end, axis, value DESC",
            (src.ref_instrument_id,))
        if df is not None and not df.empty:
            return {"source": "db",
                    "rows": [{"period_end": str(r["period_end"])[:10],
                              "axis": r["axis"], "member": r["member"],
                              "member_label": r["member_label"],
                              "value": float(r["value"])}
                             for _, r in df.iterrows()]}
    inc = _sec.fetch_income(src.ticker)
    rows = []
    if inc is not None:
        for s in (inc.segments or []):
            rows.append({"period_end": str(inc.period_end)[:10],
                         "axis": s.get("axis"), "member": s.get("member"),
                         "member_label": s.get("member_label"),
                         "value": s.get("value")})
    return {"source": "on-demand", "rows": rows}
