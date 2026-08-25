# =============================================================================
# Datei: tests/test_range_plan.py
# RangePlan.validate(): Konflikte werden nach Aufloesung der Scopes und vor
# dem ersten Set-Kommando erkannt.
#
# parse_range_value()/ranges_match() sichern zusaetzlich den Vergleich der
# Eingangsart ab.
# =============================================================================

from __future__ import annotations

import pytest

from wt3000_scpi.wt3000_core import WTError
from wt3000_scpi.wt3000_rangeio import (
    Quantity,
    RangeValue,
    parse_range_value,
    ranges_match,
)
from wt3000_scpi.wt3000_ranging import AutoRangeSpec, RangePlan, RangeSpec

# Elemente 1-3 haengen am externen Stromsensor (10 V), Element 4 direkt (5 A);
# alle vier stehen auf 1000 V. Siehe conftest.range_responses().


# ---------------------------------------------------------------------------
# Bereichsantworten - RANGEIO-2
# ---------------------------------------------------------------------------


def test_sensorantwort_wird_als_volt_erkannt():
    wert = parse_range_value("EXTERNAL,10.00E+00")
    assert wert == RangeValue(10.0, sensor=True)
    assert wert.describe(Quantity.CURRENT) == "10 V (Sensor)"


def test_kurzform_ext_wird_ebenfalls_erkannt():
    assert parse_range_value("EXT,5.00E+00").sensor is True


def test_direkter_bereich_bleibt_ampere():
    assert parse_range_value("30.0E+00") == RangeValue(30.0, sensor=False)


def test_header_vor_der_antwort_stoert_nicht():
    antwort = ":INPUT:CURRENT:RANGE:ELEMENT1 EXTERNAL,10.00E+00"
    assert parse_range_value(antwort) == RangeValue(10.0, sensor=True)


def test_sensorantwort_ohne_wert_bricht_ab():
    with pytest.raises(WTError):
        parse_range_value("EXTERNAL")


def test_gleicher_zahlenwert_andere_eingangsart_ist_keine_uebereinstimmung():
    """10 A direkt und 10 V am Sensor sind nicht derselbe Zustand."""
    assert not ranges_match(RangeValue(10.0, True), RangeValue(10.0, False))
    assert ranges_match(RangeValue(10.0, True), RangeValue(10.004, True))


# ---------------------------------------------------------------------------
# RangePlan.validate
# ---------------------------------------------------------------------------


def test_gueltiger_plan_wird_angenommen(access):
    plan = RangePlan.of(
        RangeSpec(Quantity.VOLTAGE, "SIGMA", 300.0),
        AutoRangeSpec(Quantity.VOLTAGE, 4, True),
    )
    plan.validate(access)


def test_leerer_plan_bricht_ab(access):
    with pytest.raises(WTError):
        RangePlan().validate(access)


def test_bereichswert_null_oder_negativ_bricht_ab(access):
    with pytest.raises(WTError):
        RangePlan.of(RangeSpec(Quantity.VOLTAGE, 1, 0.0)).validate(access)


def test_widerspruch_erst_nach_scope_aufloesung_sichtbar(access):
    """'SIGMA' und '1' meinen bei V3A3,P1W2 dasselbe Element.

    Ohne Aufloesung der Scopes waeren das zwei verschiedene Schluessel und der
    Widerspruch fiele erst am bereits veraenderten Geraet auf.
    """
    plan = RangePlan.of(
        RangeSpec(Quantity.VOLTAGE, "SIGMA", 300.0),
        RangeSpec(Quantity.VOLTAGE, 1, 600.0),
    )
    with pytest.raises(WTError) as fehler:
        plan.validate(access)
    assert "Widerspruch" in str(fehler.value)


def test_gleicher_wert_ueber_zwei_scopes_ist_kein_widerspruch(access):
    plan = RangePlan.of(
        RangeSpec(Quantity.VOLTAGE, "SIGMA", 300.0),
        RangeSpec(Quantity.VOLTAGE, 2, 300.0),
    )
    plan.validate(access)


def test_fester_bereich_und_autorange_ein_schliessen_sich_aus(access):
    plan = RangePlan.of(
        RangeSpec(Quantity.VOLTAGE, "ALL", 300.0),
        AutoRangeSpec(Quantity.VOLTAGE, 3, True),
    )
    with pytest.raises(WTError):
        plan.validate(access)


def test_autorange_ein_und_aus_zugleich_bricht_ab(access):
    plan = RangePlan.of(
        AutoRangeSpec(Quantity.CURRENT, "SIGMA", True),
        AutoRangeSpec(Quantity.CURRENT, 2, False),
    )
    with pytest.raises(WTError):
        plan.validate(access)


def test_nicht_vorhandenes_element_bricht_ab(access):
    with pytest.raises(WTError):
        RangePlan.of(RangeSpec(Quantity.VOLTAGE, 7, 300.0)).validate(access)


def test_sigma_ohne_wiring_units_wird_nicht_geraten(fake_session):
    """Ohne sigma_members wird der Scope abgelehnt statt geraten."""
    from wt3000_scpi.wt3000_rangeio import RangeAccess

    blind = RangeAccess(fake_session, allow_changes=True)
    with pytest.raises(WTError):
        RangePlan.of(RangeSpec(Quantity.VOLTAGE, "SIGMA", 300.0)).validate(blind)


# --- Eingangsart (Ergaenzung aus der Korrektur zu RANGEIO-2) ---------------


def test_amperewert_an_sensorelement_wird_vorher_abgefangen(access):
    """Element 1 haengt am Sensor - ein Direktbereich waere Fehlkonfiguration."""
    plan = RangePlan.of(RangeSpec(Quantity.CURRENT, 1, 10.0))
    with pytest.raises(WTError) as fehler:
        plan.validate(access)
    assert "Sensoreingang" in str(fehler.value)
    # Entscheidend: es wurde nichts gesendet.
    assert access._session.written == []


def test_sensorbereich_an_sensorelement_ist_zulaessig(access):
    plan = RangePlan.of(RangeSpec(Quantity.CURRENT, 1, 5.0, sensor=True))
    plan.validate(access)
    assert plan.describe() == ["Strombereich 1 = 5 V (Sensor)"]


def test_sensorbereich_an_direktelement_wird_abgefangen(access):
    plan = RangePlan.of(RangeSpec(Quantity.CURRENT, 4, 5.0, sensor=True))
    with pytest.raises(WTError):
        plan.validate(access)


def test_spannungspfad_kennt_keinen_sensoreingang(access):
    with pytest.raises(WTError):
        RangePlan.of(RangeSpec(Quantity.VOLTAGE, 1, 300.0, sensor=True)).validate(access)
