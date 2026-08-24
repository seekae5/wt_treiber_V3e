# Änderungen — konsolidierter Stand 2026-08-18 bis 2026-08-21

**Projektstand:** `wt3000-scpi 0.3.0`

**Geprüft am:** 2026-08-20

**Prüfstand:** 282 Tests bestanden, Ruff ohne Befund, Mypy ohne Befund in 17 Quellmodulen

**Gerätebezug:** Die automatischen Prüfungen laufen ohne WT3000 und ohne TMCTL-DLL.

Dieses Dokument ersetzt die früheren Einzelprotokolle zu F-01…F-09, M1-1,
P-1…P-8, den Kommentarbereinigungen sowie M4-1 und M4-2. Es hält den erreichten
Stand und die weiterhin relevanten Entwurfsentscheidungen fest. Offene Aufgaben
stehen ausschließlich in [OFFENE_PUNKTE.md](OFFENE_PUNKTE.md) und
[ROADMAP.md](ROADMAP.md).

---

## 2026-08-18 — Fehlerprüfung und erste Bereinigung

Ausgangspunkt waren 115 Tests und 14 Quellmodule. Neun klar abgrenzbare Änderungen
wurden vorgenommen:

| Nr. | Ergebnis |
|---|---|
| F-01/F-02 | Zwei unbenutzte Importe entfernt |
| F-03 | Doppelten Kommentarkopf in `wt3000_itemspec.py` entfernt |
| F-04 | Die SIGMA/SIGMB-Normalisierung auf `wt3000_common.canonical_element()` zurückgeführt |
| F-05 | Setter für Synchronisationsquelle und Messmodus akzeptieren die vom Gerät gelesenen Kurzformen und normalisieren sie eindeutig |
| F-06 | Verdeckte Modulsitzung aus Stufe 2 entfernt; die Sitzung wird ausdrücklich übergeben |
| F-07 | Stufe 2 nimmt Fernsteuerung auch im Fehlerfall zurück |
| F-08 | Fünf Kopien von `setup_logging()` in `wt3000_common.py` zusammengeführt |
| F-09 | Die zwei verschiedenartigen Schreibproben eindeutig in Item- und Bereichsprobe umbenannt |

Die wesentliche Fehlerkorrektur war F-05: Ein zuvor vom Gerät gelesener Kurztext wie
`EXT` oder `RMEA` konnte beim Wiederherstellen abgewiesen werden. Die gemeinsame
Normalisierung verhindert seitdem einen Abbruch mitten in der Rückstellung.

Die damalige Prüfung endete mit 124 Tests. Die parallel erfassten Befunde wurden in
den folgenden Schritten entweder behoben oder in die aktuelle Liste offener Punkte
überführt; die ursprüngliche Momentaufnahme ist daher nicht mehr als Aufgabenliste
zu verwenden.

---

## 2026-08-19 — Bibliotheksfundament

### Transport-Protokoll (M1-2)

`TmctlTransport` wurde aus `wt3000_core.py` in `wt3000_transport.py` verschoben und
hinter dem Protokoll `Transport` abstrahiert. `WTSession` hängt seitdem nicht mehr an
der konkreten TMCTL-Implementierung. `FakeTransport` bildet Antworten, Blockdaten,
Fehler und gestückelte Lesevorgänge ohne Gerät nach. Die bisherigen Namen werden über
`wt3000_core` weitergereicht, damit bestehende Importe funktionieren.

### Fassade `WT3000` (M1-1)

`wt3000_device.py` stellt den zentralen Einstiegspunkt bereit:

- `WT3000.connect()`, `from_config()` und `from_transport()`
- Context Manager mit bestmöglichem Aufräumen von HOLD, REMOTE und Transport
- gebundene Zugriffe über `wt.input`, `wt.ranges`, `wt.items`, `wt.measure` und
  `wt.device`
- `DeviceInfo` mit Identifikation, Verdrahtung, Modultypen und den erkannten
  bestückten Elementen
- `ItemAccess.applied()` für Sichern, Anwenden, Prüfen und Wiederherstellen der
  Item-Tabelle
- `check_protocol_state()` für den lesenden Abgleich von Header-, Zahlenformat- und
  Statusvoraussetzungen

