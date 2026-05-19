"""Portfolio Excel importer (ConID-based, B-Phase Schema).

Schreibt in:
  - ref_instruments       (ein Eintrag pro unique ConID, ref_instrument_id als
                           VARCHAR-PK aus '{SOURCE}:{SYMBOL}:{CURRENCY}')
  - pos_holdings          (lot-level positions, joined via ref_instrument_id)
  - audit_portfolio_imports (Import-Audit-Trail mit Filehash)

Usage:
    python -m modules.portfolio.import_xlsx <path-to-xlsx> [--dry-run]

Excel-Format: con_id + quantity + broker required. Symbol/asset_class/currency/
isin optional (User-Override; sonst aus IB resolved). Siehe template.py.

Resolve-Flow:
    1. Excel parsen + validieren.
    2. IB connect (IBResolver, Client-ID 12).
    3. Pro unique con_id: ContractDetails + ResolvedContract.
    4. Cross-Checks: ISIN, symbol, asset_class — User-Wert behaelt Vorrang,
       Mismatch wird geloggt.
    5. ref_instrument_id berechnen aus (preferred_source, symbol, currency).
    6. Voll-Sync:
       - ref_instruments: upsert pro ID (insert if neu, update if exists).
       - pos_holdings: DELETE + INSERT komplett.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import sys
import uuid
from datetime import date, datetime, timezone

import duckdb
import pandas as pd

from ._ib_resolver import IBResolver, ResolvedContract


# ---------- Configuration ----------
DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SCHEMA_FILE = pathlib.Path(__file__).parent / "sql" / "0001_holdings.sql"

# Im aktuellen Setup ist preferred_source fuer Portfolio-Imports immer 'IB'.
# Spaeter kann das pro Run via ENV oder params-file konfigurierbar werden.
PREFERRED_SOURCE = "IB"


# Column aliases: lower-case Excel header -> canonical name.
COLUMN_ALIASES = {
    # lot_id
    "lot_id": "lot_id", "lot-id": "lot_id", "lotid": "lot_id",
    # con_id (primary identifier)
    "con_id": "con_id", "conid": "con_id", "contract_id": "con_id",
    # symbol
    "symbol": "symbol", "ticker": "symbol",
    "yahoo-symbol": "symbol", "yahoo_symbol": "symbol", "yfinance_symbol": "symbol",
    # exchange (legacy / display)
    "exchange": "exchange", "boerse": "exchange", "börse": "exchange",
    # asset_class — IB-style sec-type code
    "asset_class": "asset_class", "assetclass": "asset_class",
    "asset_type": "asset_class", "asset type": "asset_class", "type": "asset_class",
    "sec_type": "asset_class", "sectype": "asset_class",
    # isin
    "isin": "isin",
    # quantity
    "quantity": "quantity", "qty": "quantity", "anzahl": "quantity", "stk": "quantity",
    # cost_per_share
    "cost_per_share": "cost_per_share", "cost": "cost_per_share",
    "anschaffungspreis": "cost_per_share", "preis": "cost_per_share",
    # currency
    "currency": "currency", "ccy": "currency",
    "waehrung": "currency", "währung": "currency",
    # acquired_at
    "acquired_at": "acquired_at", "acquisition_date": "acquired_at",
    "purchase_date": "acquired_at",
    "anschaffungstag": "acquired_at", "anschaffungszeitraum": "acquired_at",
    # broker
    "broker": "broker",
    # account
    "account": "account", "account_id": "account",
    # name
    "name": "name",
    # notes
    "notes": "notes", "note": "notes", "notiz": "notes",
}

REQUIRED_FIELDS = ["con_id", "quantity", "broker"]
ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

VALID_ASSET_CLASSES = {
    "STK", "ETF", "BOND", "OPT", "FUT", "FOP", "IND", "CASH", "CRYPTO",
    "FUND", "WAR", "BAG", "CMDTY", "CFD",
}

ASSET_TYPE_TO_CLASS = {
    "stock":         "STK",
    "etf":           "ETF",
    "bond":          "BOND",
    "option":        "OPT",
    "future":        "FUT",
    "future_option": "FOP",
    "index":         "IND",
    "fx":            "CASH",
    "crypto":        "CRYPTO",
    "fund":          "FUND",
    "warrant":       "WAR",
    "combo":         "BAG",
    "commodity":     "CMDTY",
    "cfd":           "CFD",
}


# ---------- ref_instrument_id ----------

def make_ref_instrument_id(symbol: str, currency: str, source: str) -> str:
    """Deterministic VARCHAR-PK: '{SOURCE}:{SYMBOL}:{CURRENCY}'.

    Reproduzierbar ueber Re-Imports (im Gegensatz zu Sequence-PKs).
    Spaces im symbol → Underscores. Alles uppercase.
    """
    sym = (symbol or "").strip().upper().replace(" ", "_")
    cur = (currency or "").strip().upper()
    src = (source or "").strip().upper()
    if not (sym and cur and src):
        raise ValueError(
            f"ref_instrument_id needs all three non-empty: "
            f"source={src!r} symbol={sym!r} currency={cur!r}"
        )
    return f"{src}:{sym}:{cur}"


# ---------- Helpers ----------

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename: dict[str, str] = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in COLUMN_ALIASES:
            rename[col] = COLUMN_ALIASES[key]
    df = df.rename(columns=rename)
    keep = [c for c in df.columns if c in set(COLUMN_ALIASES.values())]
    return df[keep]


def file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_date_lenient(value) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, pd.Timestamp):
        return value.date()
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def coerce_str(v, *, upper: bool = False) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if not s:
        return None
    return s.upper() if upper else s


def coerce_float(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def coerce_int(v) -> int | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def validate_row(row: dict, ix: int) -> tuple[bool, str | None]:
    for f in REQUIRED_FIELDS:
        v = row.get(f)
        if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
            return False, f"row {ix}: '{f}' missing"

    con_id = coerce_int(row.get("con_id"))
    if con_id is None or con_id <= 0:
        return False, f"row {ix}: con_id '{row.get('con_id')}' is not a positive integer"

    isin = coerce_str(row.get("isin"), upper=True)
    if isin is not None and not ISIN_PATTERN.match(isin):
        return False, f"row {ix}: ISIN '{isin}' has invalid format"

    asset_class = coerce_str(row.get("asset_class"), upper=True)
    if asset_class is not None and asset_class not in VALID_ASSET_CLASSES:
        return False, (
            f"row {ix}: asset_class '{asset_class}' not in valid set "
            f"({sorted(VALID_ASSET_CLASSES)})"
        )

    qty = coerce_float(row.get("quantity"))
    if qty is None or qty <= 0:
        return False, f"row {ix}: quantity must be > 0 (got {row.get('quantity')!r})"

    return True, None


def detect_legacy_format(df: pd.DataFrame) -> bool:
    return "symbol" in df.columns and "con_id" not in df.columns


# ---------- Schema ----------

def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Laedt zuerst ingest-Schema (ref_instruments + audit_ingest_runs etc.),
    dann portfolio-eigenes (pos_holdings + audit_portfolio_imports)."""
    ingest_schema_dir = pathlib.Path(__file__).parent.parent / "ingest" / "sql"
    if ingest_schema_dir.is_dir():
        for sql_file in sorted(ingest_schema_dir.glob("0*.sql")):
            con.execute(sql_file.read_text())
    con.execute(SCHEMA_FILE.read_text())


