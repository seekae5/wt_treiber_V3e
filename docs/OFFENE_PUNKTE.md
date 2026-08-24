# Offene Punkte — geprüfter Stand 2026-08-20

**Projekt:** `wt3000-scpi 0.3.0`  
**Basis:** aktueller Quellstand, 682 bestandene Tests, Ruff und Mypy ohne Befund  
**Abgrenzung:** Geräteverhalten ist nur dort als belegt bezeichnet, wo der Bestand
einen konkreten Geräteversuch dokumentiert.

Dieses Dokument ersetzt die früheren Dateien `Befund.md`,
`ANALYSE_2026-08-19.md` und `PLAN_BEFUNDE_2026-08-19.md`. Deren erledigte
Momentaufnahmen wurden aus der Aufgabenliste entfernt. Historische Änderungen stehen
in [AENDERUNGEN_2026-08-18.md](AENDERUNGEN_2026-08-18.md), die Einordnung in
[ROADMAP.md](ROADMAP.md).

---

## 1 — Am Gerät zu klären

| Nr. | Frage | Auswirkung | Roadmap |
|---|---|---|---|
| H-01 | Akzeptiert der Direktstrombereich die reine Zahl, und lautet der Sensorbereich `EXTernal,10` ohne Einheit? | Schließt die letzte Unsicherheit der Bereichssyntax. Die Spannungssyntax ohne Einheit ist bereits belegt. | M0-1 |
| H-02 | Wie reagiert das Gerät auf einen nicht vorhandenen Bereichswert: Ablehnung, Rundung oder Übernahme? | Legt das Verhalten von `verify_plan(allow_snapping=…)` fest. | M0-2 |
| H-03 | Benötigen schreibende `:INPut`-Kommandos über Ethernet `:COMMunicate:REMote ON`? | Bestimmt den begründeten Standard von `WTConfig.use_remote`. | M0-3 |
| H-04 | Welche realen Antworten liefern `:INPut:MODUle?` und `:INPut:WIRing?`, auch bei unbestückten Elementen? | Schließt Parser- und Gerätesteckbriefannahmen. | M0-4/M1-3 |
| H-05 | Woran ist ein neuer Messdatensatz zuverlässig erkennbar? | Bestimmt Ereignissteuerung oder Dublettenerkennung in M3-3. | M0-5 |
| H-06 | Ist die angenommene Einheit von `TmcSetTimeout` Millisekunden, und wie antwortet das Gerät auf verkettete Kommandos? | Verhindert eine falsche Timeoutinterpretation und entscheidet Parserregeln für Mehrfachantworten. | M0/M1-5 |
| H-07 | Welche Line-Filterwerte und wie viele SIGMA-Einheiten unterstützt die eingesetzte Firmware? | Legt gültige Enumerationen und Scope-Namen fest. | M1-3/M2-3 |

Diese Fragen sollten in einem einzigen protokollierten Gerätetermin abgearbeitet
werden. Rohantwort, gesendetes Kommando, Rücklesewert, Fehlerqueue, Modell und Firmware
gehören jeweils in den Beleg.

---

## 2 — Offene Softwarebefunde

### S-01 — Bereichstabellen sind noch nicht modulabhängig — **Rest von M1-3**

Der Hauptteil ist am 25.08.2026 erledigt: `InputConfig` bekommt dieselbe Elementliste
wie `RangeAccess`, `_elements_of("ALL")` löst gegen die Bestückung auf, eine
Elementnummer wird geprüft, und `WT3000.refresh_device()` hält den Steckbrief nach einer
Umverdrahtung aktuell — nach `set_wiring()` von selbst.

**Offen bleibt:** Die Bereichstabellen (`VOLTAGE_RANGES`, `CURRENT_RANGES`) werden noch
über feste Schlüssel `(Modultyp, Crest)` ausgewählt. Ein unbekanntes Modul ergibt dort
weiterhin einen `KeyError` statt einer `WTError` mit Kontext.

### S-02 — Parser- und Scope-Regeln liegen mehrfach vor

`wt3000_input.py` führt eigene Varianten von Kopfentfernung, Boolean-, Zahlen- und
Vergleichslogik neben `wt3000_common.py`. Die Varianten verhalten sich nicht in allen
Fällen gleich, insbesondere bei Antworten mit Headern. `target_node()` und
`scope_suffix()` akzeptieren zudem verschiedene Kurzformen.