Die Fassade verdrahtet Wiring-Units und Bereichszugriff intern. Ein Aufrufer muss die
Fachobjekte deshalb nicht mehr von Hand zusammensetzen. `DeviceInfo` ist bewusst noch
nicht der vollständige Gerätesteckbrief; die Restarbeit steht unter M1-3 der Roadmap.

### Garantien und Fehlerpfade (P-1 bis P-4)

| Punkt | Umgesetzte Garantie |
|---|---|
| P-1 | Scheitert der Verbindungsaufbau nach `REMOTE ON`, wird bestmöglich `REMOTE OFF` gesendet, ohne die ursprüngliche Ausnahme zu verdecken |
| P-2 | Fehler oder wirkungslose Schreibvorgänge beim Restore der Item-Tabelle werden durch Ausnahme und Gegenprobe sichtbar |
| P-3 | Eine abweichende Messwertanzahl bricht im Datenpfad ab; keine verschobene oder unbenannte Ausgabespalte wird geschrieben |
| P-4 | Abgeschnittene, nichtnumerische, negative oder unplausibel große Blocklängen werden als `ProtocolError` gemeldet |

Die Längenregel aus P-3 wurde später in `require_matching_columns()` zentralisiert und
gilt heute für alle Ausgabesenken.

### Sichere Werkzeuge und portable Konfiguration (P-5 bis P-8)

| Punkt | Ergebnis |
|---|---|
| P-5 | Stufe 5b ist standardmäßig rein lesend; die Schreibprobe erfordert ausdrücklich `--write-probe` |
| P-6 | Das echte Geräteskript liegt unter `tools/hardware/`; die Testsuite verhindert die Erzeugung eines echten `TmctlTransport` |
| P-7 | Verbindungswerte werden je Feld in der Reihenfolge Parameter → Umgebung → JSON-Datei → neutraler Klassenstandard aufgelöst |
| P-8 | Test- und Entwicklungsabhängigkeiten sowie Ruff und Mypy sind in `pyproject.toml` reproduzierbar konfiguriert |

`WTConfig()` enthält seit P-7 keine feste IP und keine Zugangsdaten. Ein bloßer
DLL-Dateiname wird an die Windows-Suche übergeben; ein ausdrücklich angegebener Pfad
muss existieren. `wt3000.example.json` dient als Vorlage.

### Kommentarbereinigung

In `wt3000_core.py` wurde der auskommentierte Archivblock des verschobenen Transports
entfernt. In `wt3000_input.py` entfielen historische Meilensteinpräfixe und
Fehlergeschichten. Erhalten blieben Begründungen, Sicherheitsregeln, Docstrings und
alle noch offenen `ZU VERIFIZIEREN`-Marken.

Die Spannungssyntax wurde am Gerät bereits belegt:
`:INPut:VOLTage:RANGe:ELEMent4 1000` wird ohne Einheit geschrieben und identisch
zurückgelesen. Die Direktstrom- und Sensorsyntax ist weiterhin am Gerät zu prüfen.

---

## 2026-08-20 — Datensatz und austauschbarer Export

### `Sample` (M4-1)

Eine Messzeile wird seitdem als unveränderlicher Datensatz `Sample` übertragen:
Zeitstempel, verstrichene Zeit, laufende Nummer, Condition-Register, Messwerte und
`SampleMark`. `SampleMark` kennt `OK`, `DUPLICATE` und `MISSING`; die Kennzeichnung
fließt ohne zusätzliche CSV-Spalte in `status_flags` ein.

Die frühere Parameterliste und `CsvRecorder.write_row()` entfielen. Messende und
schreibende Seite tauschen nur noch `Sample` aus. Für M3-4 bleibt zu entscheiden, wie
ein `MISSING`-Datensatz ohne Werte die strenge Spaltenregel erfüllt.

### `SampleSink` und Senken (M4-2)

`SampleSink` liegt als kleines Protokoll neben `Sample` in `wt3000_measure.py`.
`wt3000_sinks.py` enthält:

- `CsvSink`
- `JsonlSink`
- `CallbackSink`
- `MultiSink`

