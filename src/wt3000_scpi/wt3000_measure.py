# =============================================================================
# Datei: wt3000_measure.py
# Layer 3 (HOLD) + Layer 4 (Messschleife) - wiederverwendbare Bausteine
# fuer die Messschleife.
#
# UEBERARBEITET (ROADMAP M4-2): Die CSV-Aufzeichnung ist hier ausgezogen und
# liegt jetzt als 'CsvSink' in wt3000_sinks.py, neben den uebrigen
# Ausgabeformaten. Geblieben sind die Messschleife, der Datensatz 'Sample'
# (M4-1) und der Vertrag 'SampleSink', den die Schleife voraussetzt - Datentyp
# und Vertrag gehoeren zusammen, und dadurch bleibt die Importrichtung
# eindeutig: wt3000_sinks holt sich beide von hier, nie umgekehrt.
#
# Aendert nichts an wt3000_core.py, wt3000_numeric.py, wt3000_itemspec.py.
# =============================================================================

from __future__ import annotations

import json
import logging
# UEBERARBEITET (F-01, siehe AENDERUNGEN_2026-08-18.md): 'import math' entfernt -
# das Modul wurde hier nie benutzt.
import statistics
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Protocol, runtime_checkable

# UEBERARBEITET (Punkt 4, src-Layout): paketrelative Importe.
# UEBERARBEITET (Schritt 5b, Befund A-06): parse_condition() fuer die
# Statusabfrage in der Messschleife.
from .wt3000_common import parse_condition
from .wt3000_core import WTError, WTSession
from .wt3000_itemspec import ItemSpec
from .wt3000_numeric import ItemTable, NumericValue, ValueStatus, read_numeric_values

_log = logging.getLogger("wt3000.measure")


# ---------------------------------------------------------------------------
# Messprofile
# ---------------------------------------------------------------------------


def build_standard_profile() -> tuple[ItemSpec, ...]:
    """Standardprofil fuer die Verdrahtung V3A3,P1W2.

    Elemente 1-3 = Drehstromseite (Wiring-Unit SigmaA)
    SIGMA        = Summe der Drehstromseite
    Element 4    = separater DC-Kanal (Wiring-Unit SigmaB)

    FU wird nur fuer Element 3 gefuehrt: die Frequenzmessquelle steht laut
    ':MEASure?' auf U3/I3, deshalb liefern FU1 und FU2 strukturell NAN.
    Aendert sich die Frequenzmessquelle, ist diese Liste anzupassen.

    UEBERARBEITET (21.08.2026): Der frueher hier stehende offene Punkt zu den
    Integrationsitems ist erledigt - sie stehen jetzt in
    'build_integration_profile()' direkt darunter. Die damalige Einschaetzung
    hat getragen: anzupassen war tatsaechlich nichts ausser dieser Datei.
    """
    three_phase = ("U", "I", "P", "S", "Q", "LAMBDA", "PHI")
    sum_functions = ("U", "I", "P", "S", "Q", "LAMBDA")
    dc_functions = ("U", "I", "P")  # Element 4 ist DC: S/Q/LAMBDA/PHI waeren NAN

    specs: list[ItemSpec] = []
    for element in ("1", "2", "3"):
        specs.extend(ItemSpec(f, element) for f in three_phase)
    specs.append(ItemSpec("FU", "3"))  # einzige konfigurierte Frequenzquelle
    specs.extend(ItemSpec(f, "SIGMA") for f in sum_functions)
    specs.extend(ItemSpec(f, "4") for f in dc_functions)
    return tuple(specs)


#: Die Groessen der Integrationsfunktion (Handbuch 6-99, Musterbelegung 3).
#
# WH/WHP/WHM  Energie gesamt, nur aufgenommene, nur abgegebene    [Wh]
# AH/AHP/AHM  Ladung gesamt, positiv, negativ                     [Ah]
# WS, WQ      Schein- und Blindenergie                            [VAh, varh]
INTEGRATION_FUNCTIONS: tuple[str, ...] = ("WH", "WHP", "WHM", "AH", "AHP", "AHM", "WS", "WQ")