# ---------- DB Writers ----------

def upsert_ref_instrument(
    con: duckdb.DuckDBPyConnection,
    *,
    ref_instrument_id: str,
    con_id: int | None,
    isin: str | None,
    symbol: str,
    currency: str,
    preferred_source: str,
    name: str | None,
    asset_type: str,
    exchange: str | None,
) -> bool:
    """Upsert in ref_instruments. Returnt True wenn neu eingefuegt."""
    existing = con.execute(
        "SELECT 1 FROM ref_instruments WHERE ref_instrument_id = ?",
        [ref_instrument_id],
    ).fetchone()
    if existing:
        # Touch + fuelle Felder die noch leer sind (defensive merge).
        con.execute(
            """
            UPDATE ref_instruments
            SET con_id     = COALESCE(con_id, ?),
                isin       = COALESCE(isin, ?),
                exchange   = COALESCE(exchange, ?),
                asset_type = COALESCE(asset_type, ?),
                name       = COALESCE(name, ?),
                updated_at = current_timestamp
            WHERE ref_instrument_id = ?
            """,
            [con_id, isin, exchange, asset_type, name, ref_instrument_id],
        )
        return False

    con.execute(
        """
        INSERT INTO ref_instruments
            (ref_instrument_id, con_id, isin, symbol, currency, preferred_source,
             name, asset_type, exchange, active, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, true, 'auto-added by portfolio import')
        """,
        [
            ref_instrument_id, con_id, isin, symbol, currency, preferred_source,
            name or symbol, asset_type or "stock", exchange or "",
        ],
    )
    return True


