# =============================================================================
# Datei: wt3000_sinks.py
# NEU (ROADMAP M4-2): Layer 3 - die Ausgabeformate.
#
# Zur Einordnung: dieses Modul steht neben wt3000_measure und NICHT neben der
# Fassade. Es kennt kein SCPI-Kommando und keine Sitzung, sondern setzt nur den
# Vertrag 'SampleSink' um - ein Fachmodul also, kein Einstiegspunkt.
# tests/test_package_layout.py haelt das fest, in beide Richtungen: die Fassade
# darf hierher greifen, wt3000_measure ausdruecklich nicht.
#
# Hintergrund. Bis hierher gab es genau ein Ausgabeformat, und es war fest in
# die Messschleife verdrahtet: 'CsvRecorder'. Wer ein zweites Format wollte,
# musste die Schleife anfassen - und damit den Teil des Treibers, an dem ein
# Fehler eine ganze Messreihe kostet.
#
# Ab jetzt kennt die Messschleife nur noch das Protocol 'SampleSink'
# (wt3000_measure.py, dort neben 'Sample'). Ein neues Format ist eine Klasse in
# dieser Datei und keine Zeile Aenderung an der Schleife - das ist das
# 'Fertig, wenn' aus M4-2.
#
#     from wt3000_scpi import WT3000, CsvSink, JsonlSink, MultiSink
#
#     with WT3000.connect() as wt:
#         tabelle = wt.items.read()
#         wt.measure.record(MultiSink(CsvSink(pfad), JsonlSink(pfad2)), tabelle)
#
# SCHICHTUNG. Diese Datei importiert aus wt3000_measure (Sample, SampleSink)
# und wird von wt3000_measure NICHT importiert - die Schleife kommt mit dem
# Protocol aus. Die Importrichtung bleibt damit ausnahmslos nach unten.
#
# BEWUSST NICHT hier: 'ParquetSink'. Das Paket hat heute 'dependencies = []',
# und Parquet braucht pyarrow oder fastparquet. Ob der Treiber eine erste
# Laufzeitabhaengigkeit bekommt, ist eine Projektentscheidung und kein
# Nebenprodukt dieses Meilensteins. Die Fuge ist offen: eine Datei, eine
# Klasse, drei Methoden.
# =============================================================================

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

from .wt3000_core import WTError
from .wt3000_measure import Sample, SampleSink
from .wt3000_numeric import NumericValue, ValueStatus

__all__ = [
    "CallbackSink",
    "CsvSink",
    "JsonlSink",
    "MultiSink",
    "require_matching_columns",
    "SinkNotOpen",
]

_log = logging.getLogger("wt3000.sinks")


class SinkNotOpen(WTError):
    """Es wurde geschrieben, bevor 'open()' gerufen wurde.

    Eine eigene Klasse und keine nackte WTError, weil dies der einzige Fehler
    dieser Datei ist, der ausschliesslich am Aufrufer liegt: die Reihenfolge
    open - write - close ist nicht eingehalten. Alles andere (Dateisystem,
    Spaltenzahl) kann auch bei richtigem Gebrauch auftreten.
    """


# ---------------------------------------------------------------------------
# Gemeinsame Regel: Befund B-07
# ---------------------------------------------------------------------------