def build_integration_profile() -> tuple[ItemSpec, ...]:
    """Messprofil fuer eine Wh-/Ah-Messung - das Gegenstueck zur Steuerung.

    NEU (ROADMAP M3-2, Rang 1 der Analyse): 'IntegrationConfig' aus
    wt3000_deviceconfig startet und stoppt die Integration, LIEST sie aber
    nicht aus - die aufgelaufenen Werte kommen wie alle Messwerte ueber die
    Item-Tabelle. Ohne dieses Profil koennte ein Anwender die Integration
    steuern und danach nur Momentanwerte abholen; die Funktion waere zur
    Haelfte da.

    Aufbau, gleiche Verdrahtung wie 'build_standard_profile()' (V3A3,P1W2):

      1.  TIME          verstrichene Integrationszeit, EINMAL - die Groesse
                        gilt geraeteweit, nicht je Element. Bei
                        ':NUMeric:FORMat FLOat' kommt sie als gewoehnlicher
                        Gleitkommawert in SEKUNDEN (Handbuch zur
                        NUMeric-Gruppe: 1 Stunde -> 3600). Genau dieser Wert
                        geht in 'IntegrationConfig.remaining_seconds()'.
      2.  U, I, P       je Element und SIGMA - der Momentanwertkontext, ohne
                        den eine Energiebilanz nicht einzuordnen ist
      3.  Integration   INTEGRATION_FUNCTIONS je Element und SIGMA

    'verify=True' bei den Integrationsitems ist kein Schmuck: keine dieser
    acht Funktionen ist an diesem Geraet je gelesen worden. Die Kennzeichnung
    aus ItemSpec sagt genau das - "auf dem Original-WT3000 nicht gesichert".
    """
    specs: list[ItemSpec] = [ItemSpec("TIME", "1", verify=True)]

    instant = ("U", "I", "P")
    for element in ("1", "2", "3", "SIGMA", "4"):
        specs.extend(ItemSpec(f, element) for f in instant)
    for element in ("1", "2", "3", "SIGMA", "4"):
        specs.extend(ItemSpec(f, element, verify=True) for f in INTEGRATION_FUNCTIONS)
    return tuple(specs)


# ---------------------------------------------------------------------------
# Layer 3 - Snapshot ueber :NUMeric:HOLD
# ---------------------------------------------------------------------------


class NumericHold:
    """Context Manager fuer ':NUMeric:HOLD'.

    Ein erneutes ON bei aktivem HOLD verwirft die alten Daten und friert die
    aktuellsten ein - laut Handbuch der vorgesehene Weg fuer Dauermessungen.
    Es muss also nicht zwischendurch auf OFF geschaltet werden.

    Wichtig: bleibt HOLD nach einem Absturz aktiv, liefert das Geraet in der
    naechsten Sitzung eingefrorene Werte, waehrend die Anzeige weiterlaeuft.
    OFF wird deshalb im __exit__ garantiert gesendet.

    BEFUND zu ROADMAP M3-2, Spiegelstrich 'Einzelmessung im HOLD-Betrieb:
    :SINGle (pruefen)': In der Kommandouebersicht des Projekts
    (MarkDowns/WT3000_Commands_Overview.md) kommt ein Knoten ':SINGle' NICHT
    vor - weder als eigene Gruppe noch unter :NUMeric oder :MEASure. Vorhanden
    sind ':NUMeric:HOLD' (dieser Weg hier) und das Common Command '*TRG'. Der
    Spiegelstrich ist damit in der vorliegenden Form nicht umsetzbar und vor
    der Umsetzung neu zu fassen. Gegenprobe an IM WT3001E-17EN und am Geraet
    steht aus.
    """

    def __init__(self, session: WTSession, enabled: bool = True) -> None:
        self._session = session
        self._enabled = enabled
        self._armed = False

    def __enter__(self) -> "NumericHold":
        if not self._enabled:
            _log.info("HOLD deaktiviert - Werte werden ungefroren gelesen")
            return self
        # Ein bereits aktives HOLD aus einem frueheren Lauf erkennen.
        state = self._session.query(":NUMeric:HOLD?").strip()
        if state == "1":
            _log.warning("HOLD war bereits aktiv (Rest eines frueheren Laufs) - wird uebernommen")
        return self

    def refresh(self) -> None:
        """Aktuellsten Datensatz einfrieren. Vor jedem VALue? aufrufen."""
        if not self._enabled:
            return
        self._session.write(":NUMeric:HOLD ON")
        self._armed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._enabled and not self._armed:
            return
        try:
            self._session.write(":NUMeric:HOLD OFF")
            _log.info("HOLD abgeschaltet")
        except WTError as error:
            _log.error("HOLD OFF fehlgeschlagen: %s - Geraet ggf. manuell pruefen", error)


