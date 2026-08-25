# Offene Punkte

**Stand:** 25. August 2026 · `wt3000-scpi 0.3.0`
**Abgrenzung:** Geräteverhalten gilt nur dann als belegt, wenn ein protokollierter
Versuch vorliegt. Erledigte Punkte stehen im
[Änderungsprotokoll](AENDERUNGEN_2026-08-18.md), Abnahmekriterien in der
[Roadmap](ROADMAP.md).

## Am Gerät zu klären

| Nr. | Frage | Folge |
|---|---|---|
| H-01 | Akzeptiert der Direktstrombereich die reine Zahl, und lautet der Sensorbereich `EXTernal,10` ohne Einheit? | letzte Unsicherheit der Bereichssyntax schließen |
| H-02 | Wie reagiert das Gerät auf einen nicht vorhandenen Bereichswert: Ablehnung, Rundung oder Übernahme? | Verhalten von `verify_plan(allow_snapping=…)` festlegen |
| H-03 | Benötigen schreibende `:INPut`-Kommandos über Ethernet `:COMMunicate:REMote ON`? | Standard von `WTConfig.use_remote` begründen |
| H-04 | Welche Antworten liefern `:INPut:MODUle?` und `:INPut:WIRing?`, auch bei unbestückten Elementen? | Parser- und Steckbriefannahmen schließen |
| H-05 | Eignet sich Statusfilter plus Service Request zuverlässig zur Erkennung eines neuen Datensatzes? | festen Messtakt möglicherweise durch Ereignissteuerung ersetzen |
| H-06 | Ist die Einheit von `TmcSetTimeout` Millisekunden, und wie antwortet das Gerät auf verkettete Kommandos? | Timeout- und Mehrfachantwortregeln belegen |
| H-07 | Welche Line-Filterwerte und wie viele SIGMA-Einheiten unterstützt die Firmware? | gültige Enumerationen und Scopes festlegen |

Die Fragen sollten in einem Termin protokolliert werden. Zu jedem Versuch gehören
Kommando, Rohantwort, Rücklesewert, Fehlerqueue, Modell und Firmware.

## Offene Softwarebefunde

### S-01 — Bereichstabellen sind nicht vollständig modulabhängig

Elementliste, Zielprüfung und Auffrischung nach Umverdrahtung sind umgesetzt. Offen
bleibt die Auswahl von `VOLTAGE_RANGES` und `CURRENT_RANGES` über feste Schlüssel
`(Modultyp, Crest)`: ein unbekanntes Modul kann noch einen `KeyError` statt eines
Treiberfehlers mit Kontext erzeugen. Gehört zu M1-3/M1-5.

### S-02 — Parser- und Scope-Regeln liegen mehrfach vor

`wt3000_input.py` dupliziert Kopfentfernung, Boolean-, Zahlen- und Vergleichslogik aus
`wt3000_common.py`; `target_node()` und `scope_suffix()` akzeptieren unterschiedliche
Kurzformen. Nach H-04/H-06 soll jede Normalisierungsregel genau einmal vorliegen.
Gehört zu M2-5 und sollte vor weiteren Konfigurationsgruppen erfolgen.

### S-03 — Bereinigung nach fehlgeschlagenen Queries ist nicht zentral geregelt

`drain_after_failure()` wird in bekannten kritischen Pfaden bereits verwendet. Offen
ist, wer nach einem beliebigen Timeout oder einer verspäteten Antwort zuständig ist.
Benötigt einen simulierten Timeout mit nachfolgender Abfrage. Gehört zu M1-5.

### S-04 — Schreibfreigabe ist breiter als die genannten Gruppen

`InputConfig.unlocked(...)` setzt zusätzlich `allow_changes=True`; damit sind im Block
auch nicht standardmäßig geschützte Gruppen schreibbar. Entweder nur die genannten
Gruppen freigeben oder die zweite Sperrebene entfernen. Vor M2-3 entscheiden.

### S-05 — Fehlersemantik ist nicht durchgängig

Einige Tabellen- und Parserpfade können `KeyError` oder `ValueError` liefern; eine
eigene Timeout-Unterklasse fehlt. Erwartbare Fehler sollen an öffentlichen Grenzen in
aussagekräftige `WTError`-Unterklassen überführt werden, ohne Programmierfehler zu
maskieren. Gehört zu M1-5.

### S-06 — Stufenskripte duplizieren Ablaufwissen

Vorbedingungen, Profile und einzelne Laufparameter werden mehrfach geführt. Die
Skripte sollen Beispiele bleiben; gemeinsame Abläufe und benannte Profile gehören in
Fassade bzw. CLI. Gehört zu M2-5/M5-2.

### S-07 — Bedeutung von `use_hold=False` ist unklar

Nebenläufigkeit und Sitzungsbesitz sind umgesetzt. Offen bleibt nur, ob
`use_hold=False` „HOLD nicht anfassen“ oder „HOLD muss aus sein“ bedeutet.

### S-08 — Fehlende Zyklen passen nicht zur strengen Spaltenzahl

`SampleMark.MISSING` existiert, aber `require_matching_columns()` lehnt einen leeren
Zyklus ab. Bevorzugte Lösung für M3-4: mit `NO_DATA` auf die feste Spaltenzahl
auffüllen.

### S-09 — Auslieferung ist unvollständig

Es fehlen `py.typed`, vollständige Paketmetadaten und Lizenz, Release-Änderungsformat,
gemeinsame CLI, CI, bewertete Testabdeckung sowie eine feste Bindung zwischen
Messdatei und Metadaten. Gehört zu M4-3/M5.

### S-10 — Ereignisgesteuertes Lesen fehlt

Geräterate und Dubletten werden erkannt, die Schleife wartet aber weiterhin nach
einem festen Takt. Nach H-05 ist zu entscheiden, ob ein Geräteereignis diesen Takt
ersetzen kann. Die Dublettenerkennung bleibt als Absicherung bestehen.

## Priorität

1. H-01 bis H-07 in einem reproduzierbaren Gerätetermin.
2. S-02, damit neue Konfigurationsgruppen keine weiteren Parservarianten erzeugen.
3. S-03/S-05 und danach M3-4: Kommunikationsabbrüche sind die wichtigste Lücke für
   unbeaufsichtigte Langzeitmessungen.
4. M4-3/M4-4 für dauerhaft interpretierbare und rotierende Messdateien.
5. S-09/M5 für auslieferbares Paket, CLI und CI.