def require_matching_columns(sample: Sample, columns: Sequence[str], ziel: str) -> None:
    """Abbrechen, wenn die Werteanzahl nicht zum Spaltenkopf passt.

    UEBERARBEITET (ROADMAP M4-2): Die Regel stand bis hierher in
    'CsvRecorder.write_row()' und galt damit nur fuer die CSV. Sie gehoert
    aber zu JEDER Senke mit festem Spaltenkopf - deshalb liegt sie jetzt an
    einer Stelle und wird von dort aufgerufen. Das ist dieselbe Lehre wie bei
    Befund B-03: eine Regel, eine Fassung.

    HINTERGRUND (Befund B-07, verschaerft in P-3). Bisher entstand eine
    CSV-Zeile aus vier festen Feldern, 'len(values)' Wertzellen und der
    Flag-Spalte - ohne jeden Abgleich mit dem Kopf. Bei zu wenigen Werten
    rutschte 'status_flags' unter eine Messwertspalte, bei zu vielen entstanden
    unbenannte Spalten. Beides sieht man der fertigen Datei nicht an, weil jede
    Zeile fuer sich plausibel bleibt - die Verschiebung zeigt sich erst im
    Vergleich mit dem Kopf, und dann meist Wochen spaeter bei der Auswertung.

    Abbruch statt Auffuellen ist Absicht: eine abweichende Werteanzahl heisst,
    dass die Item-Tabelle nicht mehr die ist, gegen die der Kopf geschrieben
    wurde. Aufgefuellte Zeilen waeren dann inhaltlich falsch, nicht bloss
    unvollstaendig - und niemand wuerde es der Datei ansehen.

    OFFEN (ROADMAP M3-4): Ein Datensatz mit 'SampleMark.MISSING' traegt
    definitionsgemaess keine Werte und bricht deshalb hier ab. M3-4 verlangt
    aber, ausgefallene Zyklen als Zeile zu schreiben statt sie auszulassen.
    Dort ist zu entscheiden, ob solche Datensaetze vor dem Schreiben mit
    NO_DATA-Werten aufgefuellt werden - dann bleibt diese Regel unangetastet -
    oder ob sie hier einen ausdruecklichen Sonderweg bekommen. Bis dahin ist
    der Abbruch das richtige Verhalten: er ist laut.
    """
    if len(sample.values) != len(columns):
        raise WTError(
            f"Sample {sample.number}: {len(sample.values)} Messwerte passen nicht zu "
            f"{len(columns)} Wertspalten von {ziel}. "
            "Der Datensatz wird nicht geschrieben, weil er sonst gegen den "
            "Spaltenkopf verrutschen wuerde."
        )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


class CsvSink:
    """Schreibt Datensaetze zeilenweise in eine CSV-Datei.

    UEBERARBEITET (ROADMAP M4-2): hiess 'CsvRecorder' und lag in
    wt3000_measure.py. Inhaltlich unveraendert geblieben sind Trennzeichen,
    Statuskodierung und der Sofort-Flush; neu ist die Aufteilung in
    Konstruktor (Format) und 'open()' (Spalten und Metadaten), ohne die
    formatunabhaengiger Code keine Senke in Betrieb nehmen koennte.

    Kodierung der Sonderfaelle so, dass gaengige Auswertewerkzeuge sie ohne
    Nacharbeit richtig einlesen:
        OK        -> Zahl
        NO_DATA   -> leere Zelle  (pandas: NaN)
        OVERRANGE -> 'INF'        (pandas: inf)
    Zusaetzlich listet die Spalte 'status_flags' alle Auffaelligkeiten im
    Klartext - Einzelwerte wie Kennzeichnung des Zyklus -, damit die
    Unterscheidung auch beim Sichten der Rohdatei erhalten bleibt.

    'metadata' wird entgegengenommen und bewusst NICHT geschrieben: eine CSV
    hat keinen Ort dafuer, der nicht zugleich den Spaltenkopf beschaedigt.
    Sie liegt heute im Sidecar von 'write_metadata()'; sie an die Daten zu
    binden ist ROADMAP M4-3.

    EINE AUSNAHME (ROADMAP M4-3, Maßnahme A5): 'unit_row=True' schreibt unter
    den Spaltenkopf eine zweite Zeile mit den Einheiten. Sie stammen aus
    'metadata["units"]', das die Messschleife aus der Item-Tabelle fuellt -
    also aus derselben Quelle wie der Kopf.

    Warum das nicht die Voreinstellung ist: eine zweite Kopfzeile ist eine
    Formataenderung. Jedes Werkzeug, das bisher 'eine Kopfzeile, dann Daten'
    erwartet, laese die Einheiten als ersten Datensatz. Wer die Datei selbst
    auswertet, schaltet sie ein; wer eine bestehende Auswertekette bedient,
    laesst sie aus und nimmt die Einheiten aus dem Sidecar oder aus JSONL.
    Eine unbekannte Einheit steht als '?' in der Zeile, eine bekannte
    dimensionslose Groesse als leere Zelle - der Unterschied bleibt bis hier
    erhalten.
    """

    def __init__(self, path: Path, delimiter: str = ",", unit_row: bool = False) -> None:
        self._path = path
        self._delimiter = delimiter
        self._unit_row = unit_row
        self._columns: list[str] = []
        self._handle: TextIO | None = None
        # csv.writer() liefert '_csv._writer' - kein oeffentlich benannter Typ.
        # 'Any' ist hier ehrlicher als 'object' plus ein type-ignore an der
        # Aufrufstelle.
        self._writer: Any = None

    def open(self, columns: Sequence[str], metadata: Mapping[str, object] | None = None) -> None:
        """Datei anlegen und den Spaltenkopf schreiben."""
        self._columns = list(columns)
        self._handle = self._path.open("w", newline="", encoding="utf-8")
        writer = csv.writer(self._handle, delimiter=self._delimiter)
        self._writer = writer
        header = ["timestamp_iso", "elapsed_s", "sample", "condition"]
        header.extend(self._columns)
        header.append("status_flags")
        writer.writerow(header)

        # NEU (ROADMAP M4-3): die Einheitenzeile, wenn verlangt.
        if self._unit_row:
            roh = (metadata or {}).get("units")
            einheiten: Mapping[str, object] = roh if isinstance(roh, Mapping) else {}
            zeile = ["", "s", "", ""]
            zeile.extend(self._unit_cell(einheiten.get(name)) for name in self._columns)
            zeile.append("")
            writer.writerow(zeile)

        self._handle.flush()
        _log.info("CSV geoeffnet: %s (%d Spalten)", self._path, len(header))

    @staticmethod
    def _unit_cell(unit: object) -> str:
        """Einheit in die Zellendarstellung wandeln.

        None heisst 'nicht belegt' und wird als '?' geschrieben - nicht als
        leere Zelle, denn die ist bereits vergeben: sie heisst 'dimensionslos'.
        """
        return "?" if unit is None else str(unit)

    @staticmethod
    def _cell(value: NumericValue) -> str:
        """Einen Messwert in die Zellendarstellung wandeln."""
        if value.status is ValueStatus.OK:
            return repr(value.value)  # volle float-Genauigkeit, Dezimalpunkt
        if value.status is ValueStatus.NO_DATA:
            return ""
        return "INF"

    def write(self, sample: Sample) -> None:
        """Einen Datensatz als Zeile schreiben und sofort flushen."""
        if self._handle is None:
            raise SinkNotOpen(f"CsvSink({self._path.name}): open() wurde nicht gerufen")
        require_matching_columns(sample, self._columns, f"der Datei {self._path.name}")

        row: list[str] = [
            sample.timestamp.isoformat(timespec="milliseconds"),
            f"{sample.elapsed_s:.3f}",
            str(sample.number),
            "" if sample.condition is None else str(sample.condition),
        ]
        row.extend(self._cell(v) for v in sample.values)
        row.append(";".join(sample.status_flags(self._columns)))
        self._writer.writerow(row)
        # Bei 1 Hz kostenlos; ein harter Abbruch kostet damit hoechstens
        # die letzte Zeile.
        self._handle.flush()

    def close(self) -> None:
        """Datei schliessen. Mehrfachaufruf ist unschaedlich."""
        if self._handle is not None and not self._handle.closed:
            self._handle.close()
            _log.info("CSV geschlossen: %s", self._path)

    def __enter__(self) -> "CsvSink":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


