# =============================================================================
# Datei: tests/test_computation.py
# Geraetefreie Tests der Rechengruppe ':MEASure'.
#
# Der Schwerpunkt liegt woanders als bei der Integration. Dort ging es um
# Zustandsuebergaenge und darum, dass ein Lauf zuverlaessig endet. Hier geht es
# um GUELTIGE PARAMETER: fast jedes Kommando dieser Gruppe hat einen
# Wertebereich, der von etwas anderem abhaengt -
#
#   * die Averaging-Zahl von der Averaging-ART (2..64 gegen 8..256),
#   * 'P<x>'/'U<x>' von der bestueckten Elementliste,
#   * 'PB' von der Elementzahl, 'PM' von der Motorvariante,
#   * TYPE3 von der Rechenoption /G6.
#
# Die Tests stellen insbesondere sicher, dass unzulaessige Werte das Geraet
# gar nicht erreichen.
# =============================================================================

from __future__ import annotations

import pytest
from conftest import base_responses, computation_responses

from wt3000_scpi import WT3000, WTConfig, WTError
from wt3000_scpi.wt3000_core import WTSession
from wt3000_scpi.wt3000_deviceconfig import (
    AveragingType,
    ComputationConfig,
    ConfigLocked,
    EfficiencyEquation,
    SQFormula,
    SyncMode,
    parse_efficiency,
)
from wt3000_scpi.wt3000_transport import FakeTransport


def comp(transport: FakeTransport, allow_changes: bool = True, **kwargs) -> ComputationConfig:
    """ComputationConfig auf einer schreibfaehigen Sitzung."""
    session = WTSession(transport, WTConfig(use_remote=False), read_only=False)
    return ComputationConfig(session, allow_changes=allow_changes, **kwargs)


def gesendet(transport: FakeTransport) -> list[str]:
    """Nur die Set-Kommandos - Abfragen interessieren hier nicht."""
    return [c for c in transport.written if not c.endswith("?")]


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


def test_momentaufnahme_liest_die_ganze_gruppe():
    cfg = comp(FakeTransport(computation_responses()), allow_changes=False)
    snapshot = cfg.capture()
    assert snapshot.averaging.enabled is False
    assert snapshot.averaging.type is AveragingType.EXPONENT
    assert snapshot.averaging.count == 8
    assert snapshot.frequency_items == ("U3", "I3")
    assert all(not eq.enabled for eq in snapshot.efficiency)
    assert snapshot.sq_formula is SQFormula.TYPE1
    assert snapshot.sync_mode is SyncMode.MASTER


def test_kurzformen_werden_auch_hier_verstanden():
    """Dieselbe Regel wie bei ':INTEGrate' - das Geraet kuerzt seine Antworten."""
    cfg = comp(
        FakeTransport(computation_responses(avg_type="EXP", sq="TYPE2", sync="MAST")),
        allow_changes=False,
    )
    assert cfg.averaging().type is AveragingType.EXPONENT
    assert cfg.sq_formula() is SQFormula.TYPE2
    assert cfg.sync_mode() is SyncMode.MASTER


def test_steckbrief_der_gruppe_sagt_auch_im_aus_zustand_etwas():
    """Eine abgeschaltete Mittelung hat trotzdem eine eingestellte Art und Zahl."""
    cfg = comp(
        FakeTransport(computation_responses(avg_state="0", avg_count="32")),
        allow_changes=False,
    )
    zeile = cfg.averaging().describe()
    assert "aus" in zeile
    assert "32" in zeile

    cfg = comp(
        FakeTransport(computation_responses(avg_state="1", avg_count="32")),
        allow_changes=False,
    )
    zeile = cfg.averaging().describe()
    assert "EIN" in zeile
    assert "Daempfungskonstante" in zeile


def test_beschreibung_nennt_aktive_wirkungsgradgleichungen():
    cfg = comp(
        FakeTransport(computation_responses(eta1="PB,PA")),
        allow_changes=False,
    )
    text = "\n".join(cfg.capture().describe())
    assert "eta1=PB / PA" in text
    assert "U3" in text


# ---------------------------------------------------------------------------
# Averaging - die Abhaengigkeit zwischen Art und Zahl
# ---------------------------------------------------------------------------


def test_averaging_zahl_wird_gegen_die_art_geprueft():
    """128 ist bei LINear richtig und bei EXPonent falsch (Handbuch 6-76)."""
    transport = FakeTransport(computation_responses())
    with pytest.raises(WTError) as fehler:
        comp(transport).set_averaging(True, AveragingType.EXPONENT, 128)
    assert "128" in str(fehler.value)
    # Und zwar bevor irgendetwas gesendet wurde.
    assert gesendet(transport) == []