# ---------------------------------------------------------------------------
# NEU (ROADMAP M4-1): Layer 4 - der Datensatz
# ---------------------------------------------------------------------------


class SampleMark(Enum):
    """Kennzeichnung eines ganzen Datensatzes - nicht eines einzelnen Werts.

    Abzugrenzen von 'ValueStatus': der bewertet einen einzelnen Messwert
    (NO_DATA, OVERRANGE) und kommt aus dem Bitmuster, das das Geraet liefert.
    'SampleMark' bewertet den Zyklus als Ganzes und entsteht erst im Treiber,
    aus dem Vergleich mit dem vorigen Zyklus.

    DUPLICATE und MISSING werden heute von niemandem gesetzt - die Erkennung
    ist ROADMAP M3-3 bzw. M3-4. Sie stehen hier trotzdem schon, weil M4-1 die
    Stelle festlegt, an der sie kuenftig transportiert werden; ein Sink, der
    jetzt gegen 'Sample' gebaut wird, muss dafuer spaeter nicht angefasst
    werden.
    """

    OK = "OK"
    #: M3-3: bitgleich zum vorigen Zyklus - das Geraet hat nicht aktualisiert.
    DUPLICATE = "DUPLICATE"
    #: M3-4: der Zyklus ist ausgefallen. Ein solcher Datensatz traegt keine
    #: Werte; er steht in der Ausgabe, damit die Luecke sichtbar bleibt,
    #: statt stillschweigend zu fehlen.
    MISSING = "MISSING"


@dataclass(frozen=True)
class Sample:
    """Ein vollstaendiger Messzyklus.

    NEU (ROADMAP M4-1). Bis hierher wanderte eine Messzeile als fuenf
    getrennte Parameter in 'CsvRecorder.write_row()'. Jedes weitere
    Ausgabeformat haette diese Signatur nachbauen muessen - und jede
    Erweiterung (die Kennzeichnung aus M3-3/M3-4) haette jede Fassung einzeln
    getroffen. Ab jetzt gilt: alles, was misst, liefert 'Sample'; alles, was
    schreibt, nimmt 'Sample'.

    'timestamp' bezieht sich auf den Moment des ':NUMeric:HOLD ON', nicht auf
    den Antworteingang - der Datensatz ist zu diesem Zeitpunkt im Geraet
    eingefroren, das Auslesen danach dauert unbestimmt lange. 'elapsed_s'
    zaehlt dagegen auf einer monotonen Uhr ab Beginn der Messreihe und ist
    deshalb der richtige Bezug fuer Zeitdifferenzen; 'timestamp' folgt der
    Systemuhr und kann springen.

    Die Klasse ist eingefroren, weil ein einmal gelesener Datensatz sich nicht
    mehr aendert. 'values' bleibt trotzdem eine veraenderliche Liste - dadurch
    ist 'Sample' nicht hashbar, was hier niemanden stoert und die Liste bei
    Messreihen mit 40 Items vor einer Kopie je Zyklus bewahrt.
    """

    #: Zeitpunkt des HOLD ON, zeitzonenbehaftet.
    timestamp: datetime
    #: Sekunden seit Beginn der Messreihe, monotone Uhr.
    elapsed_s: float
    #: Laufende Nummer ab 1.
    number: int
    #: ':STATus:CONDition?' oder None, wenn nicht mitgelesen.
    condition: int | None
    #: Messwerte in der Reihenfolge der Item-Tabelle.
    values: list[NumericValue]
    #: Bewertung des Zyklus. Siehe SampleMark.
    mark: SampleMark = SampleMark.OK

    def status_flags(self, column_names: Sequence[str]) -> list[str]:
        """Alle Auffaelligkeiten des Datensatzes im Klartext.

        Gemeinsame Grundlage jedes Ausgabeformats: der Aufrufer bekommt eine
        Liste wie ['mark=DUPLICATE', 'U2=OVERRANGE'] und entscheidet selbst,
        wie er sie unterbringt. 'CsvSink' haengt sie in die Spalte
        'status_flags'; ein kuenftiger Sink (M4-2) kann sie anders fuehren.

        Die Kennzeichnung des Zyklus steht bewusst VOR den Einzelwerten: bei
        einem ausgefallenen Zyklus (MISSING) ist sie die einzige Angabe, die
        es ueberhaupt gibt.

        Ist 'column_names' kuerzer als 'values', bleiben die ueberzaehligen
        Werte unerwaehnt - 'zip' bricht am kuerzeren Ende ab. Das ist hier
        richtig so: die Laengenpruefung gehoert an die schreibende Stelle,
        die den Spaltenkopf kennt, und findet dort auch statt.
        """
        flags: list[str] = []
        if self.mark is not SampleMark.OK:
            flags.append(f"mark={self.mark.value}")
        flags.extend(
            f"{name}={value.status.value}"
            for name, value in zip(column_names, self.values)
            if value.status is not ValueStatus.OK
        )
        return flags


