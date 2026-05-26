"""nova-lab screener — Quality-GARP-Aktien-Screening mit lokalem LLM.

Trichter-Pipeline auf dem kuratierten Quality-Universum
(config/screener_quality_universe.yaml, 100 Namen):

  Stufe 1: Regel-Filter ueber Kennzahlen (Qualitaet/Wachstum/Bewertung).
  Stufe 2: Trendanalyse aus ref_income_statement-Historie (computed,
           kein LLM).
  Stufe 3: On-demand LLM-Bewertung mit 10-K-Auszuegen + News.

Daten kommen aus:
  - ref_fundamentals_latest    (Margen, Kapitalrenditen, Multiples)
  - ref_income_statement       (Historie pro Periode)
  - ref_revenue_segments       (optional, fuer Segment-Mix)
  - ref_sa_articles            (News)
  - sec-api Extractor          (10-K Item 1 + 1A + 7, on-demand fuer Stufe 3)

Distinkt von screener_value:
  screener_value          = reiner Value-Screener auf full sp500_universe.
  screener (dieses Modul) = Quality-GARP-Screener auf kuratiertem
                            100-Namen-quality_universe; Wachstums-KPIs
                            haben gleiches Gewicht wie Value.
"""

UNIVERSE_WATCHLIST = "quality_universe"