**Ziel:** Das tatsächliche Geräteformat zuerst mit H-04/H-06 belegen, danach jede
Normalisierungsregel an genau einer Stelle führen. Gehört zu M2-5 und muss vor neuen
Konfigurationsgruppen abgeschlossen sein.

### S-03 — `drain_after_failure()` deckt noch nicht alle Fehlerpfade ab

**Korrektur:** Die frühere Fassung dieses Befunds — „wird im Quellcode nicht
aufgerufen“ — trifft nicht mehr zu. Aufgerufen wird die Methode heute an vier Stellen:
nach `*IDN?` und `*OPT?` in `DeviceInfo.read()`, nach jeder fehlgeschlagenen Abfrage in
`write_metadata()` und seit M3-3 nach einem fehlgeschlagenen `:RATE?` in
`device_update_rate()`.

**Offen bleibt:** eine allgemeine Zuständigkeitsregel. Heute entscheidet jede Aufrufstelle
für sich, ob sie nachräumt; ein fehlgeschlagener Query in einem beliebigen anderen Pfad
tut es nicht. Dazu gehören die Timeoutwiederherstellung in `WTSession` und ein Test mit
simulierten verspäteten Antworten. Gehört zu M1-5.

### S-04 — Schreibfreigabe ist breiter als die übergebenen Gruppen

`InputConfig.unlocked(...)` setzt zusätzlich `allow_changes=True`. Damit werden im
Block alle nicht standardmäßig geschützten Gruppen schreibbar, nicht nur die genannten.
Die vorhandene Wiederherstellung nutzt dieses Verhalten teilweise.

**Ziel:** Entweder ausschließlich die genannten Gruppen freigeben und alle Aufrufer
vollständig deklarieren oder die zweite Sperrebene entfernen und den Vertrag
vereinfachen. Vor M2-3 entscheiden.

### S-05 — Fehlersemantik ist noch nicht durchgängig

Einige Tabellenzugriffe können weiterhin `KeyError` oder `ValueError` liefern, obwohl
die öffentlichen Abläufe überwiegend `WTError` als Treibergrenze verwenden. Eine
eigene Timeout-Unterklasse fehlt ebenfalls.

**Ziel:** Fehler an Paketgrenzen in aussagekräftige `WTError`-Unterklassen überführen,
ohne Programmierfehler pauschal zu verschlucken. Gehört zu M1-5.

### S-06 — Stufenskripte duplizieren Ablaufwissen

`check_preconditions()` existiert in mehreren Skripten; das Stufe-3-Profil und
`build_standard_profile()` sind absichtlich verschieden, aber nicht als benannte
Profile zentral erfasst. Laufparameter liegen noch teilweise als Modulkonstanten vor.

**Ziel:** Stufenskripte als Beispiele erhalten, gemeinsamen Ablauf über Fassade und
spätere CLI führen und Profile zentral benennen. Gehört zu M2-5/M5-2.

### S-07 — Messsteuerung braucht vorab zwei Entscheidungen

Vor M3-1 ist festzulegen, ob `WTSession` intern serialisiert oder eine Sitzung exklusiv
einem Mess-Thread gehört. Außerdem ist die Bedeutung von `use_hold=False` eindeutig zu
machen: „HOLD nicht anfassen“ oder „HOLD muss aus sein“.

**Ziel:** Sitzungsbesitz, Stoppsignal, Fehlerweitergabe und Cleanup als Vertrag der
steuerbaren Messung dokumentieren und testen.

### S-08 — Fehlende Zyklen und strenge Spaltenzahl kollidieren

`SampleMark.MISSING` ist vorhanden, aber ein fehlender Zyklus enthält naturgemäß keine
Messwerte. `require_matching_columns()` lehnt ihn deshalb ab.

**Ziel:** In M3-4 entscheiden, ob fehlende Werte als `NO_DATA` auf die feste
Spaltenzahl aufgefüllt werden. Das erhält die strenge Datenintegritätsregel und ist der
bevorzugte Weg, solange kein Gerätebeleg dagegen spricht.

### S-10 — Ereignisgesteuertes Lesen fehlt weiterhin

