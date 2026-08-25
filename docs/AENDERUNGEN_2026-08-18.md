# Änderungsprotokoll — 18. bis 21. August 2026

Dieses Protokoll hält die fachlich wichtigen Änderungen fest. Wiederholte
Entwurfsbegründungen, Zwischenstände und einzelne Testzahlen wurden entfernt; genaue
Patches und Prüfverläufe bleiben über die Git-Historie nachvollziehbar.

## 18. August — Fehlerprüfung und Bereinigung

- Sicherheits- und Fehlerpfade der Stufenskripte geprüft.
- Historische, auskommentierte Implementierungen und überholte Meilensteinkommentare
  entfernt.
- Ausgangsbefunde in offene, erledigte und geräteabhängige Punkte getrennt.

## 19. August — Bibliotheksfundament

### Transport und Fassade

- `Transport`-Protokoll eingeführt; `TmctlTransport` und `FakeTransport` sind
  austauschbar.
- `WT3000` als öffentlicher Einstieg für Sitzung, Input, Bereiche, Items und Messung
  eingeführt.
- Öffnen, REMOTE-Umschaltung und Schließen besitzen definierte Cleanup-Pfade.

### Fehler- und Sicherheitsgarantien

- Cleanup läuft auch bei unerwarteten Fehlerklassen.
- Restore-Fehler werden nicht mehr verschluckt und durch Rückleseproben sichtbar.
- Messwert- und Spaltenzahl werden zentral geprüft.
- Blockantworten werden vollständig validiert und als Protokollfehler gemeldet.
- Schreibende Hardwareproben benötigen einen ausdrücklichen Schalter und stellen
  veränderten Zustand garantiert zurück.

### Projektbasis

- Konfiguration aus Parameter, Umgebung und JSON vereinheitlicht.
- `pyproject.toml`, Test-/Entwicklungsabhängigkeiten, Ruff, Mypy und
  Zeilenendungsregeln ergänzt.

## 20. August — Datensatz und austauschbarer Export

- `Sample` als Formatgrenze zwischen Messung und Ausgabe eingeführt.
- Statuswerte `OK`, `DUPLICATE` und `MISSING` vorgesehen.
- `SampleSink` mit `CsvSink`, `JsonlSink`, `CallbackSink` und `MultiSink` umgesetzt.
- Senkenlebenszyklus und strenge Spaltenprüfung in die gemeinsame Messschleife gelegt.
- Importgrenzen und gerätefreie Einstiegstests erweitert.

## 21. August — Gerätesteckbrief und Optionen

- `DeviceInfo` liest `*IDN?`, `*OPT?`, Bestückung und relevante Geräteangaben.
- Optionscodes werden normalisiert; Fachgruppen können ihre Voraussetzung vor dem
  ersten Gerätekommando prüfen.
- Ein Gerätecheck bestätigte unter anderem `G6`, `DT`, `CC` sowie die Motorfunktion
  der Modellvariante `-MV`; Flicker und Analogausgang fehlen am geprüften Gerät.
- Fehlerpfade nach fehlgeschlagenen Identitäts-/Optionsabfragen bereinigen die
  Antwortqueue.

## 21. August — Integration und Rechenfunktionen

- `IntegrationConfig` mit Modus, Timer, Echtzeitfenster, Autokalibrierung,
  Start/Stop/Reset, Zustandsprüfung und `capture()`/`restore()` umgesetzt.
- Integrationsprofil für Zeit-, Energie- und Ladungsmesswerte ergänzt.
- `ComputationConfig` für Averaging, Frequenzquellen, Wirkungsgradgleichungen,
  SQ-Formel und Synchronisation ergänzt.
- Gemeinsame Parserregeln in `wt3000_common` wiederverwendet; keine neue
  Parserfamilie eingeführt.

## 21. August — Oberschwingungen

- `HarmonicsConfig` mit Band, Ordnungsbereich, PLL-Quelle/-Warnung, THD-Bezug,
  IEC-Objekt und Gruppierung umgesetzt.
- Optionsprüfung (`G5` oder `G6`) an der Fassade erzwungen.
- Harmonics-Messprofil ergänzt.
- Eine falsche Annahme zur Rückleseprobe korrigiert: relevante Schreibknoten werden
  über ihre belegte Kurzform zurückgelesen.

## 21. August — Gemeinsames Sitzungsbackup

- `SessionBackup` bündelt Gerätesteckbrief, Input, Bereiche, Item-Tabelle samt Tail,
  Integration, Berechnung und Harmonics in einer versionierten JSON-Struktur.
- `WT3000.backup()` und `restore_backup()` prüfen Geräteidentität, führen die
  Wiederherstellung in definierter Reihenfolge aus und melden verbleibende
  Abweichungen.
- Das Backup orchestriert vorhandene Fachobjekte; es dupliziert weder Parser noch
  SCPI-Kommandos.

Spätere Änderungen, insbesondere Einheiten, Protokollzustand, Geräteratenprüfung und
steuerbare Messungen, sind im aktuellen [Anwendungshandbuch](ANWENDUNGSHANDBUCH.md)
und in der [Roadmap](ROADMAP.md) beschrieben.