# ---------------------------------------------------------------------------
# JSON Lines
# ---------------------------------------------------------------------------


class JsonlSink:
    """Schreibt je Datensatz eine JSON-Zeile.

    Der Beleg fuer das 'Fertig, wenn' aus M4-2: dieses Format ist entstanden,
    ohne eine einzige Zeile der Messschleife anzufassen.

    Gegenueber der CSV drei Unterschiede, die den Zusatzaufwand rechtfertigen:
    die Metadaten stehen IN der Datei (erste Zeile, 'kind': 'metadata') statt
    in einem Sidecar, der beim Verschieben verlorengeht; Werte tragen ihren
    Namen statt ihrer Position, eine verrutschte Spalte ist also gar nicht
    moeglich; und eine angebrochene Datei bleibt bis zur letzten vollstaendigen
    Zeile auswertbar.

    NAN und INF werden ausgeschrieben ('NO_DATA'/'OVERRANGE' als Status, der
    Zahlwert entfaellt) statt als JSON-Literal: 'NaN' und 'Infinity' sind in
    JSON nicht zulaessig, und Pythons json-Modul erzeugt sie nur, weil es
    'allow_nan' voreingestellt hat. Ein fremder Parser stolperte darueber.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._columns: list[str] = []
        self._handle: TextIO | None = None

    def open(self, columns: Sequence[str], metadata: Mapping[str, object] | None = None) -> None:
        self._columns = list(columns)
        self._handle = self._path.open("w", encoding="utf-8")
        kopf = {"kind": "metadata", "columns": self._columns, "metadata": dict(metadata or {})}
        self._handle.write(json.dumps(kopf, default=str) + "\n")
        self._handle.flush()
        _log.info("JSONL geoeffnet: %s (%d Spalten)", self._path, len(self._columns))

    def write(self, sample: Sample) -> None:
        if self._handle is None:
            raise SinkNotOpen(f"JsonlSink({self._path.name}): open() wurde nicht gerufen")
        require_matching_columns(sample, self._columns, f"der Datei {self._path.name}")

        werte: dict[str, object] = {}
        for name, value in zip(self._columns, sample.values):
            werte[name] = value.value if value.status is ValueStatus.OK else None

        zeile = {
            "kind": "sample",
            "timestamp": sample.timestamp.isoformat(timespec="milliseconds"),
            "elapsed_s": round(sample.elapsed_s, 3),
            "sample": sample.number,
            "condition": sample.condition,
            "mark": sample.mark.value,
            "values": werte,
            "status_flags": sample.status_flags(self._columns),
        }
        self._handle.write(json.dumps(zeile, default=str) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None and not self._handle.closed:
            self._handle.close()
            _log.info("JSONL geschlossen: %s", self._path)

    def __enter__(self) -> "JsonlSink":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Rueckruf und Buendelung
# ---------------------------------------------------------------------------


class CallbackSink:
    """Reicht jeden Datensatz an eine Funktion weiter, schreibt selbst nichts.

    Die Senke, auf der eine Live-Anzeige aufsetzt. Eine grafische Oberflaeche
    steht ausdruecklich nicht auf der ROADMAP - aber sie ist damit ein eigenes
    Projekt, das diesen Treiber unveraendert benutzt, statt ein Eingriff in
    ihn.

    Der Rueckruf laeuft im Takt der Messschleife. Was er tut, verzoegert den
    naechsten Zyklus - eine langsame Anzeige erzeugt also Overruns. Wer mehr
    als eine Zuweisung darin erledigt, gehoert in einen eigenen Thread mit
    einer Queue dazwischen.

    Fehler werden bewusst NICHT abgefangen: ein kaputter Rueckruf soll die
    Messung anhalten und nicht stillschweigend nichts anzeigen, waehrend
    die Datei weiterlaeuft.
    """

    def __init__(self, callback: Callable[[Sample], None]) -> None:
        self._callback = callback
        self.columns: list[str] = []
        self.metadata: dict[str, object] = {}

    def open(self, columns: Sequence[str], metadata: Mapping[str, object] | None = None) -> None:
        self.columns = list(columns)
        self.metadata = dict(metadata or {})

    def write(self, sample: Sample) -> None:
        self._callback(sample)

    def close(self) -> None:
        return None


class MultiSink:
    """Verteilt jeden Datensatz an mehrere Senken.

    Damit wird aus 'ein Format statt eines anderen' ein 'ein Format zusaetzlich
    zu einem anderen' - CSV fuer die Auswertung und gleichzeitig ein Rueckruf
    fuer die Anzeige, ohne dass die Messschleife davon weiss.

    'close()' geht ueber ALLE Senken, auch wenn eine dabei scheitert, und
    meldet den ersten Fehler erst danach. Das ist dieselbe Regel wie in
    'WT3000.close()': ein misslungener Aufraeumschritt darf die folgenden nicht
    ueberspringen - sonst bleibt wegen einer vollen Platte die zweite Datei
    offen.

    'open()' und 'write()' brechen dagegen beim ersten Fehler ab. Dort ist ein
    Fehlschlag kein Aufraeumproblem, sondern heisst, dass die Messreihe so
    nicht zustande kommt.
    """

    def __init__(self, *sinks: SampleSink) -> None:
        if not sinks:
            raise WTError("MultiSink ohne Senken - das schreibt nirgendwohin")
        self._sinks = sinks

    def open(self, columns: Sequence[str], metadata: Mapping[str, object] | None = None) -> None:
        for sink in self._sinks:
            sink.open(columns, metadata or {})

    def write(self, sample: Sample) -> None:
        for sink in self._sinks:
            sink.write(sample)

    def close(self) -> None:
        erster: BaseException | None = None
        for sink in self._sinks:
            try:
                sink.close()
            except Exception as error:  # bewusst breit: Senken sind austauschbar
                _log.error("Senke %s liess sich nicht schliessen: %s", type(sink).__name__, error)
                if erster is None:
                    erster = error
        if erster is not None:
            raise erster

    def __enter__(self) -> "MultiSink":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
