# Aufwärts-Aufrufkette der Schichten — konsolidierter Befund

**Ursprüngliche Prüfung:** 20. August 2026, Commit `7bfd5e7`
**Heutige Bedeutung:** historische Analyse; aktive Aufgaben stehen in
[OFFENE_PUNKTE.md](OFFENE_PUNKTE.md) und [ROADMAP.md](ROADMAP.md).

Die ursprüngliche Fassung dokumentierte jeden reproduzierten Fehler, Quelltextauszug
und Testlauf einzeln. Die meisten Befunde wurden noch am selben Tag umgesetzt. Diese
Kurzfassung bewahrt Ursache, Entscheidung und Restarbeit, ohne überholte
Reproduktionen als aktuellen Projektstand erscheinen zu lassen.

## Untersuchte Aufrufkette

```text
TmctlTransport -> WTSession -> Fachzugriffe -> Abläufe -> Stufenskripte
```

Die Schichten waren grundsätzlich sinnvoll getrennt. Schwachstellen lagen vor allem
an ihren Übergängen: rohe Transportfehler, unvollständiges Cleanup, mehrfach
aufgelöste Konfiguration und kaum durchspielbare Einstiegsskripte.

## Stand der 17 Befunde

| Nr. | Ursprünglicher Befund | Stand |
|---|---|---|
| A-01 | REMOTE konnte nach einem Restore-Fehler aktiv bleiben. | **Erledigt:** Cleanup läuft unabhängig von der Fehlerklasse. |
| A-02 | Hardwarewerkzeuge schrieben ohne garantiertes `finally`. | **Erledigt:** veränderter Zustand wird garantiert zurückgestellt. |
| A-03 | `RangeAccess.set_range()` prüfte die Elementnummer nicht. | **Erledigt:** Ziele werden gegen die Gerätebestückung geprüft. |
| A-04 | Der Transportkonstruktor ließ rohe DLL-/Typfehler entweichen. | **Erledigt:** erwartbare Initialisierungsfehler werden als Treiberfehler übersetzt und aufgeräumt. |
| A-05 | `FakeTransport` und realer Transport hatten abweichende Fehlerverträge. | **Bewusste Restentscheidung:** Test-Doubles sollen Programmierfehler weiterhin sichtbar machen; öffentliche Paketgrenzen bleiben maßgeblich. |
| A-06 | Geräteantworten wurden mehrfach mit rohem `int()`/`float()` geparst. | **Erledigt:** betroffene öffentliche Pfade liefern Fehler mit Kontext. Weitere Vereinheitlichung siehe S-02. |
| A-07 | `write_metadata()` fragte nach einem fehlgeschlagenen Query ohne Bereinigung weiter. | **Erledigt:** Fehlerpfad ruft `drain_after_failure()` auf. Die allgemeine Zuständigkeit bleibt unter S-03 offen. |
| A-08 | Konfiguration wurde vor Logging und Fehlerbehandlung aufgelöst. | **Erledigt:** Auflösung liegt im geschützten Einstiegspfad. |
| A-09 | Stufenskript-`main()` akzeptierte keine fertige `WTConfig`. | **Offen, niedrige Priorität:** gemeinsame CLI bzw. injizierbare Einstiegspunkte sollen die Skripte ablösen. |
| A-10 | `OUTPUT_DIR` wurde uneinheitlich und teils zur Importzeit bestimmt. | **Weitgehend erledigt:** gemeinsame Testvorrichtung und einheitliche Auflösung; Rest zusammen mit A-09. |
| A-11 | Importgrenzen galten nicht für die Stufenskripte. | **Erledigt:** Layouttests decken die Einstiegsmodule ab. |
| A-12 | Layer 0 war nur indirekt über `wt3000_core` erreichbar. | **Bewusst belassen:** die Fassade ist der öffentliche Einstieg; direkte Nutzung des Transports bleibt möglich. |
| A-13 | Vier Stufenskripte waren nicht gerätefrei durchspielbar. | **Erledigt:** gemeinsame Fake-Vorrichtung und Abdeckung der Einstiegspfade. |
| A-14 | Stufe 2 sperrte das Bedienfeld, obwohl sie nur las. | **Bewusste Vereinfachung:** Beispiele werden nicht weiter als eigenständige Produktoberflächen ausgebaut. |
| A-15 | „Schreibt nichts“ berücksichtigte die Fehlerqueue nicht. | **Dokumentiert:** Diagnoseabfragen können Gerätezustand verändern; `read_only` bedeutet keine Konfigurationsschreibbefehle. |
| A-16 | `WTSession` benötigte für wenige Werte eine vollständige `WTConfig`. | **Belassen:** die Konfiguration ist das gemeinsame Übergabeobjekt und vermeidet parallele Parameterlisten. |
| A-17 | README und Dokumente enthielten veraltete Testzahlen und Aussagen. | **Erledigt und laufende Pflegeaufgabe:** aktive Angaben wurden nachgezogen; flüchtige Prüfzahlen sollen sparsam verwendet werden. |

## Verbleibende Konsequenzen

Aus der Analyse bleiben keine sicherheitskritischen Sofortmaßnahmen offen. Relevant
sind nur noch die allgemeinen Projektaufgaben:

- Parser- und Scope-Regeln zusammenführen (S-02/M2-5),
- Fehler- und Timeoutsemantik vereinheitlichen (S-03/S-05/M1-5),
- Stufenskripte langfristig durch eine gemeinsame CLI ergänzen (M5-2),
- Hardwareannahmen in einem protokollierten Gerätetermin belegen (M0).

Die damalige Umsetzungsreihenfolge und Zuordnung der Befunde steht kompakt in
[PLAN_AUFRUFKETTE.md](PLAN_AUFRUFKETTE.md). Detaillierte historische Patches und
Testausgaben bleiben über die Git-Historie nachvollziehbar und werden hier nicht
doppelt gepflegt.
