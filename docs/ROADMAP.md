# Roadmap — vom Stufenskript zur Treiberbibliothek

**Stand:** 25. August 2026 · `wt3000-scpi 0.3.0`
**Qualität:** gerätefreie Tests, Ruff und Mypy ohne Befund; Hardwareabnahmen sind
gesondert markiert.

Diese Datei beschreibt Ziele und Abnahmekriterien. Technische Bedienung steht im
[Anwendungshandbuch](ANWENDUNGSHANDBUCH.md), konkrete Restbefunde in
[OFFENE_PUNKTE.md](OFFENE_PUNKTE.md), abgeschlossene Änderungen im
[Änderungsprotokoll](AENDERUNGEN_2026-08-18.md).

## Reifegrad

| Bereich | Vorhanden | Wesentliche Lücke |
|---|---|---|
| Fundament | Transport-Protokoll, Fassade, Konfigurationsauflösung, Geräte-Fake | einheitliche Timeout- und Fehlerstrategie |
| Gerätekonfiguration | Input, Bereiche, Items, Integration, Berechnung, Harmonics, Backups | Hardwarebelege und Parserkonsolidierung |
| Messung | blockierend, Generator und steuerbarer Hintergrundlauf | Wiederverbindung und fehlende Zyklen |
| Export | CSV, JSONL, Callback, MultiSink, Status und Einheiten | Rotation und feste Metadatenbindung |
| Auslieferung | installierbares Paket, README, Tests, Ruff, Mypy | CLI, CI, Lizenz und vollständige Metadaten |

## Meilensteine

### M0 — Gerätefragen schließen

| Punkt | Ziel | Stand |
|---|---|---|
| M0-1 | Bereichssyntax für Spannung, Direktstrom und Sensorstrom belegen | Spannung belegt; Strom/Sensor offen |
| M0-2 | Verhalten bei ungültigen Stellwerten belegen | offen |
| M0-3 | Notwendigkeit von REMOTE für Schreibkommandos klären | offen |
| M0-4 | reale Modul- und Wiringantworten erfassen | teilweise belegt |
| M0-5 | zuverlässige Erkennung eines neuen Datensatzes prüfen | UPD-Polling widerlegt; Statusfilter/SRQ offen |
| M0-6 | Timeout-Einheit und Mehrfachantworten belegen | offen |

**Fertig, wenn:** alle Versuche mit Kommando, Rohantwort, Rücklesewert, Fehlerqueue,
Modell und Firmware protokolliert sind.

### M1 — Fundament

| Punkt | Ergebnis bzw. Ziel | Stand |
|---|---|---|
| M1-1 | öffentliche Fassade `WT3000` | erledigt 19.08.2026 |
| M1-2 | austauschbares `Transport`-Protokoll und `FakeTransport` | erledigt 19.08.2026 |
| M1-3 | Gerätesteckbrief statt harter Annahmen | weitgehend erledigt; modulabhängige Bereichstabellen offen |
| M1-4 | Protokollzustand erheben, herstellen und garantiert zurückstellen | erledigt 25.08.2026 |
| M1-5 | Timeout, Antwortqueue und öffentliche Fehlersemantik härten | offen |

M1-5 ist fertig, wenn ein simulierter Timeout mitten im Ablauf weder Folgeantworten
verschiebt noch Cleanup verhindert und erwartbare Paketfehler konsistent unter
`WTError` liegen.

### M2 — Konfiguration lesen und einstellen

| Punkt | Ergebnis bzw. Ziel | Stand |
|---|---|---|
| M2-1 | Integration, Averaging, Frequenzquelle, Effizienz, Synchronisation und Harmonics | Kernumfang erledigt 21.08.2026; Spezialgruppen offen |
| M2-2 | geräteeigenen Setup-Speicher als zusätzliches Sicherheitsnetz prüfen | offen, am Gerät |
| M2-3 | `InputPlan`, unabhängige Eingänge, NULL/Peak-Over und klare Freigabesemantik | offen |
| M2-4 | gemeinsames, versioniertes `SessionBackup` mit Identitäts- und Endprüfung | erledigt 21.08.2026 |
| M2-5 | Parser-, Header-, Scope- und Profilregeln zusammenführen | offen |

Der offene Spezialumfang von M2-1 umfasst insbesondere CBCycle, Motor,
benutzerdefinierte Ausdrücke und weitere optionsabhängige Gruppen. Er wird nur bei
konkretem Bedarf umgesetzt.

### M3 — Messung starten und stoppen

| Punkt | Ergebnis bzw. Ziel | Stand |
|---|---|---|
| M3-1 | `Measurement` mit `start()`, `stop()`, `wait()`, Status und Fehlerweitergabe; außerdem `stream()` | erledigt 25.08.2026 |
| M3-2 | Integration starten/stoppen und passendes Messprofil | Softwarepfad erledigt; Geräteabnahme offen |
| M3-3 | Geräterate berücksichtigen und Dubletten erkennen | Ersatzweg erledigt; Ereignissteuerung offen |
| M3-4 | Kommunikationsabbrüche überleben | erledigt 25.08.2026 |

M3-4 ist über `ErrorPolicy` umgesetzt — als **Opt-in**: ohne Policy beendet ein
Kommunikationsfehler den Lauf wie bisher. Eine Bibliothek darf Ausnahmen nicht
ungefragt in Datenzeilen verwandeln; wer Nachsicht will, vereinbart sie
(`ErrorPolicy.unattended()` ist die fertige Voreinstellung dafür).