Die Messschleife kennt nur das Protokoll, öffnet die Senke mit Spalten und Metadaten
und schließt sie im `finally`. Das vermeidet eine Abhängigkeit von einem konkreten
Format. `JsonlSink` schreibt nicht endliche Messwerte standardkonform als `null` und
erhält deren Bedeutung in den Statusmarken. `MultiSink.close()` versucht alle Senken
zu schließen und meldet erst danach den ersten Fehler.

`MeasureControl.record()` nimmt eine beliebige Senke; `record_csv()` bleibt der
bequeme CSV-Einstieg. Parquet wurde nicht ergänzt, weil das Projekt weiterhin keine
Laufzeitabhängigkeit benötigt.

---

## 2026-08-21 — Geräteoptionen im Steckbrief (M1-3, Teilpunkt)

### Warum zuerst

Zehn der 22 SCPI-Kommandogruppen des WT3000 hängen an einer verbauten
Hardwareoption. Fehlt sie, wird das Kommando nicht abgelehnt — es bleibt
**unbeantwortet**, der Query läuft in den Timeout, und die Meldung sieht nach
Verbindungsabbruch aus. Das ist der Grund, warum
[ANALYSE_FEHLENDE_FUNKTIONEN.md](ANALYSE_FEHLENDE_FUNKTIONEN.md) diesen Punkt
als **Rang 0** vor alle Gerätegruppen stellt: ohne ihn kann Arbeit an den
Rängen 3 (`:HARMonics`), 5 (`:CBCycle`), 8 (`:MOTor`) und 10 an nicht
vorhandener Hardware vorbeigehen.

### `DeviceInfo` erhebt die Bestückung

`DeviceInfo.read()` fragt `*OPT?` direkt nach `*IDN?` ab. Neu sind die Felder
`options`, `options_raw` und `options_known` sowie die Methoden `has_option()`,
`is_motor_model`, `supports()`, `require_option()`, `unavailable_groups()` und
`options_summary()`. Die Zuordnung Gruppe → Option steht als
`OPTION_REQUIREMENTS` in der Paketwurzel, weil jede künftige optionsgebundene
Gruppe dagegen prüft.

`describe()` — und damit `log_summary()` beim Verbinden — nennt jetzt die
verbauten Optionen und die nachweislich gesperrten Gruppen. Was am Gerät nicht
geht, steht damit im Steckbrief statt erst im Timeout des ersten Kommandos.

### Drei Entscheidungen aus dem Gerätebefund

- **`:MOTor` steht bewusst nicht in `OPTION_REQUIREMENTS`.** Am eingemessenen
  Gerät meldete `*OPT?` kein `MTR`, obwohl `:MOTor:PM?` antwortete; zuverlässig
  war der Modellcode `-MV`. Entschieden wird deshalb über Modellcode **oder**
  `MTR`. Stünde die Gruppe mit `('MTR',)` in der Tabelle, würde der Treiber
  eine vorhandene Gruppe abweisen.
- **Unbekannt ist nicht „fehlt".** Bleibt `*OPT?` unbeantwortet, liefert
  `supports()` für jede Gruppe `True`: das Kommando läuft im Zweifel ins Gerät
  und scheitert dort mit dessen eigener Meldung. Gesperrt wird nur, was
  nachweislich fehlt.
- **Anforderungen sind Tupel.** `:HARMonics` verlangt `G5` **oder** `G6`; am
  Gerät ist nur `G6` verbaut, und das genügt.

### Nebenbefund, mit repariert

Ein fehlgeschlagenes `*IDN?` räumte bisher keine verspätete Antwort ab. Sie
hätte den nächsten Query beantwortet — nach dieser Änderung `*OPT?`, davor
`:INPut:WIRing?`, das die Verdrahtung trägt. Beide informativen Abfragen rufen
im Fehlerfall jetzt `drain_after_failure()`.

### Prüfung

16 neue Prüfsätze in `tests/test_device_facade.py` (491 → 507), darunter der Motorbefund
als Regressionsschutz. Das Gerätemodell der Testsuite (`conftest.base_responses`)
antwortet auf `*OPT?` mit der Bestückung des realen Geräts, deckt also beide
Richtungen ab.

```text
pytest: 507 passed
ruff:   All checks passed
mypy:   Success: no issues found in 17 source files
```

