# Messlogik mit HOLD, Datensatz, Sink-Vertrag und drei Ausfuehrungsarten.
# Konkrete Ausgabeformate liegen in wt3000_sinks und importieren von hier.

from __future__ import annotations

import hashlib
import json
import logging
# Fuer den NaN eines ausgefallenen Zyklus, siehe missing_values().
import math
import statistics
# Hintergrundlauf und Stoppsignal gehoeren zur Messschleife, nicht zur Fassade.
import threading
import time
import uuid
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Protocol, runtime_checkable

from .wt3000_common import parse_condition, parse_nr3, strip_response_header
from .wt3000_core import ProtocolError, TmctlError, WTError, WTSession
from .wt3000_itemspec import ItemSpec
from .wt3000_numeric import (
    FLOAT_NO_DATA,
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

    FU wird nur fuer Element 3 gefuehrt. Vor einer Anpassung ist die aktuelle
    Quelle mit 'wt.computation.frequency_item(1)' zu pruefen; FU anderer
    Quellen liefert sonst strukturell NAN. Integrationswerte haben ein eigenes
    Profil in 'build_integration_profile()'.
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
    """Messprofil fuer eine Wh-/Ah-Messung.

    'IntegrationConfig' steuert die Integration; die aufgelaufenen Werte
    werden wie alle Messwerte ueber die Item-Tabelle gelesen.

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

    'verify=True' kennzeichnet die noch nicht am Original-WT3000 bestaetigten
    Integrationsitems.
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

    'HarmonicsConfig' konfiguriert die Analyse; dieses Profil macht ihre
    Ergebnisse lesbar. Einzelordnungen sind normale Items: 'U,1,5' bezeichnet
    die 5. Spannungsoberschwingung an Element 1. ':NUMeric:LIST' wird bewusst
    nicht verwendet, weil es einen zweiten Blockleser erfordern wuerde.

    Aufbau:

      1. je Element die Summengroessen (ohne Ordnung)
      2. je Element und Ordnung U, I, P - die eigentliche Ordnungsanalyse
      3. dazu jeweils der Gesamtwert (TOTal) als Bezug

    Ordnungen und Elemente sind anwendungsabhaengig; die Voreinstellung deckt
    die ungeraden Ordnungen bis 13 ab. 'verify=True' kennzeichnet die noch
    nicht am Originalgeraet bestaetigten Funktionen.
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

    Die Kommandoreferenz kennt kein ':SINGle'. Fuer Einzelmessungen kommen
    daher ':NUMeric:HOLD' oder gegebenenfalls '*TRG' infrage; das Verhalten
    von '*TRG' ist noch am Geraet zu pruefen.
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
# Datensatz
# ---------------------------------------------------------------------------


class SampleMark(Enum):
    """Kennzeichnung eines ganzen Datensatzes - nicht eines einzelnen Werts.

    Abzugrenzen von 'ValueStatus': der bewertet einen einzelnen Messwert
    (NO_DATA, OVERRANGE) und kommt aus dem Bitmuster, das das Geraet liefert.
    'SampleMark' bewertet den Zyklus als Ganzes und entsteht im Treiber aus
    dem Vergleich mit dem vorigen Zyklus. DUPLICATE wird bereits gesetzt;
    MISSING ist fuer die spaetere Fehlerfortsetzung vorbereitet.
    """

    OK = "OK"
    #: Bitgleich zum vorigen Zyklus - das Geraet hat nicht aktualisiert.
    #: Gesetzt von der Messschleife, siehe 'mark_duplicates'.
    DUPLICATE = "DUPLICATE"
    #: Der Zyklus ist ausgefallen. Ein solcher Datensatz traegt keine
    #: Werte; er steht in der Ausgabe, damit die Luecke sichtbar bleibt,
    #: statt stillschweigend zu fehlen.
    MISSING = "MISSING"


class MeasurementAborted(WTError):
    """Die Fehlerstrategie hat den Lauf beendet.

    Abzugrenzen von dem Fehler, der sie ausgeloest hat: der steht als
    '__cause__' daran. Diese Klasse sagt nicht "die Leitung ist weg", sondern
    "die vereinbarte Grenze ist ueberschritten" - die Zahl der Fehlversuche
    steht in der Meldung, die bereits geschriebenen Daten liegen vollstaendig
    in der Senke.
    """


#: Fehler, die als Kommunikationsstoerung gelten und deshalb unter eine
#: 'ErrorPolicy' fallen.
#
# Bewusst nur diese zwei. TmctlError ist der Abriss auf der Leitung,
# ProtocolError die verstuemmelte Antwort - beides Zustaende, die ein
# naechster Versuch beheben kann. NICHT dabei sind ReadOnlyViolation,
# ChangesNotAllowed und ConcurrentAccessError: sie melden einen Fehler im
# aufrufenden Programm, und den durch Wiederholen zu uebergehen hiesse, ihn zu
# verstecken. DeviceError ebenfalls nicht - das Geraet beanstandet dann ein
# Kommando, was kein zweiter Versuch heilt.
COMMUNICATION_ERRORS: tuple[type[WTError], ...] = (TmctlError, ProtocolError)


@dataclass(frozen=True)
class ErrorPolicy:
    """Was bei einem Kommunikationsfehler waehrend der Messung geschehen soll.

    OHNE Policy (der Vorgabewert 'None' an der Messschleife) verhaelt sich der
    Treiber wie bisher: der Fehler verlaesst die Schleife und beendet den Lauf.
    Das bleibt die Voreinstellung, weil das Gegenteil - Ausnahmen
    stillschweigend in Datenzeilen zu verwandeln - kein Standardverhalten sein
    darf, das man versehentlich bekommt.

    MIT Policy wird ein fehlgeschlagener Zyklus zu einem Datensatz mit
    'SampleMark.MISSING'. Er traegt NO_DATA in jeder Wertspalte und behaelt
    damit die feste Spaltenzahl - die Luecke steht sichtbar in der Datei,
    statt stillschweigend zu fehlen, und die strenge Spaltenregel der Senken
    bleibt unangetastet.

    Die vier Grenzen:

      max_consecutive   So viele Fehler HINTEREINANDER beenden den Lauf.
                        Ein einzelner Aussetzer ist ein Aussetzer; zehn in
                        Folge sind ein abgerissenes Kabel.
      max_total         Gesamtbudget ueber den ganzen Lauf. None = unbegrenzt.
                        Fuer lange Laeufe sinnvoll: eine Leitung, die jede
                        Minute einmal zuckt, liefert am Ende mehr Luecken als
                        Messwerte, ohne je 'max_consecutive' zu reissen.
      reconnect_after   Nach so vielen Fehlern in Folge wird die Verbindung
                        neu aufgebaut. None = nie. Verlangt einen Transport
                        mit 'reconnect()'.
      max_reconnects    Obergrenze der Neuaufbauten im ganzen Lauf.

    'pause_s' wartet vor dem naechsten Versuch. Voreinstellung 0: der
    Messtakt wartet ohnehin bis zum naechsten Tick.
    """

    max_consecutive: int = 3
    max_total: int | None = None
    reconnect_after: int | None = None
    max_reconnects: int = 3
    pause_s: float = 0.0

    def __post_init__(self) -> None:
        if self.max_consecutive < 1:
            raise WTError(
                "max_consecutive muss mindestens 1 sein - eine Policy, die keinen "
                "einzigen Fehler zulaesst, ist 'error_policy=None'."
            )
        if self.max_total is not None and self.max_total < 1:
            raise WTError("max_total muss mindestens 1 sein oder None (unbegrenzt)")
        if self.reconnect_after is not None:
            if self.reconnect_after < 1:
                raise WTError("reconnect_after muss mindestens 1 sein oder None (nie)")
            if self.reconnect_after > self.max_consecutive:
                raise WTError(
                    f"reconnect_after={self.reconnect_after} liegt ueber "
                    f"max_consecutive={self.max_consecutive} - der Lauf braeche ab, "
                    "bevor je ein Neuaufbau versucht wuerde."
                )

    @classmethod
    def unattended(cls) -> "ErrorPolicy":
        """Voreinstellung fuer den unbeaufsichtigten Langzeitlauf.

        Nach zwei Fehlern in Folge wird neu verbunden, nach fuenf abgebrochen;
        hoechstens zehn Neuaufbauten. Bewusst kein 'max_total': wer ueber Tage
        misst, will einen einzelnen Aussetzer je Stunde nicht als Abbruchgrund.
        """
        return cls(max_consecutive=5, reconnect_after=2, max_reconnects=10)


@dataclass(frozen=True)
class Sample:
    """Ein vollstaendiger Messzyklus.

    Alles, was misst, liefert 'Sample'; alle Ausgabesenken nehmen diesen Typ.

    'timestamp' bezieht sich auf den Moment des ':NUMeric:HOLD ON', nicht auf
    den Antworteingang - der Datensatz ist zu diesem Zeitpunkt im Geraet
    eingefroren, das Auslesen danach dauert unbestimmt lange. 'elapsed_s'
    zaehlt dagegen auf einer monotonen Uhr ab Beginn der Messreihe und ist
    deshalb der richtige Bezug fuer Zeitdifferenzen; 'timestamp' folgt der
    Systemuhr und kann springen.

    Die Klasse ist eingefroren; 'values' bleibt zur Vermeidung einer Kopie je
    Zyklus eine veraenderliche Liste und macht 'Sample' damit nicht hashbar.
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
        'status_flags'; andere Senken koennen sie anders abbilden.

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
        # Bei einem Ausfall traegt JEDE Spalte NO_DATA - das ist die Folge von
        # 'mark=MISSING' und keine zusaetzliche Beobachtung. Sie einzeln
        # aufzuzaehlen blaehte die Spalte bei einer Tabelle mit 100 Items auf
        # ueber tausend Zeichen auf, ohne ein Byte Information zu tragen.
        if self.mark is SampleMark.MISSING:
            return flags
        flags.extend(
            f"{name}={value.status.value}"
            for name, value in zip(column_names, self.values)
            if value.status is not ValueStatus.OK
        )
        return flags


# ---------------------------------------------------------------------------
# Vertrag der Ausgabeseite
# ---------------------------------------------------------------------------


@runtime_checkable
class SampleSink(Protocol):
    """Wohin ein Messlauf seine Datensaetze schreibt.

    Der kleine Vertrag trennt Messlogik und Ausgabeformat. Konstruktoren
    enthalten formatspezifische Angaben; 'open()' erhaelt Spalten und
    Metadaten des Laufs. Die Messschleife ruft 'open()' einmal und 'close()'
    garantiert in einem 'finally' auf. 'close()' muss mehrfachen Aufruf
    vertragen, 'write()' vor 'open()' dagegen als Treiberfehler ablehnen.

    Die Senke prueft die Wertezahl gegen ihren Spaltenkopf, weil nur sie beide
    kennt. '@runtime_checkable' prueft zur Laufzeit lediglich die vorhandenen
    Methodennamen; die Signaturen bleiben Aufgabe des Typpruefers.
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


#: Aufbau der Sidecar-Datei. Wird mitgeschrieben, damit ein spaeterer Leser
#: eine aeltere Fassung erkennen kann, statt an einem fehlenden Feld zu raten.
SIDECAR_VERSION = 1

#: Abfragen, die den Geraetezustand fuer die Metadaten erheben. Reine Queries.
_METADATA_QUERIES: dict[str, str] = {
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


def read_device_context(session: WTSession) -> dict[str, str]:
    """Geraetezustand fuer die Metadaten erheben - ausschliesslich Queries.

    Ein fehlgeschlagener Query wird als Text im betroffenen Feld vermerkt und
    nicht verschwiegen; danach wird nachgeraeumt, damit eine verspaetete
    Antwort nicht im naechsten Feld landet.
    """
    device: dict[str, str] = {}
    for key, command in _METADATA_QUERIES.items():
        try:
            device[key] = session.query(command)
        except WTError as error:
            device[key] = f"<Fehler: {error}>"
            session.drain_after_failure()
    return device


def file_digest(path: Path) -> str:
    """SHA-256 einer Datei als Hexziffern."""
    hasher = hashlib.sha256()
    with path.open("rb") as datei:
        for block in iter(lambda: datei.read(65536), b""):
            hasher.update(block)
    return hasher.hexdigest()


def sidecar_path(data_path: Path) -> Path:
    """Standardname der Metadatendatei: 'messung.csv' -> 'messung.meta.json'.

    Aus dem Datennamen ABGELEITET und nicht frei gewaehlt - das ist die halbe
    Bindung: wer die Datendatei hat, findet die Metadaten, ohne sie zu suchen.
    Die andere Haelfte ist der Inhalt, siehe 'verify_sidecar()'.
    """
    return data_path.with_suffix(data_path.suffix + ".meta.json")


def output_paths_of(sink: object) -> list[Path]:
    """Die Dateien einer Senke einsammeln, auch aus Buendeln und Rotation.

    Ueber 'getattr' und nicht ueber isinstance: eine selbstgeschriebene Senke,
    die 'output_paths()' anbietet, wird damit ohne Aenderung hier erfasst.
    Senken ohne Dateien (CallbackSink) liefern eine leere Liste.
    """
    methode = getattr(sink, "output_paths", None)
    if callable(methode):
        pfade = methode()
        return [Path(p) for p in pfade]
    return []


@dataclass(frozen=True)
class RunMetadata:
    """Alles, was eine Messdatei ohne Zusatzwissen interpretierbar macht (M4-3).

    Bis hierher gab es diese Angaben zweimal in halber Form: die Laufparameter
    gingen an die Senken (und damit in die JSONL), der Geraetezustand in eine
    optionale Sidecar-Datei. Keines von beidem war vollstaendig, und nichts
    verband die CSV mit ihrem Sidecar - wer beide Dateien hatte, konnte nicht
    feststellen, ob sie zusammengehoeren.

    'RunMetadata' entsteht EINMAL je Lauf und geht an beide Stellen. Die
    Bindung ruht dabei auf drei Saeulen:

      1. 'run_id' - eine Kennung, die in den Metadaten JEDER Senke steht und
         damit in der JSONL-Kopfzeile und im Sidecar auftaucht.
      2. Der abgeleitete Dateiname ('messung.csv' -> 'messung.meta.json').
      3. Der Inhalt: das Sidecar nennt jede Datendatei mit Groesse und
         SHA-256. Damit laesst sich nachweisen, dass ein Sidecar zu genau
         DIESER Datei gehoert - und nebenbei, dass die Datei vollstaendig ist.

    Der Geraetezustand wird beim Anlegen erhoben, das Sidecar dagegen erst zum
    Schluss geschrieben: erst dann stehen Pruefsummen und Ergebnis fest.
    """

    run_id: str
    recorded_at: str
    columns: list[str]
    units: dict[str, "str | None"]
    device: dict[str, str]
    item_table: dict
    parameters: dict

    @classmethod
    def capture(
        cls,
        session: WTSession,
        table: ItemTable,
        parameters: Mapping[str, object] | None = None,
        include_device: bool = True,
    ) -> "RunMetadata":
        """Den Zustand VOR dem Lauf erheben.

        'include_device=False' laesst die elf Geraeteabfragen weg - fuer den
        Fall, dass die Sitzung schon einem Mess-Thread gehoert oder ein
        schneller Start wichtiger ist als der volle Steckbrief.
        """
        return cls(
            run_id=uuid.uuid4().hex[:16],
            recorded_at=datetime.now(timezone.utc).astimezone().isoformat(),
            columns=[item.key for item in table.items],
            units=table.unit_map(),
            device=read_device_context(session) if include_device else {},
            item_table=table.to_dict(),
            parameters=dict(parameters or {}),
        )

    def as_sink_metadata(self) -> dict[str, object]:
        """Die Form, die an die Senken geht.

        Bewusst FLACH und mit den Laufparametern auf oberster Ebene: die
        bisherigen Schluessel ('sample_interval_s', 'update_rate_s', 'units')
        bleiben dort, wo Senken und Auswertungen sie erwarten. Neu kommen
        'run_id' und der Geraeteblock dazu - eine JSONL ist damit ohne
        Sidecar vollstaendig.
        """
        daten: dict[str, object] = dict(self.parameters)
        daten["run_id"] = self.run_id
        daten["recorded_at"] = self.recorded_at
        daten["units"] = self.units
        if self.device:
            daten["device"] = self.device
        return daten

    def as_dict(self, data_files: Sequence[Mapping[str, object]] = ()) -> dict[str, object]:
        """Die Form, die ins Sidecar geht - vollstaendig, verschachtelt."""
        return {
            "sidecar_version": SIDECAR_VERSION,
            "run_id": self.run_id,
            "recorded_at": self.recorded_at,
            "parameters": self.parameters,
            "device": self.device,
            "item_table": self.item_table,
            "columns": self.columns,
            "units": self.units,
            "data_files": list(data_files),
        }

    def write_sidecar(
        self,
        path: Path,
        data_paths: Sequence[Path] = (),
        stats: "LoopStatistics | None" = None,
    ) -> Path:
        """Das Sidecar schreiben - nach dem Lauf, mit Pruefsummen und Ergebnis.

        Eine Datendatei, die nicht (mehr) existiert, wird mit 'missing: true'
        vermerkt statt uebergangen: dass sie fehlt, ist selbst eine Aussage.
        """
        dateien: list[dict[str, object]] = []
        for datei in data_paths:
            if not datei.exists():
                dateien.append({"name": datei.name, "missing": True})
                continue
            dateien.append(
                {
                    "name": datei.name,
                    "bytes": datei.stat().st_size,
                    "sha256": file_digest(datei),
                }
            )

        payload = self.as_dict(dateien)
        if stats is not None:
            payload["result"] = {
                "samples": stats.samples,
                "measured_samples": stats.measured_samples,
                "duplicates": stats.duplicates,
                "missing": stats.missing,
                "overruns": stats.overruns,
                "reconnects": stats.reconnects,
                "update_rate_s": stats.update_rate_s,
            }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        _log.info("Metadaten gesichert nach %s (run_id %s)", path, self.run_id)
        return path


class SidecarMismatch(WTError):
    """Sidecar und Datendatei gehoeren nicht zusammen oder passen nicht mehr.

    Getrennt von 'AppendMismatch': dort geht es um zwei Laeufe in einer Datei,
    hier um die Zuordnung zwischen Daten und ihrer Beschreibung.
    """


def verify_sidecar(data_path: Path, sidecar: Path | None = None) -> dict[str, object]:
    """Nachweisen, dass ein Sidecar zu dieser Datendatei gehoert (M4-3).

    Prueft in dieser Reihenfolge und bricht bei der ersten Abweichung ab:

      1. Das Sidecar existiert und ist lesbares JSON dieses Formats.
      2. Die Datei ist darin ueberhaupt genannt.
      3. Groesse und SHA-256 stimmen ueberein.

    Der Hash ist der eigentliche Nachweis. Ein gleicher Dateiname beweist
    nichts - zwei Laeufe heissen leicht gleich; ein gleicher Hash schliesst
    auch aus, dass die Datei seither abgeschnitten oder veraendert wurde.

    Liefert die Metadaten zurueck, wenn alles stimmt - so ist der uebliche
    Aufruf zugleich das Einlesen:

        meta = verify_sidecar(Path("messung.csv"))
        print(meta["device"]["idn"], meta["units"])
    """
    pfad = sidecar if sidecar is not None else sidecar_path(data_path)
    if not pfad.exists():
        raise SidecarMismatch(
            f"Zu {data_path.name} gibt es kein Sidecar ({pfad.name} fehlt). Ohne "
            "Metadaten ist die Datei nur mit Zusatzwissen interpretierbar."
        )
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SidecarMismatch(f"{pfad.name} ist kein lesbares JSON: {error}") from error
    if not isinstance(daten, dict) or "sidecar_version" not in daten:
        raise SidecarMismatch(
            f"{pfad.name} ist kein Sidecar dieses Treibers (Feld 'sidecar_version' fehlt)."
        )

    eintraege = daten.get("data_files")
    if not isinstance(eintraege, list):
        raise SidecarMismatch(f"{pfad.name} nennt keine Datendateien ('data_files' fehlt).")

    passend = [e for e in eintraege if isinstance(e, dict) and e.get("name") == data_path.name]
    if not passend:
        genannt = [e.get("name") for e in eintraege if isinstance(e, dict)]
        raise SidecarMismatch(
            f"{pfad.name} beschreibt {data_path.name} nicht - genannt sind {genannt}. "
            "Die beiden Dateien gehoeren nicht zusammen."
        )

    eintrag = passend[0]
    if not data_path.exists():
        raise SidecarMismatch(f"{data_path} existiert nicht")

    groesse = data_path.stat().st_size
    if eintrag.get("bytes") != groesse:
        raise SidecarMismatch(
            f"{data_path.name} misst {groesse} Bytes, das Sidecar nennt "
            f"{eintrag.get('bytes')}. Die Datei wurde seit dem Lauf veraendert oder "
            "abgeschnitten."
        )
    tatsaechlich = file_digest(data_path)
    if eintrag.get("sha256") != tatsaechlich:
        raise SidecarMismatch(
            f"{data_path.name} hat die Pruefsumme {tatsaechlich[:16]}..., das Sidecar "
            f"nennt {str(eintrag.get('sha256'))[:16]}.... Der Inhalt stimmt nicht mit "
            "dem beschriebenen Lauf ueberein."
        )
    return daten


def write_metadata(
    path: Path,
    session: WTSession,
    table: ItemTable,
    parameters: dict,
) -> None:
    """Geraetezustand und Laufparameter neben der CSV ablegen.

    AELTERER WEG, erhalten fuer die Stufenskripte und bestehende Aufrufer. Er
    schreibt VOR dem Lauf und kann deshalb weder Pruefsummen noch das Ergebnis
    enthalten - es entsteht eine Beschreibung ohne nachweisbare Bindung an die
    Datendatei.

    Fuer neue Aufrufe ist 'RunMetadata' der vollstaendige Weg: dieselben
    Angaben, aber einmal erhoben, an Senke UND Sidecar gereicht und ueber
    SHA-256 an die Datendatei gebunden. Ueber die Fassade genuegt
    'record(..., sidecar=True)'.

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
            # Hier folgt nach einem fehlgeschlagenen Query eine weitere
            # Abfrage. Eine verspaetete Antwort koennte daher im falschen
            # Sidecar-Feld landen. Queue bereinigen, den sichtbaren Fehler im
            # Feld aber beibehalten.
            session.drain_after_failure()

    payload = {
        "recorded_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "parameters": parameters,
        "device": device,
        "item_table": table.to_dict(),
        # Spaltenname -> Einheit aus derselben Item-Tabelle. None bedeutet
        # "nicht belegt", die leere Zeichenkette "dimensionslos".
        "units": table.unit_map(),
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
    #: Bitgleiche Zyklen ohne Geraeteaktualisierung. Sie zaehlen als gelesen,
    #: aber nicht als neue Messpunkte.
    duplicates: int = 0
    #: Ausgefallene Zyklen (SampleMark.MISSING). Sie stehen als Zeile in der
    #: Ausgabe, tragen aber keine Messwerte.
    missing: int = 0
    #: Erfolgreiche Neuaufbauten der Verbindung waehrend des Laufs.
    reconnects: int = 0
    #: ':RATE?' zu Beginn des Laufs; None, wenn nicht ermittelbar.
    update_rate_s: float | None = None
    cycle_times: list[float] = field(default_factory=list)
    status_counts: dict[ValueStatus, int] = field(
        default_factory=lambda: {s: 0 for s in ValueStatus}
    )

    @property
    def measured_samples(self) -> int:
        """Datensaetze ohne Dubletten und Ausfaelle - die echten Messpunkte."""
        return self.samples - self.duplicates - self.missing

    def log_summary(self, interval_s: float) -> None:
        """Zusammenfassung ausgeben."""
        _log.info("=" * 78)
        _log.info("Samples: %d, Overruns: %d", self.samples, self.overruns)
        # Ausfaelle stehen VOR den Dubletten: eine Luecke ist der schwerere
        # Befund, und wer nur die erste Zeile liest, soll sie sehen.
        if self.missing:
            _log.warning(
                "Ausgefallene Zyklen: %d von %d Datensaetzen (%.1f %%)%s",
                self.missing,
                self.samples,
                100.0 * self.missing / self.samples if self.samples else 0.0,
                f", Verbindung {self.reconnects}x neu aufgebaut" if self.reconnects else "",
            )
        # Dublettenzahl und Rate gehoeren zusammen: nur ihr Verhaeltnis zum
        # Messtakt zeigt, ob Wiederholungen erwartbar waren.
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
# Taktkopplung
# ---------------------------------------------------------------------------
# ':RATE' bestimmt die Geraeteaktualisierung, 'interval_s' den Lesetakt.
# Eine Vorabpruefung warnt vor zu schnellem Lesen; der Rohdatenvergleich
# kennzeichnet tatsaechliche Wiederholungen und deckt auch Phasenversatz ab.


#: Toleranz beim Vergleich von Takt und Geraeterate.
#
# Reine Rechengenauigkeit, keine fachliche Groesse: 0.5 gegen 0.5 darf nicht
# an der Binaerdarstellung scheitern. Eine echte Unterschreitung liegt immer
# um Groessenordnungen darueber - die Stufenliste des Geraets kennt keine
# zwei Werte, die naeher als Faktor zwei beieinander lagen.
RATE_TOLERANCE: float = 1e-6


def device_update_rate(session: WTSession) -> float | None:
    """':RATE?' lesen. None, wenn das Geraet die Frage nicht beantwortet.

    Die Abfrage ist nur eine Plausibilitaetspruefung und darf die Messung nicht
    verhindern. Nach einem Fehler wird die Antwortqueue bereinigt, damit eine
    verspaetete ':RATE'-Antwort nicht den ersten Messwertblock verschiebt.
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

    Die Funktion warnt, bricht aber nicht ab: zu schnell gelesene Daten sind
    durch 'SampleMark.DUPLICATE' erkennbar und koennen fuer eine genauere
    zeitliche Eingrenzung beabsichtigt sein. Anders als bei einer falschen
    Spaltenzahl bleiben die Daten fachlich richtig, nur redundant.
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
    # Taktpruefung und Dublettenerkennung sind sicherheitsrelevante Defaults.
    check_update_rate: bool = True,
    mark_duplicates: bool = True,
    # Ohne Hintergrundlauf gibt es kein externes Stoppsignal.
    stop_event: threading.Event | None = None,
    # Ohne Fehlerstrategie beendet der erste Kommunikationsfehler den Lauf.
    error_policy: "ErrorPolicy | None" = None,
) -> LoopStatistics:
    """Messschleife mit driftfreier Taktung.

    Bricht sauber ab bei KeyboardInterrupt, erreichter Sampleanzahl oder
    abgelaufener Maximaldauer.

    Die Schleife OEFFNET und SCHLIESST die Senke selbst: die Spaltennamen
    stehen in 'table', und ein 'finally' ist der einzige Ort, an dem sich
    'close()' auch bei Abbruch, Fehler und Strg+C zusagen laesst. Der Aufrufer
    baut die Senke also nur noch, statt ihren Lebenszyklus zu fuehren.
    Nebenwirkung, die man kennen muss: nach einem Lauf ist die Senke
    geschlossen; zwei Messreihen in eine Datei gehen so nicht.

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
    Dubletten geplant oder auffaellig waren.

    Ebenso gehen die EINHEITEN mit hinaus, unter 'units'
    als Abbildung Spaltenname -> Einheit. Sie stammen aus derselben
    Item-Tabelle wie die Spaltennamen; ein Wert 'null' heisst "Einheit dieser
    Funktion nicht belegt" und ist von "dimensionslos" (leere Zeichenkette)
    unterschieden - siehe FUNCTION_UNITS in wt3000_numeric.py.

    Der gemeinsame Rumpf ist 'iter_samples()'. Darauf sitzen drei Wege:

        run_measurement_loop()  blockierend, schreibt in eine Senke
        iter_samples()          Generator, liefert Samples an den Aufrufer
        Measurement             Hintergrundlauf mit start()/stop()/wait()

    Zwei Dinge bleiben deshalb hier und wandern nicht mit:

      * 'except KeyboardInterrupt'. Es gehoert dorthin, wo die Schleife im
        Thread des Aufrufers laeuft - Python stellt SIGINT ausschliesslich dem
        Haupt-Thread zu. Im Hintergrundlauf waere es wirkungslos; dort ist
        'stop()' der Abbruchweg.
      * Der Lebenszyklus der Senke. Der Generator kennt kein Ausgabeformat.

    'stop_event' ist der Weg, auf dem 'Measurement' seinen Abbruch hier
    hereinreicht; ohne Hintergrundlauf bleibt es None.
    """
    stats = LoopStatistics()

    # Vor dem Oeffnen: die Rate gehoert in die Metadaten von 'open()'.
    prepare_update_rate(session, interval_s, stats, check_update_rate)

    # Spalten, Rate und Einheiten stammen aus demselben Laufkontext. Explizite
    # Metadaten des Aufrufers haben Vorrang.
    ausgabe_metadaten: dict[str, object] = dict(metadata or {})
    ausgabe_metadaten.setdefault("update_rate_s", stats.update_rate_s)
    # Einheiten und Spaltennamen stammen aus derselben Item-Tabelle.
    ausgabe_metadaten.setdefault("units", table.unit_map())

    sink.open([item.key for item in table.items], ausgabe_metadaten)
    strom = iter_samples(
        session=session,
        table=table,
        stats=stats,
        interval_s=interval_s,
        max_samples=max_samples,
        max_duration_s=max_duration_s,
        use_hold=use_hold,
        record_condition=record_condition,
        log_every=log_every,
        mark_duplicates=mark_duplicates,
        stop_event=stop_event,
        error_policy=error_policy,
    )
    try:
        for sample in strom:
            sink.write(sample)
    except KeyboardInterrupt:
        _log.info("Abbruch durch Benutzer (Strg+C) nach %d Samples", stats.samples)
    finally:
        # Die Reihenfolge ist hier nicht beliebig, und der Generator steht
        # ZUERST: solange er nur ausgesetzt ist, haelt er HOLD.
        #
        # Der Fall, den 'close()' abdeckt und ein blosses Verlassen der
        # Schleife nicht: eine Ausnahme im RUMPF - etwa ein Strg+C, das
        # waehrend 'sink.write()' eintrifft. Dann ist der Generator am 'yield'
        # ausgesetzt, sein 'finally' hat nicht gelaufen, und ohne diesen
        # Aufruf haenge HOLD bis zur naechsten Speicherbereinigung. Das Geraet
        # liefert danach eingefrorene Werte, waehrend die Anzeige weiterlaeuft.
        # (Laeuft der Generator dagegen von selbst aus oder wirft er selbst,
        # ist er bereits beendet und 'close()' tut nichts.)
        strom.close()
        # Auch bei Fehler, Abbruch und Strg+C. Die Senke ist das Einzige, was
        # ausserhalb des Prozesses weiterlebt.
        sink.close()
    return stats


def prepare_update_rate(
    session: WTSession,
    interval_s: float,
    stats: LoopStatistics,
    check_update_rate: bool,
) -> None:
    """':RATE?' lesen und den Takt dagegen pruefen.

    Ausgelagert, weil es VOR dem ersten Zyklus geschehen muss und damit vor
    dem ersten 'yield' eines Generators - der laeuft aber erst beim ersten
    'next()' an. Beide Aufrufer (die blockierende Schleife und 'Measurement')
    rufen es deshalb selbst, bevor sie die Senke oeffnen.
    """
    if not check_update_rate:
        return
    stats.update_rate_s = device_update_rate(session)
    check_sample_interval(interval_s, stats.update_rate_s)


def iter_samples(
    *,
    session: WTSession,
    table: ItemTable,
    stats: LoopStatistics,
    interval_s: float,
    max_samples: int | None,
    max_duration_s: float | None,
    use_hold: bool,
    record_condition: bool,
    log_every: int,
    mark_duplicates: bool = True,
    stop_event: threading.Event | None = None,
    error_policy: "ErrorPolicy | None" = None,
    # 'Generator' und nicht 'Iterator': nur der erste Typ sagt zu, dass
    # 'close()' vorhanden ist - und genau darauf verlassen sich
    # 'run_measurement_loop()' und 'Measurement._run()', um HOLD auch dann
    # zurueckzunehmen, wenn die Ausnahme im Schleifenrumpf entstand.
) -> Generator[Sample, None, None]:
    """Gemeinsamer Generatorrumpf; liefert je Zyklus ein 'Sample'.

    Ausgabeformat, Metadaten und KeyboardInterrupt behandelt der Aufrufer.
    Der Generator garantiert dagegen die HOLD-Rueckstellung, auch bei
    vorzeitigem 'close()'. 'stats' wird waehrend des Laufs fortgeschrieben.
    Ein 'stop_event' unterbricht das Warten sofort statt erst nach dem Takt.

    'error_policy' entscheidet ueber Kommunikationsfehler: ohne sie beendet
    der erste Fehler den Lauf, mit ihr wird der Zyklus zu einem Datensatz mit
    'SampleMark.MISSING' und die Reihe laeuft weiter. Siehe 'ErrorPolicy'.
    """
    # Nur Rohbytes entscheiden ueber eine Dublette; geparste NaN-Werte waeren
    # wegen NaN != NaN dafuer ungeeignet.
    previous_payload: bytes | None = None

    started_monotonic = time.monotonic()
    next_tick = started_monotonic
    number = 0
    fehler_in_folge = 0

    with NumericHold(session, enabled=use_hold) as hold:
        while True:
            if max_samples is not None and number >= max_samples:
                _log.info("Sampleanzahl erreicht (%d)", max_samples)
                break
            elapsed = time.monotonic() - started_monotonic
            if max_duration_s is not None and elapsed >= max_duration_s:
                _log.info("Maximaldauer erreicht (%.1f s)", max_duration_s)
                break
            # Auch nach einem langsamen Lesevorgang keinen weiteren Zyklus
            # beginnen, wenn inzwischen stop() gerufen wurde.
            if stop_event is not None and stop_event.is_set():
                _log.info("Stoppsignal erhalten nach %d Samples", number)
                break

            # Auf den naechsten Takt warten.
            wait = next_tick - time.monotonic()
            if wait > 0:
                if stop_event is not None:
                    if stop_event.wait(wait):
                        _log.info("Stoppsignal erhalten nach %d Samples", number)
                        break
                else:
                    time.sleep(wait)

            cycle_start = time.monotonic()

            mark = SampleMark.OK
            condition: int | None = None
            abbruch: WTError | None = None

            try:
                # Snapshot einfrieren, dann lesen. Der Zeitstempel bezieht sich
                # auf den Moment des HOLD ON, nicht auf den Antworteingang.
                hold.refresh()
                timestamp = datetime.now(timezone.utc).astimezone()
                # Rohbytes sind die Grundlage der Dublettenpruefung.
                payload, values = read_numeric_block(
                    session, expected_count=len(table.items)
                )

                if mark_duplicates:
                    if previous_payload is not None and payload == previous_payload:
                        mark = SampleMark.DUPLICATE
                        stats.duplicates += 1
                        # Gestaffelt wie die Overrun-Meldung: die erste
                        # Dublette ist eine Nachricht, die tausendste ist
                        # Laerm. Ueber Stunden bleibt das Protokoll lesbar,
                        # ohne dass die Auffaelligkeit untergeht.
                        if stats.duplicates in (1, 10, 100) or stats.duplicates % 500 == 0:
                            _log.warning(
                                "Zyklus %d ist bitgleich zum vorigen - das Geraet hat "
                                "nicht aktualisiert. Dubletten bisher: %d",
                                number + 1,
                                stats.duplicates,
                            )
                    previous_payload = payload

                if record_condition:
                    # Bewusst OHNE condition_warnings(): eine Warnung je Zyklus
                    # ueber Stunden nuetzt niemandem. Der Zustand wird aufgezeichnet,
                    # nicht kommentiert.
                    condition = parse_condition(session.query(":STATus:CONDition?"))

            except COMMUNICATION_ERRORS as error:
                # Ohne Fehlerstrategie bleibt es beim bisherigen Verhalten:
                # der Fehler beendet den Lauf.
                if error_policy is None:
                    raise

                fehler_in_folge += 1
                stats.missing += 1
                mark = SampleMark.MISSING
                timestamp = datetime.now(timezone.utc).astimezone()
                # Feste Spaltenzahl auch fuer den Ausfall - siehe missing_values().
                values = missing_values(len(table.items))
                # Der vorige Rohblock bleibt der Bezug: ein ausgefallener
                # Zyklus sagt nichts darueber, was das Geraet zuletzt lieferte.

                # Kann eine MeasurementAborted ausloesen (Geraetezustand passt
                # nach einem Neuaufbau nicht mehr). Dann darf keine weitere
                # Zeile entstehen, auch diese nicht - deshalb faengt sie hier
                # niemand ab.
                abbruch = _handle_cycle_failure(
                    error,
                    session=session,
                    table=table,
                    policy=error_policy,
                    stats=stats,
                    consecutive=fehler_in_folge,
                    number=number,
                )
                if error_policy.pause_s > 0:
                    time.sleep(error_policy.pause_s)
            else:
                # Erst ein vollstaendig gelesener Zyklus beweist, dass die
                # Verbindung wieder traegt.
                fehler_in_folge = 0

            number += 1
            stats.samples = number
            for value in values:
                stats.status_counts[value.status] += 1

            # Die Ausgabeseite kennt nur den zusammengefassten Datensatz.
            yield Sample(
                timestamp=timestamp,
                elapsed_s=cycle_start - started_monotonic,
                number=number,
                condition=condition,
                values=values,
                mark=mark,
            )

            # NACH dem yield: der Datensatz, der den Abbruch ausgeloest hat,
            # ist damit geschrieben. Andernfalls fehlte in der Datei
            # ausgerechnet die Zeile, die das Ende erklaert.
            if abbruch is not None:
                raise abbruch

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


def missing_values(count: int) -> list[NumericValue]:
    """Wertliste eines ausgefallenen Zyklus: 'count' mal NO_DATA.

    Der Kern der Loesung fuer den Zielkonflikt aus S-08. Ein ausgefallener
    Zyklus hat naturgemaess keine Messwerte, aber die Senken bestehen zu Recht
    auf der festen Spaltenzahl - sonst verrutschen Werte gegen den
    Spaltenkopf. Aufgefuellt wird mit genau dem Bitmuster, das auch das Geraet
    fuer 'kein Wert' schickt (FLOAT_NO_DATA), damit die Zeile durch jede
    vorhandene Auswertung laeuft: die CSV schreibt leere Zellen, JSONL 'null'.

    Verwechslungsgefahr besteht nicht: 'mark=MISSING' steht in derselben
    Zeile und unterscheidet den Ausfall von einem Zyklus, in dem das Geraet
    selbst NO_DATA gemeldet hat.
    """
    return [NumericValue(math.nan, ValueStatus.NO_DATA, FLOAT_NO_DATA) for _ in range(count)]


def verify_after_reconnect(session: WTSession, table: ItemTable) -> None:
    """Nach einem Neuaufbau pruefen, ob weitergemessen werden DARF.

    Ein 'reconnect()' stellt die Verbindung wieder her, nicht den
    Geraetezustand. War das Geraet zwischendurch aus oder hat jemand am
    Bedienfeld gearbeitet, stimmen Zahlenformat oder Item-Tabelle womoeglich
    nicht mehr - und dann bedeutet jede weitere Zeile etwas anderes als ihr
    Spaltenkopf behauptet. Solche Daten sind schlimmer als keine, weil sie
    hinterher nicht mehr als falsch zu erkennen sind.

    Geprueft wird deshalb beides, und eine Abweichung beendet den Lauf:

      * ':NUMeric:FORMat' muss FLOat sein - sonst ist die Antwort auf
        ':NUMeric:NORMal:VALue?' kein Binaerblock mehr.
      * ':COMMunicate:HEADer' muss 0 sein - sonst tragen alle Antworten einen
        Kopf, den die Parser hier nicht erwarten.
      * Die Item-Tabelle muss Element fuer Element dieselbe sein. Die
        Reihenfolge ist die ganze Zuordnung: der Messwertblock ist rein
        positionsbezogen.
    """
    fmt = strip_response_header(session.query(":NUMeric:FORMat?"))
    if not fmt.upper().startswith("FLO"):
        raise MeasurementAborted(
            f"Nach dem Neuaufbau steht ':NUMeric:FORMat' auf {fmt!r} statt FLOat. "
            "Das Geraet war vermutlich stromlos. Weitermessen wuerde den "
            "Messwertblock falsch auslesen."
        )

    header = strip_response_header(session.query(":COMMunicate:HEADer?"))
    if header not in ("0", "OFF"):
        raise MeasurementAborted(
            f"Nach dem Neuaufbau steht ':COMMunicate:HEADer' auf {header!r} statt 0. "
            "Alle Antworten traegen dann einen Kopf, den die Auswertung hier "
            "nicht erwartet."
        )

    aktuell = ItemTable.read_from_device(session)
    if [item.key for item in aktuell.items] != [item.key for item in table.items]:
        raise MeasurementAborted(
            f"Nach dem Neuaufbau hat die Item-Tabelle {len(aktuell.items)} Eintraege "
            f"statt {len(table.items)} beziehungsweise eine andere Reihenfolge. Die "
            "Spaltenzuordnung der bisherigen Datei gilt damit nicht mehr - der Lauf "
            "wird beendet, statt Zeilen unter falschen Spalten fortzuschreiben."
        )
    _log.info("Nach dem Neuaufbau geprueft: Zahlenformat, Header und Item-Tabelle stimmen")


def _handle_cycle_failure(
    error: WTError,
    *,
    session: WTSession,
    table: ItemTable,
    policy: ErrorPolicy,
    stats: LoopStatistics,
    consecutive: int,
    number: int,
) -> WTError | None:
    """Einen fehlgeschlagenen Zyklus abarbeiten; liefert einen Abbruchgrund oder None.

    Ausgelagert, damit die Schleife lesbar bleibt: hier stehen Aufraeumen,
    Wiederverbinden und die beiden Grenzen, dort der Messtakt.

    Der Rueckgabewert ist bewusst KEINE Ausnahme, die gleich fliegt. Der
    Aufrufer soll den MISSING-Datensatz erst noch ausgeben und dann abbrechen -
    sonst fehlte ausgerechnet die Zeile, die den Abbruch erklaert.
    """
    _log.warning(
        "Zyklus %d fehlgeschlagen (%d in Folge): %s", number + 1, consecutive, error
    )

    # Eine verspaetete Antwort wuerde sonst dem naechsten Query zugeordnet -
    # der bekaeme dann die Werte des fehlgeschlagenen Zyklus.
    try:
        session.drain_after_failure()
    except WTError as aufraeumfehler:
        _log.warning("Nachraeumen nach dem Fehler misslang: %s", aufraeumfehler)

    if policy.max_total is not None and stats.missing >= policy.max_total:
        return _aborted(
            f"{stats.missing} ausgefallene Zyklen erreichen das Gesamtbudget "
            f"max_total={policy.max_total}. Der Lauf wird beendet; die bis hierher "
            "geschriebenen Daten sind vollstaendig.",
            error,
        )

    if (
        policy.reconnect_after is not None
        and consecutive >= policy.reconnect_after
        and stats.reconnects < policy.max_reconnects
    ):
        if not session.can_reconnect:
            _log.error(
                "Wiederverbindung angefordert, aber der Transport kann es nicht - "
                "reconnect_after bleibt wirkungslos"
            )
        else:
            try:
                session.reconnect()
                verify_after_reconnect(session, table)
            except MeasurementAborted:
                # Der Geraetezustand passt nicht mehr: nicht weitermessen.
                raise
            except WTError as neuaufbau:
                _log.error("Neuaufbau der Verbindung fehlgeschlagen: %s", neuaufbau)
            else:
                stats.reconnects += 1
                _log.warning(
                    "Verbindung neu aufgebaut (%d von hoechstens %d)",
                    stats.reconnects,
                    policy.max_reconnects,
                )
                # Der Neuaufbau ist der Erfolg, auf den die Zaehlung wartet.
                return None

    if consecutive >= policy.max_consecutive:
        return _aborted(
            f"{consecutive} Kommunikationsfehler in Folge erreichen "
            f"max_consecutive={policy.max_consecutive}. Der Lauf wird beendet; die "
            "bis hierher geschriebenen Daten sind vollstaendig.",
            error,
        )
    return None


def _aborted(meldung: str, ursache: WTError) -> MeasurementAborted:
    """Abbruchgrund bauen und die ausloesende Ausnahme daranhaengen.

    'MeasurementAborted' sagt "die vereinbarte Grenze ist erreicht" - warum
    die Zyklen scheiterten, steht in der Ursache. Weil der Abbruch erst NACH
    dem 'yield' ausgeloest wird, ist 'raise ... from ...' an der Wurfstelle
    nicht mehr moeglich; '__cause__' wird deshalb hier gesetzt und ergibt
    dieselbe verkettete Ausgabe im Traceback.
    """
    abbruch = MeasurementAborted(meldung)
    abbruch.__cause__ = ursache
    return abbruch


def _preview(table: ItemTable, values: list[NumericValue], count: int = 3) -> str:
    """Kurze Vorschau der ersten Werte fuer die Logzeile."""
    parts = [
        f"{item.key}={value}" for item, value in list(zip(table.items, values))[:count]
    ]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Steuerbare Messung
# ---------------------------------------------------------------------------


class Measurement:
    """Eine Messreihe als Gegenstand: start(), stop(), wait(), is_running.

    Eine laufende Messung besitzt ihre Sitzung; Fremdzugriffe werden mit
    'ConcurrentAccessError' abgelehnt. Wer zwischen Samples auf das Geraet
    zugreifen muss, verwendet 'wt.measure.stream()'.

    HOLD stellt der Generator zurueck. Bereiche und Item-Tabelle bleiben in
    den Context Managern des Aufrufers. 'Measurement' ist deshalb selbst ein
    Context Manager und beendet den Thread, bevor diese aeusseren Klammern
    geschlossen werden.

    EINWEG: Ein 'Measurement' laesst sich genau einmal starten. Ein zweiter
    Lauf ist ein zweites Objekt - schon weil die Senke nach dem ersten Lauf
    geschlossen ist.
    """

    def __init__(
        self,
        *,
        session: WTSession,
        table: ItemTable,
        sink: SampleSink,
        interval_s: float = 1.0,
        max_samples: int | None = None,
        max_duration_s: float | None = None,
        use_hold: bool = True,
        record_condition: bool = True,
        log_every: int = 0,
        metadata: Mapping[str, object] | None = None,
        check_update_rate: bool = True,
        mark_duplicates: bool = True,
        error_policy: "ErrorPolicy | None" = None,
        # Metadaten des Laufs; wird nach dem Schliessen der Senke als Sidecar
        # abgelegt, wenn 'write_sidecar' gilt (M4-3).
        run_metadata: "RunMetadata | None" = None,
        sidecar_target: Path | None = None,
        write_sidecar: bool = False,
    ) -> None:
        self._session = session
        self._table = table
        self._sink = sink
        self._interval_s = interval_s
        self._max_samples = max_samples
        self._max_duration_s = max_duration_s
        self._use_hold = use_hold
        self._record_condition = record_condition
        self._log_every = log_every
        self._metadata = dict(metadata or {})
        self._check_update_rate = check_update_rate
        self._mark_duplicates = mark_duplicates
        self._error_policy = error_policy
        self._run_metadata = run_metadata
        self._sidecar_target = sidecar_target
        self._write_sidecar = write_sidecar

        self._stats = LoopStatistics()
        self._thread: threading.Thread | None = None
        # Ein Event unterbricht das Warten zwischen zwei Takten sofort.
        self._stop = threading.Event()
        # Startfreigabe - siehe start(). Schliesst das Fenster zwischen
        # 'Thread.start()' und 'session.claim()'.
        self._go = threading.Event()
        self._aborted = False
        self._error: BaseException | None = None

    # -- Zustand ------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Laeuft der Mess-Thread noch?"""
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> LoopStatistics:
        """Die Statistik - waehrend des Laufs fortgeschrieben, nicht erst danach.

        Der Fortschritt einer laufenden Messung ist damit ansehbar
        ('messung.stats.samples'), ohne die Sitzung anzufassen. Das ist der
        einzige Weg, der waehrend eines Hintergrundlaufs ohne
        ConcurrentAccessError funktioniert - er fragt nicht das Geraet,
        sondern die Schleife.
        """
        return self._stats

    @property
    def error(self) -> BaseException | None:
        """Die Ausnahme aus dem Mess-Thread, falls eine aufgetreten ist."""
        return self._error

    # -- Steuerung ----------------------------------------------------------

    def start(self) -> "Measurement":
        """Den Hintergrundlauf beginnen. Kehrt sofort zurueck.

        Die Reihenfolge hier ist der eigentliche Inhalt der Methode:

          1. Thread anlegen und starten - er blockiert sofort auf '_go'.
             Erst dadurch steht seine Thread-Kennung ueberhaupt fest.
          2. Die Sitzung auf DIESE Kennung eintragen.
          3. '_go' freigeben.

        Wuerde der Thread den Besitz selbst eintragen, gaebe es zwischen
        'start()' und dem ersten Takt ein Fenster, in dem ein Fremdzugriff
        noch durchginge - und der Vertrag 'waehrend der Messung gehoert die
        Sitzung dem Thread' gaelte erst ein paar Millisekunden spaeter.
        """
        if self._thread is not None:
            raise WTError(
                "Diese Messung laeuft bereits oder ist beendet - ein 'Measurement' "
                "ist einmal verwendbar. Fuer einen zweiten Lauf ein neues anlegen "
                "(die Senke des ersten ist geschlossen)."
            )

        thread = threading.Thread(target=self._run, name="wt3000-measurement", daemon=True)
        thread.start()
        assert thread.ident is not None  # von Thread.start() zugesichert
        try:
            self._session.claim(thread.ident, "laufende Messung (M3-1)")
        except WTError:
            # Den soeben gestarteten Thread nicht haengen lassen: er wartet
            # auf '_go' und wuerde es sonst bis zum Prozessende tun.
            self._aborted = True
            self._go.set()
            thread.join(timeout=5.0)
            raise

        self._thread = thread
        self._go.set()
        _log.info(
            "Messung gestartet (Takt %.3f s, Grenze: %s Samples / %s s)",
            self._interval_s,
            self._max_samples if self._max_samples is not None else "-",
            self._max_duration_s if self._max_duration_s is not None else "-",
        )
        return self

    def stop(self, timeout: float | None = None) -> LoopStatistics:
        """Stoppsignal setzen und auf das Ende warten.

        Das Signal greift sofort und nicht erst nach dem laufenden Intervall -
        dafuer ist es ein 'Event' und kein Flag. Ein bereits begonnener
        Lesevorgang wird noch zu Ende gefuehrt; ein halb gelesener Datensatz
        waere der eine Fall, den die Senke nicht sauber wegschreiben kann.
        """
        if self._thread is None:
            raise WTError("Diese Messung wurde nie gestartet - stop() ohne start()")
        self._stop.set()
        return self.wait(timeout)

    def wait(self, timeout: float | None = None) -> LoopStatistics:
        """Auf das Ende warten und die Statistik liefern.

        Ein Fehler aus dem Mess-Thread wird HIER erneut ausgeloest. Das ist
        die einzige Stelle, an der er den Aufrufer ueberhaupt erreichen kann:
        eine Ausnahme in einem Thread beendet nur diesen Thread und wuerde
        sonst als Textausgabe von 'threading' enden - also als etwas, das kein
        'except' je faengt und keine Ablaufsteuerung bemerkt.
        """
        if self._thread is None:
            raise WTError("Diese Messung wurde nie gestartet - wait() ohne start()")

        self._thread.join(timeout)
        if self._thread.is_alive():
            raise WTError(
                f"Messung laeuft nach {timeout} s noch. Ohne Limit laeuft sie "
                "unbegrenzt - stop() beendet sie."
            )
        if self._error is not None:
            raise self._error
        return self._stats

    # -- Context Manager ----------------------------------------------------

    def __enter__(self) -> "Measurement":
        """Startet, falls noch nicht gestartet."""
        if self._thread is None:
            self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stoppen und abwarten - damit keine Messung ihre Klammer ueberlebt.

        Der Grund steht im Klassenkopf: schliesst die Konfigurationsklammer
        ('applied_ranges()') vor der Messung, stellt sie Bereiche zurueck,
        waehrend noch gemessen wird.
        """
        if self._thread is None:
            return
        self._stop.set()
        try:
            self.wait()
        except BaseException:
            # Eine bereits laufende Ausnahme ist die aeltere Nachricht und
            # wiegt schwerer - der Fehler aus dem Thread wird dann
            # protokolliert statt sie zu verdecken.
            if exc_type is None:
                raise
            _log.exception("Fehler beim Beenden der Messung; urspruengliche Ausnahme bleibt")

    # -- Der Thread ---------------------------------------------------------

    def _sidecar_ablegen(self) -> None:
        """Metadaten neben die Datendatei legen (M4-3).

        Laeuft im Mess-Thread, direkt nach dem Schliessen der Senke - dieselbe
        Regel wie beim uebrigen Aufraeumen: wer 'wait()' vergisst, verliert
        hoechstens die Statistik, nie das Sidecar.

        Fehler werden protokolliert und nicht ausgeloest: die Messdaten liegen
        bereits, und ein misslungenes Sidecar darf eine gelungene Messreihe
        nicht nachtraeglich zum Fehlschlag machen.
        """
        if not self._write_sidecar or self._run_metadata is None:
            return
        dateien = output_paths_of(self._sink)
        if self._sidecar_target is not None:
            ziel = self._sidecar_target
        elif dateien:
            ziel = sidecar_path(dateien[0])
        else:
            _log.warning("Kein Sidecar: die Senke schreibt keine Datei")
            return
        try:
            self._run_metadata.write_sidecar(ziel, dateien, self._stats)
        except OSError as error:
            _log.error("Sidecar %s konnte nicht geschrieben werden: %s", ziel, error)

    def _run(self) -> None:
        """Mess-Thread; oeffnet und schliesst seine Senke selbst."""
        self._go.wait()
        if self._aborted:
            return
        try:
            # Erst jetzt, unter dem Besitz: es ist ein Geraetezugriff.
            prepare_update_rate(
                self._session, self._interval_s, self._stats, self._check_update_rate
            )

            metadaten: dict[str, object] = dict(self._metadata)
            metadaten.setdefault("update_rate_s", self._stats.update_rate_s)
            metadaten.setdefault("units", self._table.unit_map())

            self._sink.open([item.key for item in self._table.items], metadaten)
            strom = iter_samples(
                session=self._session,
                table=self._table,
                stats=self._stats,
                interval_s=self._interval_s,
                max_samples=self._max_samples,
                max_duration_s=self._max_duration_s,
                use_hold=self._use_hold,
                record_condition=self._record_condition,
                log_every=self._log_every,
                mark_duplicates=self._mark_duplicates,
                stop_event=self._stop,
                error_policy=self._error_policy,
            )
            try:
                for sample in strom:
                    self._sink.write(sample)
            finally:
                # Generator vor Senke - die Begruendung steht in
                # 'run_measurement_loop()'. Hier wiegt sie schwerer: wirft die
                # Senke, wuerde HOLD sonst an einem Daemon-Thread haengen
                # bleiben, den niemand mehr ansieht.
                strom.close()
                self._sink.close()
                # NACH close(): erst jetzt ist die Datei vollstaendig und
                # ihre Pruefsumme gueltig.
                self._sidecar_ablegen()
        except BaseException as error:  # bewusst breit - siehe wait()
            self._error = error
            _log.error("Messung mit Fehler beendet: %s", error)
        finally:
            # In JEDEM Fall, sonst bliebe die Sitzung fuer immer vergeben und
            # der naechste Zugriff scheiterte an einer Messung, die es nicht
            # mehr gibt.
            self._session.release()