- [x] fehlgeschlagenen Zyklus als `SampleMark.MISSING` erfassen,
- [x] Werte mit `NO_DATA` auf die feste Spaltenzahl auffüllen — löst den
  Zielkonflikt aus S-08, ohne die strenge Spaltenregel anzutasten,
- [x] nach konfigurierbarer Fehlerzahl neu verbinden (`reconnect_after`), begrenzt
  durch `max_reconnects`; der Transport zeigt die Fähigkeit über
  `ReconnectableTransport` an, ein Transport ohne sie misst ohne Neuaufbau weiter,
- [x] Zahlenformat, Header und Item-Tabelle vor der Fortsetzung prüfen
  (`verify_after_reconnect()`) — eine Abweichung beendet den Lauf, statt Zeilen
  unter falschen Spalten fortzuschreiben,
- [x] nach zu vielen Fehlern sauber abbrechen (`max_consecutive`, `max_total`) mit
  `MeasurementAborted`; die auslösende Zeile steht noch in der Datei.

Als Kommunikationsfehler gelten ausschließlich `TmctlError` und `ProtocolError`.
Aufruffehler (`ReadOnlyViolation`, `ConcurrentAccessError`) und `DeviceError` fallen
nicht darunter — sie durch Wiederholen zu übergehen hieße, sie zu verstecken.

**Offen bleibt** die Abnahme am realen Gerät: gerätefrei ist der Abriss abgedeckt,
der echte (Kabel, Stromausfall, Firmware-Neustart) nicht. Die frühere Abhängigkeit
von M1-5 bestand nicht — M3-4 braucht nur `drain_after_failure()`, und das ist
vorhanden.

### M4 — Datenexport

| Punkt | Ergebnis bzw. Ziel | Stand |
|---|---|---|
| M4-1 | formatunabhängiger Datensatz `Sample` mit Status | erledigt 20.08.2026 |
| M4-2 | `SampleSink`, CSV, JSONL, Callback und MultiSink | erledigt 20.08.2026 |
| M4-3 | Einheiten und verbindliche Metadaten | Einheiten erledigt; feste Bindung an Geräte-/Messkontext offen |
| M4-4 | Rotation und sicheres Fortsetzen | offen |

M4-3 ist fertig, wenn eine Messdatei ohne Zusatzwissen eindeutig interpretierbar ist.
M4-4 benötigt Rotation nach Zeit, Größe oder Zeilenanzahl sowie eine Prüfung von
Format und Spaltenkopf vor dem Fortsetzen.

### M5 — Auslieferbarkeit

| Punkt | Vorhanden | Offen |
|---|---|---|
| M5-1 Paket | `pyproject.toml`, `src`-Layout, Python ≥ 3.10, Abhängigkeitsgruppen | `py.typed`, Lizenz, Autoren, Klassifizierer, URLs, Versions- und Änderungsregel |
| M5-2 CLI | gemeinsame Konfigurationsauflösung; sichere Hardwareproben | Einstieg `wt3000` mit Unterbefehlen wie `info`, `config`, `measure`, `restore` |
| M5-3 Dokumentation | README, Handbuch, Roadmap und Referenz | Hardwarebelege nachziehen; Zustands-/Restore-Leitfaden bei Bedarf |
| M5-4 Prüfautomatisierung | pytest, Ruff, Mypy, Zeilenendungsregel | CI und fachlich begründete Abdeckungsziele |

## Architektur

```text
Transport       wt3000_transport
Sitzung         wt3000_core, wt3000_common
Fachzugriffe    wt3000_numeric, wt3000_rangeio, wt3000_input,
                wt3000_deviceconfig
Abläufe         wt3000_itemspec, wt3000_ranging, wt3000_measure,
                wt3000_backup
Ausgabe         wt3000_sinks
Fassade         wt3000_device
Geplant         cli.py
```

`Sample` und das `SampleSink`-Protokoll bleiben bei der Messlogik, konkrete Senken in
`wt3000_sinks.py`. Die Importgrenzen werden durch Layouttests geschützt.

## Abhängigkeiten und nächste Schritte

```text
M0-4/M0-6 -> M2-5 -> weitere Konfigurationsgruppen
M0-1/M0-2 -> M2-3
M0-3       -> Geräteabnahme M3-2
M0-5       -> Ereignisweg M3-3
M4-3       -> M4-4
```

Empfohlene Reihenfolge:

1. M0 als gebündelter Gerätetermin,
2. M2-5 für eindeutige Parser- und Scope-Regeln,
3. M4-3/M4-4 für dauerhaft interpretierbare Dateien,
4. M1-5 für eine durchgängige Fehlersemantik an den Paketgrenzen,
5. M5 für CLI, CI und Auslieferung.

## Bewusst nicht im Kernumfang

| Thema | Grund |
|---|---|
| GUI | kann als eigenes Projekt auf `CallbackSink` aufbauen |
| Wellenform-/Rohdaten | anderer Datenpfad; erst bei konkreter Messaufgabe |
| weitere Yokogawa-Modelle | erst nach vollständigem Gerätesteckbrief |
| VISA-/Socket-Transport | Transportfuge ist vorhanden; Umsetzung bei Bedarf |
| `asyncio`-API | Threads und Generator decken den geplanten Betrieb ab |
| Parquet im Kernpaket | würde eine schwere Laufzeitabhängigkeit einführen |
| automatische Kalibrierprüfung | berührt die Eichung und braucht einen getrennt freigegebenen Ablauf |
