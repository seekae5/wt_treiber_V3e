# Roadmap — vom Stufenskript zur Treiberbibliothek

**Stand:** 2026-08-20, `wt3000-scpi 0.3.0`

**Abgeschlossen:** M1-1, M1-2, M4-1, M4-2 sowie die Befundpakete P-1…P-8

**Prüfstand:** 282 Tests, Ruff und Mypy ohne Befund

**Bezug:** [OFFENE_PUNKTE.md](OFFENE_PUNKTE.md) ·
[AENDERUNGEN_2026-08-18.md](AENDERUNGEN_2026-08-18.md)

Der fertige Treiber soll fünf Aufgaben abdecken:

1. Gerätekonfiguration einlesen
2. Gerätekonfiguration sicher einstellen und wiederherstellen
3. Messkonfiguration lesen und anpassen
4. Messungen starten, stoppen und nach Unterbrechungen fortsetzen
5. Messdaten samt Einheiten und Metadaten exportieren

SCPI-Knoten, die noch nicht am Gerät belegt sind, bleiben mit **(prüfen)** markiert.
Die Kommandoübersicht ist eine Geräte- und keine Implementierungsübersicht.

---

## 1 — Aktueller Reifegrad

| Zielfunktion | Vorhanden | Offen | Reifegrad |
|---|---|---|---|
| Gerätekonfiguration lesen | `DeviceInfo` liest Identifikation, Verdrahtung, Modultypen und Bestückung; Metadatenabzug liest weitere Gruppen roh | strukturierter Snapshot für Kommunikation, Averaging, Frequenzquelle, Integration, Harmonische und System | **30 %** |
| Gerätekonfiguration einstellen | sichere Schreib- und Restoremuster für Item-Tabelle, Bereiche und Eingangskonfiguration | Gerätegruppen jenseits `:INPut`, ein gemeinsames Backup, Setup-Speicher | **15 %** |
| Messkonfiguration | `InputConfig`, `RangePlan`, Snapshots, Diff und Restore | geräteabhängige Element-/Bereichstabellen, `InputPlan`, Eingangsart und unabhängiger Modus setzen | **75 %** |
| Messsteuerung | blockierende Messschleife, HOLD, driftfreie Taktung, Statistik, `Sample` | start-/stoppbares Objekt, Generator, Geräteintegration, Ereignistakt, Wiederverbindung | **35 %** |
| Datenexport | `SampleSink`, CSV, JSONL, Callback und Bündel; strenge Spaltenregel | Einheiten, verbindliche Metadaten, Rotation und Fortsetzung | **80 %** |
| Querschnitt | Transport-Protokoll, FakeTransport, Fassade, Konfigurationsauflösung, 282 gerätefreie Tests, Ruff, Mypy, LF-Regel | robuste Fehlerpfade, CI, Paketmetadaten, gemeinsame CLI | **solide Basis** |

Die Fassade und der austauschbare Export sind nicht mehr Zielbild, sondern Bestand.
Der Schwerpunkt liegt jetzt auf Hardwarebelegen, vollständiger Konfiguration und einer
steuerbaren Langzeitmessung.

---

## 2 — Status der Meilensteine

| Meilenstein | Status | Nächster Abschluss |
|---|---|---|
| M0 — Gerätefragen | **teilweise** | ein protokollierter Gerätetermin; Spannungssyntax ist bereits belegt |
| M1 — Fundament | **teilweise** | M1-3 bis M1-5 |
| M2 — Konfiguration | **teilweise** | M2-1 zur Hälfte (`:INTEGrate`, `:MEASure`, `:HARMonics`); Parser vereinheitlichen, dann M2-4 |
| M3 — Messsteuerung | **teilweise** | M3-2 Integration umgesetzt; Sitzungsbesitz entscheiden, danach M3-1 |
| M4 — Export | **teilweise** | M4-3 Einheiten und Metadaten |
| M5 — Auslieferung | **teilweise** | CLI, Paketmetadaten und CI |

---

## M0 — Offene Gerätefragen schließen

Die offenen Prüfungen sollten als ein Messtermin durchgeführt werden. Jeder Beleg
enthält Modell, Firmware, gesendetes Kommando, Rohantwort, Rücklesewert und
Fehlerqueue.

