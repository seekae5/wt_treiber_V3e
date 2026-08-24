# Gesamturteil zur Eignung für automatisierte Messabläufe

**Projekt:** `wt3000-scpi` für Yokogawa WT3000  
**Prüfstand:** 25. August 2026  
**Bewertungsumfang:** Gerätekonfiguration, Messwerterfassung und Speicherung  

> **Fortschreibung vom 25.08.2026, nach M1-3.** Die Erstfassung entstand zwischen den
> Meilensteinen M3-3 und M1-3. Zwei ihrer Befunde sind seither erledigt — die
> Elementliste von `InputConfig` und die veraltende SIGMA-/SIGMB-Zuordnung nach einer
> Umverdrahtung; die betroffenen Zeilen sind unten fortgeschrieben und mit **erledigt
> 25.08.2026** gekennzeichnet. Neu hinzugekommen ist der Abschnitt
> [Einschätzung der Maßnahmen](#einschätzung-der-maßnahmen), der die Prioritätenliste
> nach Aufwand, Abhängigkeit und Nutzen bewertet. Alle übrigen Befunde der Erstfassung
> wurden gegen den Quellstand nachgeprüft und bestätigt.

| Gesamtstatus | Urteil |
|---|---|
| **Noch nicht vollständig für unbeaufsichtigte automatisierte Messabläufe geeignet** | • Gerätekonfiguration und Datenspeicherung sind weitgehend vorhanden.<br>• Eine blockierende Messaufzeichnung ist bereits möglich.<br>• Die entscheidende Lücke ist eine programmatisch steuerbare Messung mit `start()`, sofortigem `stop()`, `wait()` und klarer Fehlerweitergabe.<br>• Kommunikationsabbrüche, fehlende Messzyklen und ein sicherer Langzeit-Dateibetrieb sind noch nicht vollständig behandelt.<br>• Mehrere schreibende Funktionen müssen noch abschließend am realen WT3000 verifiziert werden. |

**Zwei Betriebsarten, zwei Urteile.** Das Gesamturteil oben gilt für den
*unbeaufsichtigten* Betrieb. Für den häufigeren Fall fällt es anders aus:

| Betriebsart | Urteil |
|---|---|
| **Skriptgesteuerter Lauf mit vorab bekanntem Ende** — konfigurieren, `n` Datensätze oder `t` Sekunden messen, speichern, fertig | **Geeignet, mit drei Vorbehalten.** Der Ablauf ist gerätefrei vollständig durchgespielt — Wiring, Bereiche, Rate, Messung, Echtzeitausgabe, CSV/JSONL/Sidecar. Was ihn trotzdem trifft: ein einzelner Kommunikationsfehler beendet auch einen kurzen Lauf (**A4**), die Ergebnisdatei trägt keine Einheiten (**A5**), und ein am Bedienfeld verstellter Protokollzustand lässt ihn gar nicht erst beginnen (**A8**). Das steuerbare Messobjekt braucht dieser Fall dagegen **nicht**. |
| **Überwachter Automat** — Messung läuft, während der Ablauf andere Anlagenaktionen ausführt und aufgrund externer Bedingungen beendet | **Nicht geeignet.** Es fehlen das steuerbare Messobjekt und die Entscheidung zum Sitzungsbesitz. |
| **Unbeaufsichtigter Langzeitlauf** über Stunden oder Tage | **Nicht geeignet.** Ein einzelner Kommunikationsfehler beendet die Reihe; Rotation und Fortsetzung fehlen. |

Diese Unterscheidung ist für die Reihenfolge der Maßnahmen entscheidend — siehe
[Einschätzung der Maßnahmen](#einschätzung-der-maßnahmen).

## Bewertung der geforderten Funktionen

| Bereich | Status | Vorhanden | Fehlt oder ist unvollständig | Bedeutung für den automatisierten Einsatz |
|---|---|---|---|---|
| **1. Gerätekonfiguration – Gesamt** | **Weitgehend vorhanden** | • Lesen und Schreiben der Eingangskonfiguration<br>• Schreibschutz, Rücklesekontrolle und Gerätefehlerprüfung<br>• Sicherung, Vergleich und Wiederherstellung von Konfigurationen | • Kein übergeordneter, zusammenhängender Konfigurationsablauf für Wiring, Ranges, Rate, Item-Tabelle und Protokollzustand<br>• Einige Geräteannahmen noch nicht praktisch bestätigt<br>• `applied_ranges()` — der Context Manager, der Bereiche setzt und garantiert zurückstellt — ist **nicht durch Tests abgedeckt** (`wt3000_ranging.py`: 60 % Zeilenabdeckung, die Lücke liegt genau auf Apply, Verify und Restore) | Die benötigten Einzelbausteine existieren. Der aufrufende Automatisierungsablauf muss Reihenfolge, Freigaben, Sicherung und Abschlussprüfung derzeit selbst koordinieren. Die Elementliste ist seit M1-3 zwischen `wt.input` und `wt.ranges` einheitlich, ein Ablauf kann sich also auf ein Zielverständnis verlassen. |
| **Wiring der Elemente** | **Vorhanden** | • Wiring lesen<br>• Muster `P1W2`, `P1W3`, `P3W3`, `P3W4`, `V3A3` und `NONE` setzen<br>• Eingaben vor dem Senden prüfen<br>• Ergebnis nach dem Schreiben zurücklesen<br>• **erledigt 25.08.2026:** die Folgezustände werden selbsttätig nachgezogen — `set_wiring()` frischt Gerätesteckbrief, Elementliste und SIGMA-/SIGMB-Zuordnung auf, `wt.refresh_device()` tut es nach einem Eingriff am Bedienfeld | • Vollständige Geräteabnahme und Firmwarevarianten nicht abschließend dokumentiert | Wiring ist vor Bereichen, Rechenfunktionen und Item-Tabelle zu setzen. Die anschließende Neubewertung der SIGMA-/SIGMB-Ziele war bis M1-3 Sache des Aufrufers und ohne Auffrischungsmethode gar nicht durchführbar — sie geschieht jetzt von selbst. |
| **Messbereiche der Elemente** | **Weitgehend vorhanden** | • Spannungsbereiche<br>• Direkte Strombereiche<br>• Externe Stromsensorbereiche<br>• Auto-Range<br>• Element-, ALL- und SIGMA-Ziele<br>• Range-Pläne mit Backup, Verifikation und Restore | • Schreibsyntax für Direktstrom- und Sensorbereiche im Code noch als „ZU VERIFIZIEREN“ markiert<br>• Bereichstabellen werden weiterhin über feste Schlüssel `(Modultyp, Crest)` gewählt; ein unbekanntes Modul ergibt einen `KeyError` statt einer `WTError` mit Kontext<br>• **erledigt 25.08.2026:** `InputConfig` löst `ALL` gegen die gelesene Bestückung auf statt gegen die feste Liste 1 bis 4, und eine nicht bestückte Elementnummer wird abgelehnt statt gesendet | Vor produktivem Einsatz sind Strom- und Sensorbereich an der konkreten Geräte-/Firmwareversion praktisch zu prüfen. Die Gefahr, auf einem 3-Element-Gerät den Knoten `:ELEMent4` zurückzulesen, besteht nicht mehr. |
| **Geräte-Datenaktualisierungsrate `:RATE`** | **Vorhanden** | • Lesen und Setzen von 0,05 bis 20 Sekunden<br>• Zulässige Werte werden vor dem Senden geprüft<br>• Rücklesekontrolle nach dem Setzen | • Nicht identisch mit dem Python-Ausgabeintervall<br>• Nicht identisch mit einer Wellenform-Samplingrate | Für normale numerische Messwerte ist die Geräterate konfigurierbar. Der Automatisierungsablauf muss zusätzlich das Ausgabeintervall festlegen. |
| **Interne Wellenform-Samplingrate** | **Fehlt** | • Gerätehandbuch ist im Repository vorhanden | • Keine Implementierung der `:ACQuisition`-Gruppe<br>• Kein Abruf von Wellenform-Samplingdaten | Nur relevant, wenn mit „Sample-Rate“ die interne Rohdaten-/Wellenformabtastung gemeint ist. Für normale `:NUMeric`-Messwerte ist stattdessen `:RATE` maßgeblich. |
| **Konfigurationssicherung** | **Vorhanden** | • Eingangskonfiguration, Bereiche, Item-Tabelle, Integration, Rechenfunktionen und Harmonics sicherbar<br>• JSON-Sicherung möglich<br>• Restore mit abschließender Gegenprüfung | • Kein einzelner Context Manager, der eine vollständige Messrezeptur automatisch anwendet und danach komplett zurückstellt | Gute Grundlage für sichere Automatisierung; die Orchestrierung liegt noch beim Anwenderprogramm. |
| **Erforderlicher Protokollzustand** | **Nur Prüfung vorhanden** | • Prüfung von `:COMMunicate:HEADer 0` und `:NUMeric:FORMat FLOat` | • Sollzustand wird nicht automatisch hergestellt und später wiederhergestellt<br>• Messstart führt die Prüfung nicht automatisch aus | Der Ablauf muss die Prüfung ausdrücklich vor der ersten Messung aufrufen oder den Gerätezustand anderweitig garantieren. |
| **2. Messung – Gesamt** | **Teilweise vorhanden** | • Einzelmessungen<br>• Blockierende Messschleife<br>• Zeit- oder samplebegrenzte Läufe<br>• Live-Callback und gleichzeitige Dateiausgabe<br>• HOLD-Snapshot, Zeitstempel und Statistik | • Kein steuerbares Messobjekt<br>• Kein sofortiges programmgesteuertes Stoppen<br>• Keine robuste Weiterführung nach Kommunikationsfehlern<br>• Kein ereignisgesteuerter Gerätetakt | Für einfache, im Voraus begrenzte Messläufe geeignet. Für komplexe Automaten, externe Stopbedingungen oder unbeaufsichtigte Langzeitmessungen noch unvollständig. |
| **Messwerte lesen und formatieren** | **Vorhanden** | • Werteliste oder Zuordnung nach Messgrößennamen<br>• Zeitstempel, monotone Laufzeit, Sample-Nummer und Condition<br>• Kennzeichnung `OK`, `NO_DATA`, `OVERRANGE` und `DUPLICATE` | • Messwerte tragen in der Ergebnisstruktur keine verbindlichen Einheiten<br>• Item-Namen und Einheiten müssen extern interpretiert werden | Die Werte sind technisch auswertbar, aber noch nicht vollständig selbsterklärend. |
| **Echtzeitausgabe zur definierten Rate** | **Teilweise vorhanden** | • Ausgabe über `CallbackSink`<br>• Gleichzeitige Ausgabe in Callback und Datei über `MultiSink`<br>• Driftarme Taktplanung<br>• Erkennung von Overruns<br>• Vergleich von Ausgabeintervall und Geräterate | • Nur Soft-Realtime über `time.sleep()`<br>• Langsame Abfragen oder Callbacks verzögern den nächsten Zyklus<br>• Kein Warten auf ein nachgewiesenes Geräte-Aktualisierungsereignis | Für Anzeigen und übliche Sekundenraten brauchbar. Keine Garantie für harte Echtzeit oder exakt phasensynchrone Ausgabe. |
| **Messung beginnen** | **Teilweise vorhanden** | • `record()` beginnt unmittelbar mit der blockierenden Aufzeichnung<br>• Geräteintegration besitzt ein separates `start()` | • Kein allgemeines `Measurement.start()`<br>• Kein Hintergrundbetrieb als Bibliotheksfunktion<br>• Kein allgemeiner Hardwaretrigger über `*TRG` | „Start“ bedeutet aktuell hauptsächlich „blockierende Aufzeichnung aufrufen“. Ein Automat kann keine eigenständige Messinstanz starten und anschließend andere Aufgaben fortsetzen. |
| **Messung beenden** | **Teilweise vorhanden** | • Ende nach `max_samples`<br>• Ende nach `max_duration_s`<br>• Abbruch mit Strg+C<br>• Sink und HOLD werden im Cleanup geschlossen beziehungsweise abgeschaltet | • Kein programmgesteuertes `stop()` für einen laufenden normalen Messlauf<br>• Kein `wait()` und kein `is_running`<br>• Kein sofort unterbrechbares Stop-Event | Vorab bekannte Laufzeiten funktionieren. Externe Stopbedingungen, Bedienereingriffe oder Anlagenereignisse lassen sich nicht sauber als Stoppsignal übergeben. |
| **Nebenläufigkeit und Sitzungsbesitz** | **Fehlt** | • Der Quellcode dokumentiert das Problem ausdrücklich | • `WTSession` ist nicht threadsicher<br>• Kein Lock und keine Sperre anderer Gerätezugriffe während einer Messung<br>• Eigenständiges Verschieben von `record()` in einen Thread kann Antworten vertauschen | Muss vor Einführung eines Hintergrund-Messobjekts entschieden und umgesetzt werden. |
| **Taktkopplung und Dubletten** | **Teilweise vorhanden** | • `:RATE?` wird vor dem Lauf gelesen<br>• Zu schnelles Lesen wird gemeldet<br>• Bitgleiche Datensätze werden als `DUPLICATE` gespeichert<br>• Dubletten und Overruns werden statistisch erfasst | • Zu schnelles Lesen wird nur erkannt, nicht verhindert<br>• Kein ereignisgesteuertes Warten auf neue Daten<br>• Phasensynchronität mit dem Geräteupdate nicht garantiert | Datenqualität ist nachvollziehbar, aber die Aufzeichnung erzeugt gegebenenfalls bewusst redundante Datensätze. |
| **Kommunikations- und Gerätefehler während der Messung** | **Unvollständig** | • Sink wird bei einem Fehler geschlossen<br>• HOLD-Cleanup wird versucht<br>• Fehler werden nicht stillschweigend ignoriert | • Kein Wiederverbinden<br>• Kein Retry-Konzept<br>• Kein Fortsetzen nach kurzem Ausfall<br>• `SampleMark.MISSING` existiert, wird von der Messschleife aber nicht erzeugt | Ein einzelner Kommunikationsfehler beendet eine möglicherweise lange Messreihe. Für unbeaufsichtigten Betrieb ist eine ausdrückliche Fehlerstrategie erforderlich. |
| **Geräteseitige Integration** | **Softwareseitig vorhanden, Geräteabnahme offen** | • `start()`, `stop()`, `reset()` und `running()`<br>• Betriebsart, Timer, Echtzeitfenster und Autokalibrierung<br>• Integrations-Messprofil für Wh/Ah | • Nicht dasselbe wie Start/Stop der normalen Echtzeitaufzeichnung<br>• Schreibbefehle noch nicht vollständig am realen Gerät abgenommen | Für Energie-/Wh-/Ah-Messungen ist der Softwareweg vorhanden, benötigt aber noch einen protokollierten Gerätetest. |
| **3. Speicherung – Gesamt** | **Weitgehend vorhanden** | • CSV<br>• JSON Lines<br>• Callback<br>• Mehrfachausgabe<br>• Sofortiges Flush und Cleanup | • Einheiten und verbindliche Metadaten fehlen<br>• Keine Rotation, Fortsetzung oder Append-Funktion<br>• Fehlende Zyklen noch nicht speicherbar | Für begrenzte Messläufe verwendbar. Für selbstbeschreibende Prüfdateien und lange unbeaufsichtigte Läufe noch zu ergänzen. |
| **CSV-Ausgabe** | **Vorhanden** | • Zeitstempel, Laufzeit, Sample-Nummer, Condition und Messwerte<br>• Statusflags<br>• `NO_DATA` als leere Zelle<br>• `OVERRANGE` als `INF`<br>• Flush nach jeder Zeile | • Gerätemetadaten werden nicht direkt in die CSV geschrieben<br>• Sidecar ist optional<br>• Datei wird neu angelegt beziehungsweise überschrieben | Datenverlust bei Prozessabbruch ist auf höchstens die gerade geschriebene Zeile begrenzt. Die Zuordnung zur Gerätekonfiguration muss separat abgesichert werden. |
| **JSONL-Ausgabe** | **Vorhanden** | • Benannte Messwerte<br>• Eine JSON-Zeile je Sample<br>• Laufparameter in einer Metadatenzeile<br>• Statuswerte ohne ungültige JSON-Literale | • Keine vollständige Gerätekonfiguration in der Standard-Metadatenzeile<br>• Keine verbindlichen Einheiten | Robuster und leichter selbsterklärend als CSV, aber noch kein vollständiges Prüfprotokoll. |
| **Metadaten und Einheiten** | **Unvollständig** | • Optionales Sidecar mit Geräteidentifikation, Konfiguration und Item-Tabelle<br>• Update-Rate und Laufparameter können gespeichert werden | • Sidecar nicht verpflichtend<br>• Keine feste Bindung zwischen CSV und Sidecar<br>• Keine Einheitenabbildung für die Messwertspalten | Eine Ergebnisdatei ist ohne Zusatzwissen nicht in jedem Fall eindeutig interpretierbar. |
| **Langzeit-Dateibetrieb** | **Fehlt** | • Datei wird während des Laufs kontinuierlich geschrieben | • Keine Rotation nach Zeit, Größe oder Zeilenanzahl<br>• Kein kontrolliertes Anhängen<br>• Kein Fortsetzen nach Abbruch<br>• Keine automatische Kollisionsvermeidung bei Dateinamen | Für sehr lange oder wiederaufnehmbare Messungen ist eine zusätzliche Dateiverwaltung erforderlich. |

## Bedeutung der verschiedenen Raten

| Bezeichnung | Bedeutung | Stand |
|---|---|---|
| **Geräterate `:RATE`** | Intervall, in dem das WT3000 einen neuen normalen numerischen Datensatz bildet | **Konfigurierbar** |
| **Ausgabeintervall `interval_s`** | Intervall, in dem die Python-Messschleife das Gerät abfragt und einen Datensatz ausgibt | **Konfigurierbar, aber nur Soft-Realtime** |
| **Wellenform-Samplingrate `:ACQuisition:SRATe`** | Interne Abtastrate der Wellenform-/Rohdatenerfassung | **Nicht implementiert** |

## Empfohlene Prioritäten vor dem produktiven Einsatz

> Diese Liste ist die der Erstfassung und bleibt unverändert stehen. Der nachfolgende
> Abschnitt [Einschätzung der Maßnahmen](#einschätzung-der-maßnahmen) bewertet sie nach
> Aufwand und Abhängigkeit und schlägt eine abweichende Reihenfolge vor — wer nach
> dieser Liste arbeitet, sollte ihn vorher lesen.

| Priorität | Maßnahme | Begründung |
|---|---|---|
| **1 – zwingend** | Steuerbares Messobjekt mit `start()`, `stop()`, `wait()`, `is_running` und sauberer Fehlerweitergabe | Erst damit kann ein automatisierter Ablauf eine Messung kontrolliert parallel zu anderen Anlagenaktionen führen und aufgrund externer Bedingungen beenden. |
| **2 – zwingend** | Sitzungsbesitz oder Thread-Synchronisation eindeutig umsetzen | Verhindert vertauschte Geräteantworten und beschädigte Messdaten bei Nebenläufigkeit. |
| **3 – zwingend für unbeaufsichtigte Läufe** | Strategie für Kommunikationsfehler, Retry, Abbruch und `MISSING`-Samples festlegen | Ein kurzer Kommunikationsfehler darf nicht unkontrolliert eine lange Messreihe zerstören oder unsichtbare Lücken erzeugen. |
| **4 – hoch** | Vollständige Messrezeptur mit Konfiguration, Backup, Apply, Verifikation und Restore bereitstellen | Verringert Fehlbedienung und stellt einen reproduzierbaren Ausgangszustand sicher. |
| **5 – hoch** | Schreibfunktionen am realen WT3000 protokolliert abnehmen | Besonders Strombereich, Sensorbereich, REMOTE-Verhalten und Integrationssteuerung sind noch praktisch zu bestätigen. |
| **6 – hoch** | Einheiten und vollständige Gerätemetadaten verbindlich mit jeder Ergebnisdatei speichern | Macht Messdateien später ohne externes Wissen eindeutig interpretierbar. |
| **7 – mittel** | Dateirotation, Kollisionsschutz und kontrolliertes Fortsetzen ergänzen | Erforderlich für lange und wiederaufnehmbare Messkampagnen. |

## Einschätzung der Maßnahmen

Die Prioritätenliste oben beantwortet die Frage *was fehlt*. Dieser Abschnitt beantwortet
*was zuerst* — nach Aufwand, Abhängigkeit und Nutzen für den konkreten Zweck
„Skripte zur automatisierten Messung“. Aufwand in der Notation der ROADMAP:
`S` unter einem Tag, `M` ein bis drei Tage, `L` darüber.

### Bewertung der einzelnen Maßnahmen

| Nr. | Maßnahme (Nummer der Liste oben) | Aufwand | Hängt ab von | Einschätzung |
|---|---|---|---|---|
| **A1** | **Gerätetermin M0** *(Liste: 5)* | `S` Durchführung, **Wochen Vorlauf** | Gerät, Termin, Person | **Höher einzustufen als Rang 5.** Als einzige Maßnahme der Liste lässt sie sich nicht durch Programmieren erledigen und nicht nachholen, wenn alles andere fertig ist. Sie blockiert die Abnahme jedes schreibenden Pfades — Strombereich, Sensorbereich, REMOTE-Verhalten, Integrationssteuerung. **Sofort anstoßen und parallel laufen lassen**; die Prüfliste steht fertig in [OFFENE_PUNKTE.md](OFFENE_PUNKTE.md), Abschnitt 1 (H-01…H-07). |
| **A2** | **Sitzungsbesitz entscheiden** *(Liste: 2)* | `S` Entscheidung, `S`–`M` Umsetzung | — | **Ist Voraussetzung von A3, nicht deren Nachbar.** Die Liste führt sie als eigenständigen Rang 2; tatsächlich lässt sich das steuerbare Messobjekt ohne diese Entscheidung nicht bauen, ohne sie später wieder aufzureißen. Die beiden Wege stehen samt Abwägung im Klassenkopf von `WTSession` — es ist eine Entscheidung, keine Untersuchung. |
| **A3** | **Steuerbares Messobjekt** *(Liste: 1)* | `M` | A2 | **Gut vorbereitet, aber nicht für jeden Zweck dringend.** Die drei Umbaustellen sind im Docstring von `run_measurement_loop()` bereits benannt: `KeyboardInterrupt` wandert zum Aufrufer, `time.sleep()` wird `stop_event.wait()`, und die Zuständigkeit für Bereiche und Item-Tabelle ist zu klären. **Für skriptgesteuerte Läufe mit bekanntem Ende wird die Maßnahme nicht gebraucht** — dort ist die blockierende Schleife die einfachere und ehrlichere Lösung. |
| **A4** | **Fehlerstrategie, Retry, `MISSING`** *(Liste: 3)* | `M` | A2 (Wiederverbinden greift in den Sitzungsbesitz ein) | **Für unbeaufsichtigte Läufe die wichtigste Maßnahme überhaupt.** Nachgemessen: ein einzelner fehlgeschlagener Lesevorgang beendet die Reihe. Entlastend ist, dass dabei nichts verloren geht — die CSV wird pro Zeile geflusht und im `finally` geschlossen. Der Ausfall ist also ein Abbruch, kein Datenverlust. Enthält die noch offene Teilentscheidung aus **S-08**: ein `MISSING`-Datensatz trägt keine Werte und kollidiert mit der strengen Spaltenregel. |
| **A5** | **Einheiten und verbindliche Metadaten** *(Liste: 6)* | `S`–`M` | — | **Deutlich zu weit hinten.** Die Maßnahme ist klein, hängt von nichts ab und verbessert **jede** ab dann erzeugte Datei dauerhaft. Die Zuordnung Funktionsname → Einheit ist eine Tabelle; die Funktionsnamen stehen bereits in der Item-Tabelle. Jede Messdatei, die vorher entsteht, bleibt dagegen für immer auf externes Wissen angewiesen. **Vorziehen.** |
| **A6** | **`applied_ranges()` unter Test stellen** *(in der Liste nicht enthalten)* | `S` | — | **Fehlt in der Liste und gehört vor A7.** Der Context Manager, der Messbereiche setzt und garantiert zurückstellt, ist heute ungetestet (60 % Zeilenabdeckung in `wt3000_ranging.py`, die Lücke genau auf Apply/Verify/Restore). Eine Messrezeptur auf einem ungeprüften Rückstellpfad aufzubauen, ist die falsche Reihenfolge. Die Vorlage existiert: `tests/test_backup.py` spielt denselben Zyklus für `SessionBackup` bereits durch. |
| **A7** | **Vollständige Messrezeptur** *(Liste: 4)* | `M` | A6 | **Richtig eingeordnet, aber mit A6 als Vorbedingung.** Die Bausteine sind vorhanden — `SessionBackup`, `applied_ranges()`, `ItemAccess.applied()`. Zu bauen ist die Orchestrierung, nicht die Mechanik. |
| **A8** | **Protokollzustand herstellen (M1-4)** *(in der Liste nicht enthalten)* | `S` | — | **Fehlt in der Liste, obwohl die Bewertungstabelle den Punkt führt.** Heute prüft `check_protocol_state()` nur, und `record()` ruft sie nicht auf. Folge: Hat jemand am Bedienfeld `:COMMunicate:HEADer 1` eingestellt, scheitert jeder automatisierte Lauf — an einer Ursache, die der Treiber in einem Kommando selbst beheben und beim Verlassen zurückstellen könnte. Für unbeaufsichtigten Betrieb ist das eine häufige und vermeidbare Abbruchursache. |
| **A9** | **Dateirotation und Fortsetzen** *(Liste: 7)* | `S` | — | **Richtig eingeordnet.** Wird erst bei Läufen über Stunden gebraucht; vorher genügt ein Dateiname je Lauf. Der Hinweis auf fehlenden Kollisionsschutz ist berechtigt: `CsvSink` öffnet mit `"w"` und überschreibt eine bestehende Datei wortlos. |
| **A10** | **Wellenform-Samplingrate `:ACQuisition`** | `M` | Bedarf klären | **Nur bei tatsächlichem Bedarf.** Am Quellstand bestätigt: `:ACQuisition` kommt ausschließlich in der Optionstabelle vor, nicht als umgesetzte Gruppe. Für `:NUMeric`-Messwerte — den erklärten Zweck — ist `:RATE` maßgeblich und vollständig vorhanden. Die Maßnahme sollte erst begonnen werden, wenn jemand Rohdaten wirklich braucht. |

### Empfohlene Reihenfolge

| Zug | Maßnahmen | Ergebnis danach |
|---|---|---|
| **Sofort, parallel** | **A1** anstoßen | Der Termin läuft, während weitergearbeitet wird. |
| **Zug 1** | **A5**, **A6**, **A8** — drei kleine, voneinander unabhängige Schritte | Messdateien sind selbsterklärend, der Rückstellpfad ist abgesichert, ein verstellter Protokollzustand bricht keinen Lauf mehr ab. |
| **Zug 2** | **A2** entscheiden, dann **A4** | Skriptgesteuerte Läufe überleben einen kurzen Kommunikationsfehler; Lücken sind als `MISSING` sichtbar statt unsichtbar. |
| **Zug 3** | **A3**, danach **A7** | Ein Automat kann eine Messung nebenher führen und von außen beenden; die Rezeptur bündelt Konfiguration und Rückstellung. |
| **Später** | **A9**, **A10 nach Bedarf** | Langzeit- und Rohdatenbetrieb. |

### Was an der Ausgangsbewertung anzupassen ist

1. **Rang 1 und 2 sind vertauscht.** Der Sitzungsbesitz (A2) ist Voraussetzung des
   steuerbaren Messobjekts (A3), nicht dessen gleichrangiger Nachbar.
2. **Rang 5 ist zu spät.** Der Gerätetermin (A1) hat als einziger Punkt einen Vorlauf,
   den kein Programmieraufwand aufholt.
3. **Rang 6 ist zu spät.** Einheiten (A5) sind klein und wirken dauerhaft auf jede Datei.
4. **Zwei Maßnahmen fehlen:** die Testabdeckung von `applied_ranges()` (A6) und das
   Herstellen des Protokollzustands (A8).
5. **Rang 1 ist zweckabhängig.** Für Skripte mit vorab bekanntem Ende — der erklärte
   Anwendungsfall — wird das steuerbare Messobjekt nicht benötigt. Dort ist **A4** die
   Maßnahme mit dem größten Nutzen.

## Prüfhinweise

| Punkt | Ergebnis |
|---|---|
| Quellcodeänderungen durch die Bewertung selbst | **Keine.** Die Bewertung liest und urteilt. Die anschließend umgesetzten Änderungen (M3-3, M1-3) sind eigene Vorgänge und in [ROADMAP.md](ROADMAP.md) verzeichnet. |
| Importprüfung | **Erfolgreich:** Paketversion `0.3.0`, 13 Fachmodule importierbar. Zusammen mit den 5 Stufenskripten und `__init__.py` sind es 19 Dateien — die Angabe „13 Module“ meint die Fachmodule und ist zutreffend. |
| Vollständiger Testlauf | **Nachgeholt am 25.08.2026:** `682 passed, 1 skipped` in 1,08 s. Die Erstfassung konnte nicht messen, weil `pytest` in der dortigen Umgebung fehlte; die Suite läuft in einer eigenen Umgebung (`python3 -m venv`, `pip install -e ".[test]"`) ohne Gerät und ohne DLL. |
| Statische Prüfung | **Nachgeholt:** `ruff check .` und `mypy` ohne Befund. |
| Zeilenabdeckung | **Gemessen:** 87 % über das Paket. Schwächste Stelle ist `wt3000_ranging.py` mit 60 %, und die Lücke liegt auf `apply_plan`, `verify_plan`, `restore_ranges` und `applied_ranges` — also auf der Zusage „jede Änderung ist umkehrbar“. Siehe Maßnahme **A6**. |
| Hardwareprüfung | Im Repository sind einzelne reale Geräteprüfungen dokumentiert; mehrere schreibende Pfade sind weiterhin ausdrücklich als offen beziehungsweise zu verifizieren gekennzeichnet. Unverändert der wichtigste Vorbehalt dieser Bewertung. |
| Dokumentationsstand | **Nachgezogen am 25.08.2026.** Der Befund traf zu: README und Anwendungshandbuch nannten erledigte Punkte als offen (Bereichssyntax, `dll_path`, Elementliste), führten veraltete Testzahlen und einen toten Link. Beide sind überarbeitet; `docs/OFFENE_PUNKTE.md` behauptete zusätzlich, `drain_after_failure()` werde nirgends aufgerufen, obwohl es vier Aufrufstellen gibt. |

## Zugehörige Quellstellen

| Thema | Datei |
|---|---|
| Wiring, Bereiche und Update-Rate | [`wt3000_input.py`](../src/wt3000_scpi/wt3000_input.py) |
| Bereichspläne und Wiederherstellung | [`wt3000_ranging.py`](../src/wt3000_scpi/wt3000_ranging.py) |
| Messschleife und Datensatz | [`wt3000_measure.py`](../src/wt3000_scpi/wt3000_measure.py) |
| CSV, JSONL, Callback und MultiSink | [`wt3000_sinks.py`](../src/wt3000_scpi/wt3000_sinks.py) |
| Gerätefassade und Backup | [`wt3000_device.py`](../src/wt3000_scpi/wt3000_device.py) |
| Integration und weitere Gerätekonfiguration | [`wt3000_deviceconfig.py`](../src/wt3000_scpi/wt3000_deviceconfig.py) |
| Thread-Sicherheit der Sitzung | [`wt3000_core.py`](../src/wt3000_scpi/wt3000_core.py) |
| Taktkopplung und Dublettenerkennung (M3-3) | [`tests/test_takt_und_dubletten.py`](../tests/test_takt_und_dubletten.py) |
| Elementliste und Steckbrief-Auffrischung (M1-3) | [`tests/test_geraetebezug.py`](../tests/test_geraetebezug.py) |
| Offene Punkte und Gerätefragen | [`OFFENE_PUNKTE.md`](OFFENE_PUNKTE.md) |

