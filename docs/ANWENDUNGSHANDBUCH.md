# Anwendungshandbuch des Python-Treibers `wt3000-scpi`

Stand: 20. August 2026, Paketversion `0.3.0` (experimentell)

Dieses Handbuch beschreibt die im aktuellen Repository bereits verfügbaren, für die
Benutzung des Yokogawa WT3000 wichtigen Python-Schnittstellen. Die Gliederung folgt
dem Schema **Funktionsart → Datei → Klasse/Funktion/Methode**. Interne Hilfsfunktionen
wie Parser mit führendem Unterstrich und die ausführbaren Entwicklungsstufen
`stage2_...` bis `stage5b_...` sind nicht Teil der Anwender-API und werden deshalb
nicht einzeln aufgeführt.

> **Wichtiger Reifegrad-Hinweis:** Der Treiber ist noch experimentell. Messwerte,
> Eingangsparameter, Bereiche und Item-Tabellen sind bereits zugänglich. Eine
> steuerbare Messung mit `start()`/`stop()`, Integrationsmessungen, Averaging,
> Oberschwingungskonfiguration und Gerätesetups sind noch nicht implementiert.

## Inhaltsübersicht

1. Schnellstart und Sicherheitsmodell
2. Verbindung aufbauen und beenden
3. Geräteinformationen und Protokollzustand
4. Eingangs- und Messkonfiguration lesen
5. Eingangs- und Messkonfiguration einstellen
6. Gesamte Eingangskonfiguration sichern und vergleichen
7. Messbereiche lesen und direkt setzen
8. Messbereiche geplant, geprüft und reversibel einstellen
9. Item-Tabelle auswählen, sichern und ändern
10. Einzelne Messwerte lesen
11. Messreihen aufzeichnen
12. Eigene Item-Tabelle und Messreihe kombiniert verwenden
13. Fehlerklassen
14. Transport, Tests und allgemeine Hilfsfunktionen
15. Noch nicht verfügbare Gerätefunktionen

## 1. Schnellstart und Sicherheitsmodell

Der normale Einstiegspunkt ist `WT3000` aus der Paketwurzel. Eine Sitzung sollte als
Context Manager geöffnet werden, damit HOLD, Fernsteuerung und Transport auch nach
einem Fehler sauber beendet werden.

```python
from wt3000_scpi import WT3000, Quantity

with WT3000.connect(ip="192.168.10.20") as wt:
    wt.check_protocol_state()
    print(wt.device.describe())
    print(wt.ranges.get_range(Quantity.VOLTAGE, 1))
    print(wt.measure.read_mapped())
```

Schreibzugriffe besitzen mehrere Sicherungen:

1. `read_only=True` verhindert auf Sitzungsebene jedes Nicht-Query-Kommando.
2. `allow_changes=False` verhindert Schreibzugriffe über `InputConfig`,
   `RangeAccess` und `ItemAccess`.
3. Kritische Eingangsgruppen bleiben zusätzlich gesperrt und müssen mit
   `wt.input.unlocked(...)` vorübergehend freigegeben werden.

```python
from wt3000_scpi import WT3000
from wt3000_scpi.wt3000_input import GROUP_RANGE

with WT3000.connect(read_only=False, allow_changes=True) as wt:
    with wt.input.unlocked(GROUP_RANGE):
        wt.input.set_voltage_range(600.0, target=4)
```

Zulässige Ziele für elementbezogene Einstellungen sind die Elementnummern `1` bis
`4` sowie `"ALL"`, `"SIGMA"` und `"SIGMB"`. Ob ein SIGMA-Ziel existiert und welche
Elemente dazugehören, hängt von der Verdrahtung ab.

## 2. Verbindung aufbauen und beenden

### Datei `wt3000_device.py`

#### Klasse `WT3000`

| Methode/Eigenschaft | Bedeutung |
|---|---|
| `WT3000.connect(...)` | Verbindet über die TMCTL-DLL. Nicht angegebene Werte werden über `WTConfig.from_environment()` aufgelöst. |
| `WT3000.from_config(config, ...)` | Verbindet mit einer vollständig aufgebauten `WTConfig`; die Fassade besitzt und schließt den Transport. |
| `WT3000.from_transport(transport, config=None, ...)` | Verwendet einen vorhandenen alternativen Transport, beispielsweise für Tests. Standardmäßig bleibt dessen Schließen beim Aufrufer. |
| `wt.config` | Liefert die tatsächlich verwendete `WTConfig`. |
| `wt.session` | Liefert die Protokollsitzung als Notausgang für noch nicht gekapselte SCPI-Kommandos. |
| `wt.read_only` | Zeigt an, ob Nicht-Query-Kommandos gesperrt sind. |
| `wt.allow_changes` | Zeigt an, ob die Fachobjekte Änderungen zulassen. |
| `wt.close()` | Schaltet HOLD und Fernsteuerung ab und schließt einen eigenen Transport. Mehrfachaufrufe sind erlaubt. |

```python
from wt3000_scpi import WT3000, WTConfig

config = WTConfig.from_environment(timeout_ms=8000)
with WT3000.from_config(config) as wt:
    print(wt.config.describe())
```

Die wichtigsten Parameter von `connect()` sind `ip`, `dll_path`, `timeout_ms`,
`use_remote`, `read_only` und `allow_changes`. Für Schreibzugriff müssen
`read_only=False` und `allow_changes=True` gemeinsam gesetzt werden.

### Datei `wt3000_transport.py`

#### Klasse `WTConfig`

| Feld/Methode | Bedeutung |
|---|---|
| `dll_path` | DLL-Pfad oder DLL-Dateiname; Standard `tmctl64.dll`. |
| `ip` | IP-Adresse des Messgeräts. |
| `user`, `password` | Optionale Zugangsdaten. |
| `timeout_ms` | Kommunikationstimeout; Standard `5000`. |
| `drain_timeout_ms` | Kurzer Timeout zum Abräumen verspäteter Antworten; Standard `500`. |
| `read_buffer_size` | Lesepuffergröße; Standard `65536`. |
| `use_remote` | Aktiviert bei schreibbarer Sitzung die Geräte-Fernsteuerung; Standard `True`. |
| `WTConfig.from_environment(config_file=None, **overrides)` | Löst Werte in der Reihenfolge Parameter, Umgebungsvariable, JSON-Datei, Klassenstandard auf. |
| `config.with_values(**overrides)` | Erzeugt eine geänderte Kopie; `None` lässt einen Wert unverändert. |
| `config.describe()` | Liefert eine protokollierbare Kurzbeschreibung ohne Passwort. |

```python
from wt3000_scpi import WTConfig

config = WTConfig.from_environment(ip="192.168.10.20")
slower = config.with_values(timeout_ms=10000)
print(slower.describe())
```

