# Umsetzungsplan zur Aufrufkettenanalyse — Statusübersicht

**Ursprüngliche Planung:** 20. August 2026
**Bezugsdokument:** [ANALYSE_AUFRUFKETTE.md](ANALYSE_AUFRUFKETTE.md)

Dieser Plan ist abgeschlossen. Die frühere Fassung enthielt für jeden Schritt
Patchskizzen, Testausgaben und wiederholte Begründungen. Diese Details sind in der
Git-Historie besser aufgehoben; hier bleibt die rückverfolgbare Ergebnisübersicht.

## Ergebnis der Schritte

| Schritt | Befunde | Ergebnis | Status |
|---|---|---|---|
| 0 | A-10, A-11, A-13 | Importgrenzen, Ausgabepfade und gemeinsame Fake-Testvorrichtung vereinheitlicht. | erledigt 20.08.2026 |
| 1 | A-01 | REMOTE-Cleanup in Stufe 3 und 4 für alle Fehlerklassen garantiert. | erledigt 20.08.2026 |
| 2 | A-02 | Schreibende Hardwareproben sichern und restaurieren ihren Zustand in `finally`. | erledigt 20.08.2026 |
| 3 | A-08 | Konfigurationsauflösung in Logging und Fehlerbehandlung aufgenommen. | erledigt 20.08.2026 |
| 4 | A-03 | Bereichsziele werden gegen die vorhandenen Elemente geprüft. | erledigt 20.08.2026 |
| 5 | A-04, A-06 | Erwartbare DLL-, Typ- und Parserfehler werden an der Paketgrenze übersetzt. | erledigt 20.08.2026 |
| 6 | A-07 | Fehlgeschlagene Metadatenabfragen bereinigen die Antwortqueue. | erledigt 20.08.2026 |
| 7 | A-13 | Alle Stufenskripte sind gerätefrei durchspielbar. | erledigt 20.08.2026 |
| 8 | A-09, A-10 | Injizierbare Einstiegspunkte und endgültige Ablösung von Modulkonstanten. | in M5-2/CLI aufgegangen |
| 9 | A-05, A-12, A-14 bis A-16 | Architekturentscheidungen dokumentiert; kein eigener Umbau erforderlich. | entschieden |
| 10 | A-17 | Dokumentation an den damaligen Stand angepasst. | erledigt; laufende Pflege |

## Leitentscheidungen

- Cleanup hängt nicht von einer bestimmten Treiberfehlerklasse ab.
- Hardwareproben sind Transaktionen: sichern, gezielt ändern, prüfen, garantiert
  restaurieren.
- Öffentliche Paketgrenzen liefern Treiberfehler mit Kontext; Programmierfehler in
  Tests sollen nicht pauschal maskiert werden.
- Die Fassade `WT3000` bleibt der normale Einstieg. Stufenskripte sind Beispiele,
  keine parallele Produkt-API.
- Flüchtige Angaben wie Testzahlen gehören nur an wenige aktive Stellen.

## Verbleibende Arbeit

Die noch sinnvollen Teile der früheren Schritte 8 bis 10 werden nicht länger in diesem
historischen Plan gepflegt:

- gemeinsame Kommandozeile und einheitliche Einstiegspunkte: ROADMAP M5-2,
- Parser- und Scope-Konsolidierung: S-02/M2-5,
- allgemeine Fehler- und Timeoutstrategie: S-03/S-05/M1-5,
- Dokumentationspflege: M5-3.

Aktueller Status und Abnahmekriterien stehen in [ROADMAP.md](ROADMAP.md) und
[OFFENE_PUNKTE.md](OFFENE_PUNKTE.md).
