# =============================================================================
# Datei: tests/test_sample.py
# Tests der drei Zusagen des Datensatztyps Sample:
#   1. Er traegt alles, was ein Zyklus hergibt, und ist unveraenderlich.
#   2. 'status_flags()' ist die gemeinsame Grundlage jedes Ausgabeformats -
#      Einzelwert-Status UND Kennzeichnung des Zyklus, in einer Liste.
#   3. Die CSV entsteht aus ihm und prueft weiterhin die Spaltenlaenge.
# =============================================================================

from __future__ import annotations

import csv
import dataclasses
from datetime import datetime, timezone

import pytest

from wt3000_scpi.wt3000_measure import Sample, SampleMark
from wt3000_scpi.wt3000_sinks import CsvSink
from wt3000_scpi.wt3000_numeric import NumericValue, ValueStatus


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------


def wert(zahl: float, status: ValueStatus = ValueStatus.OK) -> NumericValue:
    return NumericValue(zahl, status, 0)


def datensatz(*, values=None, mark=SampleMark.OK, number=1, condition=None) -> Sample:
    """Ein Sample mit unauffaelligen Vorgaben - der Test setzt nur, was er prueft."""
    return Sample(
        timestamp=datetime(2026, 8, 20, 12, 0, 0, 500000, tzinfo=timezone.utc),
        elapsed_s=1.25,
        number=number,
        condition=condition,
        values=list(values if values is not None else [wert(230.0), wert(1.0), wert(230.0)]),
        mark=mark,
    )


# ---------------------------------------------------------------------------
# 1 - Der Typ selbst
# ---------------------------------------------------------------------------


def test_traegt_alle_bestandteile_eines_zyklus():
    """Die fuenf frueheren Einzelparameter plus die Kennzeichnung."""
    s = datensatz(number=7, condition=16)
    assert s.number == 7
    assert s.condition == 16
    assert s.elapsed_s == 1.25
    assert s.timestamp.tzinfo is not None  # Zeitstempel sind zeitzonenbehaftet
    assert len(s.values) == 3
    assert s.mark is SampleMark.OK  # Vorgabe, ohne Zutun des Aufrufers


def test_ist_unveraenderlich():
    """Ein gelesener Zyklus aendert sich nicht mehr - sonst driften Datei und Objekt."""
    s = datensatz()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.number = 2  # type: ignore[misc]


def test_kennzeichnung_ist_optional_und_nachtraeglich_setzbar():
    """'mark' wird ueber replace(), nicht durch Zuweisung gesetzt."""
    s = datensatz()
    markiert = dataclasses.replace(s, mark=SampleMark.DUPLICATE)
    assert markiert.mark is SampleMark.DUPLICATE
    assert s.mark is SampleMark.OK  # das Original bleibt unberuehrt
    assert markiert.values == s.values


# ---------------------------------------------------------------------------
# 2 - status_flags()
# ---------------------------------------------------------------------------


def test_ohne_auffaelligkeit_keine_flags():
    assert datensatz().status_flags(["U1", "I1", "P1"]) == []


def test_nicht_ok_werte_werden_mit_spaltennamen_benannt():
    """Die Zuordnung Wert -> Spalte ist die einzige Stelle, an der sie entsteht."""
    s = datensatz(
        values=[
            wert(230.0),
            wert(float("inf"), ValueStatus.OVERRANGE),
            wert(float("nan"), ValueStatus.NO_DATA),
        ]
    )
    assert s.status_flags(["U1", "I1", "P1"]) == ["I1=OVERRANGE", "P1=NO_DATA"]


def test_kennzeichnung_steht_vor_den_einzelwerten():
    """Bei MISSING ist sie die einzige Angabe, die es ueberhaupt gibt."""
    s = datensatz(
        values=[wert(230.0), wert(float("inf"), ValueStatus.OVERRANGE)],
        mark=SampleMark.DUPLICATE,
    )
    assert s.status_flags(["U1", "I1"]) == ["mark=DUPLICATE", "I1=OVERRANGE"]


