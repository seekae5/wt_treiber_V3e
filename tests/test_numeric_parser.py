# =============================================================================
# Datei: tests/test_numeric_parser.py
# Tests fuer parse_float_block(), ItemTable.from_response() und
# ItemTable.map_values().
#
# Diese drei entscheiden, welche Zahl unter welchem Namen in der CSV landet.
# Sie brauchen kein Geraet.
# =============================================================================

from __future__ import annotations

import math
import struct

import pytest

from wt3000_scpi.wt3000_core import ProtocolError
from wt3000_scpi.wt3000_numeric import (
    FLOAT_NO_DATA,
    FLOAT_OVERRANGE,
    ItemTable,
    NumericItem,
    NumericValue,
    ValueStatus,
    parse_float_block,
)


def block(*words: int) -> bytes:
    """Vier-Byte-Woerter MSB first zu einem Messwertblock zusammensetzen."""
    return b"".join(struct.pack(">I", w) for w in words)


# ---------------------------------------------------------------------------
# parse_float_block - Sentinel VOR der Float-Wandlung
# ---------------------------------------------------------------------------


def test_gewoehnlicher_messwert_wird_gewandelt():
    (value,) = parse_float_block(struct.pack(">f", 230.15))
    assert value.status is ValueStatus.OK
    assert value.is_usable
    assert value.value == pytest.approx(230.15, rel=1e-6)


def test_no_data_sentinel_wird_erkannt():
    """0x7E951BEE ist eine ENDLICHE IEEE-Single um 9.9E+37.

    Genau deshalb greift math.isnan() hier nicht. Wuerde die Erkennung erst
    nach der Wandlung laufen, stuende ein Wert von 9.9E+37 als Messwert in
    der CSV - der Fehler, den die Bitmusterpruefung verhindert.
    """
    (value,) = parse_float_block(block(FLOAT_NO_DATA))
    assert value.status is ValueStatus.NO_DATA
    assert not value.is_usable
    assert math.isnan(value.value)

    (naiv,) = struct.unpack(">f", block(FLOAT_NO_DATA))
    assert math.isfinite(naiv) and naiv > 1e37


def test_overrange_sentinel_wird_erkannt():
    (value,) = parse_float_block(block(FLOAT_OVERRANGE))
    assert value.status is ValueStatus.OVERRANGE
    assert math.isinf(value.value)

    (naiv,) = struct.unpack(">f", block(FLOAT_OVERRANGE))
    assert math.isfinite(naiv)


def test_gemischter_block_behaelt_die_reihenfolge():
    payload = struct.pack(">f", 1.0) + block(FLOAT_NO_DATA) + struct.pack(">f", 3.0)
    values = parse_float_block(payload)
    assert [v.status for v in values] == [
        ValueStatus.OK,
        ValueStatus.NO_DATA,
        ValueStatus.OK,
    ]


def test_leerer_block_ist_zulaessig():
    assert parse_float_block(b"") == []


def test_krumme_blocklaenge_bricht_ab():
    with pytest.raises(ProtocolError):
        parse_float_block(b"\x00\x01\x02")


# ---------------------------------------------------------------------------
# ItemTable.from_response
# ---------------------------------------------------------------------------


def test_antwort_wird_in_items_zerlegt():
    table = ItemTable.from_response("3;U,1;I,1;PHI,1,1")
    assert table.number == 3
    assert [i.function for i in table.items] == ["U", "I", "PHI"]
    assert table.items[2].element == "1" and table.items[2].order == "1"
    # Indizes sind 1-basiert und entsprechen ITEM<index>.
    assert [i.index for i in table.items] == [1, 2, 3]


def test_item_ohne_element_und_order():
    table = ItemTable.from_response("1;NONE")
    assert table.items[0].is_none
    assert table.items[0].argument == "NONE"


def test_eingeschaltete_header_werden_benannt():
    """Mit ':COMMunicate:HEADer 1' ist das erste Feld keine Zahl."""
    with pytest.raises(ProtocolError) as fehler:
        ItemTable.from_response(":NUMERIC:NORMAL:NUMBER 3;U,1")
    assert "HEADer" in str(fehler.value)


def test_leere_antwort_bricht_ab():
    with pytest.raises(ProtocolError):
        ItemTable.from_response("   ")


def test_abweichende_anzahl_wird_gemeldet_aber_nicht_verworfen(caplog):
    with caplog.at_level("WARNING"):
        table = ItemTable.from_response("5;U,1;I,1")
    assert table.number == 5 and len(table.items) == 2
    assert any("NUMber" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# ItemTable.map_values
# ---------------------------------------------------------------------------


def ok(number: float) -> NumericValue:
    return NumericValue(number, ValueStatus.OK, 0)


def test_werte_bekommen_sprechende_namen():
    table = ItemTable.from_response("2;U,1;I,1")
    mapped = table.map_values([ok(230.0), ok(1.5)])
    assert list(mapped) == ["U1", "I1"]
    assert mapped["U1"].value == 230.0


def test_doppelte_schluessel_bekommen_ein_suffix():
    """Die Tabelle darf dasselbe Item mehrfach enthalten (z.B. FU,1)."""
    table = ItemTable(
        number=3,
        items=[
            NumericItem(1, "FU", "1"),
            NumericItem(2, "FU", "1"),
            NumericItem(3, "FU", "1"),
        ],
    )
    mapped = table.map_values([ok(50.0), ok(50.1), ok(50.2)])
    assert list(mapped) == ["FU1", "FU1#2", "FU1#3"]
    assert mapped["FU1#3"].value == 50.2


def test_zu_wenige_werte_werden_gemeldet(caplog):
    """NUMERIC-1: zip() kuerzt still, die Warnung ist der einzige Hinweis.

    UEBERARBEITET (P-3, siehe PLAN_BEFUNDE_2026-08-19.md): Dieser Test war als
    Anschlagpunkt fuer die Entscheidung angelegt, ob NUMERIC-1 auf einen harten
    Abbruch umgestellt wird. Die Entscheidung ist gefallen - und zwar
    differenziert:

      * read_numeric_values() und CsvRecorder.write() brechen ab. Dort
        entstehen die Messdaten, dort verrutschen sonst die Spalten.
      * map_values() bleibt bei der Warnung. Es ist eine Bequemlichkeit fuer
        Anzeige und Diagnose, kein Datenpfad; ein fehlender Schluessel faellt
        beim Nachschlagen sofort auf.

    Der Test bleibt deshalb unveraendert bestehen und haelt ab jetzt nicht mehr
    eine offene Frage fest, sondern die getroffene Festlegung.
    """
    table = ItemTable.from_response("3;U,1;I,1;P,1")
    with caplog.at_level("WARNING"):
        mapped = table.map_values([ok(230.0), ok(1.5)])
    assert list(mapped) == ["U1", "I1"]
    assert any("passt nicht" in r.message for r in caplog.records)


def test_geordnete_paarung_bleibt_ueber_zip_verfuegbar():
    """Der positionsbezogene Weg ist gegen Namensverschiebung immun."""
    table = ItemTable.from_response("2;U,1;U,2")
    values = [ok(230.0), ok(231.0)]
    paare = list(zip(table.items, values))
    assert paare[1][0].element == "2" and paare[1][1].value == 231.0
