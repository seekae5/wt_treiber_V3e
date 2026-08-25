# =============================================================================
# Datei: tests/test_einheiten.py
# Tests der Einheitentabelle und ihrer Ausgabe. Spaltennamen wie 'U1,I1,P1'
# sind ohne Einheiten nicht eindeutig.
#
# Der Kern dieser Datei ist nicht die Tabelle, sondern eine Unterscheidung:
#
#     ""    dimensionslos, und das ist bekannt   (LAMBDA ist ein Verhaeltnis)
#     None  Einheit nicht belegt
#
# Beides sieht in einer schlampigen Umsetzung gleich aus - naemlich leer. Die
# Tests halten den Unterschied bis in die Ausgabedatei fest, weil eine
# geratene Einheit an einem Messwert schlimmer ist als eine fehlende: sie
# wird geglaubt.
# =============================================================================

from __future__ import annotations

import csv
import json

from wt3000_scpi.wt3000_core import WTConfig, WTSession
from wt3000_scpi.wt3000_measure import run_measurement_loop, write_metadata
from wt3000_scpi.wt3000_numeric import FUNCTION_UNITS, ItemTable, NumericItem, unit_of
from wt3000_scpi.wt3000_sinks import CsvSink, JsonlSink
from wt3000_scpi.wt3000_transport import FakeTransport, float_block


# ---------------------------------------------------------------------------
# 1 - Die Zuordnung selbst
# ---------------------------------------------------------------------------


def test_grundgroessen_und_integrationsgroessen():
    """Die belegten Faelle - Handbuch bzw. INTEGRATION_FUNCTIONS."""
    assert unit_of("U") == "V"
    assert unit_of("I") == "A"
    assert unit_of("P") == "W"
    assert unit_of("S") == "VA"
    assert unit_of("Q") == "var"
    assert unit_of("FU") == "Hz"
    assert unit_of("TIME") == "s"
    assert unit_of("WH") == "Wh"
    assert unit_of("AHM") == "Ah"
    assert unit_of("WS") == "VAh"
    assert unit_of("WQ") == "varh"


def test_dimensionslos_ist_nicht_unbekannt():
    """Die Unterscheidung, um die es geht."""
    assert unit_of("LAMBDA") == ""      # bekannt: ein Verhaeltnis
    assert unit_of("UTHD") is None      # nicht belegt
    assert "" != None                    # noqa: E711 - zur Betonung


def test_oberschwingungsfaktoren_bleiben_ausdruecklich_offen():
    """Der Kommandoauszug im Projekt enthaelt keine Einheitentabelle.

    Ein plausibles '%' waere geraten. Solange es nicht belegt ist, sagt der
    Treiber 'nicht bekannt' - das ist die Aussage, die stimmt.
    """
    for funktion in ("UTHD", "ITHD", "PTHD", "UTHF", "ITHF", "UTIF", "ITIF", "HVF", "HCF"):
        assert unit_of(funktion) is None, funktion
        assert funktion not in FUNCTION_UNITS


def test_schreibweise_ist_egal():
    assert unit_of("u") == "V"
    assert unit_of(" Wh ") == "Wh"


def test_unbekannte_funktion_wird_nicht_erfunden():
    assert unit_of("GIBTSNICHT") is None


# ---------------------------------------------------------------------------
# 2 - Am Item und an der Tabelle
# ---------------------------------------------------------------------------


def test_einheit_haengt_an_der_funktion_nicht_am_element():
    """'U1' und 'USIGMA' sind beide Volt."""
    assert NumericItem(1, "U", "1").unit == "V"
    assert NumericItem(2, "U", "SIGMA").unit == "V"
    assert NumericItem(3, "P", "4").unit == "W"


def test_none_item_hat_keine_einheit():
    assert NumericItem(1, "NONE").unit is None


def test_tabelle_liefert_einheiten_in_spaltenreihenfolge():
    tabelle = ItemTable.from_response("4;U,1;I,1;LAMBDA,1;UTHD,1")

    assert [i.key for i in tabelle.items] == ["U1", "I1", "LAMBDA1", "UTHD1"]
    assert tabelle.units() == ("V", "A", "", None)
    assert tabelle.unit_map() == {"U1": "V", "I1": "A", "LAMBDA1": "", "UTHD1": None}


# ---------------------------------------------------------------------------
# 3 - Bis in die Ausgabe
# ---------------------------------------------------------------------------


def antworten(zyklen: int = 1) -> dict:
    bloecke = [float_block([230.0 + n, 1.0, 0.99, 3.5]) for n in range(zyklen)]
    return {
        ":NUMeric:NORMal?": "4;U,1;I,1;LAMBDA,1;UTHD,1",
        ":NUMeric:HOLD?": "0",
        ":NUMeric:NORMal:VALue?": bloecke,
        ":RATE?": "1.000E+00",
    }


def messen(sink, zyklen: int = 1):
    transport = FakeTransport(antworten(zyklen))
    sess = WTSession(transport, WTConfig())
    tabelle = ItemTable.read_from_device(sess)
    run_measurement_loop(
        session=sess,
        table=tabelle,
        sink=sink,
        interval_s=0.0,
        max_samples=zyklen,
        max_duration_s=None,
        use_hold=True,
        record_condition=False,
        log_every=0,
    )
    return tabelle


