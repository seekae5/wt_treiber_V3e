# Ausgabeformate fuer SampleSink. Das Modul kennt weder SCPI-Kommandos noch
# Sitzungen; neue Formate implementieren lediglich open(), write() und close().
# Parquet bleibt wegen seiner zusaetzlichen Laufzeitabhaengigkeit ausserhalb
# des Pakets, kann aber ueber denselben Vertrag ergaenzt werden.

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

    Die eigene Klasse kennzeichnet einen Aufruffehler in der Reihenfolge
    open - write - close.
    """


# ---------------------------------------------------------------------------
# Gemeinsame Spaltenregel
# ---------------------------------------------------------------------------


def require_matching_columns(sample: Sample, columns: Sequence[str], ziel: str) -> None:
    """Abbrechen, wenn die Werteanzahl nicht zum Spaltenkopf passt.

    Jede Senke mit festem Spaltenkopf muss eine Abweichung melden, da sonst
    Werte oder Statusfelder unbemerkt unter falschen Spalten landen. Auffuellen
    waere inhaltlich falsch, weil die zugrunde liegende Item-Tabelle offenbar
    nicht mehr zum Kopf passt. Samples ohne Werte sind daher ebenfalls nicht
    mit einer solchen Senke kompatibel.
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

    Sonderfaelle werden fuer gaengige Auswertewerkzeuge wie folgt kodiert:
        OK        -> Zahl
        NO_DATA   -> leere Zelle  (pandas: NaN)
        OVERRANGE -> 'INF'        (pandas: inf)
    'status_flags' erhaelt zusaetzlich alle Auffaelligkeiten im Klartext.

    'metadata' wird entgegengenommen und bewusst NICHT geschrieben: eine CSV
    bietet dafuer keinen standardisierten Platz. Mit 'unit_row=True' folgt auf
    den Spaltenkopf eine Einheitenzeile aus 'metadata["units"]'. Sie ist wegen
    bestehender Auswerteketten nicht voreingestellt. '?' bedeutet unbekannte,
    eine leere Zelle eine bekannte dimensionslose Einheit.
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

        # Optionale Einheitenzeile.
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

    Die Metadaten stehen in der ersten Zeile ('kind': 'metadata'), Werte tragen
    ihren Namen statt nur eine Position und eine angebrochene Datei bleibt bis
    zur letzten vollstaendigen Zeile auswertbar.

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

    Eine Live-Anzeige kann so auf dem Treiber aufsetzen, ohne ihn zu aendern.

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
