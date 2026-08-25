# =============================================================================
# Datei: wt3000_numeric.py
# Layer 3 - Messwert-Layer: Item-Tabelle spiegeln, FLOat-Block parsen,
#           Werte auf Namen zurueckmappen, Tabelle sichern/wiederherstellen.
# =============================================================================

from __future__ import annotations

import json
import logging
import math
import struct
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .wt3000_core import ProtocolError, WTSession

_log = logging.getLogger("wt3000.numeric")

# --- Sentinel-Bitmuster im FLOat-Format (Handbuch, "Numeric Data Format") ----
# ACHTUNG: Das sind gueltige, ENDLICHE IEEE-Singles um 9.9E+37. math.isnan()
# und math.isinf() greifen hier NICHT. Der Vergleich erfolgt deshalb auf dem
# rohen 4-Byte-Bitmuster, bevor ueberhaupt in float gewandelt wird.
# Nicht verwechseln mit 0x7FC00000 etc. - die gelten fuer .WTD-Dateien.
FLOAT_NO_DATA: int = 0x7E951BEE  # ASCii-Aequivalent: "NAN"  -> Item existiert nicht
FLOAT_OVERRANGE: int = 0x7E94F56A  # ASCii-Aequivalent: "INF"  -> Bereich falsch


class ValueStatus(Enum):
    """Messtechnische Bedeutung eines gelesenen Werts."""

    OK = "OK"
    NO_DATA = "NO_DATA"  # NAN: Item ist NONE oder nicht zur Messung konfiguriert
    OVERRANGE = "OVERRANGE"  # INF: Overrange, Overflow, Data over, nicht eingeschwungen


@dataclass(frozen=True)
class NumericValue:
    """Ein einzelner Messwert samt Statusbewertung."""

    value: float
    status: ValueStatus
    raw_bits: int

    @property
    def is_usable(self) -> bool:
        """True, wenn der Wert messtechnisch verwertbar ist."""
        return self.status is ValueStatus.OK

    def __str__(self) -> str:
        if self.status is ValueStatus.OK:
            return f"{self.value: .6g}"
        return f"<{self.status.value}>"


@dataclass(frozen=True)
class NumericItem:
    """Ein Eintrag der Item-Tabelle: :NUMeric:NORMal:ITEM<index>."""

    index: int  # 1..255
    function: str  # z.B. "UTHD", "FU", "PHI", "NONE"
    element: str | None = None  # "1".."4", "SIGMA", "SIGMB"
    order: str | None = None  # "TOTAL", "DC", "1".."100"

    @property
    def is_none(self) -> bool:
        """True, wenn das Item auf NONE steht."""
        return self.function.upper() == "NONE"

    @property
    def argument(self) -> str:
        """Parameterstring, wie ihn ITEM<x> als Eingabe erwartet."""
        if self.is_none:
            return "NONE"
        parts = [self.function]
        if self.element is not None:
            parts.append(self.element)
        if self.order is not None:
            parts.append(self.order)
        return ",".join(parts)

    @property
    def key(self) -> str:
        """Sprechender Name fuer das Ergebnis-Dictionary, z.B. 'UTHD1', 'PHI1_1'."""
        if self.is_none:
            return f"NONE_{self.index}"
        name = self.function
        if self.element is not None:
            name += self.element
        if self.order is not None:
            name += f"_{self.order}"
        return name

    @property
    def unit(self) -> "str | None":
        """Einheit dieses Items. None heisst 'nicht bekannt'.

        Die Einheit haengt an der Funktion, nicht am Element: 'U1' und
        'USIGMA' sind beide Volt.
        """
        if self.is_none:
            return None
        return unit_of(self.function)

    @classmethod
    def parse(cls, index: int, token: str) -> "NumericItem":
        """Ein Token wie 'UTHD,1' oder 'PHI,1,1' in ein NumericItem wandeln."""
        parts = [p.strip() for p in token.split(",") if p.strip()]
        if not parts:
            raise ProtocolError(f"Leeres Item-Token an Position {index}")
        return cls(
            index=index,
            function=parts[0],
            element=parts[1] if len(parts) > 1 else None,
            order=parts[2] if len(parts) > 2 else None,
        )