def test_jsonl_traegt_die_einheiten_ohne_zutun(tmp_path):
    """JSONL ist selbstbeschreibend - dort gehoeren sie hin, immer."""
    ziel = tmp_path / "messung.jsonl"
    messen(JsonlSink(ziel))

    kopf = json.loads(ziel.read_text(encoding="utf-8").splitlines()[0])
    assert kopf["kind"] == "metadata"
    assert kopf["metadata"]["units"] == {
        "U1": "V",
        "I1": "A",
        "LAMBDA1": "",
        "UTHD1": None,   # in JSON: null - und das ist die richtige Aussage
    }


def test_csv_bleibt_ohne_einheitenzeile_unveraendert(tmp_path):
    """Voreinstellung: eine Kopfzeile, dann Daten. Kein bestehender
    Auswerteablauf darf von dieser Aenderung etwas merken."""
    ziel = tmp_path / "ohne.csv"
    messen(CsvSink(ziel))

    zeilen = list(csv.reader(ziel.open(encoding="utf-8")))
    assert zeilen[0][4:-1] == ["U1", "I1", "LAMBDA1", "UTHD1"]
    # Zeile 1 ist bereits ein Datensatz.
    assert zeilen[1][2] == "1"


def test_csv_mit_einheitenzeile(tmp_path):
    ziel = tmp_path / "mit.csv"
    messen(CsvSink(ziel, unit_row=True))

    zeilen = list(csv.reader(ziel.open(encoding="utf-8")))
    assert zeilen[0][4:-1] == ["U1", "I1", "LAMBDA1", "UTHD1"]
    # Die Einheitenzeile - und in ihr die Unterscheidung.
    assert zeilen[1][4:-1] == ["V", "A", "", "?"]
    assert zeilen[1][1] == "s", "elapsed_s traegt seine eigene Einheit"
    # Danach erst die Daten.
    assert zeilen[2][2] == "1"


def test_einheitenzeile_hat_dieselbe_breite_wie_der_kopf(tmp_path):
    """Sonst verrutscht sie beim Einlesen - der teuerste Fehler bei Messdaten."""
    ziel = tmp_path / "breite.csv"
    messen(CsvSink(ziel, unit_row=True), zyklen=2)

    zeilen = list(csv.reader(ziel.open(encoding="utf-8")))
    breiten = {len(z) for z in zeilen}
    assert len(breiten) == 1, f"uneinheitliche Spaltenzahl: {breiten}"


def test_sidecar_traegt_die_einheiten(tmp_path):
    """Die Datei, aus der eine Messreihe spaeter interpretiert wird."""
    transport = FakeTransport(
        {
            **antworten(),
            "*IDN?": "YOKOGAWA,WT3000,C1B234567,F2.11",
            ":COMMunicate?": "0,0,0",
            ":RATE?": "1.000E+00",
            ":NUMeric:FORMat?": "FLOat",
            ":INPut?": "ELEMENT1,1000V",
            ":INPut:WIRing?": "V3A3,P1W2",
            ":INPut:MODUle?": "30,30,30,30",
            ":INPut:SCALing?": "0,0,0,0",
            ":INPut:FILTer?": "OFF,OFF,OFF,OFF",
            ":INPut:CFACtor?": "3",
            ":MEASure?": "NORMAL",
        }
    )
    sess = WTSession(transport, WTConfig())
    tabelle = ItemTable.read_from_device(sess)
    ziel = tmp_path / "messung.meta.json"

    write_metadata(ziel, sess, tabelle, parameters={"sample_interval_s": 1.0})

    daten = json.loads(ziel.read_text(encoding="utf-8"))
    assert daten["units"] == {"U1": "V", "I1": "A", "LAMBDA1": "", "UTHD1": None}


def test_aufrufer_kann_die_einheiten_ueberschreiben(tmp_path):
    """Wie bei 'update_rate_s': die Angabe des Aufrufers ist die aeltere Zusage.

    Gebraucht wird das fuer die noch nicht belegten Funktionen - wer die
    Einheit seiner Oberschwingungsgroessen kennt, soll sie eintragen koennen,
    ohne auf ROADMAP M4-3 zu warten.
    """
    ziel = tmp_path / "eigen.csv"
    transport = FakeTransport(antworten())
    sess = WTSession(transport, WTConfig())
    tabelle = ItemTable.read_from_device(sess)

    run_measurement_loop(
        session=sess,
        table=tabelle,
        sink=CsvSink(ziel, unit_row=True),
        interval_s=0.0,
        max_samples=1,
        max_duration_s=None,
        use_hold=True,
        record_condition=False,
        log_every=0,
        metadata={"units": {"U1": "kV", "I1": "A", "LAMBDA1": "", "UTHD1": "%"}},
    )

    zeilen = list(csv.reader(ziel.open(encoding="utf-8")))
    assert zeilen[1][4:-1] == ["kV", "A", "", "%"]