def replace_pos_holdings(
    con: duckdb.DuckDBPyConnection,
    rows: list[dict],
    run_id: str,
) -> int:
    con.execute("DELETE FROM pos_holdings")
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    df["import_run_id"] = run_id
    con.register("incoming", df)
    try:
        con.execute(
            """
            INSERT INTO pos_holdings
                (holding_id, lot_id, ref_instrument_id, quantity, cost_per_share,
                 currency, acquired_at, broker, account, notes, import_run_id)
            SELECT
                holding_id, lot_id, ref_instrument_id, quantity, cost_per_share,
                currency, acquired_at, broker, account, notes, import_run_id
            FROM incoming
            """
        )
    finally:
        con.unregister("incoming")
    return len(df)


def log_import(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    file_path: pathlib.Path,
    file_hash: str,
    rows_read: int,
    rows_imported: int,
    rows_skipped: int,
    new_instruments: int,
    isin_mismatches: int,
    status: str,
    error_msg: str | None,
) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO audit_portfolio_imports
            (run_id, imported_at, file_path, file_hash,
             rows_read, rows_imported, rows_skipped, new_instruments, isin_mismatches,
             status, error_msg)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [run_id, datetime.now(timezone.utc), str(file_path), file_hash,
         rows_read, rows_imported, rows_skipped, new_instruments, isin_mismatches,
         status, error_msg],
    )


# ---------- Main ----------