# ---------------------------------------------------------------------------
# NEU (ROADMAP M4-2): der Vertrag der Ausgabeseite
# ---------------------------------------------------------------------------


@runtime_checkable
class SampleSink(Protocol):
    """Wohin ein Messlauf seine Datensaetze schreibt.

    NEU (ROADMAP M4-2). Bewusst klein gehalten - drei Methoden, mehr braucht
    kein Format. Die Messschleife kennt ab jetzt ausschliesslich diesen
    Vertrag und nie ein konkretes Ausgabeformat; ein zweites Format ist eine
    Datei in 'wt3000_sinks.py' und kein Eingriff in die Schleife.

    Das Protocol wohnt hier und nicht bei den Implementierungen, weil es zu
    'Sample' gehoert: Datentyp und Vertrag sind ein Paar. 'wt3000_sinks.py'
    importiert beide von hier, die Importrichtung bleibt damit nach unten.

    ARBEITSTEILUNG. Der Konstruktor einer Senke nimmt entgegen, was ihr Format
    ausmacht - Dateipfad, Trennzeichen, Rueckruffunktion. 'open()' nimmt
    entgegen, was der Messlauf mitbringt und was jede Senke gleichermassen
    braucht: die Spaltennamen und die Metadaten. Erst dadurch kann Code, der
    kein Format kennt, eine beliebige Senke in Betrieb nehmen.

    LEBENSZYKLUS. 'run_measurement_loop()' ruft 'open()' einmal vor dem ersten
    Zyklus und 'close()' in einem 'finally' - auch bei Abbruch, Fehler und
    Strg+C. Ein Aufrufer, der die Schleife nicht benutzt, haelt sich an
    dieselbe Reihenfolge. 'close()' muss mehrfachen Aufruf vertragen; ein
    'write()' vor 'open()' ist ein Programmierfehler und gehoert mit einer
    'WTError' quittiert, nicht mit einem AttributeError.

    ZUSTAENDIG FUER DIE LAENGENPRUEFUNG ist die Senke, nicht die Schleife
    (Befund B-07): nur sie kennt ihren Spaltenkopf. Passt 'len(sample.values)'
    nicht dazu, ist das ein harter Abbruch - eine stillschweigend verrutschte
    Spalte ist bei Messdaten, die Wochen spaeter ausgewertet werden, der
    teuerste Fehler ueberhaupt. 'wt3000_sinks.require_matching_columns()'
    fuehrt diese Regel an einer Stelle.

    '@runtime_checkable' wie beim Transport-Protocol aus M1-2: damit laesst
    sich 'issubclass(MeineSenke, SampleSink)' schreiben. Die Pruefung sieht
    nur die Methodennamen, nicht ihre Signaturen - sie ersetzt keinen
    Typpruefer, macht aber die Zusage 'jede fremde Klasse taugt hier' im
    Test ueberhaupt formulierbar.
    """

    def open(self, columns: Sequence[str], metadata: Mapping[str, object]) -> None:
        """Aufzeichnung beginnen. 'columns' sind die Item-Schluessel in Reihenfolge."""
        ...

    def write(self, sample: Sample) -> None:
        """Einen Datensatz aufnehmen."""
        ...

    def close(self) -> None:
        """Aufzeichnung beenden. Mehrfachaufruf ist unschaedlich."""
        ...