### M0-1 — Bereichssyntax abschließen `S · am Gerät` — **teilweise umgesetzt**

Belegt ist der Spannungsknoten ohne Einheit:
`:INPut:VOLTage:RANGe:ELEMent4 1000`.

Noch zu prüfen:

- Direktstrom: reine NRf-Zahl gegen `5A`/`500MA`
- Sensorstrom: `EXTernal,10` gegen `EXTernal,10V`
- Rücklesen und Fehlerqueue nach jeder Form

**Fertig, wenn:** Spannung, Direktstrom und Sensorstrom mit genau einer gemeinsamen
Formatierungsregel geschrieben und durch echte Geräteantworten in Tests belegt sind.

### M0-2 — Verhalten bei ungültigen Stellwerten `S · am Gerät`

Einen Wert zwischen zwei gültigen Stufen senden und unterscheiden: Ablehnung,
Rundung oder unveränderte Übernahme. Das Ergebnis bestimmt den Standard von
`verify_plan(allow_snapping=…)`.

**Fertig, wenn:** der Gerätebeleg und ein Test die Voreinstellung begründen.

### M0-3 — Notwendigkeit von REMOTE `S · am Gerät`

Die Schreibprobe aus Stufe 5b einmal ohne und einmal mit `use_remote=True` ausführen.
Ohne `--write-probe` bleibt das Skript garantiert lesend.

**Fertig, wenn:** `WTConfig.use_remote` einen belegten Standard besitzt und das
Verhalten beim Verbindungsabbruch dokumentiert ist.

### M0-4 — Modul- und Verdrahtungsantworten `S · am Gerät`

Rohantworten von `:INPut:MODUle?` und `:INPut:WIRing?` aufzeichnen, einschließlich
eines unbestückten Elements, falls verfügbar. Zusätzlich die Firmwarevarianten der
Line-Filter und die Anzahl angebotener SIGMA-Scopes festhalten.

**Fertig, wenn:** Parser und Tests echte Antworttexte statt angenommener Formate
verwenden.

### M0-5 — Erkennung eines neuen Datensatzes `M · am Gerät`

Statusregister und Ereignismechanismen des Geräts prüfen, insbesondere
`:STATus:CONDition?`, Extended Event Register und Serial Poll **(prüfen)**. Parallel
eine Messreihe schneller als die Aktualisierungsrate lesen und Dubletten zählen.

**Fertig, wenn:** M3-3 entweder auf ein belegtes Ereignis oder auf eine ausdrücklich
gewählte Dublettenerkennung setzt.

### M0-6 — Transportdetails belegen `S · am Gerät`

- Einheit von `TmcSetTimeout`
- Antwortverhalten bei mit `;` verketteten Kommandos

**Fertig, wenn:** Timeout und Kopfentfernung nicht mehr auf unbestätigten Annahmen
beruhen.

---

## M1 — Fundament

### M1-1 — Fassade `WT3000` — **umgesetzt 2026-08-19**

- [x] `WT3000.connect()`, `from_config()` und `from_transport()`
- [x] Context Manager und fehlertolerantes Cleanup
- [x] `wt.input`, `wt.ranges`, `wt.items`, `wt.measure`, `wt.device`
- [x] Wiring-Units intern verdrahtet
- [x] Item-Tabelle über `ItemAccess.applied()` sicher anwendbar
- [x] Protokollzustand über `check_protocol_state()` prüfbar

### M1-2 — Transport-Protokoll — **umgesetzt 2026-08-19**

- [x] `Transport`-Protokoll
- [x] `TmctlTransport` als konkrete Hardwareimplementierung
- [x] `FakeTransport` mit gestückelten Antworten, Blockdaten und Fehlerfällen
- [x] `WTSession` hängt am Protokoll statt an TMCTL
- [x] vollständige gerätefreie Messschleife geprüft

### M1-3 — Gerätesteckbrief statt harter Annahmen `M`

`DeviceInfo` ist begonnen, aber noch nicht vollständig:

