# Eignung für automatisierte Messabläufe

**Stand:** 25. August 2026 · `wt3000-scpi 0.3.0`
**Umfang:** Konfiguration, numerische Messwerterfassung und Speicherung

## Gesamturteil

| Betriebsart | Urteil | Hauptvorbehalt |
|---|---|---|
| Skriptlauf mit bekanntem Ende | **geeignet mit Vorbehalt** | ein Kommunikationsfehler beendet den Lauf |
| Überwachter Automat | **geeignet mit Vorbehalt** | während einer Hintergrundmessung gehört die WT3000-Sitzung exklusiv dem Mess-Thread |
| Unbeaufsichtigter Langzeitlauf | **noch nicht geeignet** | Wiederverbindung, fehlende Zyklen und Dateirotation fehlen |

Die früher größten Lücken — Einheiten, getestete Bereichsrückstellung,
Protokollzustand, Sitzungsbesitz und eine steuerbare Messung — sind geschlossen.
Entscheidend offen ist heute die Fehlerstrategie bei Kommunikationsabbrüchen.

## Funktionsstand

| Bereich | Stand | Offen |
|---|---|---|
| Eingangskonfiguration und Wiring | Lesen, geschütztes Schreiben, Rücklesen, Snapshot/Restore; Gerätesteckbrief wird nach Wiring aktualisiert | Geräteabnahme und Parserkonsolidierung |
| Messbereiche | direkt, extern, Auto-Range, Pläne, Backup und garantierter Restore | Strom-/Sensorsyntax am Gerät; unbekannte Module sauber behandeln |
| Geräterate | Lesen, Setzen und Plausibilisierung gegen Python-Takt | ereignisgesteuertes Warten |
| Item-Tabelle | Profile, Apply/Restore, Tail-Sicherung | Spezialprofile nur bei Bedarf |
| Messung | `record()`, `start()`/`stop()`/`wait()` und `stream()` mit gemeinsamem Kern | Wiederverbindung und fehlende Zyklen |
| Datenexport | CSV, JSONL, Callback, MultiSink; Status, Rate und bekannte Einheiten | Rotation und feste Metadatenbindung |
| Integration | Konfiguration, Start/Stop/Reset, Profil und Zustandsüberwachung | kompletter Lauf am realen Gerät |
| Rechenfunktionen | Averaging, Frequenzquelle, Effizienz, SQ-Formel, Synchronisation | weitere Spezialgruppen nur bei Bedarf |
| Harmonics | Konfiguration und Messprofil mit Optionsprüfung | Geräteabnahme; Einheiten einzelner Faktoren |
| Sicherung | gemeinsames, versioniertes `SessionBackup` mit Identitäts- und Endprüfung | geräteeigenes Setup optional prüfen |

## Priorisierte Maßnahmen

| Rang | Maßnahme | Nutzen |
|---|---|---|
| 1 | Fehlerstrategie und Wiederaufnahme (M1-5/M3-4) | Voraussetzung für unbeaufsichtigten Betrieb |
| 2 | Hardwarefragen gebündelt prüfen (M0) | entfernt verbliebene Syntax- und Firmwareannahmen |
| 3 | Parser und Scope-Regeln zusammenführen (M2-5) | verhindert widersprüchliche Antworten und weitere Duplikate |
| 4 | Rotation und Metadatenbindung (M4-3/M4-4) | macht lange Messungen dauerhaft auswertbar |
| 5 | CLI, CI und Paketmetadaten (M5) | macht die Bibliothek reproduzierbar auslieferbar |

Wellenform, CBCycle, Motor, Flicker und Analogausgang sind keine Voraussetzung für
normale numerische Messreihen. Sie sollten nur für einen konkreten Anwendungsfall
priorisiert werden.

## Abnahme vor produktivem Einsatz

- Schreibende Bereichs-, Wiring-, Integrations- und Harmonicsabläufe am Zielgerät
  protokolliert prüfen.
- Timeout mit verspäteter Antwort simulieren und Folgequery sowie Cleanup verifizieren.
- Verbindungsabbruch, Wiederanlauf und Abbruchgrenze durchspielen.
- Lange Messdatei mit Rotation, Metadaten, Einheiten, Dubletten und fehlenden Zyklen
  auswerten.
- Vollständige Tests, Ruff und Mypy in CI ausführen.

Einzelheiten stehen in [OFFENE_PUNKTE.md](OFFENE_PUNKTE.md) und
[ROADMAP.md](ROADMAP.md). Die historische, zugweise Fortschreibung wurde entfernt,
weil sie denselben heutigen Stand mehrfach mit verschiedenen Testzahlen beschrieb.
