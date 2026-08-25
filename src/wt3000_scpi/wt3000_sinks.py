# Ausgabeformate fuer SampleSink. Das Modul kennt weder SCPI-Kommandos noch
# Sitzungen; neue Formate implementieren lediglich open(), write() und close().
# Parquet bleibt wegen seiner zusaetzlichen Laufzeitabhaengigkeit ausserhalb
# des Pakets, kann aber ueber denselben Vertrag ergaenzt werden.

from __future__ import annotations

import csv
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, TextIO

from .wt3000_core import WTError
from .wt3000_measure import Sample, SampleSink
from .wt3000_numeric import NumericValue, ValueStatus

__all__ = [
    "AppendMismatch",
    "CallbackSink",
    "CsvSink",
    "ExistingFile",
    "JsonlSink",
    "MultiSink",
    "RotatingSink",
    "RotationPolicy",
    "require_matching_columns",
    "segment_path",
    "SinkNotOpen",
    "unique_path",
]

_log = logging.getLogger("wt3000.sinks")


class SinkNotOpen(WTError):
    """Es wurde geschrieben, bevor 'open()' gerufen wurde.

    Die eigene Klasse kennzeichnet einen Aufruffehler in der Reihenfolge
    open - write - close.
    """


class AppendMismatch(WTError):
    """Beim Anhaengen passt die vorhandene Datei nicht zum neuen Lauf.

    Der Fall, den M4-4 mit 'Format und Spaltenkopf vor dem Fortsetzen pruefen'
    meint: an eine Datei mit anderem Spaltenkopf weiterzuschreiben erzeugt
    eine Datei, in der ab einer bestimmten Zeile etwas anderes steht, als der
    Kopf behauptet - und das ist hinterher nicht mehr zu erkennen.
    """


#: Was geschehen soll, wenn die Zieldatei bereits existiert.
#
#   overwrite  Die Datei wird neu angelegt (bisheriges Verhalten). Ist sie
#              nicht leer, wird das jetzt PROTOKOLLIERT - der fruehere Befund
#              lautete "ueberschreibt wortlos", und wortlos ist es damit nicht
#              mehr.
#   error      Abbruch, bevor irgendetwas geoeffnet wird.
#   append     Weiterschreiben, nachdem Format und Spaltenkopf geprueft sind.
#   unique     Einen freien Namen daneben waehlen (messung_0001.csv).
ExistingFile = Literal["overwrite", "error", "append", "unique"]


def unique_path(path: Path) -> Path:
    """Einen noch nicht vergebenen Namen in der Form 'name_0001.suffix' finden.

    Gibt 'path' unveraendert zurueck, wenn nichts im Weg ist. Sonst wird ein
    Zaehler eingeschoben - vier Stellen, damit die Namen sich sortieren lassen.
    """
    if not path.exists():
        return path
    for index in range(1, 10000):
        kandidat = path.with_name(f"{path.stem}_{index:04d}{path.suffix}")
        if not kandidat.exists():
            return kandidat
    raise WTError(
        f"Kein freier Dateiname neben {path} gefunden (10000 Versuche). "
        "Steht dort ein altes Messverzeichnis, das aufgeraeumt werden sollte?"
    )


def segment_path(basis: Path, index: int, _started: datetime) -> Path:
    """Standardname eines Rotationsabschnitts: 'messung.csv' -> 'messung_0001.csv'.

    Der Zaehler und nicht der Zeitstempel ist die Voreinstellung, weil er die
    Reihenfolge ohne Zeitzonenwissen lesbar macht und in jeder Dateiliste
    richtig sortiert. Wer Zeitstempel will, uebergibt 'RotatingSink' eine
    eigene Funktion derselben Form.
    """
    return basis.with_name(f"{basis.stem}_{index:04d}{basis.suffix}")


