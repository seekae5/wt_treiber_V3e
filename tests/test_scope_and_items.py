# =============================================================================
# Datei: tests/test_scope_and_items.py
# Tests fuer canonical_scope(), canonical_element() und items_match().
#
# Kern dieser Datei ist die SIGMA/SIGMB-Regel. Der urspruengliche Bug -
# beidseitiges Praefixmatching setzte SigmaA und SigmaB gleich - war bei der
# Verdrahtung V3A3,P1W2 ein stiller Vertauscher von Elementen 1-3 gegen
# Element 4. Die Tests pruefen die gemeinsame Regel an ihren oeffentlichen
# Verwendungsstellen.
# =============================================================================

from __future__ import annotations

import pytest

from wt3000_scpi.wt3000_common import (
    ALL,
    SIGMA,
    SIGMB,
    canonical_element,
    canonical_scope,
    element_number,
    is_element_scope,
    scope_suffix,
)
from wt3000_scpi.wt3000_core import WTError
from wt3000_scpi.wt3000_input import target_node
from wt3000_scpi.wt3000_itemspec import _canonical_element, items_match
from wt3000_scpi.wt3000_numeric import NumericItem


# ---------------------------------------------------------------------------
# canonical_scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "eingabe,erwartet",
    [
        (3, "3"),
        ("3", "3"),
        ("ELEMent2", "2"),
        ("elem4", "4"),
        ("E1", "1"),
        ("sigma", SIGMA),
        ("SIGM", SIGMA),
        ("SIGMB", SIGMB),
        ("all", ALL),
    ],
)
def test_scope_normalisierung(eingabe, erwartet):
    assert canonical_scope(eingabe) == erwartet


def test_sigmb_wird_nie_zu_sigma():
    """Die entscheidende Zeile: 'SIGMB'.startswith('SIGM') ist wahr."""
    assert canonical_scope("SIGMB") != canonical_scope("SIGMA")
    assert canonical_scope("SIGM") == SIGMA


def test_unbekannter_scope_bricht_ab():
    with pytest.raises(WTError):
        canonical_scope("SIGMC")


def test_scope_suffix_und_elementnummer():
    assert scope_suffix(2) == ":ELEMent2"
    assert scope_suffix("SIGMA") == ":SIGMA"
    assert scope_suffix("ALL") == ":ALL"
    assert is_element_scope("ELEMent1") and not is_element_scope("SIGMA")
    assert element_number("E4") == 4
    with pytest.raises(WTError):
        element_number("SIGMA")


# ---------------------------------------------------------------------------
# canonical_element - andere Konvention als canonical_scope
# ---------------------------------------------------------------------------


def test_fehlendes_element_bedeutet_element_1():
    """Laut Handbuch setzt das Geraet Element 1, wenn <Element> fehlt."""
    assert canonical_element(None) == "1"
    assert _canonical_element(None) == "1"


@pytest.mark.parametrize("eingabe", ["SIGMA", "SIGM", "SIGMB", "1", "4"])
def test_beide_kopien_der_regel_sind_deckungsgleich(eingabe):
    """COMMON-1 vs. ITEMSPEC-2: solange beide existieren, muessen sie gleich sein."""
    assert canonical_element(eingabe) == _canonical_element(eingabe)


def test_target_node_lehnt_mehrdeutiges_sigm_ab():
    """INPUT-2: die dritte Kopie der Regel - hier ohne Kurzform-Toleranz."""
    assert target_node(3) == ":ELEMent3"
    assert target_node("SIGMB") == ":SIGMB"
    with pytest.raises(WTError):
        target_node("SIGM")
    with pytest.raises(WTError):
        target_node(5)


# ---------------------------------------------------------------------------
# items_match
# ---------------------------------------------------------------------------


def test_kurzform_der_antwort_gilt_als_treffer():
    """VERBose ist aus: gesendet 'LAMBDA', zurueckgelesen 'LAMB'."""
    assert items_match(NumericItem(1, "LAMBDA", "1"), NumericItem(1, "LAMB", "1"))


def test_praefixregel_laeuft_nur_in_eine_richtung():
    """Gesendet 'U', zurueckgelesen 'UTHD' ist KEIN Treffer.

    Beidseitiges Matching wuerde die Grundschwingungsspannung mit dem
    Klirrfaktor verwechseln - beides existiert in derselben Tabelle.
    """
    assert not items_match(NumericItem(1, "U", "1"), NumericItem(1, "UTHD", "1"))
    assert items_match(NumericItem(1, "UTHD", "1"), NumericItem(1, "UTHD", "1"))


def test_sigma_und_sigmb_gelten_nicht_als_gleich():
    a = NumericItem(1, "P", "SIGMA")
    b = NumericItem(1, "P", "SIGMB")
    assert not items_match(a, b)
    assert items_match(a, NumericItem(1, "P", "SIGM"))


def test_fehlender_order_und_total_sind_gleichwertig():
    """Verifiziert in Stufe 3: gesendet 'U,1' -> zurueckgelesen 'U,1,TOT'."""
    assert items_match(NumericItem(1, "U", "1"), NumericItem(1, "U", "1", "TOT"))
    assert items_match(NumericItem(1, "U", "1", "TOTAL"), NumericItem(1, "U", "1"))


def test_verschiedene_ordnungszahlen_sind_kein_treffer():
    assert not items_match(
        NumericItem(1, "PHI", "1", "1"), NumericItem(1, "PHI", "1", "3")
    )


def test_verschiedene_elemente_sind_kein_treffer():
    assert not items_match(NumericItem(1, "U", "1"), NumericItem(1, "U", "2"))
