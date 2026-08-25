# =============================================================================
# Datei: tests/test_input_snapshot.py
# Tests fuer InputSnapshot.diff() und seine gemeinsame Vergleichsregel.
#
# diff() entscheidet, ob eine Wiederherstellung noetig ist, UND ob sie als
# gelungen gilt. Solange Vergleich und Wiederherstellung verschiedene Regeln
# benutzten, konnte der Restore in einen Zustand laufen, den er selbst fuer
# korrekt hielt, waehrend die Schlusskontrolle VerificationError warf.
# =============================================================================

from __future__ import annotations

from conftest import element_settings, input_snapshot

from wt3000_scpi.wt3000_input import (
    MODE_TOKENS,
    SYNC_TOKENS,
    canonical_enum_token,
    enum_match,
)


# ---------------------------------------------------------------------------
# Die Vergleichsregel selbst
# ---------------------------------------------------------------------------


def test_kurzform_wird_auf_die_langform_abgebildet():
    assert canonical_enum_token("EXT", SYNC_TOKENS) == "EXTERNAL"
    assert canonical_enum_token("external", SYNC_TOKENS) == "EXTERNAL"


def test_kurz_und_langform_gelten_als_gleich():
    assert enum_match("EXTERNAL", "EXT", SYNC_TOKENS)
    assert enum_match("EXT", "EXTERNAL", SYNC_TOKENS)


def test_mehrdeutige_kurzform_gilt_als_abweichung():
    """'U' passt auf U1..U4 - ein Treffer waere hier geraten, nicht bekannt."""
    assert canonical_enum_token("U", SYNC_TOKENS) == "U"
    assert not enum_match("U1", "U", SYNC_TOKENS)


def test_verschiedene_kanaele_bleiben_verschieden():
    assert not enum_match("I3", "I2", SYNC_TOKENS)
    assert not enum_match("U3", "I3", SYNC_TOKENS)


def test_messmodi():
    assert enum_match("RMEAN", "RMEA", MODE_TOKENS)
    assert not enum_match("RMS", "MEAN", MODE_TOKENS)
    assert not enum_match("MEAN", "RMEAN", MODE_TOKENS)


# ---------------------------------------------------------------------------
# InputSnapshot.diff
# ---------------------------------------------------------------------------


def test_gleicher_zustand_ergibt_keine_abweichung():
    soll = input_snapshot()
    assert soll.diff(input_snapshot()) == []


def test_kurzform_der_sync_quelle_ist_keine_abweichung():
    """INPUT-13: Soll 'EXTERNAL', Ist 'EXT'.

    Vorher meldete diff() hier eine Abweichung, die restore() nicht aufloeste -
    die Wiederherstellung konnte nicht konvergieren.
    """
    soll = input_snapshot(element_settings(sync_source="EXTERNAL"))
    ist = input_snapshot(element_settings(sync_source="EXT"))
    assert soll.diff(ist) == []


def test_kurzform_des_messmodus_ist_keine_abweichung():
    soll = input_snapshot(element_settings(voltage_mode="RMEAN"))
    ist = input_snapshot(element_settings(voltage_mode="RMEA"))
    assert soll.diff(ist) == []


def test_echte_abweichung_der_sync_quelle_wird_gemeldet():
    soll = input_snapshot(element_settings(sync_source="EXTERNAL"))
    ist = input_snapshot(element_settings(sync_source="I3"))
    (meldung,) = soll.diff(ist)
    assert "Sync" in meldung and "I3" in meldung


def test_bereichsabweichung_wird_gemeldet():
    soll = input_snapshot(element_settings(voltage_range=1000.0))
    ist = input_snapshot(element_settings(voltage_range=600.0))
    assert any("U-Range" in m for m in soll.diff(ist))


def test_rundung_der_vierten_stelle_ist_keine_abweichung():
    """Das Geraet antwortet mit begrenzter Mantisse."""
    soll = input_snapshot(element_settings(voltage_range=1000.0))
    ist = input_snapshot(element_settings(voltage_range=1000.02))
    assert soll.diff(ist) == []


def test_wechsel_von_sensor_auf_direkteingang_wird_gemeldet():
    soll = input_snapshot(element_settings(current_sensor=10.0, current_direct=None))
    ist = input_snapshot(element_settings(current_sensor=None, current_direct=10.0))
    meldungen = soll.diff(ist)
    assert any("I-Range Sensor" in m for m in meldungen)
    assert any("I-Range direkt" in m for m in meldungen)


def test_geraetweite_groessen_werden_verglichen():
    soll = input_snapshot()
    ist = input_snapshot(crest_factor=6, update_rate_s=0.5, wiring=("P3W4",))
    meldungen = soll.diff(ist)
    assert any("Crest" in m for m in meldungen)
    assert any("Wiring" in m for m in meldungen)
    assert any("Update-Rate" in m for m in meldungen)


def test_fehlendes_element_wird_gemeldet():
    soll = input_snapshot(element_settings(element=1), element_settings(element=2))
    ist = input_snapshot(element_settings(element=1))
    (meldung,) = soll.diff(ist)
    assert "Element 2 fehlt" in meldung


def test_diff_ist_verlustfrei_ueber_json(tmp_path):
    """Ein geladener Snapshot muss zum Original passen - sonst laeuft der
    Restore nach einem Neustart des Skripts gegen ein anderes Soll."""
    from wt3000_scpi.wt3000_input import InputSnapshot

    soll = input_snapshot()
    pfad = tmp_path / "snapshot.json"
    soll.save(pfad)
    assert soll.diff(InputSnapshot.load(pfad)) == []
