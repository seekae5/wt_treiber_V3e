# =============================================================================
# Datei: tests/test_input_parsers.py
# Regulaere Tests der Eingabeparser und Stellwertpruefung. _check_allowed()
# wird zusaetzlich zum oeffentlichen Setter gezielt als Weisskasten getestet.
# =============================================================================

from __future__ import annotations

import pytest

# Gemeinsame Formatierung der Bereichsparameter.
from wt3000_scpi.wt3000_common import format_nrf
from wt3000_scpi.wt3000_core import WTError
from wt3000_scpi.wt3000_input import (
    VOLTAGE_RANGES,
    _check_allowed,
    # UEBERARBEITET (M0-1): format_voltage,
    parse_bool,
    parse_current_range,
    parse_float,
    parse_line_filter,
    resolve_wiring_units,
    split_multi,
    strip_header,
)


# --- 1) Parser gegen Beispielantworten aus dem Handbuch --------------------


def test_header_wird_entfernt():
    assert strip_header(":INPUT:VOLTAGE:RANGE:ELEMENT1 1.000E+03") == "1.000E+03"
    assert strip_header("1.000E+03") == "1.000E+03"


def test_verkettete_kurzform_liefert_den_wert():
    """Bei mehreren Werten antwortet das Geraet verkuerzt: 'ELEMENT2 OFF'."""
    assert strip_header("ELEMENT2 OFF") == "OFF"
    assert split_multi(":INPUT:FILTER:LINE:ELEMENT1 OFF;ELEMENT2 OFF") == ["OFF", "OFF"]


def test_strombereich_direkt_und_sensor():
    assert parse_current_range("EXTERNAL,10.00E+00") == (None, 10.0)
    assert parse_current_range("30.0E+00") == (30.0, None)


def test_boolean_und_float():
    assert parse_bool("1") is True
    assert parse_bool("0") is False
    assert parse_float("500.0E-03") == pytest.approx(0.5)
    with pytest.raises(WTError):
        parse_bool("VIELLEICHT")
    with pytest.raises(WTError):
        parse_float("EXTERNAL,10.00E+00")


def test_line_filter_wird_normalisiert():
    assert parse_line_filter("OFF") == "OFF"
    assert parse_line_filter("0") == "OFF"
    assert parse_line_filter("500.0E+00") == "500"


# --- 2) Zieladressierung: s. test_scope_and_items.py -----------------------
# target_node() wird dort zusammen mit den beiden anderen Kopien derselben
# Normalisierungsregel geprueft.


# --- 3) Elementzuordnung der Verdrahtung -----------------------------------


def test_wiring_units_dieses_aufbaus():
    """V3A3,P1W2: SigmaA = Elemente 1-3, SigmaB = Element 4."""
    units = resolve_wiring_units(("V3A3", "P1W2"))
    assert units[0].name == "SIGMA" and units[0].elements == (1, 2, 3)
    assert units[1].name == "SIGMB" and units[1].elements == (4,)


def test_wiring_units_einphasig():
    units = resolve_wiring_units(("P1W2", "P1W2"))
    assert units[0].elements == (1,) and units[1].elements == (2,)


# --- 4) Stellwertpruefung ---------------------------------------------------


def test_zwischenwert_ist_kein_zulaessiger_bereich():
    """700 V steht in keiner der beiden Crest-Faktor-Tabellen."""
    with pytest.raises(WTError):
        _check_allowed(700.0, VOLTAGE_RANGES[3], "Spannungsbereich")


def test_tabellenwert_wird_exakt_zurueckgegeben():
    assert _check_allowed(600.0, VOLTAGE_RANGES[3], "Spannungsbereich") == 600.0


def test_crest_faktor_bestimmt_die_tabelle():
    """7,5 V gibt es nur bei CF6 - INPUT-14 haengt genau daran."""
    assert 7.5 in VOLTAGE_RANGES[6]
    assert 7.5 not in VOLTAGE_RANGES[3]
    with pytest.raises(WTError):
        _check_allowed(7.5, VOLTAGE_RANGES[3], "Spannungsbereich")


# UEBERARBEITET (M0-1, siehe ROADMAP.md): Der alte Test hielt die
# Einheitenschreibweise fest, die am Geraet unterlegen war. Ersetzt durch den
# Test darunter.
#
# def test_drahtformat_der_spannung():
#     """Drahtformat A (INPUT-9) - am Geraet noch ZU VERIFIZIEREN."""
#     assert format_voltage(1000.0) == "1000V"
#     assert format_voltage(7.5) == "7.5V"


# NEU (M0-1): haelt die am Geraet belegte Parametersyntax der Bereichsknoten
# fest. Belegt ist ':INPut:VOLTage:RANGe:ELEMent4 1000' - gesetzt und
# unveraendert zurueckgelesen. Faellt dieser Test, hat jemand die
# Einheitenschreibweise wieder eingefuehrt.
def test_drahtformat_der_bereiche_ist_reines_nrf():
    assert format_nrf(1000.0) == "1000"
    assert format_nrf(7.5) == "7.5"
    assert format_nrf(0.5) == "0.5"
    assert "V" not in format_nrf(1000.0)
    assert "A" not in format_nrf(0.5)