Die zugehörigen freien Funktionen sind:

| Funktion | Verwendung |
|---|---|
| `config_search_paths(config_file=None)` | Listet alle geprüften Orte für `wt3000.json`. |
| `config_file_in_use(config_file=None)` | Liefert die tatsächlich verwendete Konfigurationsdatei oder `None`. |
| `resolve_dll_path(dll_path)` | Prüft beziehungsweise löst eine DLL-Pfadangabe auf. Normalerweise intern durch den Transport benutzt. |

```python
from wt3000_scpi.wt3000_transport import config_file_in_use, config_search_paths

print(config_search_paths())
print(config_file_in_use())
```

### Datei `wt3000_core.py`

#### Klasse `WTSession`

Diese Methoden sind die niedrigste sinnvolle Anwenderschnittstelle. Die Fassade und
Fachobjekte sind vorzuziehen; direkte SCPI-Zugriffe sind für noch nicht gekapselte
Gerätefunktionen vorgesehen.

| Methode | Bedeutung |
|---|---|
| `write(command)` | Sendet genau ein Set-Kommando; in Nur-Lesen-Sitzungen gesperrt. |
| `query(command)` | Sendet genau ein Query und liefert bereinigten Text. |
| `query_raw(command)` | Wie `query()`, aber mit unveränderten Bytes. |
| `query_block(command)` | Liest eine SCPI-Blockantwort und liefert deren Nutzdaten. |
| `enable_remote()` / `disable_remote()` | Schaltet die Geräte-Fernsteuerung ein beziehungsweise aus. |
| `read_error_queue(max_entries=20)` | Liest und leert die Gerätefehlerqueue bis zum Null-Eintrag. |
| `assert_no_error(context)` | Löst bei Gerätefehlern `DeviceError` aus. |
| `drain_after_failure()` | Räumt eine verspätete Antwort nach fehlgeschlagenem Query ab. |

```python
with WT3000.connect() as wt:
    identity = wt.session.query("*IDN?")
    errors = wt.session.read_error_queue()
    print(identity, errors)
```

`WTSession` ist aktuell nicht threadsicher. Während eines Zugriffs darf dieselbe
Sitzung daher nicht gleichzeitig aus mehreren Threads verwendet werden.

## 3. Geräteinformationen und Protokollzustand

### Datei `wt3000_device.py`

#### Klasse `DeviceInfo`

`wt.device` wird beim Verbindungsaufbau ermittelt. Verfügbare Felder sind
`identity`, `manufacturer`, `model`, `serial`, `firmware`, `wiring`, `wiring_units`,
`modules`, `elements`, `sigma_members` und `elements_assumed`.

| Methode | Bedeutung |
|---|---|
| `DeviceInfo.read(session, previous=None)` | Liest den Gerätesteckbrief über reine Queries. Die Fassade ruft dies automatisch auf. Mit `previous` werden Identität und Optionen übernommen statt erneut gefragt. |
| `describe()` | Liefert den Steckbrief als Liste lesbarer Textzeilen. |
| `log_summary()` | Schreibt den Steckbrief in das Python-Log. |
| `has_element(element)` | Prüft, ob ein Element bestückt ist. |
| `WT3000.refresh_device()` | Liest Verdrahtung, Module und Elementliste neu und zieht `wt.input` und `wt.ranges` nach. |
| `WT3000.protocol_state()` | Ist-Zustand von Header, Verbose und Zahlenformat. Verändert nichts. |
| `WT3000.ensured_protocol_state()` | Context Manager: stellt den Sollzustand her und nimmt ihn beim Verlassen zurück. |

**Nach einer Umverdrahtung.** Der Steckbrief trägt die Elementliste *und* die Zuordnung
der Wiring-Units; aus ihm sind `wt.input` und `wt.ranges` verdrahtet. Ändert sich die
Verdrahtung, ändert sich beides:

* über `wt.input.set_wiring()` — die Fassade frischt **selbsttätig** auf, es ist nichts
  zu tun;
* am Bedienfeld oder durch eine zweite Sitzung — dann ist `wt.refresh_device()`
  aufzurufen, denn davon kann der Treiber nichts erfahren haben.

Ohne Auffrischung löst `wt.ranges.expand_scope("SIGMA")` weiterhin auf die Elemente der
alten Verdrahtung auf — fehlerfrei, plausibel und falsch. Die Fachobjekte werden dabei
an Ort und Stelle nachgezogen: eine in einer Variablen gehaltene Referenz auf
`wt.ranges` bleibt gültig und trägt danach den neuen Stand.

```python
with WT3000.connect() as wt:
    print("Firmware:", wt.device.firmware)
    print("Element 4 vorhanden:", wt.device.has_element(4))
    for line in wt.device.describe():
        print(line)
```

#### Klasse `WT3000`: Diagnosemethoden

| Methode | Bedeutung |
|---|---|
| `check_protocol_state()` | Prüft, ob Header ausgeschaltet und das NUMeric-Format `FLOat` ist; verändert nichts. |
| `log_condition()` | Liest `:STATus:CONDition?`, protokolliert auffällige Bits und liefert die Bitmaske. |

```python
with WT3000.connect() as wt:
    wt.check_protocol_state()
    condition_bits = wt.log_condition()
    print(f"Condition: 0x{condition_bits:04X}")
```

## 4. Eingangs- und Messkonfiguration lesen

### Datei `wt3000_device.py`

#### Eigenschaft `WT3000.input`

`wt.input` liefert eine fertig mit der Sitzung verbundene Instanz der Klasse
`InputConfig` aus `wt3000_input.py`.

### Datei `wt3000_input.py`

#### Klasse `InputConfig`: Lesemethoden

