"""nova-lab sec_filings — GuV-Kerndaten aus SEC-EDGAR-Filings.

Zieht die Income-Statement-Kernzeilen (Umsatz, Herstellkosten, Bruttogewinn,
F&E, Vertrieb/Verwaltung, operatives Ergebnis, Steuern, Nettogewinn) aus dem
juengsten 10-Q/10-K eines Namens und legt sie in ref_income_statement ab.

Quelle: sec-api.io (XBRL-to-JSON). API-Key via NOVA_SEC_API_KEY.

Segment-Umsaetze (Data Center, Gaming, ...) sind bewusst NICHT Teil dieser
ersten Ausbaustufe — sie stecken in den XBRL-Dimensionen desselben Filings
und kommen als zweiter Schritt dazu.
"""
