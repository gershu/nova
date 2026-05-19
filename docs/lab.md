# nova-lab Data-Model

Das logische Datenmodell ist nicht als separate Design-Spec abgelegt. Source
der Wahrheit ist der Code:

- **Schema-DDL**: `modules/*/sql/*.sql` (idempotent via `CREATE OR REPLACE` /
  `IF NOT EXISTS`)
- **Live-Schema-Browser**: Streamlit-Dashboard, Page 3 — `Database`. Listet
  alle Tabellen + Views der laufenden DB, zeigt pro Eintrag Daten + DDL.

## Prefix-Konvention (Modul-Routing via Tabellennamen)

| Prefix    | Bedeutung                            | Modul                          |
|-----------|--------------------------------------|--------------------------------|
| `ref_`    | Reference / Master-Data              | ingest, fundamentals           |
| `mkt_`    | Marktdaten (Quotes, FX)              | ingest, ingest_fx              |
| `pos_`    | Portfolio-Positions                  | portfolio                      |
| `list_`   | Benannte Listen (Views, Watchlists)  | portfolio_core, watchlist      |
| `audit_`  | Run-Metadata pro Modul               | (per Modul)                    |
| `sig_`    | Signale / Briefings                  | signals, screener_*            |
| `v_`      | Views (lesen, kein Storage)          | portfolio_core                 |

## Portfolio-Sicht (Core)

Die zentrale View-Schicht fuer das Portfolio-MtM-Reporting ist in
`modules/portfolio_core/sql/`:

- `0001_tables.sql` — `list_portfolio_views` + `list_portfolio_view_members`
- `0002_atomic.sql` — `v_latest_quote`, `v_latest_fx` (Helper)
- `0003_views.sql` — Core-Views:
  - `v_pos_holdings`   = pos_holdings + ref_instruments
  - `v_mkt_holdings`   = v_pos_holdings + v_latest_quote (Native CCY)
  - `v_list_portfolio` = list_portfolio_views + members + v_pos_holdings
  - `v_mkt_portfolio`  = v_list_portfolio + v_latest_quote

Currency-Conversion bleibt der Anwendung ueberlassen (Dashboard / Notebooks).
Datenbank-Schicht liefert Native-Currency-Werte.

## Schema-Pflege

CRUD auf den `list_*`-Tabellen via `modules.db_edit`. View-Init/Drop via:

```bash
cd ~/nova-lab && source ~/nova/.venv/bin/activate
python -m modules.portfolio_core init           # Views erzeugen (idempotent)
python -m modules.portfolio_core drop-legacy    # dry-run; --yes zum apply
```