def main() -> int:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    if len(args) != 1:
        print("Usage: python -m modules.portfolio.import_xlsx <path-to-xlsx> [--dry-run]", file=sys.stderr)
        return 64

    xlsx_path = pathlib.Path(args[0]).expanduser().resolve()
    if not xlsx_path.is_file():
        print(f"ERROR: {xlsx_path} does not exist.", file=sys.stderr)
        return 64

    run_id = os.environ.get(
        "NOVA_JOB_ID",
        f"adhoc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    )

    print("==> nova-lab portfolio import (B-Phase schema)")
    print(f"    file             : {xlsx_path}")
    print(f"    db               : {DB_PATH}")
    print(f"    run_id           : {run_id}")
    print(f"    preferred_source : {PREFERRED_SOURCE}")
    print(f"    dry-run          : {dry_run}")

    try:
        xl = pd.ExcelFile(xlsx_path)
    except ImportError:
        print("ERROR: openpyxl not installed.", file=sys.stderr)
        return 65

    sheet_name = "Holdings" if "Holdings" in xl.sheet_names else xl.sheet_names[0]
    df_raw = xl.parse(sheet_name)
    print(f"    sheet            : {sheet_name} ({len(df_raw)} rows)")

    df = normalize_columns(df_raw)

    if detect_legacy_format(df):
        print()
        print("ERROR: Legacy-Format erkannt (symbol-Spalte ohne con_id).", file=sys.stderr)
        print(f"       Bitte zuerst migrieren:", file=sys.stderr)
        print(f"         python -m modules.portfolio.resolve_conids {xlsx_path} <new.xlsx>", file=sys.stderr)
        return 64

    # Validate
    rows_read = len(df)
    raw_rows: list[dict] = []
    skipped: list[str] = []

    for ix, raw in df.iterrows():
        row = raw.to_dict()
        ok, err = validate_row(row, int(ix) + 2)
        if not ok:
            skipped.append(err or f"row {ix}: unknown error")
            continue
        raw_rows.append({
            "_excel_row":     int(ix) + 2,
            "lot_id":         coerce_str(row.get("lot_id")),
            "con_id":         coerce_int(row.get("con_id")),
            "isin":           coerce_str(row.get("isin"), upper=True),
            "user_symbol":    coerce_str(row.get("symbol")),
            "user_class":     coerce_str(row.get("asset_class"), upper=True),
            "quantity":       coerce_float(row.get("quantity")) or 0.0,
            "cost_per_share": coerce_float(row.get("cost_per_share")),
            "currency":       coerce_str(row.get("currency"), upper=True),
            "acquired_at":    parse_date_lenient(row.get("acquired_at")),
            "broker":         coerce_str(row.get("broker")),
            "account":        coerce_str(row.get("account")),
            "name":           coerce_str(row.get("name")),
            "notes":          coerce_str(row.get("notes")),
        })

    print(f"    valid            : {len(raw_rows)}, skipped: {len(skipped)}")
    for s in skipped:
        print(f"      ! {s}", file=sys.stderr)

    if not raw_rows:
        print("ERROR: no valid rows — import aborted.", file=sys.stderr)
        return 1

    # ----- IB Resolution -----
    hints_by_conid: dict[int, tuple[str | None, str | None]] = {}
    for r in raw_rows:
        hints_by_conid.setdefault(r["con_id"], (r["user_class"], r["currency"]))

    unique_conids = sorted(hints_by_conid.keys())
    print(f"    unique con_ids   : {len(unique_conids)}")

    print()
    print("==> resolving via IB ...")
    resolved_by_conid: dict[int, ResolvedContract] = {}
    resolution_failures: list[tuple[int, str]] = []

    try:
        with IBResolver() as ib:
            print(f"    connected client_id={ib.client_id} host={ib.host} port={ib.port}")
            for cid in unique_conids:
                user_class, user_ccy = hints_by_conid[cid]
                try:
                    res = ib.resolve_by_conid(cid, sec_type=user_class, currency=user_ccy)
                except Exception as e:  # noqa: BLE001
                    resolution_failures.append((cid, f"{e.__class__.__name__}: {e}"))
                    print(f"    [FAIL] conId={cid}: {e.__class__.__name__}: {e}")
                    continue
                if res is None:
                    resolution_failures.append((cid, "no contract details returned"))
                    print(f"    [FAIL] conId={cid}: no contract details (hint sec_type={user_class!r})")
                    continue
                resolved_by_conid[cid] = res
                print(f"    [OK]   conId={cid} -> {res.symbol:<10} {res.exchange:<8} {res.currency} {res.asset_type}  ({res.name or ''})")
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: IB connection failed: {e.__class__.__name__}: {e}", file=sys.stderr)
        print("       Pruefe IB Gateway laeuft + ENV-Vars (IB_GATEWAY_HOST/PORT/IB_PORTFOLIO_CLIENT_ID).", file=sys.stderr)
        return 1

    if resolution_failures and not resolved_by_conid:
        print(f"ERROR: keine ConID konnte resolved werden — Import abgebrochen.", file=sys.stderr)
        return 1

    # ----- Build holdings rows + ref_instrument records -----
    holdings_rows: list[dict] = []
    rows_dropped: list[str] = []
    isin_mismatches = 0

    # ref_instrument_id → vollstaendige Metadaten fuer Upsert (deduped)
    ref_instruments_by_id: dict[str, dict] = {}

    for r in raw_rows:
        cid = r["con_id"]
        resolved = resolved_by_conid.get(cid)
        if resolved is None:
            rows_dropped.append(f"row {r['_excel_row']}: con_id {cid} not resolvable")
            continue

        # Cross-checks (User-Wert behaelt Vorrang, Mismatch loggen)
        user_isin = r["isin"]
        if user_isin and resolved.isin and user_isin.upper() != resolved.isin.upper():
            print(f"    [WARN] row {r['_excel_row']} con_id={cid}: ISIN mismatch — Excel={user_isin} IB={resolved.isin}")
            isin_mismatches += 1
        final_isin = user_isin or (resolved.isin or None)

        user_sym = r["user_symbol"]
        if user_sym and resolved.symbol and user_sym != resolved.symbol:
            print(f"    [WARN] row {r['_excel_row']} con_id={cid}: symbol mismatch — Excel={user_sym} IB={resolved.symbol}")
        final_symbol = user_sym or resolved.symbol

        user_class = r["user_class"]
        ib_class = ASSET_TYPE_TO_CLASS.get(
            resolved.asset_type or "stock",
            (resolved.asset_type or "stock").upper(),
        )
        if user_class and user_class != ib_class:
            print(f"    [WARN] row {r['_excel_row']} con_id={cid}: asset_class mismatch — Excel={user_class} IB={ib_class}")
        final_class = user_class or ib_class
        from ._ib_resolver import SECTYPE_MAP
        final_asset_type = SECTYPE_MAP.get(final_class, final_class.lower())

        final_currency = r["currency"] or resolved.currency
        final_name = r["name"] or resolved.name
        final_exchange = resolved.exchange or None

        # ref_instrument_id deterministisch berechnen
        if not (final_symbol and final_currency):
            rows_dropped.append(
                f"row {r['_excel_row']}: cannot build ref_instrument_id "
                f"(symbol={final_symbol!r} currency={final_currency!r})"
            )
            continue

        try:
            rid = make_ref_instrument_id(final_symbol, final_currency, PREFERRED_SOURCE)
        except ValueError as e:
            rows_dropped.append(f"row {r['_excel_row']}: {e}")
            continue

        # Refdata sammeln (per id deduped)
        if rid not in ref_instruments_by_id:
            ref_instruments_by_id[rid] = {
                "ref_instrument_id": rid,
                "con_id":            cid,
                "isin":              final_isin,
                "symbol":            final_symbol,
                "currency":          final_currency,
                "preferred_source":  PREFERRED_SOURCE,
                "name":              final_name,
                "asset_type":        final_asset_type,
                "exchange":          final_exchange,
            }

        # Holdings row
        holdings_rows.append({
            "holding_id":        str(uuid.uuid4()),
            "lot_id":            r["lot_id"],
            "ref_instrument_id": rid,
            "quantity":          r["quantity"],
            "cost_per_share":    r["cost_per_share"],
            "currency":          final_currency,
            "acquired_at":       r["acquired_at"],
            "broker":            r["broker"],
            "account":           r["account"],
            "notes":             r["notes"],
        })

    if rows_dropped:
        print()
        print(f"    HINWEIS: {len(rows_dropped)} Zeile(n) gedroppt:")
        for msg in rows_dropped[:10]:
            print(f"      ! {msg}")

    if dry_run:
        print()
        print(f"==> DRY-RUN — would write:")
        print(f"    pos_holdings    : {len(holdings_rows)}")
        print(f"    ref_instruments : {len(ref_instruments_by_id)}")
        print(f"    isin mismatches : {isin_mismatches}")
        print()
        print("    Erste 5 ref_instrument_ids:")
        for rid in list(ref_instruments_by_id.keys())[:5]:
            print(f"      {rid}")
        return 0

    # ----- Write to DB -----
    file_hash = file_sha256(xlsx_path)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_schema(con)

        last = con.execute(
            "SELECT run_id, imported_at FROM audit_portfolio_imports WHERE file_hash = ? ORDER BY imported_at DESC LIMIT 1",
            [file_hash],
        ).fetchone()
        if last:
            print(f"    [INFO] identical file hash already imported (run_id={last[0]}, at={last[1]}).")
            print(f"           Re-import will still run (full sync).")

        # ref_instruments upserts
        new_instruments = 0
        for rid, meta in ref_instruments_by_id.items():
            if upsert_ref_instrument(con, **meta):
                new_instruments += 1

        # pos_holdings full-sync
        n_inserted = replace_pos_holdings(con, holdings_rows, run_id)

        log_import(
            con, run_id, xlsx_path, file_hash,
            rows_read, n_inserted, len(skipped) + len(rows_dropped),
            new_instruments, isin_mismatches,
            "success" if not (skipped or rows_dropped) else "partial",
            None if not (skipped or rows_dropped) else "; ".join((skipped + rows_dropped)[:5]),
        )
    finally:
        con.close()

    print()
    print(f"==> done")
    print(f"    pos_holdings imported : {n_inserted}")
    print(f"    new ref_instruments   : {new_instruments}  (preferred_source={PREFERRED_SOURCE})")
    print(f"    isin mismatches       : {isin_mismatches}")
    print(f"    file_hash             : {file_hash[:16]}...")
    if new_instruments:
        print()
        print("    HINWEIS: neue Eintraege in 'ref_instruments'.")
        print("    Backfill der Quotes via IB (sobald ingest-Modul auf neues Schema refactored ist):")
        print(f"      ~/nova/scripts/nova_run.sh lab_ingest nova-hub --params-file ~/jobs/lab_ingest_ib_full.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