def test_dieselbe_zahl_ist_bei_der_anderen_art_zulaessig():
    transport = FakeTransport(
        computation_responses(avg_type="LINEAR", avg_count="128", avg_state="1")
    )
    comp(transport).set_averaging(True, AveragingType.LINEAR, 128)
    assert ":MEASure:AVERaging:TYPE LINear" in transport.written
    assert ":MEASure:AVERaging:COUNt 128" in transport.written
    assert ":MEASure:AVERaging:STATe ON" in transport.written


def test_reihenfolge_ist_art_dann_zahl_dann_schalter():
    """Sonst laeuft man durch einen Zwischenzustand, den das Geraet ablehnt."""
    transport = FakeTransport(
        computation_responses(avg_type="LINEAR", avg_count="256", avg_state="1")
    )
    comp(transport).set_averaging(True, AveragingType.LINEAR, 256)
    befehle = gesendet(transport)
    assert befehle.index(":MEASure:AVERaging:TYPE LINear") < befehle.index(
        ":MEASure:AVERaging:COUNt 256"
    )
    assert befehle.index(":MEASure:AVERaging:COUNt 256") < befehle.index(
        ":MEASure:AVERaging:STATe ON"
    )


def test_zahl_ohne_art_wird_gegen_die_eingestellte_art_geprueft():
    """Der haeufige Fall: nur die Zahl aendern, Art bleibt stehen."""
    transport = FakeTransport(computation_responses(avg_type="EXPONENT"))
    with pytest.raises(WTError):
        comp(transport).set_averaging(True, count=128)
    assert gesendet(transport) == []


def test_abschalten_laesst_art_und_zahl_unangetastet():
    transport = FakeTransport(computation_responses(avg_state="0"))
    comp(transport).set_averaging(False)
    assert gesendet(transport) == [":MEASure:AVERaging:STATe OFF"]


def test_unbekannte_art_erreicht_das_geraet_nicht():
    transport = FakeTransport(computation_responses())
    with pytest.raises(WTError):
        comp(transport).set_averaging(True, "GLEITEND", 8)
    assert gesendet(transport) == []


def test_averaging_kontextmanager_stellt_alles_zurueck():
    """Ein- und Ausschalten reicht nicht - Art und Zahl gehoeren dazu."""

    class Umschaltend(FakeTransport):
        """Merkt sich, was geschrieben wurde, und antwortet entsprechend."""

        def __init__(self):
            self.zustand = {"state": "1", "type": "LINEAR", "count": "64"}
            responses = computation_responses()
            responses[":MEASURE:AVERAGING:STATE"] = lambda _c: self.zustand["state"]
            responses[":MEASURE:AVERAGING:TYPE"] = lambda _c: self.zustand["type"]
            responses[":MEASURE:AVERAGING:COUNT"] = lambda _c: self.zustand["count"]
            super().__init__(responses)

        def write(self, command: str) -> None:
            super().write(command)
            node, _, argument = command.strip().partition(" ")
            key = node.upper()
            if key == ":MEASURE:AVERAGING:STATE":
                self.zustand["state"] = "1" if argument == "ON" else "0"
            elif key == ":MEASURE:AVERAGING:TYPE":
                self.zustand["type"] = argument.upper()
            elif key == ":MEASURE:AVERAGING:COUNT":
                self.zustand["count"] = argument

    transport = Umschaltend()
    cfg = comp(transport)
    with cfg.averaging_disabled():
        assert cfg.averaging().enabled is False
    danach = cfg.averaging()
    assert danach.enabled is True
    assert danach.type is AveragingType.LINEAR
    assert danach.count == 64


def test_averaging_kontextmanager_schreibt_nichts_wenn_ohnehin_aus():
    transport = FakeTransport(computation_responses(avg_state="0"))
    cfg = comp(transport)
    with cfg.averaging_disabled():
        pass
    assert gesendet(transport) == []


# ---------------------------------------------------------------------------
# Frequenzmessquelle
# ---------------------------------------------------------------------------


def test_frequenzquelle_lesen():
    """Die Einstellung, die im Standardprofil bisher nur als Kommentar stand."""
    cfg = comp(FakeTransport(computation_responses()), allow_changes=False)
    assert cfg.frequency_item(1) == "U3"
    assert cfg.frequency_item(2) == "I3"


def test_frequenzquelle_setzen():
    transport = FakeTransport(computation_responses(freq1="U1"))
    comp(transport).set_frequency_item(1, "U1")
    assert ":MEASure:FREQuency:ITEM1 U1" in transport.written


def test_frequenzquelle_auf_unbestuecktem_element_wird_abgewiesen():
    transport = FakeTransport(computation_responses())
    cfg = comp(transport, elements=(1, 2, 3))
    with pytest.raises(WTError) as fehler:
        cfg.set_frequency_item(1, "U4")
    assert "4" in str(fehler.value)
    assert gesendet(transport) == []