| Methode | Ergebnis |
|---|---|
| `get_crest_factor()` | Crest-Faktor `3` oder `6`. |
| `get_wiring()` | Verdrahtungsmuster als Tupel, z. B. `("V3A3", "P1W2")`. |
| `get_wiring_units()` | Liste von `WiringUnit` mit Name, Muster und Elementen. |
| `get_independent()` | Ob Elemente unabhängig eingestellt werden. |
| `get_module(element)` | Elementtyp `30`, `2` oder `0` für nicht bestückt. |
| `get_modules()` | Alle Elementtypen als `{Element: Typ}`. |
| `get_update_rate()` | Geräte-Aktualisierungsintervall in Sekunden. |
| `get_voltage_range(element)` | Spannungsbereich in Volt. |
| `get_current_range(element)` | Tupel `(Direktbereich_A, Sensorbereich_V)`; einer der Werte ist üblicherweise `None`. |
| `get_voltage_auto(element)` | Zustand der Spannungs-Autorange. |
| `get_current_auto(element)` | Zustand der Strom-Autorange. |
| `get_voltage_mode(element)` | Spannungsmodus `RMS`, `MEAN`, `DC` oder `RMEAN`. |
| `get_current_mode(element)` | Strommodus `RMS`, `MEAN`, `DC` oder `RMEAN`. |
| `get_line_filter(element)` | `"OFF"` oder Grenzfrequenz als Text in Hz. |
| `get_frequency_filter(element)` | Zustand des Filters im Synchronisationspfad. |
| `get_scaling_state(element)` | Ob Skalierung aktiv ist. |
| `get_vt_ratio(element)` | Spannungswandlerverhältnis. |
| `get_ct_ratio(element)` | Stromwandlerverhältnis. |
| `get_power_factor(element)` | Leistungsskalierungsfaktor `SFACtor`. |
| `get_sensor_ratio(element)` | Sensorkonstante des externen Stromsensors in mV/A. |
| `get_sync_source(element)` | Synchronisationsquelle, z. B. `U3`, `I3`, `EXT` oder `NONE`. |
| `get_raw_input_dump()` | Unverarbeitete Antwort auf `:INPut?`. |

```python
with WT3000.connect() as wt:
    cfg = wt.input
    print("Wiring:", cfg.get_wiring())
    print("Rate:", cfg.get_update_rate(), "s")
    for element in wt.device.elements:
        print(element, cfg.get_voltage_range(element), cfg.get_current_range(element))
```

#### Klasse `WiringUnit`

Ein `WiringUnit`-Objekt besitzt die Felder `name`, `pattern` und `elements`.
Die freie Funktion `resolve_wiring_units(patterns)` bildet eine Musterliste auf diese
Objekte ab.

```python
from wt3000_scpi.wt3000_input import resolve_wiring_units

units = resolve_wiring_units(["V3A3", "P1W2"])
print(units[0].name, units[0].elements)  # SIGMA (1, 2, 3)
```

## 5. Eingangs- und Messkonfiguration einstellen

### Datei `wt3000_input.py`

#### Klasse `InputConfig`: Schreibfreigabe

| Eigenschaft/Methode | Bedeutung |
|---|---|
| `protected_groups` | Momentan gesperrte Gruppen. |
| `unlocked(*groups)` | Context Manager zur zeitlich begrenzten Freigabe benannter Gruppen. |

Die Gruppenkonstanten heißen `GROUP_WIRING`, `GROUP_RANGE`, `GROUP_AUTO`,
`GROUP_CFACTOR`, `GROUP_FILTER`, `GROUP_SCALING`, `GROUP_SYNC`, `GROUP_MODE` und
`GROUP_RATE`. Standardmäßig zusätzlich geschützt sind Verdrahtung, Bereiche,
Skalierung und Crest-Faktor.

```python
from wt3000_scpi.wt3000_input import GROUP_FILTER, GROUP_RATE

with WT3000.connect(read_only=False, allow_changes=True) as wt:
    with wt.input.unlocked(GROUP_FILTER, GROUP_RATE):
        wt.input.set_line_filter(500, target=1)
        wt.input.set_update_rate(0.5)
```

#### Verdrahtung und Crest-Faktor

| Methode | Bedeutung |
|---|---|
| `set_wiring(patterns)` | Setzt Muster wie `Wiring.V3A3`, `Wiring.P1W2`, `P1W3`, `P3W3`, `P3W4` oder `NONE`. |
| `set_crest_factor(factor)` | Setzt Crest-Faktor `3` oder `6`; danach können andere zulässige Bereiche gelten. |

```python
from wt3000_scpi import Wiring
from wt3000_scpi.wt3000_input import GROUP_CFACTOR, GROUP_WIRING

with wt.input.unlocked(GROUP_WIRING, GROUP_CFACTOR):
    wt.input.set_wiring([Wiring.V3A3, Wiring.P1W2])
    wt.input.set_crest_factor(3)
```

#### Bereiche und Autorange

| Methode | Bedeutung |
|---|---|
| `set_voltage_range(volts, target="ALL")` | Setzt einen zulässigen festen Spannungsbereich. |
| `set_current_range(amps, target="ALL")` | Setzt den direkten Strombereich; gemischte Elementtypen müssen elementweise gesetzt werden. |
| `set_current_range_sensor(volts, target="ALL")` | Setzt den Spannungsbereich des externen Stromsensoreingangs. |
| `set_voltage_auto_range(enabled, target="ALL")` | Schaltet Spannungs-Autorange. |
| `set_current_auto_range(enabled, target="ALL")` | Schaltet Strom-Autorange. |

```python
from wt3000_scpi.wt3000_input import GROUP_AUTO, GROUP_RANGE

with wt.input.unlocked(GROUP_RANGE, GROUP_AUTO):
    wt.input.set_voltage_range(600.0, target="SIGMA")
    wt.input.set_current_range_sensor(10.0, target=1)
    wt.input.set_voltage_auto_range(False, target="SIGMA")
```

Die zulässigen Bereichsstufen hängen vom Crest-Faktor und beim direkten Strom vom
Elementtyp ab. Die Setter prüfen die Werte vor dem Senden und lesen sie anschließend
zur Verifikation zurück. Obwohl der Docstring von `set_voltage_range()` ein Abschalten
von Autorange ankündigt, sendet der aktuelle Methodenrumpf selbst kein `AUTO OFF`;
falls ein fester Bereich benötigt wird, muss Autorange daher zusätzlich über den
passenden Setter ausgeschaltet werden. `RangePlan` erledigt diese Reihenfolge
automatisch.

#### Filter

| Methode | Bedeutung |
|---|---|
| `set_line_filter(value, target="ALL")` | Setzt `LineFilter.OFF`, `HZ500`, `KHZ5P5`, `KHZ50` oder alternativ `"OFF"`, `500`, `5500`, `50000`. |
| `set_frequency_filter(enabled, target="ALL")` | Schaltet den Filter im Synchronisationspfad. |

```python
from wt3000_scpi import LineFilter
from wt3000_scpi.wt3000_input import GROUP_FILTER

with wt.input.unlocked(GROUP_FILTER):
    wt.input.set_line_filter(LineFilter.HZ500, target=3)
    wt.input.set_frequency_filter(True, target=3)
```

#### Skalierung und externe Sensoren

| Methode | Bedeutung |
|---|---|
| `set_scaling_state(enabled, target="ALL")` | Schaltet VT-/CT-/Leistungsskalierung. |
| `set_vt_ratio(ratio, target="ALL")` | Setzt das Spannungswandlerverhältnis. |
| `set_ct_ratio(ratio, target="ALL")` | Setzt das Stromwandlerverhältnis. |
| `set_power_factor(factor, target="ALL")` | Setzt `SFACtor`. |
| `set_sensor_ratio(ratio, target="ALL")` | Setzt die Sensorkonstante in mV/A. |

