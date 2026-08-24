# wt3000-scpi

SCPI-Treiber für das **Yokogawa WT3000** Präzisions-Leistungsmessgerät, angebunden über
die Yokogawa-TMCTL-DLL per Ethernet.

Python ≥ 3.10 · keine Laufzeitabhängigkeiten · Version 0.3.0 · **experimentell**

---

## Was der Treiber kann

| | Stand |
|---|---|
| **Messwerte lesen** — `:NUMeric`, Item-Tabelle, Binärblöcke im FLOat-Format | vollständig |
| **Messkonfiguration lesen und einstellen** — Verdrahtung, Bereiche, Auto-Range, Crest, Filter, Skalierung, Sync, Modus, Update-Rate; Snapshot mit `capture/save/load/diff/restore` | weitgehend |
| **Messung aufzeichnen** — blockierende Schleife mit HOLD-Anker, Zeitstempel und CSV | einfach, aber tragfähig |
| **Gerätekonfiguration jenseits von `:INPut`** — Averaging, Integration, Oberschwingungen, Setup-Speicher | fehlt |
| **Steuerbare Messung** — `start()`/`stop()`, Gerätesteuerung, Taktung am Gerät | fehlt |
| **Austauschbarer Export** — andere Formate als CSV, Einheiten an den Daten | fehlt |

Was noch fehlt und in welcher Reihenfolge es entsteht, steht in [ROADMAP.md](docs/ROADMAP.md).

> **Zum Reifegrad.** Dieser Treiber steuert ein eingemessenes Messgerät. Er ist auf
> Vorsicht ausgelegt, nicht auf Bequemlichkeit: schreibende Zugriffe sind doppelt
> gesperrt, jede Änderung wird vorher gesichert, zurückgelesen und beim Verlassen
> wiederhergestellt. Einige Annahmen über das Geräteverhalten sind noch nicht am Gerät
> belegt — sie sind im Quelltext durchgängig mit `ZU VERIFIZIEREN` markiert und in
> [ROADMAP.md](docs/ROADMAP.md) unter **M0** gesammelt.

---

## Installation

**Python 3.10 oder neuer wird zwingend gebraucht.** Das Paket benutzt
Laufzeit-Typaliase der Form `bytes | str`; unter Python 3.9 scheitert bereits der Import
mit `TypeError: unsupported operand type(s) for |`. Auf macOS genügt das mitgelieferte
System-Python (3.9) also **nicht** — der empfohlene Weg ist eine eigene Umgebung:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[test]"
```

Für den Betrieb am Gerät zusätzlich nötig:

* **Windows** — der Transport benutzt `ctypes.WinDLL`
* **`tmctl64.dll`** aus dem Yokogawa-TMCTL-Paket, samt der DLLs im selben Verzeichnis
* Netzwerkverbindung zum Gerät; Benutzername und Passwort wie am Gerät eingestellt
* eine `wt3000.json` mit den Verbindungsparametern — siehe
  [Verbindungsparameter](#verbindungsparameter)

**Für Entwicklung und Tests wird nichts davon gebraucht.** Die gesamte Testsuite läuft
ohne Gerät und ohne DLL — der Import des Pakets setzt nichts voraus, weil die DLL erst
beim Instanziieren des Transports geladen wird.

```bash
python tools_import_check.py    # Smoke-Check: lässt sich das Paket importieren?
pytest                          # die eigentliche Prüfung
```

### Prüfwerkzeuge

`pip install -e ".[dev]"` bringt zusätzlich `ruff`, `mypy` und `pyflakes`. Beide
konfigurierten Werkzeuge laufen ohne Argumente und sind **heute vollständig grün**:

```bash
ruff check .    # Stil und ungenutzte Namen (E/F/W, Zeilenlänge 100)
mypy            # Typprüfung über src/, Zielplattform Windows
pytest          # 241 Fälle, unter einer Sekunde
```

Die Einstellungen stehen in [pyproject.toml](pyproject.toml), jeweils mit Begründung —
insbesondere, welche weiteren `ruff`-Regelfamilien Kandidaten sind und warum sie einen
eigenen Schritt verdienen.

---

## Schnelleinstieg

Der einzige Einstiegspunkt ist die Fassade `WT3000`. Sie stellt Transport, Sitzung und
alle Fachobjekte fertig verdrahtet bereit:

```python
from wt3000_scpi import WT3000, Quantity