def _prepare_target(path: Path, if_exists: ExistingFile, ziel: str) -> tuple[Path, bool]:
    """Zieldatei nach 'if_exists' festlegen. Liefert (Pfad, anhaengen).

    Gemeinsam fuer alle dateibasierten Senken, damit 'unique' und 'error'
    nicht in jedem Format neu und leicht verschieden entstehen.
    """
    if if_exists not in ("overwrite", "error", "append", "unique"):
        raise WTError(
            f"Unbekanntes if_exists={if_exists!r}. Erlaubt sind 'overwrite', "
            "'error', 'append' und 'unique'."
        )

    vorhanden = path.exists() and path.stat().st_size > 0
    if not vorhanden:
        # Auch bei 'append': eine leere oder fehlende Datei bekommt einen Kopf.
        return path, False

    if if_exists == "error":
        raise WTError(
            f"{ziel}: {path} existiert bereits ({path.stat().st_size} Bytes). "
            "if_exists='append' schreibt weiter, 'unique' waehlt einen freien "
            "Namen daneben, 'overwrite' legt neu an."
        )
    if if_exists == "unique":
        neu = unique_path(path)
        _log.info("%s existiert bereits - es wird %s geschrieben", path.name, neu.name)
        return neu, False
    if if_exists == "append":
        return path, True

    _log.warning(
        "%s wird ueberschrieben (%d Bytes gehen verloren). if_exists='append', "
        "'unique' oder 'error' verhindern das.",
        path,
        path.stat().st_size,
    )
    return path, False


# ---------------------------------------------------------------------------
# Gemeinsame Spaltenregel
# ---------------------------------------------------------------------------