Zulässiger Wertebereich der vier Faktoren: `0.0001` bis `99999.9999`.

```python
from wt3000_scpi.wt3000_input import GROUP_SCALING

with wt.input.unlocked(GROUP_SCALING):
    wt.input.set_vt_ratio(100.0, target=1)
    wt.input.set_ct_ratio(2000.0, target=1)
    wt.input.set_scaling_state(True, target=1)
```

#### Synchronisation, Messmodus und Aktualisierungsrate

| Methode | Bedeutung |
|---|---|
| `set_sync_source(source, target="ALL")` | Setzt `U1..U4`, `I1..I4`, `SyncSource.EXTERNAL` oder `NONE`. |
| `set_voltage_mode(mode, target="ALL")` | Setzt `RMS`, `MEAN`, `DC` oder `RMEAN` für Spannung. |
| `set_current_mode(mode, target="ALL")` | Setzt `RMS`, `MEAN`, `DC` oder `RMEAN` für Strom. |
| `set_update_rate(seconds)` | Setzt `0.05`, `0.1`, `0.25`, `0.5`, `1`, `2`, `5`, `10` oder `20` Sekunden. |

```python
from wt3000_scpi import MeasMode, SyncSource
from wt3000_scpi.wt3000_input import GROUP_MODE, GROUP_RATE, GROUP_SYNC

with wt.input.unlocked(GROUP_SYNC, GROUP_MODE, GROUP_RATE):
    wt.input.set_sync_source(SyncSource.U3, target="SIGMA")
    wt.input.set_voltage_mode(MeasMode.RMS, target="SIGMA")
    wt.input.set_current_mode(MeasMode.RMS, target="SIGMA")
    wt.input.set_update_rate(1.0)
```

Das Intervall einer Messschleife sollte nicht kleiner als die Geräte-Aktualisierungsrate
sein, sonst können identische Datensätze mehrfach aufgezeichnet werden.

## 6. Gesamte Eingangskonfiguration sichern und vergleichen

### Datei `wt3000_input.py`

#### Klasse `InputSnapshot`

| Methode | Bedeutung |
|---|---|
| `InputSnapshot.capture(config)` | Liest die komplette aktuelle Eingangskonfiguration. |
| `save(path)` / `InputSnapshot.load(path)` | Speichert beziehungsweise lädt den Snapshot als JSON. |
| `to_dict()` / `InputSnapshot.from_dict(data)` | Wandelt zwischen Objekt und serialisierbarem Dictionary. |
| `diff(other)` | Vergleicht `self` als Soll mit `other` als Ist; eine leere Liste bedeutet Gleichheit. |
| `log_summary()` | Schreibt eine kompakte Übersicht in das Log. |

Die enthaltene Klasse `ElementSettings` repräsentiert alle gelesenen Einstellungen
eines Elements und bietet ebenfalls `to_dict()` und `from_dict()`.

```python
from pathlib import Path
from wt3000_scpi.wt3000_input import InputSnapshot

with WT3000.connect() as wt:
    snapshot = InputSnapshot.capture(wt.input)
    snapshot.save(Path("input-backup.json"))
    snapshot.log_summary()
```

#### Funktion `restore_input_snapshot(config, snapshot)`

Stellt alle abweichenden, freigegebenen Einstellungen aus einem Snapshot wieder her
und liefert die Anzahl gesendeter Set-Kommandos. Die benötigten Gruppen müssen vorher
explizit freigegeben sein.

```python
from pathlib import Path
from wt3000_scpi.wt3000_input import (
    ALL_GROUPS,
    InputSnapshot,
    restore_input_snapshot,
)

backup = InputSnapshot.load(Path("input-backup.json"))
with wt.input.unlocked(*ALL_GROUPS):
    commands = restore_input_snapshot(wt.input, backup)
print("Gesendete Kommandos:", commands)
```

Eine allgemeine automatische Context-Manager-Wiederherstellung für alle
Eingangsparameter existiert derzeit nicht. Für Bereiche und Item-Tabellen stehen die
sichereren spezialisierten Context Manager aus den folgenden Kapiteln bereit.

## 7. Messbereiche lesen und direkt setzen

### Datei `wt3000_device.py`

#### Eigenschaft `WT3000.ranges`

`wt.ranges` liefert einen `RangeAccess`, der bereits die bestückten Elemente und die
SIGMA-Zuordnungen aus `DeviceInfo` kennt. Dasselbe gilt seit M1-3 für `wt.input`: beide
Wege lösen `ALL` gegen dieselbe, gelesene Elementliste auf, und eine Elementnummer, die
das Gerät nicht bestückt hat, wird abgelehnt statt gesendet.

### Datei `wt3000_rangeio.py`

#### Enum `Quantity`

`Quantity.VOLTAGE` bezeichnet Spannung, `Quantity.CURRENT` Strom. Die Eigenschaften
`label`, `range_label` und die Methode `unit(sensor=False)` liefern passende Texte und
Einheiten.

#### Klasse `RangeValue`

Ein Bereichswert besteht aus `value: float` und `sensor: bool`. `sensor=True` bedeutet
beim Strompfad, dass der Zahlenwert ein Spannungsbereich des externen Sensors ist.

| Methode | Bedeutung |
|---|---|
| `unit(quantity)` | Liefert `V` oder `A` passend zu Messgröße und Eingangsart. |
| `describe(quantity)` | Liefert eine lesbare Kurzform wie `10 V (Sensor)`. |

```python
from wt3000_scpi import Quantity
from wt3000_scpi.wt3000_rangeio import RangeValue

sensor_range = RangeValue(10.0, sensor=True)
print(sensor_range.describe(Quantity.CURRENT))
```

#### Klasse `RangeAccess`

| Methode/Eigenschaft | Bedeutung |
|---|---|
| `elements` | Bestückte Elemente, die dieses Objekt verwaltet. |
| `allow_changes` | Ob Set-Kommandos zugelassen sind. |
| `expand_scope(scope)` | Löst Element, `ALL`, `SIGMA` oder `SIGMB` in Elementnummern auf. |
| `get_range(quantity, element)` | Liest einen Bereich als `RangeValue`. |
| `get_ranges(quantity)` | Liest die Bereiche aller Elemente als Dictionary. |
| `get_auto(quantity, element)` | Liest einen Autorange-Zustand. |
| `get_autos(quantity)` | Liest die Autorange-Zustände aller Elemente. |
| `get_independent()` | Liest die unabhängige Elementsteuerung. |
| `get_wiring()` | Liest die Verdrahtung als Rohtext. |
| `get_module()` | Liest die Modulübersicht als Rohtext. |
| `get_peak_over()` | Liest Peak-Over-Informationen. |
| `dump(quantity)` | Liest den vollständigen Rohabzug der Spannungs- oder Stromgruppe. |
| `set_range(quantity, scope, value, sensor=False)` | Setzt einen Bereich und liefert das gesendete Kommando. |
| `set_auto(quantity, scope, state)` | Setzt Autorange und liefert das gesendete Kommando. |