with WT3000.connect(ip="192.168.10.20") as wt:
    wt.check_protocol_state()          # :COMMunicate:HEADer 0, :NUMeric:FORMat FLOat
    wt.device.log_summary()            # Modell, Verdrahtung, Module, Wiring-Units

    print(wt.input.get_wiring())                     # ('V3A3', 'P1W2')
    print(wt.ranges.get_range(Quantity.VOLTAGE, 1))  # eingestellter Bereich Element 1
    print(wt.measure.read_mapped())                  # {'U1': ..., 'I1': ..., 'P1': ...}
```

Beim Verlassen des `with`-Blocks schaltet die Fassade HOLD ab, gibt das Bedienfeld
wieder frei und schließt die Verbindung — auch bei einem Fehler oder Strg+C.

### Verbindungsparameter

Im Quelltext steht **keine** IP, kein Benutzername, kein Passwort und kein
rechnerspezifischer DLL-Pfad. Woher diese Werte kommen, entscheidet eine Kette mit vier
Stufen — die erste, die etwas liefert, gewinnt, und zwar **je Feld einzeln**:

| Rang | Quelle | Beispiel |
|------|--------|----------|
| 1 | ausdrücklicher Parameter | `WT3000.connect(ip="10.0.0.5")` |
| 2 | Umgebungsvariable | `WT3000_IP=10.0.0.5` |
| 3 | Konfigurationsdatei | `wt3000.json` → `{"ip": "10.0.0.5"}` |
| 4 | Voreinstellung der Klasse | neutral, nicht verbindungsfähig |

**Der übliche Weg** ist die Konfigurationsdatei: [wt3000.example.json](wt3000.example.json)
nach `wt3000.json` kopieren und anpassen. Die Datei ist per `.gitignore` ausgeschlossen —
Zugangsdaten gehören nicht in die Versionsverwaltung. Gesucht wird sie in dieser
Reihenfolge: `WT3000_CONFIG`, dann `./wt3000.json`, dann `~/wt3000.json`.

```json
{
  "ip": "192.168.10.20",
  "user": "TEST",
  "password": "1",
  "dll_path": "tmctl64.dll"
}
```

Die Umgebungsvariablen heißen wie das Feld in Großschrift mit Präfix: `WT3000_IP`,
`WT3000_DLL_PATH`, `WT3000_USER`, `WT3000_PASSWORD`, `WT3000_TIMEOUT_MS`,
`WT3000_USE_REMOTE`. Ein leerer Wert zählt nicht als Angabe.

**`dll_path`** darf ein voller Pfad sein oder ein bloßer Dateiname. Bei einem bloßen
Namen (`tmctl64.dll`, die Voreinstellung) sucht Windows selbst in `PATH` und im
Anwendungsverzeichnis — der übliche Fall bei installierter TMCTL. Ein angegebener *Pfad*
muss dagegen existieren, sonst bricht der Verbindungsaufbau mit einer Meldung ab, die
alle drei Wege zur Abhilfe nennt.

Einzeln überschreiben geht weiterhin:

```python
wt = WT3000.connect(ip="192.168.10.20", timeout_ms=5000)
```

Wer alles selbst setzen will, baut eine `WTConfig` und benutzt `from_config()` —
`WTConfig.from_environment()` liefert dabei die aufgelöste Grundlage. Für Tests
und für spätere Transporte ohne TMCTL gibt es `from_transport()`:

```python
from wt3000_scpi import WT3000, WTConfig, FakeTransport

