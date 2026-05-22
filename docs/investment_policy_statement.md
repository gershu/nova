# Investment Policy Statement (IPS) — ENTWURF

> **Status:** Entwurf v0.2 · 2026-05-22 · noch nicht in Kraft
> Dieses Dokument hält die Anlage-Policy fest und ist der Anker für
> `config/allocation.yaml`. Geänderte Zielwerte hier zuerst beschließen,
> dann in die YAML übertragen — nicht umgekehrt.

## 1. Zweck

Das IPS übersetzt die qualitativen Anlage-Präferenzen in konkrete,
überprüfbare Zielbänder und Leitplanken. Es dient drei Zwecken: als
Entscheidungs-Maßstab bei Käufen/Verkäufen, als Grundlage für das
automatisierte Allokations-Monitoring in nova, und als Schutz gegen
emotionale Ad-hoc-Entscheidungen in Stressphasen.

Das IPS ersetzt keine Steuer- oder Anlageberatung. Alle Allokations-
Entscheidungen trifft der Anleger selbst; nova liefert Analyse, keine Order.

## 2. Portfolio-Kontext

| Merkmal | Wert |
|---|---|
| Portfolio-Wert | ca. 1.958.000 EUR |
| Basiswährung | EUR |
| Verwahrung | 4 Broker (Diversifikation der Verwahr-Gegenpartei) |
| Latente Steuerlast | nennenswerte unrealisierte Kursgewinne |
| Steuerregime | _[zu ergänzen — bestimmt die Rebalancing-Friktion]_ |

Eine laufende Kapitalentnahme wird derzeit **nicht** modelliert. Erträge
(Dividenden, Kupons, Optionsprämien) werden reinvestiert. Sollte später
eine Entnahme geplant werden, ist das IPS entsprechend zu erweitern.

## 3. Anlageziele

**Primärziel — Kapitalerhalt.** Der reale Wert des Portfolios soll über
einen vollen Marktzyklus erhalten bleiben. Drawdowns werden akzeptiert,
permanente Kapitalverluste durch Zwangsverkäufe oder Klumpenrisiken
nicht.

**Sekundärziel — Langfristiges Wachstum.** Der Technologie-Schwerpunkt
trägt das Wachstum. Er wird bewusst beibehalten, aber durch
stabilisierende Bausteine eingerahmt.

**Ertragskomponente.** Dividenden und vereinnahmte Optionsprämien (CSP)
sind eine eigenständige Ertrags- und Stabilitätsquelle. Sie werden
aktuell reinvestiert; ihre Höhe wird beobachtet, aber gegen kein
Entnahme-Ziel gemessen.

## 4. Risikoprofil

Der Anleger toleriert überdurchschnittliche Schwankungen zugunsten des
Wachstumspotenzials eines konzentrierten Technologie-Portfolios. Die
Risiko­*kapazität* ist hoch (langer Horizont, keine kurzfristige
Entnahme-Notwendigkeit), die Risiko­*toleranz* wird durch den
Kapitalerhalt-Fokus begrenzt.

Daraus folgt die Grundausrichtung „Schrittweise Stabilisierung": die
Tech-Konzentration bleibt das Herz des Portfolios, wird aber über die
Zeit durch Quality-Aktien, Bonds und einen Liquiditäts-Sleeve abgepuffert.
Der Umbau erfolgt graduell und steuerschonend (siehe Abschnitt 7).

## 5. Ziel-Allokation

Anlagehorizont: langfristig (Buy & Hold). Zielbänder, nicht Punktwerte —
solange sich eine Klasse innerhalb ihres Bandes bewegt, besteht kein
Handlungsbedarf.

| Anlageklasse | Zielwert | Band | Rolle |
|---|---|---|---|
| Technologie-Aktien | 32 % | 28–42 % | Wachstumsmotor |
| Quality-Aktien | 19 % | 14–26 % | Compounder — Stabilität durch Geschäftsqualität |
| Bonds | 18 % | 12–25 % | Stabilität, Ertrag, Drawdown-Puffer |
| Index-ETF | 12 % | 8–16 % | breite Diversifikation |
| Dividenden-Aktien | 10 % | 7–15 % | laufender Ertrag |
| Cash / CSP-Collateral | 7 % | 5–15 % | Liquidität, T-Bond-Parkplatz, Prämienbasis |
| Sonstige / Satelliten | 2 % | 0–5 % | taktische Einzelwetten außerhalb des Kerns |

Die Zielwerte summieren sich auf 100 %; die Bänder überlappen bewusst.

Klassen-Abgrenzungen:

- **Technologie** — Tech-Sektor-Einzelwerte.
- **Quality** — Kapital-Compounder mit stabilen/wachsenden Cashflows,
  geringer Verschuldung und Preissetzungsmacht; Wertzuwachs primär über
  den Kurs, geringe oder keine Dividende.
- **Dividenden-Aktien** — etablierte Ertrags-Zahler, gehalten für die
  laufende Ausschüttung. Trennlinie zu Quality: „Ausschüttung" statt
  „Compounding", keine Qualitätsabstufung.
- **Bonds** — Anleihen, gehalten als Stabilitäts- und Ertragsbaustein.
  Abzugrenzen von T-Bonds im Cash/Collateral-Sleeve (Abschnitt 8).
- **Index-ETF** — breit gestreute Index-Fonds.
- **Sonstige / Satelliten** — kleine taktische Einzelpositionen außerhalb
  des Kern-Rasters; gedeckelt bei 5 %.

## 6. Konzentrations-Leitplanken

