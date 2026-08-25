# Gesamturteil zur Eignung für automatisierte Messabläufe

**Projekt:** `wt3000-scpi` für Yokogawa WT3000  
**Prüfstand:** 25. August 2026  
**Bewertungsumfang:** Gerätekonfiguration, Messwerterfassung und Speicherung  

> **Fortschreibung vom 25.08.2026, nach Zug 1.** Die drei Maßnahmen **A5**
> (Einheiten), **A6** (`applied_ranges()` unter Test) und **A8** (Protokollzustand
> herstellen) sind umgesetzt; die betroffenen Zeilen tragen unten **erledigt
> 25.08.2026**. Prüfstand danach: 726 Tests, Abdeckung 90 %.
>
> **Fortschreibung vom 25.08.2026, nach Zug 3.** **A2** (Sitzungsbesitz) und **A3**
> (steuerbares Messobjekt) — die Ränge 1 und 2 der Prioritätenliste — sind umgesetzt.
> Damit ist die Betriebsart **überwachter Automat** von „nicht geeignet" auf „geeignet
> mit einem Vorbehalt" gewechselt. Die größte verbliebene Lücke ist **A4**
> (Fehlerstrategie); sie allein trennt den unbeaufsichtigten Langzeitlauf noch von der
> Eignung. Prüfstand danach: 758 Tests, Abdeckung 90 %.
>
> **Nachprüfung vom 25.08.2026, gegen den Quellstand nach `082afdc`.** Die Zahl der
> Testfälle wurde von 725 bestanden/1 übersprungen auf 726 bestanden/0 übersprungen
> korrigiert — die Ursache des früheren Skips (`tools/hardware/` war vom installierten
> Paket aus nicht auffindbar) tritt im aktuellen Quellbaum nicht mehr auf. Zusätzlich
> ist die Zeile zu `applied_ranges()` in der Tabelle unten (Abschnitt „Gerätekonfiguration
> – Gesamt") nachgezogen: Sie führte die vor **A6** gültige Abdeckung von 60 % noch als
> offenen Befund, obwohl **A6** bereits als erledigt vermerkt war. Alle übrigen Aussagen
> wurden gegen den Quellstand geprüft und bestätigt.
>
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
| **Noch nicht vollständig für unbeaufsichtigte automatisierte Messabläufe geeignet** | • Gerätekonfiguration und Datenspeicherung sind weitgehend vorhanden.<br>• Blockierende, nebenläufige und generatorbasierte Messaufzeichnung sind möglich.<br>• **Seit Zug 3 geschlossen:** die programmatisch steuerbare Messung mit `start()`, sofortigem `stop()`, `wait()` und klarer Fehlerweitergabe (**A3**) samt entschiedenem Sitzungsbesitz (**A2**).<br>• **Verbleibende Hauptlücke:** Kommunikationsabbrüche und fehlende Messzyklen (**A4**) sowie ein sicherer Langzeit-Dateibetrieb (**A9**).<br>• Mehrere schreibende Funktionen müssen noch abschließend am realen WT3000 verifiziert werden (**A1**). |

**Zwei Betriebsarten, zwei Urteile.** Das Gesamturteil oben gilt für den
*unbeaufsichtigten* Betrieb. Für den häufigeren Fall fällt es anders aus:

| Betriebsart | Urteil |
|---|---|
| **Skriptgesteuerter Lauf mit vorab bekanntem Ende** — konfigurieren, `n` Datensätze oder `t` Sekunden messen, speichern, fertig | **Geeignet, mit einem Vorbehalt.** Der Ablauf ist gerätefrei vollständig durchgespielt — Wiring, Bereiche, Rate, Messung, Echtzeitausgabe, CSV/JSONL/Sidecar. Seit Zug 1 tragen die Dateien ihre Einheiten (**A5**) und ein am Bedienfeld verstellter Protokollzustand lässt sich herstellen statt abzubrechen (**A8**). Bleibt: ein einzelner Kommunikationsfehler beendet auch einen kurzen Lauf (**A4**). Das steuerbare Messobjekt braucht dieser Fall **nicht**. |
| **Überwachter Automat** — Messung läuft, während der Ablauf andere Anlagenaktionen ausführt und aufgrund externer Bedingungen beendet | **Geeignet seit Zug 3 (25.08.2026), mit einem Vorbehalt.** `wt.measure.start()` liefert ein `Measurement` mit `stop()`, `wait()` und `is_running`; der Sitzungsbesitz ist entschieden und durchgesetzt (**A2**, **A3**). Vorbehalt: Der Automat darf das **WT3000** während des Laufs nicht anfassen — andere Anlagenteile sehr wohl. Wer beides braucht, nimmt `stream()`. Bleibt: **A4** (ein Kommunikationsfehler beendet die Reihe). |
| **Unbeaufsichtigter Langzeitlauf** über Stunden oder Tage | **Nicht geeignet.** Ein einzelner Kommunikationsfehler beendet die Reihe (**A4**); Rotation und Fortsetzung fehlen (**A9**). |

Diese Unterscheidung ist für die Reihenfolge der Maßnahmen entscheidend — siehe
[Einschätzung der Maßnahmen](#einschätzung-der-maßnahmen).

## Bewertung der geforderten Funktionen

| Bereich | Status | Vorhanden | Fehlt oder ist unvollständig | Bedeutung für den automatisierten Einsatz |
|---|---|---|---|---|
| **1. Gerätekonfiguration – Gesamt** | **Weitgehend vorhanden** | • Lesen und Schreiben der Eingangskonfiguration<br>• Schreibschutz, Rücklesekontrolle und Gerätefehlerprüfung<br>• Sicherung, Vergleich und Wiederherstellung von Konfigurationen<br>• **erledigt 25.08.2026:** `applied_ranges()` — der Context Manager, der Bereiche setzt und garantiert zurückstellt — ist durch 18 Fälle abgedeckt (`wt3000_ranging.py`: 93 % Zeilenabdeckung, siehe **A6**) | • Kein übergeordneter, zusammenhängender Konfigurationsablauf für Wiring, Ranges, Rate, Item-Tabelle und Protokollzustand<br>• Einige Geräteannahmen noch nicht praktisch bestätigt | Die benötigten Einzelbausteine existieren, und der Rückstellpfad steht jetzt unter Test. Der aufrufende Automatisierungsablauf muss Reihenfolge, Freigaben, Sicherung und Abschlussprüfung derzeit weiterhin selbst koordinieren. Die Elementliste ist seit M1-3 zwischen `wt.input` und `wt.ranges` einheitlich, ein Ablauf kann sich also auf ein Zielverständnis verlassen. |
| **Wiring der Elemente** | **Vorhanden** | • Wiring lesen<br>• Muster `P1W2`, `P1W3`, `P3W3`, `P3W4`, `V3A3` und `NONE` setzen<br>• Eingaben vor dem Senden prüfen<br>• Ergebnis nach dem Schreiben zurücklesen<br>• **erledigt 25.08.2026:** die Folgezustände werden selbsttätig nachgezogen — `set_wiring()` frischt Gerätesteckbrief, Elementliste und SIGMA-/SIGMB-Zuordnung auf, `wt.refresh_device()` tut es nach einem Eingriff am Bedienfeld | • Vollständige Geräteabnahme und Firmwarevarianten nicht abschließend dokumentiert | Wiring ist vor Bereichen, Rechenfunktionen und Item-Tabelle zu setzen. Die anschließende Neubewertung der SIGMA-/SIGMB-Ziele war bis M1-3 Sache des Aufrufers und ohne Auffrischungsmethode gar nicht durchführbar — sie geschieht jetzt von selbst. |
| **Messbereiche der Elemente** | **Weitgehend vorhanden** | • Spannungsbereiche<br>• Direkte Strombereiche<br>• Externe Stromsensorbereiche<br>• Auto-Range<br>• Element-, ALL- und SIGMA-Ziele<br>• Range-Pläne mit Backup, Verifikation und Restore | • Schreibsyntax für Direktstrom- und Sensorbereiche im Code noch als „ZU VERIFIZIEREN“ markiert<br>• Bereichstabellen werden weiterhin über feste Schlüssel `(Modultyp, Crest)` gewählt; ein unbekanntes Modul ergibt einen `KeyError` statt einer `WTError` mit Kontext<br>• **erledigt 25.08.2026:** `InputConfig` löst `ALL` gegen die gelesene Bestückung auf statt gegen die feste Liste 1 bis 4, und eine nicht bestückte Elementnummer wird abgelehnt statt gesendet | Vor produktivem Einsatz sind Strom- und Sensorbereich an der konkreten Geräte-/Firmwareversion praktisch zu prüfen. Die Gefahr, auf einem 3-Element-Gerät den Knoten `:ELEMent4` zurückzulesen, besteht nicht mehr. |
| **Geräte-Datenaktualisierungsrate `:RATE`** | **Vorhanden** | • Lesen und Setzen von 0,05 bis 20 Sekunden<br>• Zulässige Werte werden vor dem Senden geprüft<br>• Rücklesekontrolle nach dem Setzen | • Nicht identisch mit dem Python-Ausgabeintervall<br>• Nicht identisch mit einer Wellenform-Samplingrate | Für normale numerische Messwerte ist die Geräterate konfigurierbar. Der Automatisierungsablauf muss zusätzlich das Ausgabeintervall festlegen. |
| **Interne Wellenform-Samplingrate** | **Fehlt** | • Gerätehandbuch ist im Repository vorhanden | • Keine Implementierung der `:ACQuisition`-Gruppe<br>• Kein Abruf von Wellenform-Samplingdaten | Nur relevant, wenn mit „Sample-Rate“ die interne Rohdaten-/Wellenformabtastung gemeint ist. Für normale `:NUMeric`-Messwerte ist stattdessen `:RATE` maßgeblich. |
| **Konfigurationssicherung** | **Vorhanden** | • Eingangskonfiguration, Bereiche, Item-Tabelle, Integration, Rechenfunktionen und Harmonics sicherbar<br>• JSON-Sicherung möglich<br>• Restore mit abschließender Gegenprüfung | • Kein einzelner Context Manager, der eine vollständige Messrezeptur automatisch anwendet und danach komplett zurückstellt | Gute Grundlage für sichere Automatisierung; die Orchestrierung liegt noch beim Anwenderprogramm. |
| **Erforderlicher Protokollzustand** | **Vorhanden** | • Prüfung von `:COMMunicate:HEADer 0` und `:NUMeric:FORMat FLOat`<br>• **erledigt 25.08.2026:** `wt.protocol_state()` erhebt den Ist-Zustand (auch bei eingeschaltetem Header), `wt.ensured_protocol_state()` stellt Header, Verbose und Zahlenformat her und nimmt sie im `finally` zurück | • `record()` ruft den Ablauf bewusst nicht von selbst auf — er schreibt | Der Aufruf gehört einmal und sichtbar in den Ablauf: `with wt.ensured_protocol_state():`. Eine abweichend eingestellte Frontplatte verhindert einen Lauf damit nicht mehr. |
| **2. Messung – Gesamt** | **Weitgehend vorhanden** | • Einzelmessungen<br>• Blockierende Messschleife<br>• Zeit- oder samplebegrenzte Läufe<br>• Live-Callback und gleichzeitige Dateiausgabe<br>• HOLD-Snapshot, Zeitstempel und Statistik<br>• **erledigt 25.08.2026 (A3):** steuerbares Messobjekt `Measurement` mit `start()`, `stop()`, `wait()`, `is_running` und laufender Statistik; Generator `wt.measure.stream()` | • Keine robuste Weiterführung nach Kommunikationsfehlern (**A4**)<br>• Kein ereignisgesteuerter Gerätetakt (hängt an M0-5) | Externe Stopbedingungen und nebenläufige Anlagenabläufe sind jetzt abgedeckt. Für **unbeaufsichtigte** Langzeitmessungen fehlt weiterhin die Fehlerstrategie: ein einzelner Kommunikationsfehler beendet die Reihe. |
| **Messwerte lesen und formatieren** | **Vorhanden** | • Werteliste oder Zuordnung nach Messgrößennamen<br>• Zeitstempel, monotone Laufzeit, Sample-Nummer und Condition<br>• Kennzeichnung `OK`, `NO_DATA`, `OVERRANGE` und `DUPLICATE` | • Messwerte tragen in der Ergebnisstruktur keine verbindlichen Einheiten<br>• Item-Namen und Einheiten müssen extern interpretiert werden | Die Werte sind technisch auswertbar, aber noch nicht vollständig selbsterklärend. |
| **Echtzeitausgabe zur definierten Rate** | **Teilweise vorhanden** | • Ausgabe über `CallbackSink`<br>• Gleichzeitige Ausgabe in Callback und Datei über `MultiSink`<br>• Driftarme Taktplanung<br>• Erkennung von Overruns<br>• Vergleich von Ausgabeintervall und Geräterate | • Nur Soft-Realtime über `time.sleep()`<br>• Langsame Abfragen oder Callbacks verzögern den nächsten Zyklus<br>• Kein Warten auf ein nachgewiesenes Geräte-Aktualisierungsereignis | Für Anzeigen und übliche Sekundenraten brauchbar. Keine Garantie für harte Echtzeit oder exakt phasensynchrone Ausgabe. |
| **Messung beginnen** | **Vorhanden** | • `record()` beginnt unmittelbar mit der blockierenden Aufzeichnung<br>• Geräteintegration besitzt ein separates `start()`<br>• **erledigt 25.08.2026 (A3):** `wt.measure.start()` liefert ein `Measurement`, das im Hintergrund läuft und den Aufrufer sofort zurückkehren lässt<br>• **erledigt 25.08.2026:** `wt.measure.stream()` als Generator ohne Hintergrundthread | • Kein allgemeiner Hardwaretrigger über `*TRG` | Ein Automat kann jetzt eine Messinstanz starten und anschließend andere Aufgaben fortsetzen. Drei Wege stehen zur Wahl — blockierend (`record()`), nebenläufig (`start()`) und je Sample entscheidend (`stream()`). |
| **Messung beenden** | **Vorhanden** | • Ende nach `max_samples`<br>• Ende nach `max_duration_s`<br>• Abbruch mit Strg+C<br>• Sink und HOLD werden im Cleanup geschlossen beziehungsweise abgeschaltet<br>• **erledigt 25.08.2026 (A3):** `stop()`, `wait(timeout)` und `is_running`; das Stoppsignal ist ein `threading.Event` und greift **sofort**, nicht erst nach dem laufenden Intervall<br>• **erledigt 25.08.2026:** Fehler aus dem Mess-Thread werden bei `wait()`/`stop()` erneut ausgelöst | — | Externe Stopbedingungen, Bedienereingriffe und Anlagenereignisse lassen sich als Stoppsignal übergeben. Ein Test misst die Reaktionszeit gegen einen 3-s-Takt und fällt, wenn jemand `Event.wait()` durch `time.sleep()` ersetzt. |
| **Nebenläufigkeit und Sitzungsbesitz** | **Vorhanden** | • **erledigt 25.08.2026 (A2):** `WTSession` führt ein `RLock` um `write`, `query`, `query_raw`, `query_block` und `drain_after_failure` — die stille Antwortvertauschung ist damit für **jeden** Nebenläufigkeitsfall ausgeschlossen, nicht nur für `Measurement`<br>• **erledigt 25.08.2026:** darauf die Besitzregel — eine laufende Messung besitzt ihre Sitzung, Fremdzugriff endet in `ConcurrentAccessError` mit Nennung beider Auswege | • Die Regel ist bewusst streng: wer *während* der Messung lesen muss, nimmt `stream()` statt `start()` | Entschieden und umgesetzt. Die Prüfung sitzt in `WTSession` und nicht in der Fassade — dort käme eine vor dem Start geholte Referenz auf `wt.input` an ihr vorbei. |
| **Taktkopplung und Dubletten** | **Teilweise vorhanden** | • `:RATE?` wird vor dem Lauf gelesen<br>• Zu schnelles Lesen wird gemeldet<br>• Bitgleiche Datensätze werden als `DUPLICATE` gespeichert<br>• Dubletten und Overruns werden statistisch erfasst | • Zu schnelles Lesen wird nur erkannt, nicht verhindert<br>• Kein ereignisgesteuertes Warten auf neue Daten<br>• Phasensynchronität mit dem Geräteupdate nicht garantiert | Datenqualität ist nachvollziehbar, aber die Aufzeichnung erzeugt gegebenenfalls bewusst redundante Datensätze. |
| **Kommunikations- und Gerätefehler während der Messung** | **Unvollständig** | • Sink wird bei einem Fehler geschlossen<br>• HOLD-Cleanup wird versucht<br>• Fehler werden nicht stillschweigend ignoriert | • Kein Wiederverbinden<br>• Kein Retry-Konzept<br>• Kein Fortsetzen nach kurzem Ausfall<br>• `SampleMark.MISSING` existiert, wird von der Messschleife aber nicht erzeugt | Ein einzelner Kommunikationsfehler beendet eine möglicherweise lange Messreihe. Für unbeaufsichtigten Betrieb ist eine ausdrückliche Fehlerstrategie erforderlich. |
| **Geräteseitige Integration** | **Softwareseitig vorhanden, Geräteabnahme offen** | • `start()`, `stop()`, `reset()` und `running()`<br>• Betriebsart, Timer, Echtzeitfenster und Autokalibrierung<br>• Integrations-Messprofil für Wh/Ah | • Nicht dasselbe wie Start/Stop der normalen Echtzeitaufzeichnung<br>• Schreibbefehle noch nicht vollständig am realen Gerät abgenommen | Für Energie-/Wh-/Ah-Messungen ist der Softwareweg vorhanden, benötigt aber noch einen protokollierten Gerätetest. |
| **3. Speicherung – Gesamt** | **Weitgehend vorhanden** | • CSV<br>• JSON Lines<br>• Callback<br>• Mehrfachausgabe<br>• Sofortiges Flush und Cleanup | • Einheiten und verbindliche Metadaten fehlen<br>• Keine Rotation, Fortsetzung oder Append-Funktion<br>• Fehlende Zyklen noch nicht speicherbar | Für begrenzte Messläufe verwendbar. Für selbstbeschreibende Prüfdateien und lange unbeaufsichtigte Läufe noch zu ergänzen. |
| **CSV-Ausgabe** | **Vorhanden** | • Zeitstempel, Laufzeit, Sample-Nummer, Condition und Messwerte<br>• Statusflags<br>• `NO_DATA` als leere Zelle<br>• `OVERRANGE` als `INF`<br>• Flush nach jeder Zeile | • Gerätemetadaten werden nicht direkt in die CSV geschrieben<br>• Sidecar ist optional<br>• Datei wird neu angelegt beziehungsweise überschrieben | Datenverlust bei Prozessabbruch ist auf höchstens die gerade geschriebene Zeile begrenzt. Die Zuordnung zur Gerätekonfiguration muss separat abgesichert werden. |
| **JSONL-Ausgabe** | **Vorhanden** | • Benannte Messwerte<br>• Eine JSON-Zeile je Sample<br>• Laufparameter in einer Metadatenzeile<br>• Statuswerte ohne ungültige JSON-Literale | • Keine vollständige Gerätekonfiguration in der Standard-Metadatenzeile<br>• Keine verbindlichen Einheiten | Robuster und leichter selbsterklärend als CSV, aber noch kein vollständiges Prüfprotokoll. |
| **Metadaten und Einheiten** | **Teilweise vorhanden** | • Optionales Sidecar mit Geräteidentifikation, Konfiguration und Item-Tabelle<br>• Update-Rate und Laufparameter können gespeichert werden<br>• **erledigt 25.08.2026:** Einheitenabbildung für die Messwertspalten — in JSONL und Sidecar ohne Zutun, in der CSV über `unit_row=True`; „dimensionslos“ und „nicht belegt“ bleiben unterscheidbar | • Sidecar nicht verpflichtend<br>• Keine feste Bindung zwischen CSV und Sidecar<br>• Einheit der neun Oberschwingungsfaktoren nicht belegt (`null`) | Die Spalten sind jetzt benannt **und** bemaßt. Ohne Zusatzwissen vollständig ist eine Ergebnisdatei erst, wenn Sidecar und Daten fest aneinander hängen. |
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
| ~~**1 – zwingend**~~ **erledigt 25.08.2026** | Steuerbares Messobjekt mit `start()`, `stop()`, `wait()`, `is_running` und sauberer Fehlerweitergabe | Erst damit kann ein automatisierter Ablauf eine Messung kontrolliert parallel zu anderen Anlagenaktionen führen und aufgrund externer Bedingungen beenden. — Umgesetzt als **A3**. |
| ~~**2 – zwingend**~~ **erledigt 25.08.2026** | Sitzungsbesitz oder Thread-Synchronisation eindeutig umsetzen | Verhindert vertauschte Geräteantworten und beschädigte Messdaten bei Nebenläufigkeit. — Umgesetzt als **A2**, und zwar beides: Lock als Mechanismus, Besitz als Regel. |
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
| **A2** | **Sitzungsbesitz entscheiden** *(Liste: 2)* — **erledigt 25.08.2026** | `S` Entscheidung, `S`–`M` Umsetzung | — | **Umgesetzt, und zwar als beides.** Die Frage im Klassenkopf von `WTSession` lautete „Lock **oder** Besitzregel". Die Antwort ist „Lock **als** Mechanismus, Besitz **als** Regel": das `RLock` liegt um `write`, `query`, `query_raw`, `query_block` und `drain_after_failure` und schließt die stille Antwortvertauschung für **jeden** Nebenläufigkeitsfall; darauf sitzt die Besitzregel, die einen Fremdzugriff nicht bloß serialisiert, sondern ablehnt. Entscheidend ist der **Ort**: die Prüfung sitzt in `WTSession`, nicht in der Fassade — dort käme eine vor dem Start geholte Referenz auf `wt.input` an ihr vorbei, weil die Fachobjekte zwischengespeichert werden. Nebenwirkung: die Entscheidung ist nicht einbetoniert; eine spätere Lockerung auf geteilten Zugriff ist das Entfernen einer Prüfung, kein Umbau. — *Ursprüngliche Einschätzung:* Ist Voraussetzung von A3, nicht deren Nachbar. |
| **A3** | **Steuerbares Messobjekt** *(Liste: 1)* — **erledigt 25.08.2026** | `M` | A2 | **Umgesetzt:** `Measurement` mit `start()`, `stop()`, `wait()`, `is_running`, `stats` und `error`, dazu der Generator `wt.measure.stream()`. Alle drei im Docstring benannten Umbaustellen sind behandelt: `KeyboardInterrupt` liegt jetzt beim Aufrufer (`stream()` und `record()` laufen in dessen Thread, der Hintergrundlauf stoppt über `stop()`), `time.sleep()` ist `stop_event.wait()` — ein Test misst gegen 3 s Takt und fällt bei einem Rückbau —, und die Zuständigkeit für Bereiche und Item-Tabelle bleibt **ausdrücklich beim Aufrufer**: läge sie im Thread, geschähe die Rückstellung als Gerätezugriff zu einem Zeitpunkt, den der Aufrufer nicht kennt. Damit die Konfigurationsklammer nicht vor der Messung schließt, ist `Measurement` selbst ein Context Manager. Gemeinsamer Rumpf ist der Generator `iter_samples()`; eine zweite Signatur ist nicht entstanden. — *Ursprüngliche Einschätzung:* **Für skriptgesteuerte Läufe mit bekanntem Ende wird die Maßnahme nicht gebraucht** — dort ist die blockierende Schleife die einfachere und ehrlichere Lösung. Das gilt unverändert. |
| **A4** | **Fehlerstrategie, Retry, `MISSING`** *(Liste: 3)* | `M` | A2 (Wiederverbinden greift in den Sitzungsbesitz ein) | **Für unbeaufsichtigte Läufe die wichtigste Maßnahme überhaupt.** Nachgemessen: ein einzelner fehlgeschlagener Lesevorgang beendet die Reihe. Entlastend ist, dass dabei nichts verloren geht — die CSV wird pro Zeile geflusht und im `finally` geschlossen. Der Ausfall ist also ein Abbruch, kein Datenverlust. Enthält die noch offene Teilentscheidung aus **S-08**: ein `MISSING`-Datensatz trägt keine Werte und kollidiert mit der strengen Spaltenregel. |
| **A5** | **Einheiten** *(Liste: 6)* — **erledigt 25.08.2026** | `S`–`M` | — | **War deutlich zu weit hinten.** Umgesetzt: `unit_of()`, `NumericItem.unit`, `ItemTable.unit_map()`; die Messschleife legt die Einheiten als `metadata["units"]` in jede Senke, JSONL und Sidecar tragen sie ohne Zutun, `CsvSink(unit_row=True)` schreibt eine zweite Kopfzeile. Der Unterschied zwischen **dimensionslos** und **nicht belegt** wird bis in die Datei durchgehalten. Offen bleibt der verbindliche Metadatenverbund (Bindung Datei ↔ Sidecar) und die Einheit der neun Oberschwingungsfaktoren, für die im Projekt kein Beleg vorliegt. — *Ursprüngliche Einschätzung:* Die Maßnahme ist klein, hängt von nichts ab und verbessert **jede** ab dann erzeugte Datei dauerhaft. Die Zuordnung Funktionsname → Einheit ist eine Tabelle; die Funktionsnamen stehen bereits in der Item-Tabelle. Jede Messdatei, die vorher entsteht, bleibt dagegen für immer auf externes Wissen angewiesen. **Vorziehen.** |
| **A6** | **`applied_ranges()` unter Test stellen** — **erledigt 25.08.2026** | `S` | — | **Umgesetzt:** 18 Fälle in [`tests/test_applied_ranges.py`](../tests/test_applied_ranges.py), Abdeckung von `wt3000_ranging.py` **60 % → 93 %**. Gegen ein Zustandsmodell der Bereichsknoten, nicht gegen eine Antworttabelle — sonst prüfte der Test die Tabelle statt den Ablauf. Belegt sind Rückstellung nach Fehler und nach Strg+C, Abbruch bei abweichender Übernahme und die Ausnahme bei gescheiterter Wiederherstellung. Eine Mutationsprobe (Rückstellung ausgebaut) lässt 5 Fälle fallen. — *Ursprüngliche Einschätzung:* Der Context Manager, der Messbereiche setzt und garantiert zurückstellt, ist heute ungetestet (60 % Zeilenabdeckung in `wt3000_ranging.py`, die Lücke genau auf Apply/Verify/Restore). Eine Messrezeptur auf einem ungeprüften Rückstellpfad aufzubauen, ist die falsche Reihenfolge. Die Vorlage existiert: `tests/test_backup.py` spielt denselben Zyklus für `SessionBackup` bereits durch. |
| **A7** | **Vollständige Messrezeptur** *(Liste: 4)* | `M` | A6 | **Richtig eingeordnet, aber mit A6 als Vorbedingung.** Die Bausteine sind vorhanden — `SessionBackup`, `applied_ranges()`, `ItemAccess.applied()`. Zu bauen ist die Orchestrierung, nicht die Mechanik. |
| **A8** | **Protokollzustand herstellen (M1-4)** — **erledigt 25.08.2026** | `S` | — | **Umgesetzt:** `wt.protocol_state()` erhebt, `wt.ensured_protocol_state()` stellt her und nimmt im `finally` zurück. Rein lesende Sitzungen laufen durch, wenn der Zustand stimmt, und brechen sonst mit einer Meldung ab, die beide Auswege nennt. `record()` ruft den Ablauf bewusst **nicht** von selbst — er schreibt. — *Ursprüngliche Einschätzung:* Heute prüft `check_protocol_state()` nur, und `record()` ruft sie nicht auf. Folge: Hat jemand am Bedienfeld `:COMMunicate:HEADer 1` eingestellt, scheitert jeder automatisierte Lauf — an einer Ursache, die der Treiber in einem Kommando selbst beheben und beim Verlassen zurückstellen könnte. Für unbeaufsichtigten Betrieb ist das eine häufige und vermeidbare Abbruchursache. |
| **A9** | **Dateirotation und Fortsetzen** *(Liste: 7)* | `S` | — | **Richtig eingeordnet.** Wird erst bei Läufen über Stunden gebraucht; vorher genügt ein Dateiname je Lauf. Der Hinweis auf fehlenden Kollisionsschutz ist berechtigt: `CsvSink` öffnet mit `"w"` und überschreibt eine bestehende Datei wortlos. |
| **A10** | **Wellenform-Samplingrate `:ACQuisition`** | `M` | Bedarf klären | **Nur bei tatsächlichem Bedarf.** Am Quellstand bestätigt: `:ACQuisition` kommt ausschließlich in der Optionstabelle vor, nicht als umgesetzte Gruppe. Für `:NUMeric`-Messwerte — den erklärten Zweck — ist `:RATE` maßgeblich und vollständig vorhanden. Die Maßnahme sollte erst begonnen werden, wenn jemand Rohdaten wirklich braucht. |

### Empfohlene Reihenfolge

| Zug | Maßnahmen | Ergebnis danach |
|---|---|---|
| **Sofort, parallel** | **A1** anstoßen | Der Termin läuft, während weitergearbeitet wird. |
| ~~**Zug 1**~~ **erledigt 25.08.2026** | **A5**, **A6**, **A8** — drei kleine, voneinander unabhängige Schritte | Messdateien tragen ihre Einheiten, der Rückstellpfad ist abgesichert, ein verstellter Protokollzustand bricht keinen Lauf mehr ab. |
| ~~**Zug 3**~~ **vorgezogen und erledigt 25.08.2026** | **A2** entschieden, dann **A3** | Ein Automat kann eine Messung nebenher führen und von außen beenden; der Sitzungsbesitz ist entschieden und wird in `WTSession` durchgesetzt. *Abweichung von der Planung:* A3 wurde **vor** A4 umgesetzt — A2 war ohnehin dessen Vorbedingung, und beide Maßnahmen fassen dieselbe Stelle an. |
| **Zug 2** *(jetzt der nächste)* | **A4** | Skriptgesteuerte und unbeaufsichtigte Läufe überleben einen kurzen Kommunikationsfehler; Lücken sind als `MISSING` sichtbar statt unsichtbar. **Die größte verbliebene Lücke.** |
| **Danach** | **A7** | Die Rezeptur bündelt Konfiguration und Rückstellung. |
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
| Vollständiger Testlauf | **Nach Zug 3 (25.08.2026):** `758 passed` in 6,2 s — 32 Fälle mehr, in [`tests/test_messsteuerung.py`](../tests/test_messsteuerung.py). Zuvor `726 passed` in 3,14 s, keine übersprungenen Fälle mehr. Die frühere Angabe `725 passed, 1 skipped` stammte aus einer Umgebung, in der `tools/hardware/` relativ zum installierten Paket nicht auffindbar war und `test_die_geraeteskripte_unter_tools_ebenso` deshalb übersprang; im aktuellen Quellbaum findet der Test das Verzeichnis und läuft durch. Die Suite läuft in einer eigenen Umgebung (`python3 -m venv`, `pip install -e ".[test]"`) ohne Gerät und ohne DLL. |
| Statische Prüfung | **Nachgeholt:** `ruff check .` und `mypy` ohne Befund. |
| Zeilenabdeckung | **Gemessen:** 90 % über das Paket (vor Zug 1: 87 %), gehalten über Zug 3 hinweg trotz 187 neuer Anweisungen. Die frühere Schwachstelle `wt3000_ranging.py` steht nach **A6** bei 93 %; die in Zug 3 geänderten Module bei 97 % (`wt3000_core.py`), 96 % (`wt3000_measure.py`) und 92 % (`wt3000_device.py`). Schwächste Stelle ist unverändert `wt3000_input.py` mit 74 % — dort liegen die Setter, die am Gerät noch nicht abgenommen sind (**A1**). |
| Mutationsproben zu Zug 3 | **Drei gefahren, alle greifen:** Lock ausgebaut → `test_query_block_ist_als_ganzes_geschuetzt` fällt; Besitzprüfung ausgebaut → zwei Fälle fallen; `stop_event.wait()` durch `time.sleep()` ersetzt → `test_stop_wartet_das_laufende_intervall_nicht_ab` fällt. Die erste Fassung des Nebenläufigkeitstests blieb bei ausgebautem Lock **grün** — die Operationen des `FakeTransport` sind zu kurz, als dass sich vier Threads zuverlässig verschränken. Erst ein ausdrückliches Zeitfenster vor jedem Lesevorgang (`VerschraenkenderTransport`) macht die Vertauschung reproduzierbar. |
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
| Steuerbare Messung und Sitzungsbesitz (M3-1, A2/A3) | [`tests/test_messsteuerung.py`](../tests/test_messsteuerung.py) |
| Offene Punkte und Gerätefragen | [`OFFENE_PUNKTE.md`](OFFENE_PUNKTE.md) |