```python
from wt3000_scpi import Quantity

with WT3000.connect() as wt:
    print(wt.ranges.get_ranges(Quantity.VOLTAGE))
    print(wt.ranges.get_autos(Quantity.CURRENT))
    print(wt.ranges.get_peak_over())
```

Direktes Schreiben über `RangeAccess` benötigt eine schreibbare Fassade, aber keine
`InputConfig`-Gruppenfreigabe. Für reale Änderungen ist der automatisch
wiederherstellende `RangePlan`-Ablauf im nächsten Kapitel vorzuziehen.

```python
from wt3000_scpi import Quantity

with WT3000.connect(read_only=False, allow_changes=True) as wt:
    wt.ranges.set_auto(Quantity.VOLTAGE, 4, False)
    wt.ranges.set_range(Quantity.VOLTAGE, 4, 600.0)
```

Die freien Funktionen `parse_range_value(response)`, `ranges_match(a, b)` und
`sigma_members_from_units(units)` sind für eigene Erweiterungen verfügbar. Sie parsen
Bereichsantworten, vergleichen Bereich plus Eingangsart beziehungsweise bilden
Wiring-Units auf SIGMA-Scopes ab.

## 8. Messbereiche geplant, geprüft und reversibel einstellen

### Datei `wt3000_ranging.py`

#### Klassen `RangeSpec`, `AutoRangeSpec` und `RangePlan`

| Klasse/Methode | Bedeutung |
|---|---|
| `RangeSpec(quantity, scope, value, sensor=False)` | Beschreibt einen festen Bereich; Autorange wird dafür automatisch ausgeschaltet. |
| `AutoRangeSpec(quantity, scope, state)` | Beschreibt einen gewünschten Autorange-Zustand. |
| `RangePlan.of(*specs)` | Baut einen Plan aus gemischten Bereichs- und Autorange-Vorgaben. |
| `plan.is_empty()` | Prüft, ob der Plan keine Änderung enthält. |
| `plan.describe()` | Liefert alle Vorgaben als Textzeilen. |
| `plan.validate(access)` | Prüft Scopes, Widersprüche und die direkte/Sensor-Eingangsart vor dem Schreiben. |

```python
from wt3000_scpi import Quantity
from wt3000_scpi.wt3000_ranging import AutoRangeSpec, RangePlan, RangeSpec

plan = RangePlan.of(
    RangeSpec(Quantity.VOLTAGE, "SIGMA", 600.0),
    RangeSpec(Quantity.CURRENT, 4, 20.0),
    AutoRangeSpec(Quantity.CURRENT, 4, False),
)
for line in plan.describe():
    print(line)
```

#### Klasse `WT3000`: `range_backup()` und `applied_ranges(...)`

| Methode | Bedeutung |
|---|---|
| `range_backup()` | Liest den vollständigen Bereichs- und Autorange-Zustand. |
| `applied_ranges(plan, backup_file=None, allow_snapping=False, force_restore=False)` | Sichert, prüft den Schreibweg, setzt und verifiziert den Plan und stellt im `finally` den Ausgangszustand wieder her. |

```python
from pathlib import Path

with WT3000.connect(read_only=False, allow_changes=True) as wt:
    with wt.applied_ranges(
        plan,
        backup_file=Path("range-backup.json"),
    ) as report:
        values = wt.measure.read_mapped()
        print(values)

    print("Set-Kommandos:", report.commands_written)
    print("Restore-Abweichungen:", report.restore_problems)
```

Der Context Manager liefert einen `RangeReport` mit `backup`, `commands_written`,
`problems` und `restore_problems`. Eine beim Zurückschreiben ausgelöste `WTError` wird
weitergereicht. Reine Abweichungen der abschließenden Gegenprobe stehen dagegen in
`restore_problems` und werden protokolliert; sie müssen vom aufrufenden Programm
ausgewertet werden.

#### Klasse `RangeBackup`

| Methode | Bedeutung |
|---|---|
| `RangeBackup.capture(access)` | Liest Bereiche und Autorange aller Elemente. |
| `state_of(element)` | Liefert den `ElementRangeState` eines Elements. |
| `log_summary()` | Protokolliert das Backup tabellarisch. |
| `save(path)` / `RangeBackup.load(path)` | Speichert beziehungsweise lädt JSON. |
| `to_dict()` / `RangeBackup.from_dict(data)` | Wandelt zwischen Objekt und Dictionary. |
| `diff(other, tolerance=...)` | Listet Abweichungen inklusive Eingangsart auf. |

`ElementRangeState` bietet `range_of(quantity)`, `value_of(quantity)` und
`auto_of(quantity)`.

```python
from pathlib import Path
from wt3000_scpi.wt3000_ranging import RangeBackup

backup = RangeBackup.capture(wt.ranges)
backup.save(Path("range-backup.json"))
loaded = RangeBackup.load(Path("range-backup.json"))
print(backup.diff(loaded))  # []
```

Die freien Ablaufbausteine `check_preconditions()`,
`probe_range_write_capability()`, `apply_plan()`, `verify_plan()`,
`restore_ranges()` und `applied_ranges()` sind ebenfalls verfügbar. Bei Nutzung der
Fassade übernimmt `wt.applied_ranges()` deren sichere Zusammenschaltung.

## 9. Item-Tabelle auswählen, sichern und ändern

Die Item-Tabelle legt fest, welche Messgrößen und Elemente ein Aufruf von
`:NUMeric:NORMal:VALue?` liefert und in welcher Reihenfolge sie erscheinen.

### Datei `wt3000_device.py`

#### Eigenschaft `WT3000.items` und Klasse `ItemAccess`

| Methode/Eigenschaft | Bedeutung |
|---|---|
| `allow_changes` | Ob Änderungen an der Item-Tabelle erlaubt sind. |
| `read()` | Liest die aktuelle Tabelle als `ItemTable`. |
| `standard_profile()` | Liefert das im Treiber definierte Profil für `V3A3,P1W2`. |
| `build(specs)` | Baut aus `ItemSpec`-Objekten eine `ItemTable`. |
| `verify(target)` | Vergleicht die Gerätetabelle mit dem Ziel; leere Liste bedeutet Übereinstimmung. |
| `capture_tail(backup, target)` | Sichert Items oberhalb des aktuellen `NUMber`, die ein größeres Ziel überschreiben würde. |
| `apply(target, backup=None)` | Führt eine Ein-Item-Schreibprobe aus, schreibt die Tabelle und verifiziert sie. |
| `restore(backup, tail=(), force=False)` | Stellt eine gesicherte Tabelle wieder her und liefert die Kommandoanzahl. |
| `applied(specs_or_table, backup_file=None, force_restore=False)` | Context Manager für Sichern, Anwenden, Nutzen und garantierte Wiederherstellung. |

