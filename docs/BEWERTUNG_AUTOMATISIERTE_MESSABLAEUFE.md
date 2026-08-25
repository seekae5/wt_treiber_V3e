# Eignung für automatisierte Messabläufe

**Stand:** 25. August 2026 · `wt3000-scpi 0.3.0`
**Umfang:** Konfiguration, numerische Messwerterfassung und Speicherung

## Gesamturteil

| Betriebsart | Urteil | Hauptvorbehalt |
|---|---|---|
| Skriptlauf mit bekanntem Ende | **geeignet** | ohne `ErrorPolicy` beendet ein Kommunikationsfehler weiterhin den Lauf — das ist die Voreinstellung und Absicht |
| Überwachter Automat | **geeignet mit Vorbehalt** | während einer Hintergrundmessung gehört die WT3000-Sitzung exklusiv dem Mess-Thread |
| Unbeaufsichtigter Langzeitlauf | **geeignet** | mit `ErrorPolicy.unattended()` und `RotationPolicy`; offen bleibt allein die Geräteabnahme (M0) |

Die früher größten Lücken — Einheiten, getestete Bereichsrückstellung,
Protokollzustand, Sitzungsbesitz, eine steuerbare Messung, die Fehlerstrategie bei
Kommunikationsabbrüchen und der Langzeit-Dateibetrieb — sind geschlossen. **Alle drei
Betriebsarten sind damit softwareseitig abgedeckt.** Entscheidend offen ist nur noch
die Abnahme am realen Gerät (M0): mehrere schreibende Pfade und das Verhalten bei
einem echten Verbindungsabriss sind gerätefrei geprüft, aber nicht belegt.

## Funktionsstand

| Bereich | Stand | Offen |
|---|---|---|
| Eingangskonfiguration und Wiring | Lesen, geschütztes Schreiben, Rücklesen, Snapshot/Restore; Gerätesteckbrief wird nach Wiring aktualisiert | Geräteabnahme und Parserkonsolidierung |
| Messbereiche | direkt, extern, Auto-Range, Pläne, Backup und garantierter Restore | Strom-/Sensorsyntax am Gerät; unbekannte Module sauber behandeln |
| Geräterate | Lesen, Setzen und Plausibilisierung gegen Python-Takt | ereignisgesteuertes Warten |
| Item-Tabelle | Profile, Apply/Restore, Tail-Sicherung | Spezialprofile nur bei Bedarf |
| Messung | `record()`, `start()`/`stop()`/`wait()` und `stream()` mit gemeinsamem Kern; `ErrorPolicy` mit `MISSING`-Zyklen, Fehlergrenzen und geprüfter Wiederverbindung | ereignisgesteuertes Warten auf einen neuen Datensatz (hängt an M0-5) |
| Datenexport | CSV, JSONL, Callback, MultiSink; Status, Rate, bekannte Einheiten, sichtbare Lücken (`mark=MISSING` bei voller Spaltenzahl), Rotation nach Zeilen/Größe/Zeit und geprüftes Fortsetzen | feste Bindung zwischen Messdatei und Metadaten |
| Integration | Konfiguration, Start/Stop/Reset, Profil und Zustandsüberwachung | kompletter Lauf am realen Gerät |
| Rechenfunktionen | Averaging, Frequenzquelle, Effizienz, SQ-Formel, Synchronisation | weitere Spezialgruppen nur bei Bedarf |
| Harmonics | Konfiguration und Messprofil mit Optionsprüfung | Geräteabnahme; Einheiten einzelner Faktoren |
| Sicherung | gemeinsames, versioniertes `SessionBackup` mit Identitäts- und Endprüfung | geräteeigenes Setup optional prüfen |

## Priorisierte Maßnahmen

| Rang | Maßnahme | Nutzen |
|---|---|---|
| 1 | Hardwarefragen gebündelt prüfen (M0) | entfernt verbliebene Syntax- und Firmwareannahmen; einziger Punkt mit Vorlauf |
| 2 | Parser und Scope-Regeln zusammenführen (M2-5) | verhindert widersprüchliche Antworten und weitere Duplikate |
| 3 | Feste Metadatenbindung (M4-3) | macht eine Messdatei ohne Zusatzwissen eindeutig |
| 4 | Allgemeine Fehlersemantik (M1-5/S-03, S-05) | `WTError` an allen Paketgrenzen statt vereinzelter `KeyError` |
| 5 | CLI, CI und Paketmetadaten (M5) | macht die Bibliothek reproduzierbar auslieferbar |

Die Fehlerstrategie für Kommunikationsabbrüche (früher Rang 1) ist umgesetzt: eine
`ErrorPolicy` macht aus einem ausgefallenen Zyklus einen Datensatz mit
`SampleMark.MISSING` bei voller Spaltenzahl, begrenzt die Nachsicht über
`max_consecutive` und `max_total` und baut die Verbindung bei Bedarf neu auf. Nach
einem Neuaufbau werden Zahlenformat, Header und Item-Tabelle nachgeprüft — eine
Abweichung beendet den Lauf, statt Zeilen unter falschen Spalten fortzuschreiben.
Ohne `ErrorPolicy` bleibt es beim bisherigen Verhalten.

Wellenform, CBCycle, Motor, Flicker und Analogausgang sind keine Voraussetzung für
normale numerische Messreihen. Sie sollten nur für einen konkreten Anwendungsfall
priorisiert werden.

## Abnahme vor produktivem Einsatz

- Schreibende Bereichs-, Wiring-, Integrations- und Harmonicsabläufe am Zielgerät
  protokolliert prüfen.
- Timeout mit verspäteter Antwort simulieren und Folgequery sowie Cleanup verifizieren.
- Verbindungsabbruch, Wiederanlauf und Abbruchgrenze am echten Gerät durchspielen —
  gerätefrei sind sie abgedeckt, der reale Abriss (Kabel, Stromausfall) nicht.
- Lange Messdatei mit Rotation, Metadaten, Einheiten, Dubletten und fehlenden Zyklen
  auswerten.
- Vollständige Tests, Ruff und Mypy in CI ausführen.

Einzelheiten stehen in [OFFENE_PUNKTE.md](OFFENE_PUNKTE.md) und
[ROADMAP.md](ROADMAP.md). Die historische, zugweise Fortschreibung wurde entfernt,
weil sie denselben heutigen Stand mehrfach mit verschiedenen Testzahlen beschrieb.
