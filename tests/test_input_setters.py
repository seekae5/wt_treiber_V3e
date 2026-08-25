# =============================================================================
# Datei: tests/test_input_setters.py
# Regressionstests fuer set_sync_source() und
# set_voltage_mode()/set_current_mode().
#
# Hintergrund: Die Korrektur zu INPUT-13 hatte Vergleich (diff) und
# Wiederherstellung (restore) auf EINE Regel gebracht - enum_match(). Der
# Eingang der Setter blieb dabei aber auf exaktem Vergleich gegen die Langform
# stehen. Das Geraet antwortet mit VERBose OFF in Kurzform ('EXT', 'RMEA'),
# InputSnapshot.capture() legt genau diese Kurzform in den Snapshot, und
# restore_input_snapshot() reicht sie unveraendert an den Setter zurueck.
# Ergebnis war ein WTError ueber einen Wert, den das Geraet selbst geliefert
# hatte - und damit eine Wiederherstellung, die nicht durchlaufen konnte.
#
# Die Tests laufen ohne Geraet ueber die FakeSession aus conftest.py.
# =============================================================================

from __future__ import annotations

import pytest
from conftest import FakeSession

from wt3000_scpi.wt3000_core import WTError
from wt3000_scpi.wt3000_input import (
    GROUP_MODE,
    GROUP_SYNC,
    InputConfig,
)


def config_with(responses: dict[str, str]) -> tuple[InputConfig, FakeSession]:
    """Schreibfaehiges InputConfig auf einer FakeSession."""
    session = FakeSession(responses)
    cfg = InputConfig(session, allow_changes=True, protected_groups=frozenset())
    return cfg, session


# ---------------------------------------------------------------------------
# Sync-Quelle
# ---------------------------------------------------------------------------


def test_kurzform_der_sync_quelle_wird_angenommen():
    """'EXT' ist das, was das Geraet meldet - es muss auch hineingehen."""
    cfg, session = config_with({":INPUT:SYNCHRONIZE:ELEMENT1": "EXT"})
    cfg.set_sync_source("EXT", 1)
    assert session.written == [":INPut:SYNChronize:ELEMent1 EXTERNAL"]


def test_langform_der_sync_quelle_bleibt_zulaessig():
    cfg, session = config_with({":INPUT:SYNCHRONIZE:ELEMENT1": "EXT"})
    cfg.set_sync_source("EXTernal", 1)
    assert session.written == [":INPut:SYNChronize:ELEMent1 EXTERNAL"]


def test_eindeutige_kanalangabe_geht_unveraendert_durch():
    cfg, session = config_with({":INPUT:SYNCHRONIZE:ELEMENT3": "I3"})
    cfg.set_sync_source("I3", 3)
    assert session.written == [":INPut:SYNChronize:ELEMent3 I3"]


def test_mehrdeutige_kurzform_wird_weiterhin_abgelehnt():
    """'U' passt auf U1..U4 - hier zu raten waere schlimmer als abzubrechen."""
    cfg, _ = config_with({":INPUT:SYNCHRONIZE:ELEMENT1": "U1"})
    with pytest.raises(WTError):
        cfg.set_sync_source("U", 1)


def test_unbekannte_sync_quelle_wird_weiterhin_abgelehnt():
    cfg, _ = config_with({":INPUT:SYNCHRONIZE:ELEMENT1": "U1"})
    with pytest.raises(WTError):
        cfg.set_sync_source("X9", 1)


# ---------------------------------------------------------------------------
# Messmodus
# ---------------------------------------------------------------------------


def test_kurzform_des_messmodus_wird_angenommen():
    """'RMEA' ist die Geraeteantwort fuer RMEAN."""
    cfg, session = config_with({":INPUT:VOLTAGE:MODE:ELEMENT1": "RMEA"})
    cfg.set_voltage_mode("RMEA", 1)
    assert session.written == [":INPut:VOLTage:MODE:ELEMent1 RMEAN"]


def test_langform_des_messmodus_bleibt_zulaessig():
    cfg, session = config_with({":INPUT:CURRENT:MODE:ELEMENT2": "RMS"})
    cfg.set_current_mode("RMS", 2)
    assert session.written == [":INPut:CURRent:MODE:ELEMent2 RMS"]


def test_unbekannter_messmodus_wird_weiterhin_abgelehnt():
    cfg, _ = config_with({":INPUT:VOLTAGE:MODE:ELEMENT1": "RMS"})
    with pytest.raises(WTError):
        cfg.set_voltage_mode("SPITZENWERT", 1)


# ---------------------------------------------------------------------------
# Die Sperren gelten unveraendert
# ---------------------------------------------------------------------------


def test_gesperrte_gruppe_verhindert_den_schreibzugriff():
    """Die Korrektur zu F-05 lockert keine der beiden Sicherungen."""
    from wt3000_scpi.wt3000_input import ConfigLocked

    session = FakeSession({":INPUT:SYNCHRONIZE:ELEMENT1": "EXT"})
    cfg = InputConfig(
        session, allow_changes=True, protected_groups=frozenset({GROUP_SYNC, GROUP_MODE})
    )
    with pytest.raises(ConfigLocked):
        cfg.set_sync_source("EXT", 1)
    with pytest.raises(ConfigLocked):
        cfg.set_voltage_mode("RMS", 1)
    assert session.written == []
