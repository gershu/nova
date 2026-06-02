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
                   src: Source | None = None) -> dict:
    """GuV-Historie (unified). Returns {source, rows: [unified-dict, …]}.

    DB-Pfad liefert die persistierte Historie (10-Q + 10-K), on-Demand-Pfad
    die letzten N 10-K (annual).
    """
    src = src or resolve(ticker)
    if src.income_source == "db" and src.ref_instrument_id:
        df = _db_query(
            "SELECT period_end, form_type, currency, accession_no, filed_at, "
            + ", ".join(_INCOME_COLS) +
            " FROM ref_income_statement WHERE ref_instrument_id = ? "
            "ORDER BY period_end", (src.ref_instrument_id,))
        if df is not None and not df.empty:
            return {"source": "db",
                    "rows": [_row_from_db(r) for _, r in df.iterrows()]}
    # Fallback: on-Demand (annual)
    rows = []
    for f in _sec.find_filings(src.ticker, n=n_years, forms=("10-K",)):
        inc = _sec.fetch_income_from_filing(f)
        if inc is not None:
            rows.append(_row_from_income(inc))
    rows.sort(key=lambda d: d["period_end"])
    return {"source": "on-demand", "rows": rows}


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
