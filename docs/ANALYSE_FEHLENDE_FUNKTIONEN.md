# Fehlende Funktionen für vollständige Messabläufe — konsolidierte Analyse

**Quellen:** Yokogawa WT3000 Communication Interface User's Manual, Quellcode und
Geräteprobe vom 21. August 2026
**Zweck:** Einordnung nach Anwendungsfall; Planung und Prioritäten stehen in
[ROADMAP.md](ROADMAP.md).

Die Erstfassung entstand vor mehreren Implementierungsschritten und beschrieb danach
jeden Fortschritt in einem neuen Nachtrag. Dadurch standen Aussagen wie „fehlt
vollständig“ neben späteren Abschnitten „umgesetzt“. Diese Fassung zeigt nur den
konsolidierten Stand.

## Quellen- und Gerätebefund

Viele Kommandogruppen hängen von Hardwareoptionen ab. `DeviceInfo` liest deshalb
`*OPT?`, normalisiert die Optionscodes und prüft optionsabhängige Fachzugriffe vor dem
ersten Kommando.

Die protokollierte Probe am Gerät `760304-40-MV`, Firmware `F5.01`, ergab die Optionen
`G6,B5,DT,C7,C5,CC`:

| Funktion | Verfügbarkeit am geprüften Gerät |
|---|---|
| Harmonics und hochauflösende Acquisition | über `G6` verfügbar |
| CBCycle | über `CC` verfügbar |
| Delta-Berechnungen | über `DT` verfügbar |
| Hardcopy/Drucker | über `B5`/`C7` verfügbar, aber kein Treiberziel |
| Motorgruppe | trotz fehlendem `MTR` über Modellvariante `-MV` und Query bestätigt |
| Flicker und Analogausgang | mangels `FL` bzw. `DA` nicht verfügbar |

Das im Handbuch dokumentierte UPD-Bit ließ sich beim Polling nicht zuverlässig
beobachten. Der Treiber kennzeichnet daher Dubletten; ein ereignisgesteuerter Weg über
Statusfilter und Service Request ist noch am Gerät zu prüfen.

## Stand nach Anwendungsfall

| Anwendungsfall | Stand | Verbleibende Arbeit |
|---|---|---|
| Momentanwerte und Messreihen | vorhanden | Kommunikationsabbrüche behandeln und Langzeitdateien rotieren |
| Energie-/Wh-/Ah-Messung | `IntegrationConfig`, Integrationsprofil und Zustandsabfragen vorhanden | vollständiger Lauf am realen Gerät; `*OPC?` bzw. Triggerverhalten prüfen |
| Averaging, Frequenzquelle, Wirkungsgrad, SQ-Formel, Synchronisation | in `ComputationConfig` vorhanden | benutzerdefinierte Ausdrücke und weitere Spezialberechnungen nur bei Bedarf |
| Oberschwingungsanalyse | in `HarmonicsConfig` und Harmonics-Profil vorhanden | Geräteabnahme und Einheiten einzelner Faktoren verifizieren |
| Sitzungsbackup | Input, Bereiche, Items, Integration, Computation und Harmonics gebündelt | geräteeigenen Setup-Speicher nur als zusätzliches Sicherheitsnetz prüfen |
| Zyklusbasierte Messung (`:CBCycle`) | Gerät unterstützt sie, Treiber noch nicht | bei konkretem Anwendungsfall implementieren |
| Motorwirkungsgrad (`:MOTor`) | am Gerät ansprechbar, Treiber noch nicht | bei konkretem Anwendungsfall implementieren |
| Wellenform/Rohdaten | nicht im Treiber | einfache `:WAVeform`-Abfrage wäre ohne Option möglich; `:ACQuisition` braucht `G6` |
| Flicker | nicht im Treiber und am geprüften Gerät nicht verfügbar | kein aktuelles Ziel |
| Setup-, Datei- und Bildverwaltung am Gerät | nicht im Treiber | nur bei Bedarf; Python-seitiges Backup und Export haben Vorrang |
| Analogausgang | nicht im Treiber und am geprüften Gerät nicht verfügbar | kein aktuelles Ziel |

## Bereits geschlossene Kernlücken

- Geräteoptionen und Elementbestückung werden im Steckbrief erfasst.
- Integration kann konfiguriert, gestartet, gestoppt, zurückgesetzt und überwacht
  werden.
- Rechen- und Harmonicseinstellungen besitzen strukturierte `capture()`-/`restore()`-
  Paare.
- `SessionBackup` führt die vorhandenen Sicherungen zusammen und prüft nach dem
  Restore den Endzustand.
- Messungen können blockierend, als Generator oder als steuerbares Hintergrundobjekt
  laufen.
- Geräterate, Dublettenstatus und bekannte Einheiten werden mit den Daten ausgegeben.

## Offene Querschnittsaufgaben

Die größten Lücken sind nicht mehr einzelne SCPI-Gruppen, sondern Robustheit und
Auslieferung:

1. Hardwareannahmen aus M0 reproduzierbar abschließen.
2. Parser-, Header- und Scope-Regeln vereinheitlichen (S-02/M2-5).
3. Timeout, Antwortqueue und Wiederverbindung konsistent behandeln (S-03/S-05/M3-4).
4. Fehlende Zyklen in der festen Spaltenstruktur darstellen (S-08).
5. Rotation, Fortsetzen, CLI, CI und Paketmetadaten ergänzen (M4-4/M5).

Spezialgruppen wie CBCycle, Motor oder Wellenform werden erst umgesetzt, wenn eine
Messaufgabe sie tatsächlich benötigt. Das verhindert, dass eine breite, aber
unabgenommene SCPI-Abdeckung die wichtigeren Fehler- und Langzeitpfade verdrängt.