responses = {
    "*IDN": "YOKOGAWA,WT3000,C1B234567,F2.11",
    ":INPUT:WIRING": "V3A3,P1W2",
    ":INPUT:MODULE": "30,30,30,30",
    # ... je Kommando ein Eintrag; was fehlt, faellt als KeyError auf
}
wt = WT3000.from_transport(FakeTransport(responses), WTConfig(use_remote=False))
```

---

## Sicherheitskonzept

Das Gerät ist eingemessen. Der Treiber geht deshalb davon aus, dass **nichts geschrieben
werden soll**, solange es nicht ausdrücklich verlangt wird.

**Zwei unabhängige Schlösser**, beide in der Voreinstellung zu:

```python
WT3000.connect()                                     # liest, schreibt nichts
WT3000.connect(read_only=False, allow_changes=True)  # darf schreiben
```

| Schloss | Wirkung |
|---|---|
| `read_only=True` | die Sitzung lehnt **jedes** Kommando ab, das kein Query ist — noch vor dem Senden |
| `allow_changes=False` | `InputConfig`, `RangeAccess` und `ItemAccess` lehnen jeden Schreibaufruf schon in der eigenen Methode ab |

**Eine dritte Sperre für den eingemessenen Zustand.** Auch mit `allow_changes=True`
bleiben die Gruppen `WIRING`, `RANGE`, `SCALING` und `CFACTOR` gesperrt. Sie müssen
einzeln und benannt freigegeben werden:

```python
from wt3000_scpi.wt3000_input import GROUP_RANGE

with wt.input.unlocked(GROUP_RANGE):
    wt.input.set_voltage_range(600.0, target=4)
```

**Jede Änderung ist umkehrbar.** Für Bereiche und Item-Tabelle gibt es je einen Context
Manager, der sichert, eine Schreibprobe an einem einzigen Wert fährt, anwendet,
zurückliest und beim Verlassen den Ausgangszustand wiederherstellt — im `finally`, also
auch bei Strg+C:

```python
from pathlib import Path

from wt3000_scpi import Quantity
from wt3000_scpi.wt3000_ranging import RangePlan, RangeSpec

plan = RangePlan.of(RangeSpec(Quantity.VOLTAGE, scope=4, value=600.0))

with wt.applied_ranges(plan, backup_file=Path("konfiguration/backup.json")) as report:
    ...                       # hier messen
# ab hier stehen die Bereiche wieder wie vorher
```

Für die Item-Tabelle heißt dasselbe `wt.items.applied(specs)`.

Misslingt die Wiederherstellung, kommt das als Ausnahme heraus und nicht nur ins
Protokoll. Ein Block, den man ohne Fehler verlässt, hat den Ausgangszustand also
tatsächlich zurückgestellt — geprüft wird das nach dem Zurückschreiben durch eine
Gegenprobe am Gerät.

---

## Aufbau

Sechs Schichten, Importrichtung ausnahmslos nach unten. Die Richtung ist nicht nur
dokumentiert, sondern wird von [tests/test_package_layout.py](tests/test_package_layout.py)
per `ast` erzwungen — bei jedem neuen Modul ist die `LAYERS`-Tabelle dort mitzuführen.

```
Layer 0   wt3000_transport   Protocol 'Transport', TmctlTransport, FakeTransport, WTConfig
Layer 1   wt3000_core        WTSession: Query-Regeln, Blockdaten, Fehlerqueue, Nur-Lesen-Sperre
          wt3000_common      Scope-Regeln, Antwortparser, setup_logging
Layer 2   wt3000_numeric     ':NUMeric', Item-Tabelle, FLOat-Blockparser
          wt3000_rangeio     ':INPut'-Bereichsknoten
          wt3000_input       übrige ':INPut'-Stellgrößen
          wt3000_deviceconfig  ':INTEGrate' — Wh-/Ah-Messung steuern
Layer 3   wt3000_itemspec    Ablauf um die Item-Tabelle
          wt3000_ranging     Ablauf um die Messbereiche
          wt3000_measure     Messschleife und CSV
Layer 4   wt3000_device      Fassade WT3000  ← der Einstiegspunkt
          stage2..stage5b    ausführbare Stufenskripte