def _first_difference(vorhanden: Sequence[str], erwartet: Sequence[str]) -> str:
    """Die erste abweichende Spalte benennen - eine Zahl allein hilft nicht weiter."""
    for i, (a, b) in enumerate(zip(vorhanden, erwartet)):
        if a != b:
            return f"Spalte {i + 1} ist {a!r} statt {b!r}"
    if len(vorhanden) < len(erwartet):
        return f"es fehlen {erwartet[len(vorhanden):]}"
    return f"zusaetzlich vorhanden: {list(vorhanden[len(erwartet):])}"


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

    def __init__(
        self,
        path: Path,
        delimiter: str = ",",
        unit_row: bool = False,
        if_exists: ExistingFile = "overwrite",
    ) -> None:
        self._path = path
        self._delimiter = delimiter
        self._unit_row = unit_row
        self._if_exists = if_exists
        self._columns: list[str] = []
        self._handle: TextIO | None = None
        # csv.writer() liefert '_csv._writer' - kein oeffentlich benannter Typ.
        # 'Any' ist hier ehrlicher als 'object' plus ein type-ignore an der
        # Aufrufstelle.
        self._writer: Any = None

    @property
    def path(self) -> Path:
        """Die tatsaechlich beschriebene Datei.

        Kann von der uebergebenen abweichen: 'if_exists="unique"' waehlt einen
        freien Namen daneben. Wer den Ablageort protokolliert, liest ihn hier
        NACH 'open()' - vorher steht der Wunschname darin.
        """
        return self._path

    def output_paths(self) -> list[Path]:
        """Die geschriebenen Dateien - Grundlage der Sidecar-Bindung (M4-3)."""
        return [self._path]

    def open(self, columns: Sequence[str], metadata: Mapping[str, object] | None = None) -> None:
        """Datei anlegen oder fortsetzen und den Spaltenkopf schreiben."""
        self._columns = list(columns)
        self._path, anhaengen = _prepare_target(self._path, self._if_exists, "CsvSink")

        if anhaengen:
            self._verify_header()
            self._handle = self._path.open("a", newline="", encoding="utf-8")
            self._writer = csv.writer(self._handle, delimiter=self._delimiter)
            _log.info("CSV fortgesetzt: %s (%d Spalten)", self._path, len(self._columns))
            return

        self._handle = self._path.open("w", newline="", encoding="utf-8")
        writer = csv.writer(self._handle, delimiter=self._delimiter)
        self._writer = writer
        writer.writerow(self._header())

        # Optionale Einheitenzeile.
        if self._unit_row:
            roh = (metadata or {}).get("units")
            einheiten: Mapping[str, object] = roh if isinstance(roh, Mapping) else {}
            zeile = ["", "s", "", ""]
            zeile.extend(self._unit_cell(einheiten.get(name)) for name in self._columns)
            zeile.append("")
            writer.writerow(zeile)

        self._handle.flush()
        _log.info("CSV geoeffnet: %s (%d Spalten)", self._path, len(self._columns))

    def _header(self) -> list[str]:
        """Der Spaltenkopf - eine Quelle fuer Schreiben und Gegenprobe."""
        header = ["timestamp_iso", "elapsed_s", "sample", "condition"]
        header.extend(self._columns)
        header.append("status_flags")
        return header

    def _verify_header(self) -> None:
        """Vor dem Anhaengen pruefen, dass der vorhandene Kopf passt (M4-4).

        Ohne diese Pruefung entstuende beim Fortsetzen eine Datei, in der ab
        einer bestimmten Zeile andere Groessen stehen als im Kopf - der eine
        Fehler, der sich hinterher nicht mehr erkennen laesst.
        """
        try:
            with self._path.open("r", newline="", encoding="utf-8") as datei:
                vorhanden = next(csv.reader(datei, delimiter=self._delimiter), None)
        except OSError as error:
            raise AppendMismatch(
                f"{self._path} laesst sich zum Pruefen nicht lesen: {error}"
            ) from error

        erwartet = self._header()
        if vorhanden is None:
            raise AppendMismatch(f"{self._path} hat keinen Spaltenkopf zum Vergleichen")
        if vorhanden != erwartet:
            raise AppendMismatch(
                f"{self._path} laesst sich nicht fortsetzen: der vorhandene Spaltenkopf "
                f"({len(vorhanden)} Spalten) passt nicht zum jetzigen "
                f"({len(erwartet)} Spalten). Erste Abweichung: "
                f"{_first_difference(vorhanden, erwartet)}. "
                "Die Item-Tabelle oder das Trennzeichen ist ein anderes als beim "
                "urspruenglichen Lauf."
            )
        # 'sample' und 'elapsed_s' beginnen im neuen Lauf wieder bei 1 bzw. 0.
        # Das ist keine Panne, sondern die Folge davon, dass zwei Laeufe in
        # einer Datei stehen - 'timestamp_iso' bleibt die eindeutige Ordnung.
        _log.warning(
            "%s wird fortgesetzt: 'sample' und 'elapsed_s' beginnen erneut bei 1 "
            "bzw. 0. Zum Sortieren ueber beide Laeufe hinweg 'timestamp_iso' "
            "benutzen.",
            self._path.name,
        )

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

    def __init__(self, path: Path, if_exists: ExistingFile = "overwrite") -> None:
        self._path = path
        self._if_exists = if_exists
        self._columns: list[str] = []
        self._handle: TextIO | None = None

    @property
    def path(self) -> Path:
        """Die tatsaechlich beschriebene Datei (siehe 'CsvSink.path')."""
        return self._path

    def output_paths(self) -> list[Path]:
        """Die geschriebenen Dateien - Grundlage der Sidecar-Bindung (M4-3)."""
        return [self._path]

    def open(self, columns: Sequence[str], metadata: Mapping[str, object] | None = None) -> None:
        self._columns = list(columns)
        self._path, anhaengen = _prepare_target(self._path, self._if_exists, "JsonlSink")

        if anhaengen:
            self._verify_columns()
            self._handle = self._path.open("a", encoding="utf-8")
            # Eine zweite Metadatenzeile - beim Fortsetzen gehoert sie dazu:
            # der neue Lauf hat eigene Laufparameter, und JSONL vertraegt sie,
            # weil jede Zeile fuer sich steht. Genau das ist der Vorteil des
            # Formats gegenueber der CSV.
            self._write_metadata_line(metadata, fortsetzung=True)
            _log.info("JSONL fortgesetzt: %s (%d Spalten)", self._path, len(self._columns))
            return

        self._handle = self._path.open("w", encoding="utf-8")
        self._write_metadata_line(metadata, fortsetzung=False)
        _log.info("JSONL geoeffnet: %s (%d Spalten)", self._path, len(self._columns))

    def _write_metadata_line(
        self, metadata: Mapping[str, object] | None, fortsetzung: bool
    ) -> None:
        assert self._handle is not None
        kopf: dict[str, object] = {
            "kind": "metadata",
            "columns": self._columns,
            "metadata": dict(metadata or {}),
        }
        if fortsetzung:
            kopf["continued"] = True
        self._handle.write(json.dumps(kopf, default=str) + "\n")
        self._handle.flush()

    def _verify_columns(self) -> None:
        """Vor dem Anhaengen die Spalten der ersten Metadatenzeile vergleichen.

        JSONL macht das einfacher als die CSV: die Spalten stehen ausdruecklich
        in der Datei und muessen nicht aus einer Kopfzeile abgeleitet werden.
        """
        try:
            with self._path.open("r", encoding="utf-8") as datei:
                erste = datei.readline()
        except OSError as error:
            raise AppendMismatch(
                f"{self._path} laesst sich zum Pruefen nicht lesen: {error}"
            ) from error

        try:
            kopf = json.loads(erste)
        except json.JSONDecodeError as error:
            raise AppendMismatch(
                f"{self._path} beginnt nicht mit einer JSON-Zeile - das ist keine von "
                f"dieser Senke geschriebene Datei ({error})."
            ) from error

        if not isinstance(kopf, dict) or kopf.get("kind") != "metadata":
            raise AppendMismatch(
                f"{self._path}: die erste Zeile ist keine Metadatenzeile "
                "('kind': 'metadata'). Fortsetzen wuerde eine Datei ohne "
                "Spaltenangabe erzeugen."
            )

        vorhanden = kopf.get("columns")
        if vorhanden != self._columns:
            anzahl = len(vorhanden) if isinstance(vorhanden, list) else "?"
            raise AppendMismatch(
                f"{self._path} laesst sich nicht fortsetzen: die Datei nennt {anzahl} "
                f"Spalten, der jetzige Lauf hat {len(self._columns)}. Erste "
                f"Abweichung: "
                f"{_first_difference(vorhanden or [], self._columns)}. "
                "Die Item-Tabelle ist eine andere als beim urspruenglichen Lauf."
            )

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

    def output_paths(self) -> list[Path]:
        """Keine - diese Senke schreibt nichts auf die Platte."""
        return []