def test_unsinnige_frequenzquelle_wird_abgewiesen():
    transport = FakeTransport(computation_responses())
    for quelle in ("P1", "U", "3", "UU3"):
        with pytest.raises(WTError):
            comp(transport).set_frequency_item(1, quelle)
    assert gesendet(transport) == []


def test_es_gibt_nur_zwei_frequenzmessitems():
    cfg = comp(FakeTransport(computation_responses()), allow_changes=False)
    with pytest.raises(WTError):
        cfg.frequency_item(3)


# ---------------------------------------------------------------------------
# Wirkungsgrad
# ---------------------------------------------------------------------------


def test_wirkungsgradgleichung_wird_zerlegt():
    assert parse_efficiency("PB,PA") == EfficiencyEquation("PB", "PA")
    assert parse_efficiency("OFF") == EfficiencyEquation.off()
    assert parse_efficiency("OFF").enabled is False


def test_fehlender_zaehler_heisst_eins_und_nicht_unbekannt():
    """Handbuch 6-78: der Zaehler wird weggelassen, wenn er 1 ist."""
    gleichung = parse_efficiency("PA")
    assert gleichung.numerator is None
    assert gleichung.denominator == "PA"
    assert gleichung.enabled is True
    assert gleichung.describe() == "1 / PA"
    # Und beim Senden wird er ebenso weggelassen.
    assert gleichung.as_parameter() == "PA"


def test_wirkungsgradgleichung_setzen():
    transport = FakeTransport(computation_responses(eta1="P1,PA"))
    comp(transport).set_efficiency(1, "P1", "PA")
    assert ":MEASure:EFFiciency:ETA1 P1,PA" in transport.written


def test_wirkungsgradgleichung_abschalten():
    transport = FakeTransport(computation_responses(eta2="OFF"))
    comp(transport).set_efficiency(2, denominator=None)
    assert ":MEASure:EFFiciency:ETA2 OFF" in transport.written


def test_pb_verlangt_vier_elemente():
    transport = FakeTransport(computation_responses())
    cfg = comp(transport, elements=(1, 2, 3))
    with pytest.raises(WTError) as fehler:
        cfg.set_efficiency(1, "PB", "PA")
    assert "PB" in str(fehler.value)
    assert gesendet(transport) == []


def test_pm_verlangt_die_motorvariante():
    transport = FakeTransport(computation_responses())
    with pytest.raises(WTError) as fehler:
        comp(transport, motor=False).set_efficiency(1, "PM", "PA")
    assert "PM" in str(fehler.value)
    assert gesendet(transport) == []


def test_pm_ist_auf_dem_motorgeraet_zulaessig():
    transport = FakeTransport(computation_responses(eta1="PM,PA"))
    comp(transport, motor=True).set_efficiency(1, "PM", "PA")
    assert ":MEASure:EFFiciency:ETA1 PM,PA" in transport.written


def test_unbekannte_motoreigenschaft_sperrt_nicht():
    """Dieselbe Regel wie in DeviceInfo.supports(): unbekannt ist nicht 'fehlt'."""
    transport = FakeTransport(computation_responses(eta1="PM,PA"))
    comp(transport, motor=None).set_efficiency(1, "PM", "PA")
    assert ":MEASure:EFFiciency:ETA1 PM,PA" in transport.written


def test_unsinniges_glied_wird_abgewiesen():
    transport = FakeTransport(computation_responses())
    with pytest.raises(WTError):
        comp(transport).set_efficiency(1, "WATT", "PA")
    assert gesendet(transport) == []


def test_es_gibt_nur_vier_wirkungsgradgleichungen():
    cfg = comp(FakeTransport(computation_responses()), allow_changes=False)
    with pytest.raises(WTError):
        cfg.efficiency(5)


# ---------------------------------------------------------------------------
# Formelsatz und Synchronisation
# ---------------------------------------------------------------------------


def test_type3_verlangt_die_rechenoption():
    """Handbuch 6-80: TYPE3 nur mit /G6 - hier zahlt sich der Optionscheck aus."""
    transport = FakeTransport(computation_responses())
    with pytest.raises(WTError) as fehler:
        comp(transport, advanced_computation=False).set_sq_formula(SQFormula.TYPE3)
    assert "G6" in str(fehler.value)
    assert gesendet(transport) == []


def test_type3_geht_mit_der_option():
    transport = FakeTransport(computation_responses(sq="TYPE3"))
    comp(transport, advanced_computation=True).set_sq_formula(SQFormula.TYPE3)
    assert ":MEASure:SQFormula TYPE3" in transport.written


def test_unbekannte_option_sperrt_type3_nicht():
    transport = FakeTransport(computation_responses(sq="TYPE3"))
    comp(transport, advanced_computation=None).set_sq_formula("TYPE3")
    assert ":MEASure:SQFormula TYPE3" in transport.written