```python
from pathlib import Path
from wt3000_scpi.wt3000_itemspec import ItemSpec

specs = (
    ItemSpec("U", "1"),
    ItemSpec("I", "1"),
    ItemSpec("P", "1"),
)

with WT3000.connect(read_only=False, allow_changes=True) as wt:
    with wt.items.applied(specs, backup_file=Path("items-backup.json")) as table:
        print(wt.measure.read_mapped(table))
```

#### Datei `wt3000_itemspec.py`: Klasse `ItemSpec`

`ItemSpec(function, element=None, order=None, verify=False)` beschreibt ein Item.
`argument` liefert dessen SCPI-Parameterstring. `function` ist absichtlich nicht auf
eine feste Liste begrenzt; die tatsächlich vom WT3000 unterstützten Funktionen sind
dem Gerätehandbuch zu entnehmen. Das Feld `verify` ist im aktuellen Code vorhanden,
wird vom Aufbau und vom Verifikationsablauf aber noch nicht ausgewertet.

```python
from wt3000_scpi.wt3000_itemspec import ItemSpec, build_item_table

spec = ItemSpec("PHI", "1", "1")
print(spec.argument)  # PHI,1,1
table = build_item_table([spec])
```

Die freien Funktionen dieses Moduls sind:

| Funktion | Bedeutung |
|---|---|
| `build_item_table(specs)` | Baut eine Tabelle mit Indizes ab 1, maximal 255 Items. |
| `items_match(requested, actual)` | Vergleicht Item-Angaben unter Beachtung der SCPI-Kurzformen. |
| `probe_extra_items(session, first_index, last_index)` | Sichert Items oberhalb von `NUMber`. |
| `save_backup_bundle(path, table, tail)` / `load_backup_bundle(path)` | Persistiert Tabelle samt Tail. |
| `probe_item_write_capability(session, target, backup)` | Testet den Schreibpfad mit genau einem Item. |
| `apply_item_table(session, target)` | Schreibt Items und `NUMber`. |
| `verify_item_table(session, target)` | Liefert Abweichungen zwischen Soll und Gerät. |
| `restore_item_table(session, backup, tail, force=False)` | Stellt Tabelle und Tail wieder her. |

Für normale Anwendung ist `wt.items.applied(...)` vorzuziehen.

### Datei `wt3000_numeric.py`

#### Klassen `NumericItem` und `ItemTable`

| Klasse/Methode | Bedeutung |
|---|---|
| `NumericItem` | Ein vorhandener Eintrag mit `index`, `function`, `element` und `order`. |
| `NumericItem.is_none` | Prüft, ob das Item auf `NONE` steht. |
| `NumericItem.argument` | Liefert den SCPI-Parameterstring. |
| `NumericItem.key` | Liefert einen sprechenden Ergebnisschlüssel wie `U1` oder `PHI1_1`. |
| `NumericItem.parse(index, token)` | Parst eine Geräteantwort in ein Item. |
| `ItemTable.from_response(response)` | Parst die Antwort auf `:NUMeric:NORMal?`. |
| `ItemTable.read_from_device(session)` | Liest die aktuelle Tabelle. |
| `to_dict()` / `ItemTable.from_dict(data)` | Serialisierung. |
| `save(path)` / `ItemTable.load(path)` | JSON-Persistenz. |
| `restore_to_device(session, force=False)` | Schreibt diese Tabelle zurück. |
| `map_values(values)` | Ordnet eine Werteliste sprechenden Schlüsseln zu. |

```python
from pathlib import Path

with WT3000.connect() as wt:
    table = wt.items.read()
    table.save(Path("item-table.json"))
    for item in table.items:
        print(item.index, item.key, item.argument)
```

## 10. Einzelne Messwerte lesen

### Datei `wt3000_device.py`

#### Eigenschaft `WT3000.measure` und Klasse `MeasureControl`

| Methode | Bedeutung |
|---|---|
| `read_values(table=None)` | Liest einen Datensatz als geordnete Liste von `NumericValue`. Mit Tabelle wird die Anzahl streng geprüft. |
| `read_mapped(table=None)` | Liest einen Datensatz und ordnet ihn Schlüsseln wie `U1`, `I1` oder `P1` zu. Ohne Argument wird die aktuelle Item-Tabelle gelesen. |
| `hold(enabled=True)` | Liefert einen `NumericHold`-Context-Manager. In Nur-Lesen-Sitzungen wird HOLD nicht aktiviert. |

```python
with WT3000.connect() as wt:
    table = wt.items.read()
    values = wt.measure.read_mapped(table)
    for name, value in values.items():
        print(name, value.value, value.status.value)
```

HOLD ist ein Set-Kommando und benötigt daher `read_only=False`. `refresh()` friert den
neuesten Datensatz ein; beim Verlassen wird HOLD zuverlässig ausgeschaltet.

```python
with WT3000.connect(read_only=False) as wt:
    table = wt.items.read()
    with wt.measure.hold() as hold:
        hold.refresh()
        values = wt.measure.read_values(table)
```

### Datei `wt3000_numeric.py`

#### Klassen `NumericValue` und `ValueStatus`

Ein `NumericValue` enthält `value`, `status` und `raw_bits`. `is_usable` ist nur bei
`ValueStatus.OK` wahr. Weitere Statuswerte sind `NO_DATA` und `OVERRANGE`.

```python
for value in wt.measure.read_values():
    if value.is_usable:
        print(float(value.value))
    else:
        print(value.status.value)
```

Die freien Funktionen `parse_float_block(payload)` und
`read_numeric_values(session, expected_count=None, strict=True)` sind für die direkte
Nutzung der unteren Schicht verfügbar. `strict=True` verhindert Messreihen mit einer
nicht zur Item-Tabelle passenden Werteanzahl.

## 11. Messreihen aufzeichnen

### Datei `wt3000_device.py`

#### Klasse `MeasureControl`

| Methode | Bedeutung |
|---|---|
| `record(sink, table, ...)` | Führt eine blockierende Messschleife in eine beliebige Senke aus. |
| `record_csv(csv_path, table, ...)` | Bequemer Spezialfall, der selbst einen `CsvSink` erzeugt. |