@dataclass(frozen=True)
class RotationPolicy:
    """Wann ein neuer Dateiabschnitt begonnen wird.

    Mindestens eine Grenze muss gesetzt sein - eine Rotation, die nie
    ausloest, ist eine gewoehnliche Senke mit Umweg. Sind mehrere gesetzt,
    gilt die zuerst erreichte.

      max_rows      Datensaetze je Abschnitt. Die verlaesslichste Grenze: sie
                    haengt weder an der Zeilenlaenge noch an der Uhr.
      max_bytes     Ungefaehre Groesse je Abschnitt. Geprueft wird NACH dem
                    Schreiben, ein Abschnitt darf die Grenze also um bis zu
                    eine Zeile ueberschreiten. Ein Abschnitt exakt auf die
                    Grenze zu kuerzen hiesse, eine Zeile zu zerschneiden.
      max_seconds   Laufzeit je Abschnitt, auf einer monotonen Uhr. Damit
                    entsteht etwa eine Datei je Stunde, unabhaengig davon,
                    wie viele Zeilen zusammenkommen.
    """

    max_rows: int | None = None
    max_bytes: int | None = None
    max_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.max_rows is None and self.max_bytes is None and self.max_seconds is None:
            raise WTError(
                "RotationPolicy ohne Grenze - sie wuerde nie ausloesen. Mindestens "
                "max_rows, max_bytes oder max_seconds setzen (oder die Senke ohne "
                "RotatingSink benutzen)."
            )
        for name, wert in (
            ("max_rows", self.max_rows),
            ("max_bytes", self.max_bytes),
            ("max_seconds", self.max_seconds),
        ):
            if wert is not None and wert <= 0:
                raise WTError(f"{name}={wert} muss groesser als 0 sein oder None")