def test_type1_braucht_keine_option():
    transport = FakeTransport(computation_responses(sq="TYPE1"))
    comp(transport, advanced_computation=False).set_sq_formula(SQFormula.TYPE1)
    assert ":MEASure:SQFormula TYPE1" in transport.written


def test_synchronisationsrolle_setzen():
    transport = FakeTransport(computation_responses(sync="SLAVE"))
    comp(transport).set_sync_mode(SyncMode.SLAVE)
    assert ":MEASure:SYNChronize SLAVe" in transport.written


# ---------------------------------------------------------------------------
# Sperre und Wiederherstellung
# ---------------------------------------------------------------------------


def test_ohne_allow_changes_geht_kein_kommando_raus():
    transport = FakeTransport(computation_responses())
    cfg = comp(transport, allow_changes=False)
    for aufruf in (
        lambda: cfg.set_averaging(True, AveragingType.EXPONENT, 8),
        lambda: cfg.set_frequency_item(1, "U1"),
        lambda: cfg.set_efficiency(1, "P1", "PA"),
        lambda: cfg.set_sq_formula(SQFormula.TYPE2),
        lambda: cfg.set_sync_mode(SyncMode.SLAVE),
    ):
        with pytest.raises(ConfigLocked):
            aufruf()
    assert gesendet(transport) == []


def test_momentaufnahme_laesst_sich_zurueckschreiben():
    """Vorlage fuer den gemeinsamen SessionBackup aus M2-4."""
    tabelle = computation_responses(
        avg_state="1", avg_type="LINEAR", avg_count="32", eta1="P1,PA", sq="TYPE2", sync="SLAVE"
    )
    transport = FakeTransport(tabelle)
    cfg = comp(transport)
    snapshot = cfg.capture()
    cfg.restore(snapshot)

    befehle = gesendet(transport)
    assert ":MEASure:AVERaging:TYPE LINear" in befehle
    assert ":MEASure:AVERaging:COUNt 32" in befehle
    assert ":MEASure:AVERaging:STATe ON" in befehle
    assert ":MEASure:EFFiciency:ETA1 P1,PA" in befehle
    assert ":MEASure:SQFormula TYPE2" in befehle
    assert ":MEASure:SYNChronize SLAVe" in befehle


# ---------------------------------------------------------------------------
# Anbindung an die Fassade
# ---------------------------------------------------------------------------


def facade_responses() -> dict:
    responses = base_responses()
    responses.update(computation_responses())
    return responses


def test_fassade_reicht_die_gruppe_durch():
    with WT3000.from_transport(
        FakeTransport(facade_responses()), WTConfig(use_remote=False)
    ) as wt:
        assert wt.computation.averaging().enabled is False
        assert wt.computation is wt.computation


def test_fassade_fuellt_elementliste_und_faehigkeiten_aus_dem_steckbrief():
    """Der Nutzen des Steckbriefs aus M1-3, an einer konkreten Stelle.

    Das Modellgeraet der Suite meldet '/G6' und traegt KEINE Motorvariante -
    TYPE3 ist damit erlaubt, 'PM' nicht. Beides weiss das Fachmodul nur,
    weil die Fassade es hineinreicht.
    """
    # Schreibfaehig geoeffnet, sonst greift die Sperre VOR der
    # Faehigkeitspruefung und der Pruefsatz belegte nicht, was er behauptet.
    transport = FakeTransport(facade_responses())
    with WT3000.from_transport(
        transport, WTConfig(use_remote=False), read_only=False, allow_changes=True
    ) as wt:
        assert wt.computation.elements == (1, 2, 3, 4)
        assert wt.device.has_option("G6") is True
        assert wt.device.is_motor_model is False

        # 'PM' faellt, weil der Steckbrief keine Motorvariante meldet ...
        with pytest.raises(WTError) as fehler:
            wt.computation.set_efficiency(1, "PM", "PA")
        assert "PM" in str(fehler.value)
        assert ":MEASure:EFFiciency:ETA1 PM,PA" not in transport.written

        # ... TYPE3 dagegen geht, weil er '/G6' meldet.
        transport.responses[transport._key(":MEASURE:SQFORMULA")] = "TYPE3"
        wt.computation.set_sq_formula(SQFormula.TYPE3)
        assert ":MEASure:SQFormula TYPE3" in transport.written


def test_fassade_gibt_die_schreibsperre_weiter():
    with WT3000.from_transport(
        FakeTransport(facade_responses()), WTConfig(use_remote=False)
    ) as wt:
        assert wt.computation.allow_changes is False

    with WT3000.from_transport(
        FakeTransport(facade_responses()),
        WTConfig(use_remote=False),
        read_only=False,
        allow_changes=True,
    ) as wt:
        assert wt.computation.allow_changes is True
