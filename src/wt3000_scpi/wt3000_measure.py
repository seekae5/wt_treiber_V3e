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
# UEBERARBEITET (ROADMAP M3-3): parse_nr3() fuer ':RATE?' - die Rate des
# Geraets ist eine NR3-Zahl und wird mit derselben Regel gelesen wie jede
# andere Zahlenantwort im Paket.
from .wt3000_common import parse_condition, parse_nr3
from .wt3000_core import WTError, WTSession
from .wt3000_itemspec import ItemSpec
from .wt3000_numeric import (
    ItemTable,
    NumericValue,
    ValueStatus,
    read_numeric_block,
)

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

    UEBERARBEITET (21.08.2026): Diese Zuordnung war bis hierher ein Kommentar,
    den niemand nachpruefen konnte. Seit ':MEASure' erschlossen ist, liefert
    'wt.computation.frequency_item(1)' die tatsaechlich eingestellte Quelle -
    wer das Profil anpasst, kann vorher nachsehen, statt sich auf diesen Absatz
    zu verlassen.

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


#: Summengroessen der Oberschwingungsanalyse (Handbuch 6-44, Funktionsliste).
#
# UTHD/ITHD/PTHD  Klirrfaktor von Spannung, Strom, Leistung
# UTHF/ITHF       Telephone Harmonic Factor
# UTIF/ITIF       Telephone Influence Factor
# HVF/HCF         Harmonic Voltage/Current Factor
#
# Alle verlangen die Rechenoption (/G6) - wie die ganze Gruppe - und KEINE
# Ordnungsangabe ("Order: Not required"). Sie sind gewoehnliche Items der
# NORMal-Tabelle, kein Sonderweg.
HARMONIC_SUMMARY_FUNCTIONS: tuple[str, ...] = (
    "UTHD",
    "ITHD",
    "PTHD",
    "UTHF",
    "ITHF",
    "UTIF",
    "ITIF",
    "HVF",
    "HCF",
)