Offen aus M1-3 bleiben die Bereichstabellen nach Modultyp und die
Modellprüfung beim Verbinden. Der erste Aufruf von `require_option()` aus
Fachcode entsteht mit Rang 3 oder Rang 5 — bis dahin sind die Optionen erfasst
und abfragbar, aber von keiner Gruppe benutzt.

---

## 2026-08-21 — Integrationssteuerung (M3-2 / M2-1)

### Die größte funktionale Lücke

Der Treiber konnte bisher nur Momentanwerte lesen. Eine Wh- oder Ah-Messung —
die Kernfunktion eines Leistungsmessgeräts — war nicht steuerbar; die
Anwendungsanalyse führt das als Rang 1. Neu ist `IntegrationConfig` in
[wt3000_deviceconfig.py](../src/wt3000_scpi/wt3000_deviceconfig.py):
Zustand, Betriebsart, Timer, Echtzeitfenster, Autokalibrierung,
`start()`/`stop()`/`reset()`, der Kontextmanager `running()` und
`wait_until_finished()`. `capture()`/`restore()` machen die Gruppe zugleich zur
Vorlage für den gemeinsamen Snapshot aus M2-4.

Dazu die Leseseite: `build_integration_profile()` in `wt3000_measure` führt
TIME, WH, WHP, WHM, AH, AHP, AHM, WS und WQ je Element und SIGMA — ohne sie
wäre die Funktion steuerbar, aber nicht auslesbar. Erreichbar über
`wt.items.integration_profile()`, die Steuerung über `wt.integration`.

### Der Modulname war schon entschieden

Naheliegend wäre `wt3000_integrate.py` gewesen. Zwei Stellen im Bestand hatten
die Frage aber bereits beantwortet: ROADMAP Abschnitt 3 führt
`wt3000_deviceconfig` als geplanten Ort (M2-1), und der Klassenkopf von
`MeasureControl` warnte, ein vorgezogenes eigenes M3-2-Modul erzeuge „genau die
vierte Kopie derselben Parser, die M2-5 verhindern soll". Beides ist befolgt
worden: das Modul trägt den vorgesehenen Namen, nimmt später Averaging und
Frequenzmessquelle auf und schreibt keinen einzigen Parser neu.

### Eine Regel ist dafür eine Schicht tiefer gewandert

`canonical_enum_token()`/`enum_match()` lagen in `wt3000_input` (Layer 2) und
waren damit für ein zweites Fachmodul derselben Schicht unerreichbar —
Geschwisterimporte verbietet `LAYERS` in `tests/test_package_layout.py`.
Gebraucht werden sie, weil das Gerät Kurzformen antwortet (`RES`, `NORM`). Die
Regel steht jetzt in `wt3000_common`; in `wt3000_input` blieb nur die
modulspezifische Kopfentfernung davor, die sich von der in `wt3000_common`
tatsächlich unterscheidet. Ein Stück M2-5, nebenbei erledigt.

### Vier Entscheidungen aus dem Gerätebefund

- **Kurzformen** werden verstanden (`RES` → RESET, `NORM` → NORMAL). Ein
  Treiber, der nur die Langform kennt, fällt am Gerät um.
- **Keine Restzeit aus `:INTEGrate:RTIMe?`** — am Gerät widerlegt.
  `remaining_seconds()` rechnet `TIMer − TIME`; TIME kommt im FLOat-Format als
  Sekundenwert, im Binärpfad war dafür nichts zu ändern.
- **Polling statt UPD-Bit** in `wait_until_finished()`, weil das Bit in 3556
  Proben nicht getragen hat.
- **`:INTEGrate:RESet` ist zusätzlich gesperrt** (`GROUP_RESET`): es verwirft
  den Zählerstand, also den Messwert selbst.

### Bewusst nicht gebaut

Ein Zustandsvorbehalt vor `set_mode()`/`set_timer()` lag nahe, ist aber im
Handbuch nicht belegt. Ein erfundener Vorbehalt blockiert einen womöglich
zulässigen Aufruf; weist das Gerät ihn ab, kommt der Fall über die Fehlerqueue
heraus. Die verbliebenen Vorbehalte sind als Entscheidungen des Treibers
gekennzeichnet, nicht als Aussagen über das Gerät.

### Prüfung