# ---------------------------------------------------------------------------
# Einheiten
# ---------------------------------------------------------------------------
#
# Eine Messdatei, in deren Kopf 'U1,I1,P1' steht, ist ohne Zusatzwissen nicht
# eindeutig: dass U in Volt und WH in Wattstunden kommt, weiss der, der die
# Messung aufgesetzt hat - und niemand sonst, Wochen spaeter erst recht nicht.
#
# WAS HIER STEHT UND WAS NICHT. Aufgenommen sind ausschliesslich Groessen,
# deren Einheit belegt ist: die elektrischen Grundgroessen, die Frequenz und
# die Integrationsgroessen - letztere sind im Kopf von INTEGRATION_FUNCTIONS
# (wt3000_measure.py) aus dem Handbuch mit [Wh], [Ah], [VAh] und [varh]
# uebernommen.
#
# NICHT aufgenommen sind die Summengroessen der Oberschwingungsanalyse
# (UTHD, ITHD, PTHD, UTHF, ITHF, UTIF, ITIF, HVF, HCF). Ihre Einheit haengt
# an Geraeteeinstellungen, und der im Projekt vorliegende Auszug
# (docs/WT3000_Communication_Commands.md) ist die Kommandoreferenz - eine
# Einheitentabelle enthaelt er nicht. Sie liefern deshalb None und nicht etwa
# ein plausibles '%': eine geratene Einheit an einem Messwert ist schlimmer
# als eine fehlende, weil sie geglaubt wird.
# ZU VERIFIZIEREN: Einheiten der neun Oberschwingungsfaktoren
# am Geraet oder aus IM WT3001E-01EN nachtragen.
#
# Die leere Zeichenkette bedeutet "dimensionslos und das ist bekannt"
# (LAMBDA ist ein Verhaeltnis), None bedeutet "nicht bekannt". Der
# Unterschied ist der ganze Zweck dieser Tabelle und wird bis in die Ausgabe
# durchgehalten.
FUNCTION_UNITS: dict[str, str] = {
    # Grundgroessen
    "U": "V",
    "I": "A",
    "P": "W",
    "S": "VA",
    "Q": "var",
    "LAMBDA": "",       # Leistungsfaktor - ein Verhaeltnis
    "PHI": "deg",       # Phasenwinkel
    "FU": "Hz",
    "FI": "Hz",
    # Integration (Handbuch 6-99, siehe INTEGRATION_FUNCTIONS)
    "TIME": "s",
    "WH": "Wh",
    "WHP": "Wh",
    "WHM": "Wh",
    "AH": "Ah",
    "AHP": "Ah",
    "AHM": "Ah",
    "WS": "VAh",
    "WQ": "varh",
}


def unit_of(function: str) -> "str | None":
    """Einheit einer Messfunktion. None heisst 'nicht bekannt'.

    Zur Abgrenzung von '' siehe den Kopf von FUNCTION_UNITS: '' ist eine
    Aussage, None ist deren Fehlen.
    """
    return FUNCTION_UNITS.get(function.strip().upper())


