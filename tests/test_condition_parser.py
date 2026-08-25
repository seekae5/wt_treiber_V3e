# =============================================================================
# Datei: tests/test_condition_parser.py
# Parsertests fuer numerische Geraeteantworten. Header, leere oder mehrteilige
# Antworten muessen als WTError mit Kontext erscheinen, besonders innerhalb
# einer laufenden Messschleife.
#
# UEBER DIE ANALYSE HINAUS: beim Anfassen dieser sechs Stellen faellt auf, dass
# die AUSWERTUNG der Condition-Bits viermal im Bestand liegt - stage2, stage3,
# stage4 und wt3000_device.log_condition(), in leicht abweichenden Fassungen.
# Stufe 2 und 3 sowie die Fassade kannten Bit 15 (POV) nicht, Stufe 4 schon.
# Das ist Befund S-02 an einer weiteren Stelle; die Auswertung liegt jetzt
# einmal in wt3000_common.
# =============================================================================

from __future__ import annotations

import pytest

from wt3000_scpi.wt3000_common import condition_warnings, parse_condition, parse_nr1
from wt3000_scpi.wt3000_core import WTError

# ---------------------------------------------------------------------------
# parse_nr1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "antwort, erwartet",
    [
        ("0", 0),
        ("16", 16),
        ("  128  ", 128),
        ("+16", 16),
        ("-1", -1),
    ],
)
def test_parse_nr1_liest_ganzzahlen(antwort, erwartet):
    assert parse_nr1(antwort) == erwartet


def test_parse_nr1_entfernt_den_kommandokopf():
    """Der eigentliche Gewinn gegenueber int().

    Hat jemand ':COMMunicate:HEADer 1' gesetzt, antwortet das Geraet mit
    ':STATUS:CONDITION 16'. int() bricht daran ab; strip_response_header()
    kennt die Form laengst.
    """
    assert parse_nr1(":STATUS:CONDITION 16") == 16


@pytest.mark.parametrize("antwort", ["", "   ", "FLOat", "1,2", "16.5"])
def test_parse_nr1_wirft_wterror_statt_valueerror(antwort):
    """Der Kern von A-06: die Ausnahme ist fangbar.

    Alle sieben Skripte fangen ausschliesslich WTError. Ein ValueError lief an
    ihnen vorbei und endete als Traceback.
    """
    with pytest.raises(WTError):
        parse_nr1(antwort)


def test_parse_nr1_nennt_den_kontext():
    """Ohne Kontext ist 'Keine Zahl in der Antwort' nicht zuzuordnen."""
    with pytest.raises(WTError, match="STATus:CONDition"):
        parse_nr1("unsinn", ":STATus:CONDition")


def test_parse_nr1_gibt_die_rohantwort_wieder():
    """Was das Geraet wirklich geschickt hat, gehoert in die Meldung."""
    with pytest.raises(WTError, match="unsinn"):
        parse_nr1("unsinn", "Kontext")


# ---------------------------------------------------------------------------
# parse_condition
# ---------------------------------------------------------------------------


def test_parse_condition_ist_parse_nr1_mit_festem_kontext():
    assert parse_condition("16") == 16
    assert parse_condition(":STATUS:CONDITION 16") == 16


def test_parse_condition_meldet_den_knoten():
    with pytest.raises(WTError, match="CONDition"):
        parse_condition("")


# ---------------------------------------------------------------------------
# condition_warnings
# ---------------------------------------------------------------------------


def test_keine_auffaelligkeit_keine_meldung():
    assert condition_warnings(0) == []


@pytest.mark.parametrize(
    "bits, stichwort",
    [
        (1 << 4, "FOV"),
        (1 << 7, "PLLE"),
        (1 << 8, "Overrange"),
        (1 << 11, "Overrange"),
        (1 << 15, "POV"),
    ],
)
def test_jedes_bit_ergibt_seine_meldung(bits, stichwort):
    meldungen = condition_warnings(bits)
    assert any(stichwort in m for m in meldungen), f"{bits:#x} -> {meldungen}"


def test_mehrere_bits_ergeben_mehrere_meldungen():
    meldungen = condition_warnings((1 << 4) | (1 << 7) | (1 << 15))
    assert len(meldungen) == 3


def test_bit_15_ist_ueberall_dabei():
    """Bit 15 (POV) fehlte in Stufe 2, Stufe 3 und in der Fassade.

    Nur Stufe 4 kannte es. Vier Fassungen derselben Auswertung, drei davon
    unvollstaendig - genau das Muster, das eine gemeinsame Funktion aufloest.
    Ein Peak Over am Eingang ist die Auffaelligkeit, die eine Messreihe
    unbrauchbar macht; sie in drei von vier Skripten zu verschweigen, ist die
    schlechteste der moeglichen Aufteilungen.
    """
    assert condition_warnings(1 << 15)


# ---------------------------------------------------------------------------
# Die Aufrufstellen benutzen die neuen Funktionen
# ---------------------------------------------------------------------------


def test_kein_rohes_int_oder_float_mehr_auf_geraeteantworten():
    """Die sechs Stellen aus A-06, maschinell nachgehalten.

    Der Pruefsatz liest den Quelltext, weil die Alternative - jede der sechs
    Stellen einzeln durchspielen - fuer wt3000_measure eine vollstaendige
    Messschleife braeuchte. Er ist bewusst eng gefasst: gesucht wird genau das
    Muster 'int(...query(' bzw. 'float(...query(', also eine Geraeteantwort,
    die ohne Umweg in einen Zahlkonstruktor geht.
    """
    import re
    from pathlib import Path

    import wt3000_scpi

    muster = re.compile(r"\b(?:int|float)\s*\(\s*[^)]*\.query\s*\(")
    treffer = []
    for pfad in sorted(Path(wt3000_scpi.__file__).parent.glob("*.py")):
        for nummer, zeile in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1):
            if muster.search(zeile):
                treffer.append(f"{pfad.name}:{nummer}: {zeile.strip()}")

    assert not treffer, "Geraeteantwort geht ungeprueft in int()/float():\n" + "\n".join(treffer)