class RotatingSink:
    """Verteilt eine Messreihe auf mehrere Dateien (M4-4).

    Bewusst eine UMHUELLENDE Senke und keine Option an 'CsvSink' und
    'JsonlSink': Rotation ist eine Frage des Lebenszyklus - schliessen,
    neuen Namen waehlen, wieder oeffnen - und damit unabhaengig vom Format.
    So gilt sie fuer beide vorhandenen Formate und fuer jedes spaetere, ohne
    dass eines davon sie kennt. Dasselbe Muster wie 'MultiSink'.

        RotatingSink(CsvSink, Path("messung.csv"), RotationPolicy(max_rows=10_000))

    Der erste Parameter ist eine Funktion Pfad -> Senke. 'CsvSink' und
    'JsonlSink' erfuellen das bereits, weil ihre uebrigen Parameter
    Voreinstellungen haben; wer sie braucht, uebergibt ein Lambda:

        RotatingSink(lambda p: CsvSink(p, unit_row=True), pfad, policy)

    Geschrieben wird nach 'messung_0001.csv', 'messung_0002.csv', ... - die
    Basisdatei selbst bleibt leer, damit ein Abschnitt nie besonders heisst.
    Jeder Abschnitt bekommt denselben Spaltenkopf und dieselben Metadaten und
    ist damit fuer sich auswertbar.

    Was NICHT zurueckgesetzt wird: 'sample' und 'elapsed_s' zaehlen ueber alle
    Abschnitte durch. Sie gehoeren zur Messreihe, nicht zur Datei - eine
    Nummerierung, die je Abschnitt neu begaenne, machte die Teile hinterher
    unzusammensetzbar.
    """

    def __init__(
        self,
        factory: Callable[[Path], SampleSink],
        path: Path,
        policy: RotationPolicy,
        namer: Callable[[Path, int, datetime], Path] = segment_path,
    ) -> None:
        self._factory = factory
        self._basis = path
        self._policy = policy
        self._namer = namer

        self._columns: list[str] = []
        self._metadata: dict[str, object] = {}
        self._sink: SampleSink | None = None
        self._segment = 0
        self._rows = 0
        self._segment_started = 0.0
        # Grenze gerissen, aber der naechste Abschnitt wird erst beim
        # naechsten Datensatz geoeffnet - siehe write().
        self._wechsel_faellig = False
        self._started = datetime.now().astimezone()
        #: Die Pfade aller bisher begonnenen Abschnitte, in Reihenfolge.
        self.segments: list[Path] = []

    def open(self, columns: Sequence[str], metadata: Mapping[str, object] | None = None) -> None:
        self._columns = list(columns)
        self._metadata = dict(metadata or {})
        self._started = datetime.now().astimezone()
        self._open_segment()

    def _open_segment(self) -> None:
        """Den naechsten Abschnitt beginnen."""
        self._segment += 1
        ziel = self._namer(self._basis, self._segment, self._started)
        sink = self._factory(ziel)
        sink.open(self._columns, self._metadata)
        self._sink = sink
        self._rows = 0
        self._segment_started = time.monotonic()
        self._wechsel_faellig = False
        # Der tatsaechliche Pfad kann abweichen ('unique'); wenn die Senke ihn
        # nennt, wird er notiert, sonst der gewuenschte.
        self.segments.append(getattr(sink, "path", ziel))
        _log.info("Rotation: Abschnitt %d begonnen (%s)", self._segment, self.segments[-1])

    def write(self, sample: Sample) -> None:
        """Datensatz schreiben und bei Bedarf vorher den Abschnitt wechseln.

        Der Wechsel geschieht ABSICHTLICH erst hier und nicht schon direkt
        nach dem Datensatz, der die Grenze gerissen hat. Sonst entstuende bei
        jedem Lauf, der genau auf einer Grenze endet, eine letzte Datei mit
        nichts als einem Spaltenkopf darin - und wer die Abschnitte hinterher
        zusammenfuehrt, sucht die fehlenden Daten darin.
        """
        if self._sink is None:
            raise SinkNotOpen(f"RotatingSink({self._basis.name}): open() wurde nicht gerufen")
        if self._wechsel_faellig:
            self._sink.close()
            self._open_segment()
        self._sink.write(sample)
        self._rows += 1
        self._wechsel_faellig = self._rotation_faellig()

    def _rotation_faellig(self) -> bool:
        """Ist eine der Grenzen erreicht?

        Geprueft wird NACH dem Schreiben. Der Datensatz, der die Grenze
        reisst, steht damit noch vollstaendig im alten Abschnitt - der neue
        beginnt mit einer ganzen Zeile.
        """
        policy = self._policy
        if policy.max_rows is not None and self._rows >= policy.max_rows:
            return True
        if (
            policy.max_seconds is not None
            and time.monotonic() - self._segment_started >= policy.max_seconds
        ):
            return True
        if policy.max_bytes is not None:
            # Die Senken flushen je Zeile, die Groesse auf der Platte ist also
            # aktuell. Ein stat() je Datensatz ist bei den hier ueblichen
            # Raten (bis 20 Hz) nicht messbar; wer schneller schreibt, nimmt
            # 'max_rows'.
            aktuell = self.segments[-1]
            try:
                if aktuell.stat().st_size >= policy.max_bytes:
                    return True
            except OSError as error:  # pragma: no cover - Datei verschwunden
                _log.warning("Groesse von %s nicht lesbar: %s", aktuell, error)
        return False

    def output_paths(self) -> list[Path]:
        """Alle Abschnitte - ein Sidecar beschreibt die ganze Messreihe (M4-3)."""
        return list(self.segments)

    def close(self) -> None:
        """Den laufenden Abschnitt schliessen. Mehrfachaufruf ist unschaedlich."""
        if self._sink is not None:
            self._sink.close()
            self._sink = None
            _log.info(
                "Rotation beendet: %d Abschnitt(e), zuletzt %s",
                self._segment,
                self.segments[-1] if self.segments else "-",
            )

    def __enter__(self) -> "RotatingSink":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


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

    def output_paths(self) -> list[Path]:
        """Die Dateien aller gebuendelten Senken, in ihrer Reihenfolge."""
        pfade: list[Path] = []
        for sink in self._sinks:
            methode = getattr(sink, "output_paths", None)
            if callable(methode):
                pfade.extend(methode())
        return pfade

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