47 neue Prüfsätze, davon 44 in `tests/test_deviceconfig.py`.

```text
pytest: 554 passed
ruff:   All checks passed
mypy:   Success: no issues found in 18 source files
```

**Offen bleibt die Geräteabnahme.** Jedes Kommando dieser Gruppe ist ein
Set-Kommando und hängt an M0-3. Gebaut und gerätefrei durchgespielt ist der
Ablauf vollständig; abgehakt ist M3-2 erst nach einem Lauf am realen Gerät.

---

## 2026-08-21 — Rechenfunktionen (M2-1, Punkt 2 und 3)

### Averaging betrifft jede Messung

Eine Messreihe mit unbemerkt eingeschaltetem Averaging über 64 Zyklen ist eine
andere Messung als dieselbe Reihe ohne. Bisher ließ sich das weder prüfen noch
ändern; die Analyse führt es als Rang 2. Neu ist `ComputationConfig` im selben
Modul wie die Integration: Averaging (ein/aus, Art, Zahl), Wirkungsgradgleichung
η1…η4, Frequenzmessquelle Freq1/Freq2, `SQFormula` und `SYNChronize`, dazu
`capture()`/`restore()`. Erreichbar als `wt.computation`.

Für die zweite Gruppe im Modul ist **keine neue Parserregel** dazugekommen —
genau das war die Absicht hinter dem gemeinsamen Fachmodul.

### Die Abhängigkeiten sind der Grund für das Fachobjekt

- Die **Averaging-Zahl hängt an der Averaging-Art** (`EXPonent` 2…64, `LINear`
  8…256). Deshalb setzt `set_averaging()` beides gemeinsam und in der
  Reihenfolge TYPE → COUNt → STATe; getrennte Setter liefen je nach Reihenfolge
  durch einen Zwischenzustand, den das Gerät ablehnt.
- `U<x>`/`P<x>` werden gegen die **bestückte Elementliste** geprüft.
- `PB` verlangt vier Elemente, `PM` die Motorvariante, `TYPE3` die Option `/G6`.

Der letzte Punkt ist die erste Stelle, an der der Steckbrief aus M1-3 praktisch
genutzt wird: die Fassade reicht `has_option("G6")` und `is_motor_model` in das
Fachmodul, das selbst kein `DeviceInfo` kennt. Die Regel „unbekannt ist nicht
dasselbe wie fehlt" gilt dabei weiter.

### Nebenwirkung: ein Kommentar wurde nachprüfbar

`build_standard_profile()` behauptete, die Frequenzmessquelle stehe auf U3/I3 —
nachprüfen ließ sich das nicht. `wt.computation.frequency_item(1)` liefert die
tatsächliche Einstellung.

### Bewusst offen

`:MEASure:FUNCtion<x>` (Ausdruck als Zeichenkette — eigene kleine Sprache),
`:PC`, `:DMeasure`, `:COMPensation`, `:PHASe`, `:SAMPling`, `:MHOLd`. Alle im
Modulkopf einzeln benannt und begründet.

### Prüfung

37 neue Prüfsätze in `tests/test_computation.py`.

```text
pytest: 591 passed
ruff:   All checks passed
mypy:   Success: no issues found in 18 source files
```

Auch hier steht die **Geräteabnahme aus** (M0-3).

---

## Weitere bereits erledigte Infrastruktur

- `.gitattributes` führt Textdateien einheitlich mit LF und schützt die DLL als binär.
- Das Projekt besitzt ein `src`-Layout und ein installierbares `pyproject.toml`.
- Die Schichtung wird in `tests/test_package_layout.py` anhand der Importe geprüft.
- Die virtuelle Projektumgebung enthält die benötigten Prüfwerkzeuge.

---

## Aktueller Abnahmestand

Am 2026-08-20 wurde der unveränderte Quellstand erneut geprüft:

```text
pytest: 282 passed
ruff:   All checks passed
mypy:   Success: no issues found in 17 source files
```

Die verbleibenden Hardwarefragen, Entwurfsentscheidungen und Arbeiten sind in
[OFFENE_PUNKTE.md](OFFENE_PUNKTE.md) zusammengeführt. Die Reihenfolge und
Abhängigkeiten stehen in [ROADMAP.md](ROADMAP.md).