def test_ausgefallener_zyklus_ohne_werte_meldet_nur_die_kennzeichnung():
    """Eine Zeile ohne Messwerte macht einen ausgefallenen Zyklus sichtbar."""
    s = datensatz(values=[], mark=SampleMark.MISSING)
    assert s.status_flags(["U1", "I1", "P1"]) == ["mark=MISSING"]


def test_kuerzere_spaltenliste_laesst_ueberzaehlige_werte_unerwaehnt():
    """Dokumentiertes Verhalten: zip bricht am kuerzeren Ende ab.

    Die Laengenpruefung gehoert an die schreibende Stelle, die den Spaltenkopf
    kennt - nicht hierher. Der eigene Test haelt fest, dass das Absicht ist
    und nicht bloss unbemerkt so herauskommt.
    """
    s = datensatz(values=[wert(1.0), wert(float("inf"), ValueStatus.OVERRANGE)])
    assert s.status_flags(["U1"]) == []


# ---------------------------------------------------------------------------
# 3 - Zusammenspiel mit der CSV
# ---------------------------------------------------------------------------


def test_csv_zeile_entsteht_vollstaendig_aus_dem_sample(tmp_path):
    ziel = tmp_path / "eine_zeile.csv"
    with CsvSink(ziel) as recorder:
        recorder.open(["U1", "I1", "P1"])
        recorder.write(datensatz(number=3, condition=16))

    kopf, zeile = list(csv.reader(ziel.open(encoding="utf-8")))
    assert kopf == [
        "timestamp_iso",
        "elapsed_s",
        "sample",
        "condition",
        "U1",
        "I1",
        "P1",
        "status_flags",
    ]
    assert zeile[0].startswith("2026-08-20T12:00:00.500")
    assert zeile[1] == "1.250"
    assert zeile[2] == "3"
    assert zeile[3] == "16"
    assert zeile[-1] == ""  # keine Auffaelligkeit


def test_kennzeichnung_landet_in_der_flag_spalte_ohne_neue_spalte(tmp_path):
    """Zyklusmarken brauchen dadurch kein geaendertes Dateiformat."""
    ziel = tmp_path / "dublette.csv"
    with CsvSink(ziel) as recorder:
        recorder.open(["U1", "I1", "P1"])
        recorder.write(datensatz(mark=SampleMark.DUPLICATE))

    kopf, zeile = list(csv.reader(ziel.open(encoding="utf-8")))
    assert len(zeile) == len(kopf)  # Spaltenzahl unveraendert
    assert zeile[-1] == "mark=DUPLICATE"


def test_fehlendes_condition_bleibt_eine_leere_zelle(tmp_path):
    ziel = tmp_path / "ohne_condition.csv"
    with CsvSink(ziel) as recorder:
        recorder.open(["U1", "I1", "P1"])
        recorder.write(datensatz(condition=None))

    _, zeile = list(csv.reader(ziel.open(encoding="utf-8")))
    assert zeile[3] == ""


def test_laengenpruefung_aus_p3_gilt_weiter(tmp_path):
    """Sample darf die Absicherung der Spaltenlaenge nicht verlieren."""
    ziel = tmp_path / "verrutscht.csv"
    with CsvSink(ziel) as recorder:
        recorder.open(["U1", "I1", "P1"])
        with pytest.raises(Exception, match="Sample 4"):
            recorder.write(datensatz(number=4, values=[wert(1.0)]))

    # Nur der Kopf steht in der Datei - keine halbe Zeile.
    assert len(list(csv.reader(ziel.open(encoding="utf-8")))) == 1


# ---------------------------------------------------------------------------
# 4 - Erreichbarkeit
# ---------------------------------------------------------------------------


def test_aus_der_paketwurzel_importierbar():
    """Eigene Senken sollen den Typ an der Paketwurzel finden."""
    import wt3000_scpi

    assert wt3000_scpi.Sample is Sample
    assert wt3000_scpi.SampleMark is SampleMark
    assert "Sample" in wt3000_scpi.__all__
    assert "SampleMark" in wt3000_scpi.__all__