Seit M3-3 (25.08.2026) erkennt die Messschleife Dubletten und prüft ihren Takt gegen
`:RATE`. Damit ist ein zu schnelles Lesen **erkennbar**, aber nicht **vermieden**: die
Schleife wartet weiterhin eine feste Zeit, statt auf ein Aktualisierungsereignis des
Geräts zu warten.

**Ziel:** Nach H-05/M0-5 entscheiden, ob ein belegtes Ereignis (`:STATus:CONDition?`,
Extended Event Register, Serial Poll) den festen Takt ersetzen kann. Die
Dublettenerkennung bleibt in jedem Fall — sie deckt den Fall „Takt gleich Rate, aber
phasenverschoben“ ab, den keine Ereignissteuerung überflüssig macht.

### S-09 — Auslieferung ist nur teilweise abgeschlossen

Vorhanden sind `pyproject.toml`, Test- und Entwicklungsgruppen, Ruff, Mypy,
`.gitattributes`, README und ein installierbares Paket. Es fehlen weiterhin:

- `py.typed`
- vollständige Paketmetadaten, Lizenz und Projekt-URLs
- ein Änderungsformat für spätere Releases
- eine gemeinsame Kommandozeile
- CI und eine ausgewertete Testabdeckung
- Einheiten und ein verbindlicher Metadatenverbund für Messdateien

Diese Punkte gehören zu M4-3 und M5.

---

## 3 — Erledigte frühere Befunde

Die folgenden Aussagen aus den entfernten Momentaufnahmen sind nicht mehr offen:

| Früherer Befund | Heutiger Stand |
|---|---|
| REMOTE bleibt nach fehlgeschlagener Initialisierung aktiv | P-1: Cleanup im Konstruktor, durch Tests belegt |
| Restore-Fehler der Item-Tabelle wird verschluckt | P-2: Ausnahme plus Gegenprobe |
| Messwertanzahl kann Ausgabespalten verschieben | P-3/M4-2: harter Abbruch und zentrale Spaltenregel |
| Stufe 5b schreibt trotz lesender Beschreibung | P-5: Schreiben nur mit `--write-probe` |
| Blockheader erzeugt nackte `ValueError` | P-4: vollständige Validierung als `ProtocolError` |
| Installation und Python-Version seien nicht deklariert | `pyproject.toml` mit Python ≥ 3.10 und Abhängigkeitsgruppen ist vorhanden |
| Verbindungsdaten seien feste Quellcodewerte | P-7: Parameter, Umgebung und JSON-Konfiguration; neutrale Defaults |
| Tests liefen lokal nicht | Projektumgebung vorhanden; 682 Tests aktuell ausgeführt |
| Kein Linting, keine Typprüfung, gemischte Zeilenenden | Ruff, Mypy und `.gitattributes` sind eingerichtet |
| `wt3000_core.py` enthalte einen großen auskommentierten Transportklon | Kommentarbereinigung abgeschlossen |
| Export sei fest an CSV gekoppelt | M4-1/M4-2: `Sample`, `SampleSink` und vier Senken |
| Steckbrief veraltet nach einer Umverdrahtung stillschweigend | M1-3: `refresh_device()`, nach `set_wiring()` automatisch |
| `InputConfig` adressiert `ALL` gegen eine feste Elementliste | M1-3: Elementliste aus `DeviceInfo`, Elementnummer geprüft |
| Schleifentakt und Geräterate seien unverbunden, Dubletten unerkennbar | M3-3: Taktprüfung vorab, `SampleMark.DUPLICATE` während des Laufs, Rate in den Metadaten |

---

## 4 — Priorität

1. Gerätetermin H-01 bis H-07 mit reproduzierbarem Protokoll
2. M2-5/S-02, damit neue Konfigurationsgruppen nicht auf einer weiteren
   Parserkopie aufbauen (M1-3/S-01 ist bis auf die Bereichstabellen erledigt)
3. M1-5/S-03 und S-05 für robuste Fehlerpfade
4. M3-1/S-07 für eine steuerbare Messung
5. M4-3 und M2-1 für interpretierbare Daten und die fehlenden Gerätegruppen

Die ausführlichen Abnahmekriterien und Abhängigkeiten stehen in
[ROADMAP.md](ROADMAP.md).