- dieselbe bestückte Elementliste an `InputConfig` und `RangeAccess` geben
- Bereichstabellen nach Modultyp auswählen
- [x] **Optionen und Firmware erfassen — umgesetzt 2026-08-21.** `*OPT?` wird
  beim Verbinden abgefragt und in `DeviceInfo.options` abgelegt; die Firmware
  stand schon vorher aus `*IDN?` bereit. `supports(gruppe)` und
  `require_option(gruppe)` prüfen jede der zehn optionsgebundenen
  Kommandogruppen dagegen, bevor sie angesprochen wird — Voraussetzung für
  Rang 3, 5, 8 und 10 aus
  [ANALYSE_FEHLENDE_FUNKTIONEN.md](ANALYSE_FEHLENDE_FUNKTIONEN.md#4--priorisierte-kurzfassung).
  `:MOTor` wird bewusst am Modellcode `-MV` und nicht an `MTR` entschieden
  (Gerätebefund vom 21.08.2026).
- unbekannte Module oder Tabellenwerte als `WTError` mit Kontext melden
- Modellprüfung beim Verbinden mit deutlicher Warnung

**Fertig, wenn:** ein 3-Element-Gerät oder ein Gerät mit anderem Strommodul ohne
Codeänderung initialisiert werden kann.

### M1-4 — Protokollzustand herstellen `S`

`check_protocol_state()` prüft heute nur. Ergänzt werden soll ein abgesicherter
Ablauf, der in einer schreibfähigen Sitzung Header, Verbose-Modus und Zahlenformat
sichert, auf den Sollzustand stellt und beim Verlassen wiederherstellt. Rein lesende
Sitzungen bleiben beim klaren Abbruch.

**Fertig, wenn:** eine abweichend eingestellte Frontplatte einen schreibfähigen Lauf
nicht verhindert und danach ihren Ausgangszustand zurückerhält.

### M1-5 — Fehlerpfade härten `S`

- `drain_after_failure()` in einen begründeten Produktivpfad integrieren
- verspätete Antworten und Timeoutwiederherstellung testen
- eigene Timeout-Unterklasse unter `WTError`
- erwartbare Tabellen-/Parserfehler an der Paketgrenze in `WTError` übersetzen
- Bibliotheks-Logging mit `NullHandler`

**Fertig, wenn:** ein simulierter Timeout mitten im Ablauf weder Folgeantworten
verschiebt noch Cleanup verhindert.

---

## M2 — Konfiguration lesen und einstellen

### M2-1 — Fehlende Gerätegruppen `L` — **zur Hälfte umgesetzt 2026-08-21**

Das Fachmodul [wt3000_deviceconfig.py](../src/wt3000_scpi/wt3000_deviceconfig.py)
existiert seit dem 21.08.2026 mit Gettern, Settern, Snapshot (`capture()`) und
Restore für seine erste Gruppe. Reihenfolge:

1. Kommunikation
2. [x] **Averaging** — umgesetzt als `ComputationConfig.averaging()` /
   `set_averaging()` / `averaging_disabled()`
3. [x] **Frequenzmessquelle** — `frequency_item()` / `set_frequency_item()`
4. [x] **Integration** — umgesetzt, siehe M3-2
5. [x] **Harmonische** — `HarmonicsConfig` (`:HARMonics`): Bandbreite,
   Ordnungsbereich, PLL-Quelle und -Warnung, THD-Bezug, IEC-Objekt und
   -Gruppierung. Erste Gruppe mit Optionspflicht; die Fassade prüft sie über
   `DeviceInfo.require_option(":HARMonics")`. Weitere optionsabhängige Gruppen
   (`:CBCycle`, `:MOTor`) stehen noch aus.
6. Anzeige und System rein lesend

Mit Punkt 2 und 3 kam die Wirkungsgradgleichung (`:MEASure:EFFiciency:ETA<x>`)
sowie `SQFormula` und `SYNChronize` dazu — sie runden den Schnappschuss der
Gruppe ab. Zu jeder schreibbaren Gruppe gehört ein `capture()`/`restore()`-Paar;
damit steht die Vorlage für M2-4 (`SessionBackup`) bereit. Bewusst noch **nicht** enthalten und im Modulkopf einzeln benannt:
`:MEASure:FUNCtion<x>` (benutzerdefinierte Ausdrücke), `:PC`, `:DMeasure`,
`:COMPensation`, `:PHASe`, `:SAMPling`, `:MHOLd`.

Vorgezogen wurde die Integration bewusst gegen die Nummernfolge: sie ist Rang 1
der Anwendungsanalyse und hängt an keiner der offenen Parserfragen. Punkt 2 und 3
sind am selben Tag gefolgt (Rang 2 der Analyse). Die Sorge
aus M2-5 — jede neue Gruppe bringt eine weitere Parserkopie mit — ist dabei
nicht eingetreten, weil das Modul ausschließlich die Regeln aus `wt3000_common`
benutzt; die Aufzählungsregel ist dafür aus `wt3000_input` eine Schicht
tiefergezogen worden.

Schreiben wird nur für tatsächlich benötigte Gruppen freigegeben. Kommandonamen für
noch nicht verwendete Gruppen sind vorab am Handbuch und Gerät zu prüfen.

**Fertig, wenn:** ein strukturierter Gerätesnapshot erfasst, verglichen und für die
schreibbaren Gruppen wiederhergestellt werden kann.

### M2-2 — Setup-Speicher des Geräts `M · am Gerät`

Prüfen, ob vollständige Setups über `*SAV`/`*RCL`, `:FILE` oder `:STORe` gesichert und
geladen werden können. Das geräteeigene Setup dient als zusätzliches Sicherheitsnetz,
nicht als Ersatz für die gezielte Wiederherstellung.

### M2-3 — Messkonfiguration vervollständigen `M`

- `:INPut:INDependent` ausdrücklich setzen
- Eingangsart direkt/extern nur über eine eigens entsperrte Methode ändern
- NULL und Peak-Over-Rücksetzung ergänzen **(prüfen)**
- `InputPlan` als deklarativen Sollzustand für die gesamte Eingangskonfiguration
- Semantik von `InputConfig.unlocked()` vorher entscheiden

### M2-4 — Ein gemeinsames Backup `M`

`SessionBackup` bündelt Gerätesteckbrief, Gerätekonfiguration, Input-Snapshot,
Bereiche, Item-Tabelle und Tail in einer versionierten JSON-Datei. Ein eigenständiger
Restore-Befehl lädt, stellt in dokumentierter Reihenfolge wieder her und prüft den
Endzustand.

### M2-5 — Doppelte Regeln zusammenführen `M`

Nach den Gerätebelegen aus M0-4/M0-6:

- Parser aus `wt3000_input.py` auf die gemeinsamen Regeln umstellen
- Kopfentfernung für echte Antworten eindeutig festlegen
- Scope-/Zielnormalisierung vereinheitlichen
- Stufenskript-Vorbedingungen und Messprofile zentral benennen

**Fertig, wenn:** jede Parser- und Normalisierungsregel im Paket genau einmal
implementiert ist.

---

## M3 — Messung starten und stoppen

### M3-1 — Aufzeichnung als steuerbares Objekt `M`

Vorher ist zu entscheiden, ob `WTSession` intern serialisiert oder exklusiv dem
Mess-Thread gehört.

- Klasse `Measurement` mit `start()`, `stop()`, `wait()`, `is_running` und Statistik
- `threading.Event` als sofortiges Stoppsignal
- Fehler aus dem Thread bei `wait()`/`stop()` erneut auslösen
- Generator `wt.measure.stream()` als einfacher Weg ohne Hintergrundthread
- Cleanup im ausführenden Ablauf, nicht nur beim Aufrufer

`Sample` und `SampleSink` sind bereits vorhanden; die Schleife muss dafür nicht erneut
an ein Ausgabeformat angepasst werden.

### M3-2 — Gerätesteuerung `M · am Gerät` — **teilweise umgesetzt 2026-08-21**

- [x] **Integration starten, stoppen und zurücksetzen** — `IntegrationConfig` in
  [wt3000_deviceconfig.py](../src/wt3000_scpi/wt3000_deviceconfig.py) mit
  `start()`, `stop()`, `reset()`, `running()`, Betriebsart, Timer,
  Echtzeitfenster und Autokalibrierung. Gerätefrei geprüft; die
  **Geräteabnahme steht aus** und hängt an M0-3 (jedes dieser Kommandos ist
  ein Set-Kommando).
- [x] **Messgrößen dazu** — `build_integration_profile()` (TIME, WH, WHP, WHM,
  AH, AHP, AHM, WS, WQ je Element und SIGMA), erreichbar über
  `wt.items.integration_profile()`
- Einzelmessung im HOLD-Betrieb **(prüfen)** — unverändert offen; der Befund zu
  `:SINGle` steht im Klassenkopf von `NumericHold`
- `*OPC?` für langsame Zustandswechsel prüfen — offen. Ersatzweise gebaut ist
  `wait_until_finished()`, das den Zustand pollt; die Begründung (UPD-Bit am
  Gerät widerlegt) steht dort
- `*CLS` vor einem Lauf; `*RST` nicht als normalen Bedienweg anbieten — offen

**Fertig, wenn:** eine Wh-Messung über definierte Dauer sicher gestartet, beendet und
ausgelesen werden kann. — Der Weg dorthin ist gebaut und gerätefrei durchgespielt;
zum Abhaken fehlt der Lauf am realen Gerät.

### M3-3 — Gerätetakt statt blindem `sleep` `M`

Abhängig von M0-5: bevorzugt auf ein belegtes Aktualisierungsereignis warten,
andernfalls Dubletten erkennen und mit `SampleMark.DUPLICATE` kennzeichnen. Die
vorhandene driftfreie Taktung und Overrun-Statistik bleiben erhalten.

### M3-4 — Verbindungsabbruch überleben `M`

- fehlgeschlagenen Zyklus als `SampleMark.MISSING` erfassen
- Werte vorzugsweise mit `NO_DATA` auf die feste Spaltenzahl auffüllen
- nach konfigurierbarer Fehlerzahl neu verbinden
- Item-Tabelle, Bereiche und Protokollzustand vor dem Fortsetzen prüfen
- nach zu vielen Fehlschlägen sauber abbrechen

Benötigt M1-5 und M2-4.

---

## M4 — Datenexport

### M4-1 — Datensatz `Sample` — **umgesetzt 2026-08-20**

- [x] ein Datentyp zwischen Messung und Ausgabe
- [x] `SampleMark.OK`, `DUPLICATE`, `MISSING`
- [x] gemeinsame Statuskennzeichnung ohne zusätzliche CSV-Spalte

### M4-2 — `SampleSink` — **umgesetzt 2026-08-20**

- [x] formatunabhängiges Protokoll
- [x] `CsvSink`, `JsonlSink`, `CallbackSink`, `MultiSink`
- [x] Senkenlebenszyklus in der Messschleife
- [x] zentrale strenge Spaltenregel

### M4-3 — Einheiten und Metadaten `M`

- Funktionsname auf Einheit abbilden
- Skalierungsfaktoren und Gerätesteckbrief als Metadaten führen
- CSV wahlweise mit Einheitzeile oder eindeutigem Sidecar
- sicherstellen, dass Daten und Metadaten nicht getrennt vergessen werden können
- JSONL-Metadatenmodell als Vorlage verwenden

**Fertig, wenn:** eine Messdatei ohne Zusatzwissen eindeutig interpretierbar ist.

### M4-4 — Dateiverwaltung `S`

- Rotation nach Zeit, Größe oder Zeilenanzahl
- Fortsetzen nur bei passendem Format und Spaltenkopf
- Ziel, Namensschema und Trennzeichen aus Konfiguration/CLI

---

## M5 — Auslieferbarkeit

### M5-1 — Paket `S` — **teilweise umgesetzt**

Vorhanden: `pyproject.toml`, Python ≥ 3.10, `src`-Layout, Test-/Dev-Gruppen,
Version `0.3.0`.

Offen:

- `py.typed`
- Lizenz, Autoren, Klassifizierer und Projekt-URLs
- Änderungsformat für Releases
- klare Versionsregel für brechende Änderungen vor 1.0

### M5-2 — Gemeinsame Kommandozeile `M` — **teilweise umgesetzt**

Vorhanden: Konfigurationsauflösung über Parameter, Umgebung und JSON; Stufe 5b hat den
sicheren Schalter `--write-probe`.

Offen ist ein Einstiegspunkt `wt3000` mit Unterbefehlen wie `info`, `config`,
`measure` und `restore`. Die Stufenskripte bleiben Beispiele, sollen aber keine fünf
voneinander abweichenden Kommandozeilen entwickeln.

### M5-3 — Dokumentation `M` — **teilweise umgesetzt**

Eine README mit Installation, Verbindungsparametern, Sicherheitskonzept und Beispielen
ist vorhanden. Dieser Dokumentationsdurchgang ändert sie ausdrücklich nicht.

Offen:

- Gerätezustand und Wiederherstellung als eigenes Anwenderdokument
- README später gegen Fassade, neutrale Defaults und neue Senken abgleichen
- Hardwarebelege aus M0 in Feststellungen überführen

### M5-4 — Prüfautomatisierung `S` — **teilweise umgesetzt**

Vorhanden: pytest, Ruff, Mypy und `.gitattributes`; alle aktuellen Prüfungen sind grün.

Offen:

- CI bei jedem Commit
- Testabdeckung messen und fachlich sinnvolle Mindestwerte festlegen
- optionale weitere Ruff-Regeln getrennt und bewusst bewerten

---

## 3 — Heutige und geplante Architektur

```text
Transport        wt3000_transport
                 Transport, TmctlTransport, FakeTransport

Sitzung/Regeln   wt3000_core, wt3000_common

Fachzugriffe     wt3000_numeric, wt3000_rangeio, wt3000_input
                 wt3000_deviceconfig   (M2-1, seit 2026-08-21: ':INTEGrate',
                                        ':MEASure', ':HARMonics')

Abläufe          wt3000_itemspec, wt3000_ranging, wt3000_measure
Ausgabe          wt3000_sinks

Fassade          wt3000_device

Geplant          wt3000_backup         (M2-4)
                 cli.py                (M5-2)
```

`SampleSink` bleibt neben `Sample` in `wt3000_measure.py`; die konkreten Senken
bleiben in `wt3000_sinks.py`. Ein separates `wt3000_export`-Modul ist nicht geplant.
Die tatsächlichen Importgrenzen werden durch `tests/test_package_layout.py` geprüft.

---

## 4 — Reihenfolge und Abhängigkeiten

```text
M0-4/M0-6 ──> M1-3 ──> M2-5 ──> M2-1 ──> M2-4
M0-1/M0-2 ─────────────> M2-3 ────────────> M2-4
M0-3 ──────> M1-4 und Geräteabnahme M3-2
M0-5 ──────> M3-3

M1-5 ──────> M3-4
M3-1 ──────> M3-3/M3-4
M4-1/M4-2 ─> M4-3 ──> M4-4
```

**Empfohlene nächste Schritte:**

1. M0 als ein protokollierter Gerätetermin
2. M1-3 und M2-5, damit Elementlisten und Parser vor neuen Gerätegruppen geklärt sind
3. M1-5 für belastbare Fehlerpfade
4. M3-1 für eine steuerbare Messung
5. M4-3 und danach M2-1, je nach unmittelbarem Nutzungsbedarf

---

## 5 — Bewusst nicht enthalten

| Thema | Begründung / Eintrittsbedingung |
|---|---|
| Grafische Oberfläche | baut bei Bedarf als eigenes Projekt auf `CallbackSink` auf |
| Wellenform-/Rohdatenerfassung | andere Datenmengen und Kommandogruppen; erst bei konkreter Messaufgabe |
| Weitere Yokogawa-Modelle | erst nach dem vollständigen Gerätesteckbrief M1-3 |
| VISA-/Socket-Transport | Fuge ist offen; Umsetzung erst bei Bedarf |
| `asyncio`-API | Threads und Generator decken den geplanten Betrieb ab |
| Parquet im Kernpaket | würde eine erste schwere Laufzeitabhängigkeit einführen |
| Automatische Kalibrierprüfung | berührt die Eichung und benötigt ein getrennt freigegebenes Werkzeug |

---

## 6 — Kurzfassung

Der Unterbau ist belastbar und gerätefrei geprüft. Fassade, Datensatz und
formatunabhängiger Export sind umgesetzt. Als Nächstes müssen die verbleibenden
Geräteannahmen belegt, der Gerätesteckbrief vervollständigt, Parser vereinheitlicht
und Fehlerpfade gehärtet werden. Danach kann die vorhandene Messschleife zu einer
steuerbaren, wiederaufnahmefähigen Langzeitmessung ausgebaut werden. Einheiten,
Metadaten, CLI und CI schließen den Weg zur auslieferbaren Bibliothek ab.