Wichtige optionale Parameter sind `interval_s`, `max_samples`, `max_duration_s`,
`use_hold`, `record_condition`, `log_every`, `metadata_path` und `parameters`.
Mindestens ein Limit ist für eingebettete Anwendungen empfehlenswert; ohne Limit läuft
die Schleife bis `Strg+C`.

**Protokollzustand vor der ersten Messung.** `:COMMunicate:HEADer 0` und
`:NUMeric:FORMat FLOat` sind Voraussetzung der Binärauswertung. `record()` prüft das
nicht selbst und stellt es erst recht nicht her — beides gehört einmal und sichtbar in
den Ablauf:

```python
with WT3000.connect(read_only=False, allow_changes=True) as wt:
    with wt.ensured_protocol_state():
        wt.measure.record_csv(pfad, wt.items.read(), unit_row=True)
```

Stimmt der Zustand schon, geht kein Kommando hinaus. Beim Verlassen steht wieder, was
vorgefunden wurde.

**Einheiten.** Seit M4-3 gehen die Einheiten der Messwerte mit den Spalten hinaus — in
JSONL und im Sidecar ohne Zutun, in der CSV über `unit_row=True` als zweite Kopfzeile.
Sie stammen aus derselben Item-Tabelle wie die Spaltennamen. Zwei Werte sind zu
unterscheiden: eine leere Angabe heißt **dimensionslos** (etwa `LAMBDA`), `null`
beziehungsweise `?` heißt **nicht belegt** — für die neun Oberschwingungsfaktoren liegt
im Projekt kein Beleg vor, und ein geratenes `%` wäre schlechter als keine Angabe. Wer
die Einheit kennt, gibt sie über `metadata={"units": {...}}` selbst mit; die Angabe des
Aufrufers hat Vorrang.

**`interval_s` ist der Takt der Schleife, nicht die Rate des Geräts.** Wie oft das
WT3000 einen neuen Messdatensatz bildet, steht auf `:RATE` und wird über
`wt.input.set_update_rate()` gestellt. Beide Werte werden seit M3-3 gegeneinander
geprüft: Ein `interval_s` unterhalb der Geräterate wird beim Start ausdrücklich
gemeldet, und jeder Zyklus, in dem das Gerät nicht aktualisiert hat, ist in der Ausgabe
als `SampleMark.DUPLICATE` gekennzeichnet — in der Spalte `status_flags` jeder Senke und
gezählt in `LoopStatistics.duplicates`. Die Prüfung meldet, sie bricht nicht ab;
abschaltbar über `check_update_rate=False` bzw. `mark_duplicates=False`.

Faustregel: `interval_s >= :RATE`. Wer bewusst schneller liest, um den Zeitpunkt eines
Wertwechsels einzugrenzen, tut nichts Falsches — er bekommt die Wiederholungen dann als
solche gekennzeichnet statt als eigenständige Messpunkte.

```python
from pathlib import Path

with WT3000.connect() as wt:
    table = wt.items.read()
    stats = wt.measure.record_csv(
        Path("messung.csv"),
        table,
        interval_s=1.0,
        max_samples=60,
        use_hold=False,
        metadata_path=Path("messung.metadata.json"),
    )
    print(stats.samples, stats.overruns)
```

Für genaue Zeitstempel mit HOLD muss die Sitzung mit `read_only=False` geöffnet sein.
Das Ändern von Eingangsparametern oder Item-Tabellen erfordert zusätzlich
`allow_changes=True`.

### Datei `wt3000_sinks.py`

#### Verfügbare Senken

| Klasse | Konstruktor und Zweck |
|---|---|
| `CsvSink(path, delimiter=",")` | Schreibt Kopf und Messdatensätze in CSV. |
| `JsonlSink(path)` | Schreibt Metadaten und Datensätze zeilenweise als JSON. |
| `CallbackSink(callback)` | Übergibt jeden `Sample` an eine Python-Funktion. |
| `MultiSink(*sinks)` | Verteilt jeden Datensatz an mehrere Senken. |

Jede Senke besitzt `open(columns, metadata=None)`, `write(sample)` und `close()`.
Bei Verwendung über `record()` werden diese Methoden automatisch und fehlersicher
aufgerufen.

```python
from pathlib import Path
from wt3000_scpi import CallbackSink, CsvSink, JsonlSink, MultiSink

def show(sample):
    print(sample.number, sample.elapsed_s)

sink = MultiSink(
    CsvSink(Path("messung.csv"), delimiter=";"),
    JsonlSink(Path("messung.jsonl")),
    CallbackSink(show),
)

with WT3000.connect() as wt:
    table = wt.items.read()
    wt.measure.record(sink, table, max_samples=10, use_hold=False)
```

`require_matching_columns(sample, columns, ziel)` ist der gemeinsame Prüfer für eigene
Senken. Er verhindert, dass Messwerte bei abweichender Anzahl unter falsche
Spaltenüberschriften geschrieben werden.

### Datei `wt3000_measure.py`

#### Klassen `Sample`, `SampleMark`, `LoopStatistics` und `SampleSink`

| Klasse/Methode | Bedeutung |
|---|---|
| `Sample` | Ein Zyklus mit `timestamp`, `elapsed_s`, `number`, `condition`, `values` und `mark`. |
| `ItemTable.unit_map()` | Spaltenname → Einheit. `""` heißt dimensionslos, `None` heißt „nicht belegt“. |
| `Sample.status_flags(column_names)` | Liefert Auffälligkeiten wie `U2=OVERRANGE`. |
| `SampleMark` | Zyklusstatus `OK` und `DUPLICATE`; `MISSING` ist vorbereitet (M3-4). |
| `LoopStatistics` | Enthält `samples`, `overruns`, `duplicates`, `measured_samples`, `update_rate_s`, `cycle_times` und `status_counts`. |
| `LoopStatistics.log_summary(interval_s)` | Protokolliert Zykluszeiten und Statusverteilung. |
| `SampleSink` | Protocol für eigene Ausgabesenken mit `open`, `write`, `close`. |

```python
stats = wt.measure.record(sink, table, max_samples=100, use_hold=False)
stats.log_summary(interval_s=1.0)
```

Die freien Funktionen dieser Datei sind:

| Funktion | Bedeutung |
|---|---|
| `build_standard_profile()` | Baut das aktuelle Standardprofil für `V3A3,P1W2`. |
| `write_metadata(path, session, table, parameters)` | Schreibt Geräte- und Laufmetadaten als JSON. |
| `run_measurement_loop(...)` | Untere, blockierende Messschleife mit driftfreier Taktung. |

Normalerweise werden sie über `wt.items.standard_profile()`, `record()` und
`record_csv()` benutzt.

## 12. Eigene Item-Tabelle und Messreihe kombiniert verwenden