def build_harmonics_profile(
    orders: tuple[int, ...] = (1, 3, 5, 7, 9, 11, 13),
    elements: tuple[str, ...] = ("1", "2", "3"),
) -> tuple[ItemSpec, ...]:
    """Messprofil fuer eine Oberschwingungsmessung.

    NEU (ROADMAP M2-1 Punkt 5, Rang 3 der Analyse) - das Gegenstueck zu
    'HarmonicsConfig' aus wt3000_deviceconfig, genau wie
    'build_integration_profile()' das Gegenstueck zur Integrationssteuerung
    ist: jene Klasse stellt die Analyse ein, dieses Profil macht ihr Ergebnis
    lesbar.

    DASS DAS OHNE NEUE MASCHINERIE GEHT, IST KEIN ZUFALL: 'ItemSpec' fuehrt
    seit jeher ein Feld 'order', und ':NUMeric:NORMal:ITEM<x>' nimmt als
    drittes Glied '{TOTal|DC|<NRf>}' mit <NRf> = 1..100 (Handbuch 6-96). Die
    Einzelordnung einer Groesse ist also ein gewoehnliches Item - 'U,1,5' ist
    die 5. Oberschwingung der Spannung an Element 1. Der andere Weg,
    ':NUMeric:LIST', liefert ganze Ordnungslisten auf einmal und ist hier
    bewusst NICHT benutzt: er braeuchte einen zweiten Blockleser neben
    'read_numeric_values()', und die Analyse fuehrt ihn nicht.

    Aufbau:

      1. je Element die Summengroessen (ohne Ordnung)
      2. je Element und Ordnung U, I, P - die eigentliche Ordnungsanalyse
      3. dazu jeweils der Gesamtwert (TOTal) als Bezug

    'orders' und 'elements' sind Parameter, weil hier - anders als beim
    Standardprofil - keine sinnvolle feste Wahl existiert: eine Netzqualitaets-
    pruefung nach EN 61000-3-2 will die ungeraden Ordnungen bis 40, eine
    Umrichteruntersuchung vielleicht 1..50 lueckenlos. Die Voreinstellung deckt
    die ungeraden Ordnungen bis 13 ab - der uebliche erste Blick.

    'verify=True' durchgehend: keine dieser Funktionen ist an diesem Geraet je
    gelesen worden.
    """
    if not orders:
        raise WTError("Ordnungsliste ist leer - ohne Ordnung kein Oberschwingungsprofil")
    ungueltig = [o for o in orders if not 0 <= o <= 100]
    if ungueltig:
        raise WTError(
            f"Ordnung(en) {ungueltig} liegen ausserhalb 0..100 "
            "(0 = Gleichanteil, siehe HarmonicsConfig.set_order_range)"
        )

    specs: list[ItemSpec] = []
    for element in elements:
        specs.extend(
            ItemSpec(f, element, verify=True) for f in HARMONIC_SUMMARY_FUNCTIONS
        )
    for element in elements:
        specs.extend(ItemSpec(f, element, "TOTAL", verify=True) for f in ("U", "I", "P"))
        for order in orders:
            specs.extend(
                ItemSpec(f, element, str(order), verify=True) for f in ("U", "I", "P")
            )
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

    UEBERARBEITET (ROADMAP M3-3): DUPLICATE wird seit dem 25.08.2026 von
    'run_measurement_loop()' gesetzt. MISSING wartet weiterhin auf M3-4.
    """

    OK = "OK"
    #: M3-3: bitgleich zum vorigen Zyklus - das Geraet hat nicht aktualisiert.
    #: Gesetzt von der Messschleife, siehe 'mark_duplicates'.
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
    #: NEU (ROADMAP M3-3): Zyklen, die bitgleich zum vorigen waren - das
    #: Geraet hatte noch nicht aktualisiert. Sie stehen mit in 'samples',
    #: denn gelesen wurden sie; gemessen wurden sie nicht.
    duplicates: int = 0
    #: NEU (ROADMAP M3-3): ':RATE?' zu Beginn des Laufs, None wenn nicht
    #: ermittelbar. Gehoert zum Ergebnis, weil sich ohne diesen Wert nicht
    #: beurteilen laesst, ob die Dublettenzahl erwartbar war.
    update_rate_s: float | None = None
    cycle_times: list[float] = field(default_factory=list)
    status_counts: dict[ValueStatus, int] = field(
        default_factory=lambda: {s: 0 for s in ValueStatus}
    )

    @property
    def measured_samples(self) -> int:
        """Datensaetze ohne die Dubletten - die Zahl echter Messpunkte."""
        return self.samples - self.duplicates

    def log_summary(self, interval_s: float) -> None:
        """Zusammenfassung ausgeben."""
        _log.info("=" * 78)
        _log.info("Samples: %d, Overruns: %d", self.samples, self.overruns)
        # NEU (ROADMAP M3-3): Die Dublettenzahl steht bewusst NEBEN der Rate.
        # '40 Dubletten' allein sagt nichts; '40 Dubletten bei :RATE 1 s und
        # 0.5 s Takt' sagt, dass die Messreihe planmaessig doppelt gelesen hat.
        if self.duplicates:
            _log.warning(
                "Dubletten: %d von %d Datensaetzen (%.1f %%) - echte Messpunkte: %d"
                "%s",
                self.duplicates,
                self.samples,
                100.0 * self.duplicates / self.samples if self.samples else 0.0,
                self.measured_samples,
                ""
                if self.update_rate_s is None
                else f", Geraeterate :RATE {self.update_rate_s:g} s bei {interval_s:g} s Takt",
            )
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


# ---------------------------------------------------------------------------
# NEU (ROADMAP M3-3): Taktkopplung
# ---------------------------------------------------------------------------
#
# Das Geraet aktualisiert seinen Messdatensatz im Takt von ':RATE' - 0.05 bis
# 20 s, eingestellt ueber InputConfig.set_update_rate(). Die Messschleife
# liest im Takt von 'interval_s'. Bis hierher waren das zwei voneinander
# unabhaengige Zahlen: wer mit 0.1 s gegen ein Geraet mit ':RATE 2 s' misst,
# bekam zwanzig identische Datensaetze je Messwert - alle als gueltig
# gekennzeichnet, ohne eine einzige Warnung. Die Datei sah danach aus wie eine
# Messreihe mit 20facher Aufloesung und war eine mit 20facher Wiederholung.
#
# Zwei Dinge greifen ab jetzt ineinander:
#
#   1. VORHER. Der Takt wird beim Start gegen ':RATE?' geprueft und eine
#      Unterschreitung ausdruecklich benannt (check_sample_interval).
#   2. WAEHREND. Jeder Zyklus wird mit dem vorigen verglichen; ein bitgleicher
#      bekommt SampleMark.DUPLICATE (run_measurement_loop).
#
# Warum beides und nicht nur eines: Punkt 1 allein waere Raterei - er sagt
# vorher, was zu erwarten ist, nicht was eingetreten ist. Punkt 2 allein
# kaeme zu spaet, naemlich erst in der fertigen Datei. Und Punkt 2 faengt
# ausserdem den Fall, den Punkt 1 gar nicht sehen kann: Takt gleich Rate, aber
# phasenverschoben - dann liefert das Geraet abwechselnd denselben und einen
# neuen Datensatz, obwohl beide Zahlen zueinander passen.


#: Toleranz beim Vergleich von Takt und Geraeterate.
#
# Reine Rechengenauigkeit, keine fachliche Groesse: 0.5 gegen 0.5 darf nicht
# an der Binaerdarstellung scheitern. Eine echte Unterschreitung liegt immer
# um Groessenordnungen darueber - die Stufenliste des Geraets kennt keine
# zwei Werte, die naeher als Faktor zwei beieinander lagen.
RATE_TOLERANCE: float = 1e-6


def device_update_rate(session: WTSession) -> float | None:
    """':RATE?' lesen. None, wenn das Geraet die Frage nicht beantwortet.

    NEU (ROADMAP M3-3). Bewusst FEHLERTOLERANT, und das ist hier die
    unbequemere Entscheidung: diese Abfrage dient einer Plausibilitaetspruefung,
    und eine Messreihe an einer fehlgeschlagenen Plausibilitaetspruefung
    scheitern zu lassen, waere die falsche Rangfolge. Wer messen will, soll
    messen - er soll nur wissen, was er tut.

    Die Behandlung folgt dem Vorbild von 'DeviceInfo.read()' fuer '*IDN?':
    protokollieren, 'drain_after_failure()', weiterarbeiten. Das
    Nachraeumen ist hier genauso wenig Kosmetik wie dort - die naechste
    Abfrage in dieser Sitzung ist der erste Messwertblock, und eine
    verspaetete ':RATE'-Antwort davor wuerde ihn um eine Position verschieben.
    """
    try:
        return parse_nr3(session.query(":RATE?"), "Update-Rate")
    except WTError as error:
        _log.warning(
            ":RATE? fehlgeschlagen: %s - der Takt wird ungeprueft uebernommen; "
            "Dubletten werden weiterhin erkannt",
            error,
        )
        session.drain_after_failure()
        return None


def check_sample_interval(interval_s: float, rate_s: float | None) -> None:
    """Takt gegen die Geraeterate pruefen und eine Unterschreitung benennen.

    NEU (ROADMAP M3-3). Meldet, ABER BRICHT NICHT AB. Der Grund steht in
    'SampleMark.DUPLICATE': seit die Dubletten gekennzeichnet werden, sind zu
    schnell gelesene Daten nicht mehr falsch, sondern nur redundant - und
    Redundanz ist eine legitime Wahl. Wer bewusst schneller liest, um den
    Zeitpunkt des Wechsels genauer einzugrenzen, tut etwas Sinnvolles.

    Das unterscheidet diesen Fall von 'read_numeric_values(strict=True)', das
    sehr wohl abbricht: dort verrutschen Spalten, und verrutschte Spalten
    ergeben still falsche Daten. Hier sind die Daten richtig, es sind nur
    mehr, als das Geraet neu gebildet hat.
    """
    if rate_s is None or rate_s <= 0.0:
        return
    if interval_s >= rate_s * (1.0 - RATE_TOLERANCE):
        return
    faktor = rate_s / interval_s if interval_s > 0 else float("inf")
    _log.warning(
        "Takt %.3f s liegt unter der Geraeterate :RATE %.3f s - das Geraet "
        "bildet nur alle %.3f s einen neuen Datensatz. Es ist mit rund %s "
        "Lesevorgaengen je echtem Messpunkt zu rechnen; die Wiederholungen "
        "werden als SampleMark.DUPLICATE gekennzeichnet. Abhilfe: "
        "interval_s >= %.3f setzen oder die Geraeterate ueber "
        "InputConfig.set_update_rate() verkleinern.",
        interval_s,
        rate_s,
        rate_s,
        "unendlich vielen" if faktor == float("inf") else f"{faktor:.1f}",
        rate_s,
    )


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
    # NEU (ROADMAP M3-3): Taktkopplung und Dublettenerkennung. Beide in der
    # Voreinstellung AN - wer sie abschaltet, soll das ausdruecklich tun.
    check_update_rate: bool = True,
    mark_duplicates: bool = True,
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

    NEU (ROADMAP M3-3): TAKT UND GERAETERATE haengen ab jetzt zusammen.
    'check_update_rate' liest ':RATE?' vor dem ersten Zyklus und benennt einen
    zu schnellen Takt; 'mark_duplicates' vergleicht jeden Zyklus mit dem
    vorigen und kennzeichnet einen bitgleichen als 'SampleMark.DUPLICATE'.
    Gezaehlt werden sie in 'LoopStatistics.duplicates', ausgewiesen in der
    Spalte 'status_flags' jeder Senke.

    Eine Dublette wird AUFGEZEICHNET und nicht verworfen. Sie ist eine
    Beobachtung: das Geraet hatte zu diesem Zeitpunkt nichts Neues. Wer sie
    nicht braucht, filtert sie beim Auswerten ueber 'mark' heraus - wer sie
    braucht, um den Zeitpunkt eines Wechsels einzugrenzen, faende sie nach
    einem Verwurf nie wieder. Weggeworfene Datensaetze sind ausserdem der
    einzige Fehler dieser Klasse, der sich hinterher nicht mehr beheben laesst.

    Die ermittelte Geraeterate geht MIT IN DIE METADATEN der Senke, unter
    'update_rate_s'. Ohne sie ist die Dublettenzahl in der fertigen Datei
    nicht zu beurteilen: erst das Verhaeltnis von Takt zu Rate sagt, ob 40
    Dubletten der Plan waren oder ein Befund.

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

    # NEU (ROADMAP M3-3): VOR dem Oeffnen der Senke - die Rate gehoert in die
    # Metadaten, und die Metadaten gehen mit 'open()' hinaus.
    if check_update_rate:
        stats.update_rate_s = device_update_rate(session)
        check_sample_interval(interval_s, stats.update_rate_s)

    started_monotonic = time.monotonic()
    next_tick = started_monotonic
    # UEBERARBEITET (M4-1): hiess 'sample'. Der Name ist an den Typ 'Sample'
    # gegangen; der Zaehler heisst wie das Feld, das er fuellt.
    number = 0

    # NEU (M4-2): Die Spaltennamen entstehen hier und nicht mehr beim Aufrufer -
    # sie stehen in der Item-Tabelle, gegen die auch gemessen wird. Damit koennen
    # Kopf und Daten gar nicht mehr aus verschiedenen Quellen stammen.
    # UEBERARBEITET (ROADMAP M3-3): Die Geraeterate kommt zu den Angaben des
    # Aufrufers dazu. Sie wird NICHT ueberschrieben, falls der Aufrufer sie
    # selbst gesetzt hat - seine Angabe ist die aeltere Zusage.
    ausgabe_metadaten: dict[str, object] = dict(metadata or {})
    ausgabe_metadaten.setdefault("update_rate_s", stats.update_rate_s)

    sink.open([item.key for item in table.items], ausgabe_metadaten)
    try:
        return _loop_body(
            session=session,
            table=table,
            sink=sink,
            stats=stats,
            mark_duplicates=mark_duplicates,
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
    mark_duplicates: bool,
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
    # NEU (ROADMAP M3-3): der Rohblock des vorigen Zyklus. Nur er entscheidet
    # ueber eine Dublette - zur Begruendung siehe read_numeric_block().
    previous_payload: bytes | None = None

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
                # UEBERARBEITET (ROADMAP M3-3): derselbe Lesevorgang, aber
                # mit den Rohbytes - sie sind die Grundlage des Vergleichs.
                payload, values = read_numeric_block(
                    session, expected_count=len(table.items)
                )

                mark = SampleMark.OK
                if mark_duplicates:
                    if previous_payload is not None and payload == previous_payload:
                        mark = SampleMark.DUPLICATE
                        stats.duplicates += 1
                        # Gestaffelt wie die Overrun-Meldung: die erste
                        # Dublette ist eine Nachricht, die tausendste ist
                        # Laerm. Ueber Stunden bleibt das Protokoll lesbar,
                        # ohne dass der Befund untergeht.
                        if stats.duplicates in (1, 10, 100) or stats.duplicates % 500 == 0:
                            _log.warning(
                                "Zyklus %d ist bitgleich zum vorigen - das Geraet hat "
                                "nicht aktualisiert. Dubletten bisher: %d",
                                number + 1,
                                stats.duplicates,
                            )
                    previous_payload = payload

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
                        mark=mark,
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