# ---------------------------------------------------------------------------
# Metadaten-Sidecar
# ---------------------------------------------------------------------------


def write_metadata(
    path: Path,
    session: WTSession,
    table: ItemTable,
    parameters: dict,
) -> None:
    """Geraetezustand und Laufparameter neben der CSV ablegen.

    Ohne diese Angaben ist eine Messreihe spaeter nicht mehr interpretierbar -
    insbesondere Bereiche und Skalierung (z.B. CT = 2000 auf Element 4).
    Alle Abfragen sind reine Queries.
    """
    queries = {
        "idn": "*IDN?",
        "communicate": ":COMMunicate?",
        "rate": ":RATE?",
        "numeric_format": ":NUMeric:FORMat?",
        "input": ":INPut?",
        "input_wiring": ":INPut:WIRing?",
        "input_module": ":INPut:MODUle?",
        "input_scaling": ":INPut:SCALing?",
        "input_filter": ":INPut:FILTer?",
        "input_cfactor": ":INPut:CFACtor?",
        "measure": ":MEASure?",
    }
    device: dict[str, str] = {}
    for key, command in queries.items():
        try:
            device[key] = session.query(command)
        except WTError as error:
            device[key] = f"<Fehler: {error}>"
            # UEBERARBEITET (Schritt 6 aus MarkDowns/PLAN_AUFRUFKETTE.md,
            # Befund A-07): Dies ist die EINZIGE Stelle im Bestand, an der ein
            # fehlgeschlagener Query nicht zum Abbruch fuehrt, sondern die
            # naechste Abfrage nach sich zieht. Damit ist sie auch die einzige,
            # an der eine VERSPAETETE Antwort in die falsche Zeile geraten kann.
            #
            # Laeuft ':INPut?' - die laengste der elf Abfragen - in einen
            # Timeout und trifft die Antwort ein, waehrend schon
            # ':INPut:WIRing?' unterwegs ist, dann landet der ':INPut?'-Rumpf
            # im Feld 'input_wiring'. Das Sidecar sieht danach plausibel aus
            # und ist falsch - und es ist die Datei, aus der eine Messreihe
            # spaeter interpretiert wird. Ein plausibel aussehendes, falsches
            # Sidecar ist schlimmer als ein fehlendes.
            #
            # drain_after_failure() ist genau dafuer gebaut (Befund S-03: es
            # war getestet und im gesamten Produktivcode ungenutzt). Es senkt
            # das Timeout kurz, liest einmal, verwirft und stellt das Timeout
            # im finally wieder her.
            #
            # Der Fehler bleibt im Feld stehen: das Aufraeumen darf ihn nicht
            # verschlucken, sonst sieht das Sidecar vollstaendig aus, obwohl
            # eine Angabe fehlt.
            session.drain_after_failure()

    payload = {
        "recorded_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "parameters": parameters,
        "device": device,
        "item_table": table.to_dict(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _log.info("Metadaten gesichert nach %s", path)


# ---------------------------------------------------------------------------
# Messschleife
# ---------------------------------------------------------------------------


@dataclass
class LoopStatistics:
    """Auswertung der Zykluszeiten und Statusverteilung."""

    samples: int = 0
    overruns: int = 0
    cycle_times: list[float] = field(default_factory=list)
    status_counts: dict[ValueStatus, int] = field(
        default_factory=lambda: {s: 0 for s in ValueStatus}
    )

    def log_summary(self, interval_s: float) -> None:
        """Zusammenfassung ausgeben."""
        _log.info("=" * 78)
        _log.info("Samples: %d, Overruns: %d", self.samples, self.overruns)
        if self.cycle_times:
            _log.info(
                "Zykluszeit min/median/max: %.3f / %.3f / %.3f s (Soll %.3f s)",
                min(self.cycle_times),
                statistics.median(self.cycle_times),
                max(self.cycle_times),
                interval_s,
            )
        total = sum(self.status_counts.values())
        if total:
            for status, count in self.status_counts.items():
                _log.info("  %-10s %6d  (%.1f %%)", status.value, count, 100.0 * count / total)


def run_measurement_loop(
    session: WTSession,
    table: ItemTable,
    sink: SampleSink,
    interval_s: float,
    max_samples: int | None,
    max_duration_s: float | None,
    use_hold: bool,
    record_condition: bool,
    log_every: int,
    metadata: Mapping[str, object] | None = None,
) -> LoopStatistics:
    """Messschleife mit driftfreier Taktung.

    Bricht sauber ab bei KeyboardInterrupt, erreichter Sampleanzahl oder
    abgelaufener Maximaldauer.

    UEBERARBEITET (ROADMAP M4-2): Der Parameter hiess 'recorder' und war auf
    'CsvRecorder' festgelegt. Jetzt ist es ein 'SampleSink' - die Schleife
    kennt kein Ausgabeformat mehr. Ein zweites Format ist eine Klasse in
    'wt3000_sinks.py' und keine Zeile hier.

    Die Schleife OEFFNET und SCHLIESST die Senke selbst: die Spaltennamen
    stehen in 'table', und ein 'finally' ist der einzige Ort, an dem sich
    'close()' auch bei Abbruch, Fehler und Strg+C zusagen laesst. Der Aufrufer
    baut die Senke also nur noch, statt ihren Lebenszyklus zu fuehren.
    Nebenwirkung, die man kennen muss: nach einem Lauf ist die Senke
    geschlossen; zwei Messreihen in eine Datei gehen so nicht.

    OFFEN (ROADMAP M3-1): Diese Funktion wird der Rumpf der Klasse
    'Measurement'. Drei Stellen sind dabei anzupassen und nicht bloss zu
    verschieben:

      1. 'except KeyboardInterrupt' wird wirkungslos, sobald die Schleife in
         einem Hintergrund-Thread laeuft - Python stellt SIGINT ausschliesslich
         dem Haupt-Thread zu. Der Abbruch per Strg+C gehoert dann auf die
         Aufruferseite (stop()/wait()), nicht hierher. Als blockierender
         Generator 'stream()' bleibt er dagegen richtig, wo er ist.
      2. 'time.sleep(wait)' muss 'stop_event.wait(wait)' werden, sonst greift
         stop() erst nach dem laufenden Intervall. Bei :RATE 5 s sind das fuenf
         Sekunden Verzug auf ein Stoppsignal - genau der Fall, den M3-1 mit
         'threading.Event als Stoppsignal, nicht als Flag' meint.
      3. Rueckstellung: HOLD wird hier bereits im 'with' zurueckgenommen und
         greift damit auch bei stop(). Bereiche und Item-Tabelle liegen
         dagegen beim Aufrufer (wt3000_ranging.applied_ranges(),
         ItemAccess.applied()) - M3-1 verlangt sie im Thread. Wer diese
         Context Manager kuenftig haelt, ist vor dem ersten Handgriff zu
         entscheiden; es verschiebt die Verantwortung fuer den Geraetezustand.

    ERLEDIGT (ROADMAP M4-1): der Rueckgabeweg. Die Schleife baut je Zyklus ein
    'Sample' und reicht genau das an die Ausgabe weiter. Der von M3-1
    geforderte Generator 'stream()' hat damit bereits etwas zu liefern - aus
    'recorder.write(datensatz)' wird ein 'yield datensatz', und die Erkennung
    aus M3-3/M3-4 setzt nur noch 'Sample.mark'. Eine zweite Signatur entsteht
    dabei nicht mehr.
    """
    stats = LoopStatistics()
    started_monotonic = time.monotonic()
    next_tick = started_monotonic
    # UEBERARBEITET (M4-1): hiess 'sample'. Der Name ist an den Typ 'Sample'
    # gegangen; der Zaehler heisst wie das Feld, das er fuellt.
    number = 0

    # NEU (M4-2): Die Spaltennamen entstehen hier und nicht mehr beim Aufrufer -
    # sie stehen in der Item-Tabelle, gegen die auch gemessen wird. Damit koennen
    # Kopf und Daten gar nicht mehr aus verschiedenen Quellen stammen.
    sink.open([item.key for item in table.items], metadata or {})
    try:
        return _loop_body(
            session=session,
            table=table,
            sink=sink,
            stats=stats,
            started_monotonic=started_monotonic,
            next_tick=next_tick,
            number=number,
            interval_s=interval_s,
            max_samples=max_samples,
            max_duration_s=max_duration_s,
            use_hold=use_hold,
            record_condition=record_condition,
            log_every=log_every,
        )
    finally:
        # Auch bei Fehler, Abbruch und Strg+C. Die Senke ist das Einzige, was
        # ausserhalb des Prozesses weiterlebt.
        sink.close()


def _loop_body(
    *,
    session: WTSession,
    table: ItemTable,
    sink: SampleSink,
    stats: LoopStatistics,
    started_monotonic: float,
    next_tick: float,
    number: int,
    interval_s: float,
    max_samples: int | None,
    max_duration_s: float | None,
    use_hold: bool,
    record_condition: bool,
    log_every: int,
) -> LoopStatistics:
    """Die eigentliche Schleife. Getrennt, damit 'finally' oben lesbar bleibt."""
    with NumericHold(session, enabled=use_hold) as hold:
        try:
            while True:
                if max_samples is not None and number >= max_samples:
                    _log.info("Sampleanzahl erreicht (%d)", max_samples)
                    break
                elapsed = time.monotonic() - started_monotonic
                if max_duration_s is not None and elapsed >= max_duration_s:
                    _log.info("Maximaldauer erreicht (%.1f s)", max_duration_s)
                    break

                # Auf den naechsten Takt warten.
                wait = next_tick - time.monotonic()
                if wait > 0:
                    time.sleep(wait)

                cycle_start = time.monotonic()

                # Snapshot einfrieren, dann lesen. Der Zeitstempel bezieht sich
                # auf den Moment des HOLD ON, nicht auf den Antworteingang.
                hold.refresh()
                timestamp = datetime.now(timezone.utc).astimezone()
                values = read_numeric_values(session, expected_count=len(table.items))

                condition: int | None = None
                if record_condition:
                    # UEBERARBEITET (Schritt 5b, Befund A-06): die kritischste der
                    # sechs Stellen - sie liegt INNERHALB der laufenden Schleife.
                    # Ein ValueError beendete hier eine womoeglich stundenlange
                    # Messreihe mit einem Traceback statt mit dem sauberen Abbruch,
                    # fuer den _loop_body gebaut ist.
                    #
                    # Bewusst OHNE condition_warnings(): eine Warnung je Zyklus
                    # ueber Stunden nuetzt niemandem. Der Zustand wird aufgezeichnet,
                    # nicht kommentiert.
                    condition = parse_condition(session.query(":STATus:CONDition?"))

                number += 1
                stats.samples = number
                for value in values:
                    stats.status_counts[value.status] += 1

                # NEU (M4-1): der Zyklus wird zu EINEM Gegenstand
                # zusammengefasst, bevor er die Schleife verlaesst. Ab hier
                # kennt die Ausgabeseite nur noch diesen Typ - und der
                # Generator 'stream()' aus M3-1 hat schon jetzt etwas zu
                # liefern, ohne dass die Schleife noch einmal umgebaut wird.
                sink.write(
                    Sample(
                        timestamp=timestamp,
                        elapsed_s=cycle_start - started_monotonic,
                        number=number,
                        condition=condition,
                        values=values,
                    )
                )

                cycle_time = time.monotonic() - cycle_start
                stats.cycle_times.append(cycle_time)

                if log_every > 0 and number % log_every == 0:
                    _log.info(
                        "Sample %d | Zyklus %.3f s | Condition %s | %s",
                        number,
                        cycle_time,
                        "-" if condition is None else condition,
                        _preview(table, values),
                    )

                # Naechsten Takt setzen. Bei Overrun wird der Takt neu
                # aufgesetzt, statt aufzuholen.
                next_tick += interval_s
                if next_tick < time.monotonic():
                    stats.overruns += 1
                    if stats.overruns in (1, 10, 100) or stats.overruns % 500 == 0:
                        _log.warning(
                            "Zyklus %d ueberschreitet das Intervall (%.3f s > %.3f s), "
                            "Overruns bisher: %d",
                            number,
                            cycle_time,
                            interval_s,
                            stats.overruns,
                        )
                    next_tick = time.monotonic() + interval_s

        except KeyboardInterrupt:
            _log.info("Abbruch durch Benutzer (Strg+C) nach %d Samples", number)

    return stats


def _preview(table: ItemTable, values: list[NumericValue], count: int = 3) -> str:
    """Kurze Vorschau der ersten Werte fuer die Logzeile."""
    parts = [
        f"{item.key}={value}" for item, value in list(zip(table.items, values))[:count]
    ]
    return " ".join(parts)