Dieses Beispiel zeigt den vorgesehenen sicheren Ablauf: Verbindung öffnen,
Protokollzustand prüfen, Item-Tabelle vorübergehend setzen, messen und anschließend die
ursprüngliche Tabelle automatisch wiederherstellen.

```python
from pathlib import Path

from wt3000_scpi import CsvSink, WT3000
from wt3000_scpi.wt3000_itemspec import ItemSpec

specs = (
    ItemSpec("U", "1"),
    ItemSpec("I", "1"),
    ItemSpec("P", "1"),
)

with WT3000.connect(read_only=False, allow_changes=True) as wt:
    wt.check_protocol_state()

    with wt.items.applied(specs, backup_file=Path("items-backup.json")) as table:
        wt.measure.record(
            CsvSink(Path("messung.csv"), delimiter=";"),
            table,
            interval_s=1.0,
            max_samples=10,
            metadata_path=Path("messung.metadata.json"),
        )
```

## 13. Fehlerklassen

Die folgenden Fehler sind für Anwender relevant und erben von `WTError`:

| Datei | Fehlerklasse | Bedeutung |
|---|---|---|
| `wt3000_transport.py` | `WTError` | Gemeinsame Basisklasse des Treibers. |
| `wt3000_transport.py` | `TmctlError` | TMCTL-DLL meldet einen Fehlercode. |
| `wt3000_transport.py` | `ProtocolError` | SCPI-/Blockdatenregel wurde verletzt. |
| `wt3000_core.py` | `DeviceError` | Gerätefehlerqueue enthält einen Fehler. |
| `wt3000_core.py` | `ReadOnlyViolation` | Set-Kommando in einer Nur-Lesen-Sitzung. |
| `wt3000_input.py` | `ConfigLocked` | Eingangsgruppe oder allgemeiner Schreibzugriff ist gesperrt. |
| `wt3000_input.py` | `VerificationError` | Zurückgelesene Einstellung weicht vom gesendeten Wert ab. |
| `wt3000_rangeio.py` | `ChangesNotAllowed` | Schreibzugriff über nicht freigegebenen `RangeAccess`. |
| `wt3000_sinks.py` | `SinkNotOpen` | In eine Senke wurde vor `open()` geschrieben. |

```python
from wt3000_scpi import WT3000, WTError

try:
    with WT3000.connect() as wt:
        print(wt.measure.read_mapped())
except WTError as error:
    print("WT3000-Fehler:", error)
```

## 14. Transport, Tests und allgemeine Hilfsfunktionen

### Datei `wt3000_transport.py`

#### Protocol `Transport` und Klasse `TmctlTransport`

Ein alternativer Transport muss die fünf Methoden `write(command)`, `read()`,
`query(command)`, `set_timeout(timeout_ms)` und `close()` anbieten.
`TmctlTransport(config)` ist die konkrete Implementierung über die Yokogawa-DLL und
wird durch `WT3000.from_config()` beziehungsweise `WT3000.connect()` automatisch
erzeugt. Seine Methoden sollten bei normaler Nutzung nicht direkt aufgerufen werden;
`WTSession` ergänzt die Protokollprüfung und Blockverarbeitung.

```python
from wt3000_scpi import TmctlTransport, WTConfig, WTSession

config = WTConfig.from_environment()
transport = TmctlTransport(config)
session = WTSession(transport, config, read_only=True)
try:
    print(session.query("*IDN?"))
finally:
    transport.close()
```

#### Klasse `FakeTransport`

`FakeTransport` stellt denselben Vertrag ohne Gerät und DLL bereit. Der Konstruktor
akzeptiert `responses`, optional `chunk_size`, `error_queue` und `fail_commands`.
`prime(data)` bereitet Rohdaten für den nächsten `read()` vor. Die Attribute `written`,
`timeouts_ms`, `closed` und `reads` unterstützen Tests. `float_block(values)` baut eine
passende simulierte Binärblockantwort.

```python
from wt3000_scpi import FakeTransport, WTConfig, WTSession

fake = FakeTransport({"*IDN": "YOKOGAWA,WT3000,TEST,F1.00"})
session = WTSession(fake, WTConfig(), read_only=True)
print(session.query("*IDN?"))
```

### Datei `wt3000_common.py`

Für Anwendungen und Erweiterungen stehen folgende gemeinsame Helfer bereit:

| Funktionsart | Funktionen |
|---|---|
| Scope-Normalisierung | `canonical_scope()`, `canonical_element()`, `is_element_scope()`, `element_number()`, `scope_suffix()` |
| Antwortauswertung | `strip_response_header()`, `parse_nr3()`, `parse_nr1()`, `parse_condition()`, `parse_boolean()` |
| Zustandsauswertung | `condition_warnings(bits)` |
| Zahlenformat/-vergleich | `format_nrf(value)`, `values_match(requested, actual, tolerance=...)` |
| Ausgabeorte | `find_project_root(start=None)`, `output_dir(name=None, start=None)` |
| Logging | `setup_logging(log_file)` |

`setup_logging()` ersetzt die Handler des Root-Loggers und ist deshalb für allein
laufende Messskripte gedacht, nicht für die Einbettung in eine größere Anwendung.
`output_dir()` legt das zurückgegebene Verzeichnis nicht an.

```python
from pathlib import Path
from wt3000_scpi.wt3000_common import output_dir, setup_logging

log_dir = output_dir("protokolle")
log_dir.mkdir(parents=True, exist_ok=True)
setup_logging(log_dir / "messung.log")
```

Die ähnlich benannten Parser in `wt3000_input.py` (`target_node`, `strip_header`,
`split_multi`, `parse_bool`, `parse_float`, `parse_current_range`,
`parse_line_filter`, `canonical_enum_token`, `enum_match` und `format_rate`) sind
Bausteine für eigene Erweiterungen der `:INPut`-Gruppe. Für normale Gerätebedienung
werden sie automatisch von `InputConfig` benutzt.

## 15. Noch nicht verfügbare Gerätefunktionen

Der aktuelle Code stellt insbesondere noch keine Anwender-Methoden für folgende
Funktionen bereit:

- Integration starten, stoppen oder zurücksetzen sowie Wh-/Ah-Abläufe;
- nicht blockierende Messung mit `start()`, `stop()` oder Datenstream;
- Averaging-Konfiguration;
- Oberschwingungsanalyse und deren Gerätekonfiguration;
- Speichern und Laden von Gerätesetups;
- sichere automatische Herstellung des benötigten Protokollzustands;
- Erkennung und Markierung doppelter oder ausgefallener Messzyklen.

Diese Grenzen sind wichtig: Das Vorhandensein des allgemeinen Notausgangs
`wt.session.write(...)` bedeutet nicht, dass dafür bereits dieselben Sicherungs-,
Backup- und Verifikationsabläufe wie für die dokumentierten Fachfunktionen existieren.
