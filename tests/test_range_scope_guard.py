# =============================================================================
# Datei: tests/test_range_scope_guard.py
# RangeAccess prueft die Elementnummer vor jedem Kommando. Die Schutzregel gilt
# fuer get/set_range() und get/set_auto(), auch bei direkter Nutzung ohne Plan.
# =============================================================================

from __future__ import annotations

import pytest
from conftest import FakeSession, range_responses

from wt3000_scpi.wt3000_core import WTError
from wt3000_scpi.wt3000_rangeio import Quantity, RangeAccess

#: Der vorliegende Aufbau hat vier Elemente. 7 gibt es nicht - und 0 auch
#: nicht, das ist die Zahl, die aus einer Schleife mit falscher Untergrenze
#: kommt.
NICHT_VORHANDEN = [7, 0, 99]


@pytest.fixture
def zugriff() -> RangeAccess:
    """Schreibfaehiger RangeAccess ohne sigma_members - wie in den Skripten.

    Genau diese Konstellation benutzen stage5b und beide Geraeteskripte: sie
    legen RangeAccess mit der Voreinstellung DEFAULT_ELEMENTS = (1,2,3,4) an
    und ohne Wiring-Units.
    """
    return RangeAccess(FakeSession(range_responses()), allow_changes=True)


ALLE_ZUGRIFFE = [
    pytest.param(lambda a, e: a.get_range(Quantity.VOLTAGE, e), id="get_range"),
    pytest.param(lambda a, e: a.get_auto(Quantity.VOLTAGE, e), id="get_auto"),
    pytest.param(lambda a, e: a.set_range(Quantity.VOLTAGE, e, 1000.0), id="set_range"),
    pytest.param(lambda a, e: a.set_auto(Quantity.VOLTAGE, e, True), id="set_auto"),
]


# ---------------------------------------------------------------------------
# Der Kern von A-03
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("zugriffsart", ALLE_ZUGRIFFE)
@pytest.mark.parametrize("element", NICHT_VORHANDEN)
def test_nicht_vorhandenes_element_wird_abgelehnt(zugriff, zugriffsart, element):
    """Reproduktion 7.2 der Analyse, ausgeweitet auf alle vier Methoden."""
    with pytest.raises(WTError, match=f"Element {element} existiert nicht"):
        zugriffsart(zugriff, element)


@pytest.mark.parametrize("zugriffsart", ALLE_ZUGRIFFE)
@pytest.mark.parametrize("element", NICHT_VORHANDEN)
def test_nichts_erreicht_die_leitung(zugriff, zugriffsart, element):
    """Der eigentliche Pruefsatz: die Ablehnung kommt VOR dem Senden.

    Vorher lieferte 'set_range(Quantity.VOLTAGE, 7, 1000.0)' das Kommando
    ':INPut:VOLTage:RANGe:ELEMent7 1000' und SENDETE es. Am Geraet faellt das
    als Eintrag in der Fehlerqueue auf - also erst bei 'assert_no_error()',
    oder gar nicht.

    Das ist dasselbe Prinzip, das WTSession._validate() bereits durchzieht:
    pruefen, bevor die Nachricht hinausgeht, nicht danach.
    """
    with pytest.raises(WTError):
        zugriffsart(zugriff, element)

    gesendet = zugriff._session.written
    assert gesendet == [], f"Trotz Ablehnung gesendet: {gesendet}"


@pytest.mark.parametrize("zugriffsart", ALLE_ZUGRIFFE)
def test_vorhandenes_element_geht_weiterhin_durch(zugriff, zugriffsart):
    """Gegenprobe - die Pruefung darf den Normalfall nicht behindern."""
    for element in zugriff.elements:
        zugriffsart(zugriff, element)


def test_meldung_nennt_die_vorhandenen_elemente(zugriff):
    """Ohne die Liste ist der Fehler nicht zu beheben.

    'Element 7 existiert nicht' allein laesst offen, welche es denn gibt - die
    Bestueckung steht nirgends im Aufruf.
    """
    with pytest.raises(WTError, match=r"vorhanden: \(1, 2, 3, 4\)"):
        zugriff.get_range(Quantity.VOLTAGE, 7)


# ---------------------------------------------------------------------------
# Die Elementliste ist geraeteabhaengig
# ---------------------------------------------------------------------------


def test_pruefung_folgt_der_uebergebenen_bestueckung():
    """Ein 3-Element-Geraet lehnt Element 4 ab.

    Damit haengt der Schutz an der Bestueckung, die der Aufrufer uebergibt -
    nicht an der Voreinstellung. Das ist die Vorarbeit fuer S-01/M1-3: sobald
    DeviceInfo die Elementliste liefert, wirkt diese Pruefung ohne weitere
    Aenderung richtig.
    """
    zugriff = RangeAccess(
        FakeSession(range_responses()), allow_changes=True, elements=(1, 2, 3)
    )
    with pytest.raises(WTError, match="Element 4 existiert nicht"):
        zugriff.set_range(Quantity.VOLTAGE, 4, 1000.0)

    zugriff.set_range(Quantity.VOLTAGE, 3, 1000.0)