@dataclass
class ItemTable:
    """Spiegelbild der geraeteseitigen Item-Tabelle.

    Die Reihenfolge ist die zentrale Information: :NUMeric:NORMal:VALue?
    liefert die Werte ausschliesslich positionsbezogen.
    """

    number: int
    items: list[NumericItem] = field(default_factory=list)

    # -- Erzeugen -----------------------------------------------------------

    @classmethod
    def from_response(cls, response: str) -> "ItemTable":
        """Antwort auf ':NUMeric:NORMal?' parsen (Header-Modus OFF vorausgesetzt).

        Format: '<NUMber>;<Item1>;<Item2>;...'
        """
        fields = [f.strip() for f in response.split(";") if f.strip()]
        if not fields:
            raise ProtocolError("Leere Antwort auf :NUMeric:NORMal?")
        try:
            number = int(fields[0])
        except ValueError as exc:
            raise ProtocolError(
                f"Erstes Feld ist keine Zahl: {fields[0]!r}. "
                "Sind die Header eingeschaltet (:COMMunicate:HEADer)?"
            ) from exc

        items = [NumericItem.parse(i, token) for i, token in enumerate(fields[1:], start=1)]
        if len(items) != number:
            _log.warning(
                "NUMber=%d, aber %d Items in der Antwort - Feldzuordnung pruefen",
                number,
                len(items),
            )
        return cls(number=number, items=items)

    def units(self) -> tuple["str | None", ...]:
        """Einheiten in der Reihenfolge der Items. None heisst 'nicht bekannt'.

        Gegenstueck zu 'item.key': dieselbe Reihenfolge und Laenge.
        """
        return tuple(item.unit for item in self.items)

    def unit_map(self) -> dict[str, "str | None"]:
        """Spaltenname -> Einheit. Die Form, die in die Metadaten geht."""
        return {item.key: item.unit for item in self.items}

    @classmethod
    def read_from_device(cls, session: WTSession) -> "ItemTable":
        """Aktuelle Item-Tabelle vom Geraet lesen."""
        response = session.query(":NUMeric:NORMal?")
        table = cls.from_response(response)
        _log.info("Item-Tabelle gelesen: NUMber=%d, %d Items", table.number, len(table.items))
        return table

    # -- Sichern / Wiederherstellen ----------------------------------------

    def to_dict(self) -> dict:
        """Serialisierbare Darstellung fuer das JSON-Backup."""
        return {
            "number": self.number,
            "items": [
                {
                    "index": it.index,
                    "function": it.function,
                    "element": it.element,
                    "order": it.order,
                    "argument": it.argument,
                }
                for it in self.items
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ItemTable":
        """Gegenstueck zu to_dict()."""
        items = [
            NumericItem(
                index=int(d["index"]),
                function=d["function"],
                element=d.get("element"),
                order=d.get("order"),
            )
            for d in data["items"]
        ]
        return cls(number=int(data["number"]), items=items)

    def save(self, path: Path) -> None:
        """Backup als JSON auf Platte schreiben."""
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        _log.info("Item-Tabelle gesichert nach %s", path)

    @classmethod
    def load(cls, path: Path) -> "ItemTable":
        """Backup aus JSON laden."""
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def restore_to_device(self, session: WTSession, force: bool = False) -> int:
        """Diese Tabelle auf dem Geraet wiederherstellen.

        Es werden nur Items geschrieben, die vom Ist-Zustand abweichen -
        ausser force=True, dann werden alle Items geschrieben (nuetzlich, um
        den Schreibpfad einmal bewusst zu testen).

        Bewusst wird KEIN ':NUMeric:NORMal:CLEar ALL' gesendet: Items jenseits
        von NUMber sind im Backup nicht enthalten und bleiben so unangetastet.

        Rueckgabe: Anzahl tatsaechlich geschriebener Kommandos.
        """
        current = ItemTable.read_from_device(session)
        current_by_index = {it.index: it.argument for it in current.items}

        written = 0
        for item in self.items:
            if not force and current_by_index.get(item.index) == item.argument:
                continue
            session.write(f":NUMeric:NORMal:ITEM{item.index} {item.argument}")
            written += 1

        if force or current.number != self.number:
            session.write(f":NUMeric:NORMal:NUMber {self.number}")
            written += 1

        if written:
            session.assert_no_error("Wiederherstellung der Item-Tabelle")
            _log.info("Item-Tabelle wiederhergestellt (%d Kommandos)", written)
        else:
            _log.info("Item-Tabelle unveraendert - kein Schreibzugriff noetig")
        return written

    # -- Werte zuordnen -----------------------------------------------------

    def map_values(self, values: list[NumericValue]) -> dict[str, NumericValue]:
        """Positionsbezogene Werte auf sprechende Namen abbilden.

        Doppelte Schluessel (die Tabelle enthaelt z.B. FU,1 zweimal) bekommen
        das Suffix '#2', '#3' usw. Die geordnete Liste bleibt ueber
        zip(table.items, values) jederzeit verfuegbar.

        Eine abweichende Anzahl bleibt hier bewusst eine WARNUNG - anders als
        im Datenpfad von 'read_numeric_values()' und den Senken.

        Die Trennung ist Absicht. Diese Methode ist eine Bequemlichkeit fuer
        Anzeige und Diagnose: sie liefert ein Dictionary zum Nachschlagen, und
        wer nachschlaegt, merkt einen fehlenden Schluessel sofort. Sie liegt
        nicht im Datenpfad einer Messreihe - dort steht die geordnete Liste,
        und genau die ist gegen Verrutschen abgesichert. Ein Abbruch hier
        wuerde die Diagnose gerade dann unmoeglich machen, wenn man sie
        braucht: naemlich um nachzusehen, welche Items ueberhaupt geliefert
        wurden.
        """
        if len(values) != len(self.items):
            _log.warning(
                "Anzahl Werte (%d) passt nicht zur Anzahl Items (%d)", len(values), len(self.items)
            )

        mapped: dict[str, NumericValue] = {}
        seen: dict[str, int] = {}
        for item, value in zip(self.items, values):
            key = item.key
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                key = f"{key}#{seen[key]}"
            mapped[key] = value
        return mapped


# ---------------------------------------------------------------------------
# FLOat-Parser
# ---------------------------------------------------------------------------


def parse_float_block(payload: bytes) -> list[NumericValue]:
    """Binaeren Messwertblock (IEEE single, MSB first) in NumericValue-Liste wandeln.

    Die Sentinel-Bitmuster fuer NAN und INF werden VOR der Float-Wandlung
    erkannt, weil sie als IEEE-Zahl voellig unauffaellig aussehen.
    """
    if len(payload) % 4 != 0:
        raise ProtocolError(f"Blocklaenge {len(payload)} ist kein Vielfaches von 4 Bytes")

    values: list[NumericValue] = []
    for offset in range(0, len(payload), 4):
        chunk = payload[offset : offset + 4]
        (bits,) = struct.unpack(">I", chunk)  # MSB first
        if bits == FLOAT_NO_DATA:
            values.append(NumericValue(math.nan, ValueStatus.NO_DATA, bits))
        elif bits == FLOAT_OVERRANGE:
            values.append(NumericValue(math.inf, ValueStatus.OVERRANGE, bits))
        else:
            (number,) = struct.unpack(">f", chunk)
            values.append(NumericValue(number, ValueStatus.OK, bits))
    return values


def read_numeric_block(
    session: WTSession,
    expected_count: int | None = None,
    strict: bool = True,
) -> tuple[bytes, list[NumericValue]]:
    """Wie read_numeric_values(), liefert aber auch die Rohbytes des Blocks.

    Die Dublettenerkennung vergleicht Rohbytes statt geparster Werte:

      * NaN. Ein Wert ohne Daten kommt als NaN heraus, und NaN != NaN. Ein
        Vergleich der Wertelisten wuerde jeden Zyklus mit einem einzigen
        NO_DATA-Wert fuer verschieden erklaeren - also ausgerechnet dort
        versagen, wo das Geraet gerade nichts liefert.
      * Genauigkeit. Der Block ist die Antwort des Geraets. Zwei Zyklen sind
        genau dann derselbe Datensatz, wenn das Geraet dieselben Bytes
        geschickt hat; jede Umrechnung davor kann nur verlieren.

    Die geparsten Werte kommen mit heraus, damit der Aufrufer den Block nicht
    ein zweites Mal auswerten muss.
    """
    payload = session.query_block(":NUMeric:NORMal:VALue?")
    values = parse_float_block(payload)
    if expected_count is not None and len(values) != expected_count:
        if strict:
            raise ProtocolError(
                f"Erwartet: {expected_count} Werte, erhalten: {len(values)}. "
                "Die Item-Tabelle im Geraet passt nicht zur erwarteten - wurde sie "
                "am Bedienfeld oder von einer zweiten Sitzung veraendert? "
                "(strict=False liefert die Werte trotzdem, dann aber ohne "
                "verlaessliche Spaltenzuordnung.)"
            )
        _log.warning("Erwartet: %d Werte, erhalten: %d", expected_count, len(values))
    return payload, values


def read_numeric_values(
    session: WTSession,
    expected_count: int | None = None,
    strict: bool = True,
) -> list[NumericValue]:
    """':NUMeric:NORMal:VALue?' im FLOat-Format lesen und parsen.

    Eine von 'expected_count' abweichende Werteanzahl ist im strengen Modus
    ein Fehler, keine Warnung.

    Der Grund liegt eine Schicht hoeher: der Spaltenkopf der CSV entsteht aus
    der Item-Tabelle, die Datenzeilen aus dieser Liste. Weichen die Laengen
    voneinander ab, verrutschen die Spalten gegeneinander - und zwar so, dass
    jede Zeile fuer sich plausibel aussieht. Die Abweichung faellt hier auf,
    eine Abfrage frueher als in der Datei.

    Eine abweichende Anzahl bedeutet immer, dass die Item-Tabelle im Geraet
    nicht mehr die ist, gegen die der Aufrufer plant - typischerweise, weil
    jemand am Bedienfeld etwas umgestellt hat. Weitermessen waere dann kein
    Notbetrieb, sondern das Erzeugen falsch beschrifteter Messdaten.

    strict=False warnt nur und ist fuer Diagnose, nicht fuer Messlaeufe.
    Wer zusaetzlich Rohbytes braucht, verwendet 'read_numeric_block()'.
    """
    _, values = read_numeric_block(session, expected_count, strict)
    return values