Ein konzentriertes Portfolio ist gewollt — unkontrollierte Klumpen sind
es nicht. Es gelten:

Keine Einzelposition über **15 %** des Portfolio-Werts. Wächst eine
Position über diese Grenze, wird ein `review` ausgelöst (nicht
zwangsweise ein Verkauf — die Steuerlage entscheidet mit).

Die Top-5-Positionen zusammen nicht über **55 %**.

Pro Broker nicht mehr als das, was im Verlust-Fall einer Verwahr-
Gegenpartei verschmerzbar wäre — Richtwert zur Bestätigung im Review.

## 7. Rebalancing-Policy

Rebalancing ist **steuerbewusst**. In einem Buy-&-Hold-Portfolio mit
hohen unrealisierten Gewinnen ist jeder Verkauf ein steuerlich
relevantes Ereignis; ein nominell „optimaler" Rebalancing-Trade kann nach
Steuer wertvernichtend sein.

Reihenfolge der Mittel zur Drift-Korrektur:

1. **Neue Zuflüsse** und vereinnahmte Optionsprämien fließen bevorzugt in
   die untergewichtete Klasse.
2. **Dividenden/Kupons** werden gezielt statt automatisch reinvestiert.
3. **Verkäufe** nur, wenn 1.–2. nicht ausreichen — und dann mit
   ausgewiesener Steuer-Konsequenz; bevorzugt Tax-Lots mit geringem
   Gewinn oder Verlust.

Ausgelöst wird eine Prüfung, wenn eine Klasse ihr Band verlässt oder vom
Zielwert um mehr als die Drift-Toleranz (Entwurf: 5 Prozentpunkte)
abweicht. Eine Prüfung ist kein Trade-Zwang — sie ist ein `review`.

Der Aufbau des Cash/CSP-Collateral-Sleeve (aktuell 0 %, Ziel 7 %) erfolgt
bewusst über Positionsverkäufe. Die Lot-Auswahl folgt derselben
Steuer-Logik: zuerst gewinn­arme oder verlust­behaftete Lots.

## 8. Cash / CSP-Collateral-Sleeve

Der Cash/Collateral-Sleeve ist kein totes Kapital. Er wird durch
Positionsverkäufe aufgebaut und in **T-Bonds** (kurzlaufende
Staatsanleihen) geparkt. Die T-Bonds dienen zugleich als Sicherheit für
**Cash Secured Short Puts** — der Sleeve erwirtschaftet so die
Anleiherendite *und* die Optionsprämie, bleibt aber jederzeit liquide.

Diese T-Bonds zählen zum Cash/Collateral-Sleeve, **nicht** zur Bonds-Klasse
(Abschnitt 5) — die Bonds-Klasse meint Anleihen als eigenständigen
Allokations-Baustein, der Sleeve meint sicheres, prämientragendes
Park-Kapital.

CSP-Leitplanken:

Das insgesamt gebundene Collateral bleibt innerhalb des Cash/Collateral-
Bandes (5–15 % des Portfolios).

Puts werden nur auf Basiswerte geschrieben, die im Fall der Zuteilung
auch als Direktinvestment gewollt sind und ins Allokations-Ziel passen.

Das Assignment-Exposure (Summe aller Strikes bei vollständiger Zuteilung)
wird überwacht und gegen die T-Bond-Basis gehalten.

## 9. Handelsfrequenz

Die Strategie ist Buy & Hold mit moderater Handelsfrequenz. Aktivität
entsteht durch Rebalancing, CSP-Rollen und Zuflüsse — nicht durch
Markt-Timing. Die Turnover-Ratio wird als Kennzahl mitgeführt, um
schleichende Über-Aktivität sichtbar zu machen.

## 10. Monitoring-KPIs

nova überwacht die Policy laufend. Zentrale Kennzahlen:

| Bereich | KPI |
|---|---|
| Allokation | Klassen-Gewichte, Drift Ist−Ziel, Tech-Sektor-Anteil |
| Konzentration | größtes Einzelgewicht, Top-5-Anteil, Broker-Verteilung |
| Ertrag | Dividenden + Optionsprämien p.a. (beobachtet, ohne Zielwert) |
| Risiko | Volatilität, Max-Drawdown, Anteil Stabilisatoren |
| Steuer | unrealisierte Gewinne gesamt/je Lot, rebalancing-ausgelöste Steuer |
| CSP | gebundenes vs. freies Collateral, Prämienrendite, Assignment-Exposure |
| Aktivität | Turnover-Ratio |

## 11. Review-Zyklus

Planmäßiges Review der Ziel-Allokation und Bänder einmal jährlich.
Anlassbezogenes Review bei wesentlichen Änderungen der Lebenssituation
oder des Steuerregimes. Das laufende KPI-Monitoring ist kein Ersatz für
das Review — es überwacht die Einhaltung der Policy, nicht ihre
Angemessenheit.

## 12. Offene Punkte

Vor Inkrafttreten zu klären:

- Steuerregime und -sätze — bestimmen die Rebalancing-Friktion (Abschnitt 2/7).
- Broker-Konzentrations-Richtwert (Abschnitt 6).
- Objektive Kriterien für die Quality-Klassifikation (z. B. ROIC,
  Verschuldungsgrad, Free-Cashflow-Stabilität).
- Instrument→Klassen-Zuordnung als gepflegte Tabelle (T-Bonds müssen vom
  Allokations-Monitoring dem Cash/Collateral-Sleeve, nicht der Bonds-
  Klasse zugeordnet werden).