# ---------------------------------------------------------------------------
# Sammelscopes - die beabsichtigte Verhaltensaenderung
# ---------------------------------------------------------------------------


def test_all_bleibt_erlaubt(zugriff):
    """':ALL' loest gegen die Elementliste auf und geht unveraendert hinaus."""
    kommando = zugriff.set_range(Quantity.VOLTAGE, "ALL", 1000.0)
    assert kommando == ":INPut:VOLTage:RANGe:ALL 1000"
    assert zugriff._session.written == [kommando]


def test_sigma_ohne_wiring_units_wird_jetzt_abgelehnt(zugriff):
    """BEABSICHTIGTE VERHALTENSAENDERUNG - siehe Plan, Schritt 4.

    Vorher ging 'set_range(quantity, "SIGMA", ...)' auf einem RangeAccess ohne
    sigma_members stillschweigend hinaus. Jetzt nicht mehr.

    Fuer den geplanten Weg ueber wt3000_ranging aendert das nichts - das ist
    nachgemessen, nicht hergeleitet: 'apply_plan()' ruft als erstes
    'plan.validate()', und das loest jeden Scope ueber 'expand_scope()' auf.
    Derselbe WTError fiel dort schon vorher, ebenfalls bevor ein Kommando
    hinausging. Der Gegentest dazu steht in
    test_geplanter_weg_verhaelt_sich_unveraendert().

    Betroffen ist allein, wer Layer 2 direkt benutzt und den RangePlan umgeht -
    also genau die Gruppe, um die es in A-03 geht.
    """
    with pytest.raises(WTError, match="nicht aufloesbar"):
        zugriff.set_range(Quantity.VOLTAGE, "SIGMA", 1000.0)
    assert zugriff._session.written == []


def test_sigma_mit_wiring_units_geht_durch():
    """Mit uebergebenen Wiring-Units bleibt der Sammelscope nutzbar."""
    zugriff = RangeAccess(
        FakeSession(range_responses()),
        allow_changes=True,
        sigma_members={"SIGMA": (1, 2, 3), "SIGMB": (4,)},
    )
    kommando = zugriff.set_range(Quantity.VOLTAGE, "SIGMA", 1000.0)
    assert kommando == ":INPut:VOLTage:RANGe:SIGMA 1000"
    assert zugriff._session.written == [kommando]


# ---------------------------------------------------------------------------
# Die Schreibsperre bleibt das erste Schloss
# ---------------------------------------------------------------------------


def test_schreibsperre_greift_weiterhin_zuerst():
    """allow_changes=False lehnt ab, bevor der Scope ueberhaupt zaehlt.

    Die Reihenfolge ist hier gleichgueltig fuer das Ergebnis - gesendet wird in
    keinem Fall etwas -, aber die Meldung soll die Sperre nennen und nicht das
    Element: 'RangeAccess wurde mit allow_changes=False angelegt' ist die
    Auskunft, die weiterhilft.
    """
    zugriff = RangeAccess(FakeSession(range_responses()), allow_changes=False)
    with pytest.raises(WTError, match="allow_changes=False"):
        zugriff.set_range(Quantity.VOLTAGE, 1, 1000.0)
    assert zugriff._session.written == []


def test_geplanter_weg_verhaelt_sich_unveraendert():
    """Der Gegentest zur Verhaltensaenderung: ueber RangePlan aendert sich nichts.

    'apply_plan()' ruft als erstes 'plan.validate(access)', und das loest jeden
    Scope ueber 'expand_scope()' auf - der SIGMA-Fehler fiel dort schon vor
    Schritt 4, und ebenfalls bevor ein Kommando hinausging.

    Dieser Pruefsatz haelt fest, dass die neue Sperre in RangeAccess den
    geplanten Weg weder verschaerft noch verschiebt: gleiche Ausnahme, gleicher
    Zeitpunkt, nichts gesendet. Ohne ihn stuende die Begruendung fuer die
    Verhaltensaenderung nur in einem Kommentar.
    """
    from wt3000_scpi.wt3000_ranging import RangePlan, RangeSpec, apply_plan

    sitzung = FakeSession(range_responses())
    ohne_units = RangeAccess(sitzung, allow_changes=True)
    plan = RangePlan(ranges=[RangeSpec(Quantity.VOLTAGE, "SIGMA", 1000.0)], autos=[])

    with pytest.raises(WTError, match="nicht aufloesbar"):
        apply_plan(ohne_units, plan)

    assert sitzung.written == [], "Der geplante Weg hat trotz Ablehnung geschrieben"