```

**Der Transport ist austauschbar.** `Transport` ist ein `typing.Protocol` mit fünf
Methoden — `write/read/query/set_timeout/close`. `TmctlTransport` spricht die
Yokogawa-DLL, `FakeTransport` beantwortet Kommandos aus einer Tabelle. Ein
Socket- oder VISA-Transport dockt an derselben Stelle an; die Fugen sind am Ende von
[wt3000_transport.py](src/wt3000_scpi/wt3000_transport.py) beschrieben.

---

## Die Stufenskripte

Vor der Fassade waren sie der einzige Weg, den Treiber zu benutzen. Sie bleiben als
Beispiele bestehen — und als die einzigen Abläufe, die am realen Gerät erprobt sind.
Sie werden als Modul gestartet, nicht als Datei (paketrelative Importe):

```bash
python -m wt3000_scpi.stage2_read_numeric
```

| Stufe | Zweck | schreibt |
|---|---|---|
| `stage2_read_numeric` | Messwerte gegen die **vorhandene** Item-Tabelle lesen | nichts |
| `stage3_own_itemtable` | eigene Item-Tabelle setzen, Werte lesen, Namen zuordnen, zurückstellen | Item-Tabelle |
| `stage4_measure` | Messschleife mit HOLD-Anker, Zeitstempel, CSV und Metadaten-Sidecar | Item-Tabelle, `:NUMeric:HOLD` |
| `stage5_input_config` | Eingangskonfiguration erfassen und als JSON sichern | nichts |
| `stage5b_range_probe` | offene Fragen zur Bereichseinstellung klären, mit Nulleffekt-Schreibprobe | ein Kommando, abschaltbar |

Laufparameter stehen jeweils als Konstanten am Kopf der Datei. Ausgaben landen in
`messungen/` (Stufe 4) bzw. `konfiguration/` (Stufe 5 und 5b), Stufe 2 und 3 schreiben
ins aktuelle Verzeichnis. Alle drei Ziele sind über `.gitignore` ausgenommen.

Für PyCharm liegen fertige Startkonfigurationen unter [.run/](.run).

---

## Tests

```bash
pytest                                  # 241 Tests, unter einer Sekunde
pytest tests/test_device_facade.py -v   # nur die Fassade
```

Die Suite braucht **kein Gerät und keine `tmctl.dll`**. Sie setzt auf `FakeTransport`
auf, damit auch die Schichten mitlaufen, die sonst nur am Gerät zu prüfen wären:
Query-Regeln, Zusammenbau von Blockdaten über mehrere Lesevorgänge, Fehlerqueue,
Nur-Lesen-Sperre und die vollständige Wiederherstellung nach einem Abbruch.

---

## Dokumentation

| Datei | Inhalt |
|---|---|
| [ROADMAP.md](docs/ROADMAP.md) | Zielbild, Meilensteine M0–M5, Zielarchitektur, Abhängigkeiten |
| [AENDERUNGEN_2026-08-18.md](docs/AENDERUNGEN_2026-08-18.md) | Fehlerprüfung: Änderungen F-01…F-09, offene Befunde B-01…B-15 |
| [AENDERUNGEN_2026-08-19_M1-1.md](docs/AENDERUNGEN_2026-08-19_M1-1.md) | Fassade `WT3000`: Umsetzung, Erkenntnisse, was bewusst offen blieb |
| [WT3000_Commands_Overview.md](docs/WT3000_Commands_Overview.md) | Kurzübersicht der SCPI-Kommandogruppen des Geräts |

Referenz für alles Gerätebezogene ist das Handbuch **IM WT3001E-17EN**. Jede
SCPI-Eigenheit im Quelltext nennt die Fundstelle oder ist als offene Frage markiert.

---

## Bekannte Einschränkungen

* **Windows-gebunden** am Gerät — `TmctlTransport` braucht `ctypes.WinDLL`. Ein
  plattformunabhängiger Socket-Transport ist vorgesehen, aber nicht gebaut.
* **`WTConfig.dll_path`** zeigt in der Voreinstellung auf einen Pfad des
  Entwicklungsrechners. Über `WT3000.connect(dll_path=...)` überschreibbar (Befund B-08).
* **Zwei Schreibwege auf dieselben Bereichsknoten** mit unterschiedlicher
  Parametersyntax — `wt3000_rangeio` sendet `1000`, `wt3000_input` sendet `1000V`.
  Höchstens eine Form kann richtig sein; zu klären am Gerät (Befund B-01, ROADMAP M0-1).
* **Die Messschleife blockiert** und bricht nur über Strg+C oder ein gesetztes Limit ab
  (ROADMAP M3-1).
* **Vier Elemente und 30-A-Module** stecken an einigen Stellen noch als Konstante im
  Code. Die Fassade liest die Bestückung inzwischen, gibt sie aber erst an `RangeAccess`
  weiter, nicht an `InputConfig` (ROADMAP M1-3, Befund B-12).
