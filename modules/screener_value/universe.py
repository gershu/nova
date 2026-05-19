"""Static Universe-Loader fuer screener_value.

Lese-only. Liefert die Liste der S&P-500-Member-Symbole + Metadaten
aus config/universe_sp500.yaml. Pflege ist manuell — siehe Header dort.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Optional

import yaml


@dataclass(frozen=True)
class UniverseMember:
    symbol:   str
    name:     str
    sector:   Optional[str] = None
    currency: str = "USD"   # S&P 500 ist all-USD by definition


# Repo-Root via Modul-Pfad ableiten.
_DEFAULT_YAML = (
    pathlib.Path(__file__).parent.parent.parent
    / "config"
    / "universe_sp500.yaml"
)


def load_universe(path: pathlib.Path | str | None = None) -> list[UniverseMember]:
    """Lese Universe-YAML, gibt geordnete Liste von UniverseMember zurueck.

    Reihenfolge bleibt wie im YAML (= Sektor-gruppiert, lesbar fuer Stefan).
    """
    p = pathlib.Path(path) if path else _DEFAULT_YAML
    if not p.is_file():
        raise FileNotFoundError(f"Universe-YAML fehlt: {p}")
    data = yaml.safe_load(p.read_text())
    if not isinstance(data, dict) or "symbols" not in data:
        raise ValueError(f"Ungueltiges YAML-Format in {p} — erwarte top-level 'symbols'-Liste.")
    out: list[UniverseMember] = []
    for entry in data["symbols"]:
        if not isinstance(entry, dict) or "symbol" not in entry:
            continue
        out.append(UniverseMember(
            symbol   = str(entry["symbol"]).strip(),
            name     = str(entry.get("name") or entry["symbol"]).strip(),
            sector   = (str(entry["sector"]).strip() if entry.get("sector") else None),
            currency = "USD",
        ))
    return out


def ref_instrument_id_for(member: UniverseMember) -> str:
    """Deterministisches PK-Format. S&P 500 -> 'IB:SYMBOL:USD'.

    BRK.B -> 'IB:BRK.B:USD' (Punkt im Symbol bleibt erhalten; yfinance-
    Mapping zu 'BRK-B' macht der yf_adapter spaeter via _candidate_tickers).
    """
    return f"IB:{member.symbol}:{member.currency}"
