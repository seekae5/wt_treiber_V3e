# =============================================================================
# Datei: tests/test_deviceconfig.py
# Geraetefreie Tests der Integrationsfunktion. Der Schwerpunkt liegt auf drei
# sicherheits- und geraeterelevanten Fragen:
#
#   1. Kommt ueberhaupt ein Kommando am Geraet an, das nicht ankommen darf?
#      (Sperre, GROUP_RESET, Nur-Lesen-Sitzung)
#   2. Wird ein Lauf zuverlaessig beendet, auch wenn der Block scheitert?
#      (running() und sein finally)
#   3. Verstehen die Parser die Geraete-Kurzformen 'RES' und 'NORM'?
#
# Gefahren wird gegen 'FakeTransport' - also mit WTSession, Nur-Lesen-Sperre
# und Fehlerqueue im Ruecken, nicht gegen eine Attrappe der Sitzung.
# =============================================================================

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import base_responses, integrate_responses

from wt3000_scpi import WT3000, WTConfig, WTError
from wt3000_scpi.wt3000_core import ReadOnlyViolation
from wt3000_scpi.wt3000_deviceconfig import (
    GROUP_INTEGRATE,
    GROUP_RESET,
    GROUP_RUN,
    ConfigLocked,
    IntegrationConfig,
    IntegrationMode,
    IntegrationSettings,
    IntegrationState,
    IntegrationStateError,
    format_datetime,
    format_timer,
    parse_datetime,
    parse_mode,
    parse_state,
    parse_timer,
)
from wt3000_scpi.wt3000_measure import build_integration_profile
from wt3000_scpi.wt3000_transport import FakeTransport


def open_facade(transport: FakeTransport, **kwargs) -> WT3000:
    """Fassade auf einem Fake-Transport, ohne Fernsteuerung."""
    kwargs.setdefault("read_only", True)
    return WT3000.from_transport(transport, WTConfig(use_remote=False), **kwargs)


def integ(transport: FakeTransport, allow_changes: bool = True, **kwargs) -> IntegrationConfig:
    """IntegrationConfig direkt auf einer Sitzung - ohne den Umweg ueber die Fassade."""
    from wt3000_scpi.wt3000_core import WTSession

    session = WTSession(transport, WTConfig(use_remote=False), read_only=False)
    return IntegrationConfig(session, allow_changes=allow_changes, **kwargs)


# ---------------------------------------------------------------------------
# Parser - die Kurzformen des Geraets
# ---------------------------------------------------------------------------


def test_zustand_versteht_die_kurzform_des_geraets():
    """Am 21.08.2026 gemessen: ':INTEGrate:STATe?' antwortet 'RES'."""
    assert parse_state("RES") is IntegrationState.RESET
    assert parse_state("RESET") is IntegrationState.RESET
    assert parse_state("READ") is IntegrationState.READY
    assert parse_state("STAR") is IntegrationState.START
    assert parse_state("STOP") is IntegrationState.STOP
    assert parse_state("ERR") is IntegrationState.ERROR
    assert parse_state("TIM") is IntegrationState.TIMEUP
    # Auch mit eingeschaltetem Antwortkopf.
    assert parse_state(":INTEGRATE:STATE RESET") is IntegrationState.RESET


def test_betriebsart_versteht_die_kurzform():
    """Gemessen: ':INTEGrate:MODE?' antwortet 'NORM'."""
    assert parse_mode("NORM") is IntegrationMode.NORMAL
    assert parse_mode("NORMAL") is IntegrationMode.NORMAL
    assert parse_mode("CONT") is IntegrationMode.CONTINUOUS
    assert parse_mode("RNOR") is IntegrationMode.RNORMAL
    assert parse_mode("RCON") is IntegrationMode.RCONTINUOUS


def test_unbekannter_zustand_ist_ein_fehler_und_keine_annahme():
    with pytest.raises(WTError) as fehler:
        parse_state("VIELLEICHT")
    assert "VIELLEICHT" in str(fehler.value)


def test_timer_wird_in_sekunden_gefuehrt():
    assert parse_timer("0,0,0") == 0
    assert parse_timer("1,30,0") == 5400
    assert parse_timer("1,0,0") == 3600
    assert format_timer(5400) == "1,30,0"
    assert format_timer(0) == "0,0,0"
    # Hin und zurueck.
    assert parse_timer(format_timer(12345)) == 12345


def test_timer_grenzen_kommen_aus_dem_handbuch():
    """0,0,0 bis 10000,0,0 (Handbuch 6-75)."""
    assert format_timer(10000 * 3600) == "10000,0,0"
    with pytest.raises(WTError):
        format_timer(10000 * 3600 + 1)
    with pytest.raises(WTError):
        format_timer(-1)


def test_zeitangaben_gehen_hin_und_zurueck():
    moment = datetime(2006, 1, 1, 0, 0, 0)
    assert parse_datetime("2006,1,1,0,0,0") == moment
    assert format_datetime(moment) == "2006,1,1,0,0,0"
    # Die Antwort, die das Geraet am 21.08.2026 auf ':RTIMe?' gab.
    assert parse_datetime("2006,1,1,1,0,0") == datetime(2006, 1, 1, 1, 0, 0)


def test_jahr_ausserhalb_des_geraetebereichs_wird_abgewiesen():
    with pytest.raises(WTError):
        format_datetime(datetime(1999, 1, 1))


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


def test_momentaufnahme_liest_die_ganze_gruppe():
    cfg = integ(FakeTransport(integrate_responses()), allow_changes=False)
    snapshot = cfg.capture()
    assert snapshot.mode is IntegrationMode.NORMAL
    assert snapshot.state is IntegrationState.RESET
    assert snapshot.timer_seconds == 0
    assert snapshot.auto_calibration is False
    assert snapshot.real_time_start == datetime(2006, 1, 1, 0, 0, 0)
    assert snapshot.real_time_end == datetime(2006, 1, 1, 1, 0, 0)


def test_lesen_braucht_keine_freigabe():
    """Die Sperre gilt fuer Schreibzugriffe, nicht fuer Abfragen."""
    cfg = integ(FakeTransport(integrate_responses()), allow_changes=False)
    assert cfg.state() is IntegrationState.RESET
    assert cfg.is_running() is False
    assert cfg.mode() is IntegrationMode.NORMAL


def test_steckbrief_der_gruppe_nennt_zustand_und_timer():
    cfg = integ(FakeTransport(integrate_responses(timer="1,30,0")), allow_changes=False)
    text = "\n".join(cfg.capture().describe())
    assert "RESET" in text
    assert "1,30,0" in text


# ---------------------------------------------------------------------------
# Die Sperre
# ---------------------------------------------------------------------------


def test_ohne_allow_changes_geht_kein_einziges_kommando_raus():
    transport = FakeTransport(integrate_responses())
    cfg = integ(transport, allow_changes=False)
    for aufruf in (
        lambda: cfg.set_mode(IntegrationMode.CONTINUOUS),
        lambda: cfg.set_timer(minutes=15),
        lambda: cfg.set_auto_calibration(True),
        cfg.start,
        cfg.reset,
    ):
        with pytest.raises(ConfigLocked):
            aufruf()
    # Der eigentliche Beleg: nichts davon hat das Geraet erreicht.
    assert not [c for c in transport.written if not c.endswith("?")]


def test_reset_ist_auch_mit_allow_changes_noch_gesperrt():
    """Der Zaehlerstand ist der Messwert selbst - nicht bloss eine Einstellung."""
    transport = FakeTransport(integrate_responses())
    cfg = integ(transport, allow_changes=True)
    with pytest.raises(ConfigLocked) as fehler:
        cfg.reset()
    assert "unlocked" in str(fehler.value)
    assert ":INTEGrate:RESet" not in transport.written


def test_reset_geht_nach_ausdruecklicher_freigabe():
    transport = FakeTransport(integrate_responses())
    cfg = integ(transport, allow_changes=True)
    with cfg.unlocked(GROUP_RESET):
        cfg.reset()
    assert ":INTEGrate:RESet" in transport.written
    # Und danach ist wieder zu.
    with pytest.raises(ConfigLocked):
        cfg.reset()


def test_freigabe_gilt_nur_fuer_die_genannte_gruppe():
    transport = FakeTransport(integrate_responses())
    cfg = integ(transport, allow_changes=False)
    with cfg.unlocked(GROUP_RUN):
        cfg.start()
        with pytest.raises(ConfigLocked):
            cfg.reset()
    assert ":INTEGrate:STARt" in transport.written


def test_unbekannte_gruppe_faellt_sofort_auf():
    cfg = integ(FakeTransport(integrate_responses()))
    with pytest.raises(WTError) as fehler:
        with cfg.unlocked("ZAEHLERSTAND"):
            pass
    assert "ZAEHLERSTAND" in str(fehler.value)


def test_nur_lesen_sitzung_haelt_das_kommando_vor_dem_transport_auf():
    """Die zweite, unabhaengige Sperre - eine Schicht tiefer.

    'allow_changes' ist die Sperre dieses Moduls, 'read_only' die der Sitzung.
    Wer nur die erste oeffnet, kommt trotzdem nicht durch.
    """
    from wt3000_scpi.wt3000_core import WTSession

    transport = FakeTransport(integrate_responses())
    session = WTSession(transport, WTConfig(use_remote=False), read_only=True)
    cfg = IntegrationConfig(session, allow_changes=True, protected_groups=frozenset())
    with pytest.raises(ReadOnlyViolation):
        cfg.start()
    assert ":INTEGrate:STARt" not in transport.written


# ---------------------------------------------------------------------------
# Einstellen
# ---------------------------------------------------------------------------


def test_betriebsart_setzen_wird_zurueckgelesen():
    transport = FakeTransport(integrate_responses(mode="CONT"))
    cfg = integ(transport)
    cfg.set_mode(IntegrationMode.CONTINUOUS)
    assert ":INTEGrate:MODE CONTinuous" in transport.written


def test_betriebsart_wird_gegen_die_kurzantwort_verifiziert():
    """Gesendet wird 'CONTinuous', zurueck kommt 'CONT' - das ist kein Fehler."""
    transport = FakeTransport(integrate_responses(mode="CONT"))
    integ(transport).set_mode("CONTinuous")


def test_abweichende_rueckmeldung_ist_ein_fehler():
    """Das Geraet hat etwas anderes uebernommen als verlangt."""
    transport = FakeTransport(integrate_responses(mode="NORM"))
    with pytest.raises(WTError) as fehler:
        integ(transport).set_mode(IntegrationMode.CONTINUOUS)
    assert "NORM" in str(fehler.value)


def test_unzulaessige_betriebsart_erreicht_das_geraet_nicht():
    transport = FakeTransport(integrate_responses())
    with pytest.raises(WTError):
        integ(transport).set_mode("SCHNELL")
    assert not [c for c in transport.written if c.startswith(":INTEGrate:MODE ")]


def test_timer_rechnet_bequeme_angaben_in_die_geraeteform_um():
    """90 Minuten sind am Geraet '1,30,0' - Minuten gehen dort nur bis 59."""
    transport = FakeTransport(integrate_responses(timer="1,30,0"))
    integ(transport).set_timer(minutes=90)
    assert ":INTEGrate:TIMer 1,30,0" in transport.written


def test_timer_ueber_der_geraetegrenze_wird_nicht_gesendet():
    transport = FakeTransport(integrate_responses())
    with pytest.raises(WTError):
        integ(transport).set_timer(hours=10001)
    assert not [c for c in transport.written if c.startswith(":INTEGrate:TIMer ")]


def test_autokalibrierung_schalten():
    transport = FakeTransport(integrate_responses(acal="1"))
    integ(transport).set_auto_calibration(True)
    assert ":INTEGrate:ACAL ON" in transport.written


def test_echtzeitfenster_setzen():
    start = datetime(2026, 8, 21, 8, 0, 0)
    ende = datetime(2026, 8, 21, 18, 30, 0)
    transport = FakeTransport(
        integrate_responses(rtime_start="2026,8,21,8,0,0", rtime_end="2026,8,21,18,30,0")
    )
    integ(transport).set_real_time_window(start, ende)
    assert ":INTEGrate:RTIMe:STARt 2026,8,21,8,0,0" in transport.written
    assert ":INTEGrate:RTIMe:END 2026,8,21,18,30,0" in transport.written


def test_umgekehrtes_echtzeitfenster_wird_abgewiesen():
    """Das Geraet naehme es an - der Lauf startete dann nie."""
    transport = FakeTransport(integrate_responses())
    with pytest.raises(WTError):
        integ(transport).set_real_time_window(
            datetime(2026, 8, 21, 18, 0), datetime(2026, 8, 21, 8, 0)
        )
    assert not [c for c in transport.written if c.startswith(":INTEGrate:RTIMe")]


def test_momentaufnahme_laesst_sich_zurueckschreiben():
    """Die Vorlage fuer den gemeinsamen SessionBackup aus M2-4."""
    transport = FakeTransport(
        integrate_responses(mode="NORM", timer="0,15,0", acal="0")
    )
    settings = IntegrationSettings(
        mode=IntegrationMode.NORMAL,
        timer_seconds=900,
        auto_calibration=False,
        state=IntegrationState.STOP,
        real_time_start=None,
        real_time_end=None,
    )
    integ(transport).restore(settings)
    assert ":INTEGrate:MODE NORMal" in transport.written
    assert ":INTEGrate:TIMer 0,15,0" in transport.written
    assert ":INTEGrate:ACAL OFF" in transport.written
    # 'state' ist kein Einstellwert und wird nicht zurueckgeschrieben.
    assert ":INTEGrate:STOP" not in transport.written


# ---------------------------------------------------------------------------
# Steuern
# ---------------------------------------------------------------------------


def test_start_und_stop():
    transport = FakeTransport(integrate_responses(state="RES"))
    cfg = integ(transport)
    cfg.start()
    assert ":INTEGrate:STARt" in transport.written


def test_zweiter_start_faellt_auf_statt_durchzugehen():
    transport = FakeTransport(integrate_responses(state="STAR"))
    with pytest.raises(IntegrationStateError):
        integ(transport).start()
    assert ":INTEGrate:STARt" not in transport.written


def test_start_nach_timeup_verlangt_ein_ausdrueckliches_reset():
    """Sonst bliebe unklar, ob der neue Lauf auf dem alten Stand aufsetzt."""
    transport = FakeTransport(integrate_responses(state="TIM"))
    with pytest.raises(IntegrationStateError) as fehler:
        integ(transport).start()
    assert "reset()" in str(fehler.value)


def test_stop_ist_nachsichtig_und_mehrfach_aufrufbar():
    """Weil er im finally steht: ein Aufraeumpfad darf die Ursache nicht verdecken."""
    transport = FakeTransport(integrate_responses(state="STOP"))
    cfg = integ(transport)
    cfg.stop()
    cfg.stop()
    assert ":INTEGrate:STOP" not in transport.written


def test_reset_waehrend_des_laufs_wird_abgelehnt():
    """Messdaten wegwerfen, waehrend sie entstehen - nicht stillschweigend."""
    transport = FakeTransport(integrate_responses(state="STAR"))
    cfg = integ(transport)
    with cfg.unlocked(GROUP_RESET):
        with pytest.raises(IntegrationStateError):
            cfg.reset()
    assert ":INTEGrate:RESet" not in transport.written


class LaufenderTransport(FakeTransport):
    """Geraetemodell mit Zustandsuebergaengen: STARt -> START, STOP -> STOP.

    Ohne diese Rueckkopplung liesse sich 'running()' zwar aufrufen, aber nicht
    pruefen: der Zustand bliebe konstant, und ob das finally den Lauf
    tatsaechlich beendet, waere nicht sichtbar.
    """

    def __init__(self, start_state: str = "RES", **kwargs) -> None:
        self.state = start_state
        responses = integrate_responses()
        responses[":INTEGRATE:STATE"] = lambda _cmd: self.state
        super().__init__(responses, **kwargs)

    def write(self, command: str) -> None:
        super().write(command)
        node = command.strip().upper()
        if node == ":INTEGRATE:START":
            self.state = "STAR"
        elif node == ":INTEGRATE:STOP":
            self.state = "STOP"
        elif node == ":INTEGRATE:RESET":
            self.state = "RES"


def test_running_startet_und_stoppt():
    transport = LaufenderTransport()
    cfg = integ(transport)
    with cfg.running():
        assert cfg.is_running() is True
    assert cfg.state() is IntegrationState.STOP
    assert transport.written.count(":INTEGrate:STARt") == 1
    assert transport.written.count(":INTEGrate:STOP") == 1


def test_running_stoppt_auch_wenn_der_block_scheitert():
    """Der eigentliche Grund fuer den Kontextmanager.

    Ein Zaehlvorgang laeuft am Geraet weiter, auch wenn das Python-Programm
    laengst abgestuerzt ist. Ohne das finally waere ein abgebrochener Lauf der
    Normalfall - und der naechste Start faende einen Zaehlerstand vor, den
    niemand erwartet hat.
    """
    transport = LaufenderTransport()
    cfg = integ(transport)
    with pytest.raises(ZeroDivisionError):
        with cfg.running():
            raise ZeroDivisionError("Messauswertung gescheitert")
    assert cfg.state() is IntegrationState.STOP
    assert ":INTEGrate:STOP" in transport.written


def test_running_stoppt_auch_bei_strg_c():
    """KeyboardInterrupt ist keine Exception - 'finally' faengt ihn trotzdem."""
    transport = LaufenderTransport()
    cfg = integ(transport)
    with pytest.raises(KeyboardInterrupt):
        with cfg.running():
            raise KeyboardInterrupt
    assert ":INTEGrate:STOP" in transport.written


# ---------------------------------------------------------------------------
# Fortschritt und Warten
# ---------------------------------------------------------------------------


def test_restzeit_kommt_aus_timer_minus_verstrichener_zeit():
    """Und ausdruecklich NICHT aus ':INTEGrate:RTIMe?' - am Geraet widerlegt."""
    cfg = integ(FakeTransport(integrate_responses(timer="1,0,0")), allow_changes=False)
    assert cfg.remaining_seconds(0) == 3600.0
    assert cfg.remaining_seconds(900) == 2700.0
    # Ueber die Zeit hinaus wird nicht negativ gezaehlt.
    assert cfg.remaining_seconds(4000) == 0.0


def test_ohne_timer_gibt_es_keine_restzeit():
    cfg = integ(FakeTransport(integrate_responses(timer="0,0,0")), allow_changes=False)
    assert cfg.remaining_seconds(120) is None


def test_warten_endet_mit_dem_lauf():
    transport = LaufenderTransport(start_state="STAR")
    cfg = integ(transport)

    # Nach der zweiten Abfrage meldet das Geraet TIMEUP.
    abfragen = {"n": 0}
    ursprung = transport.responses[":INTEGRATE:STATE"]

    def zustand(cmd):
        abfragen["n"] += 1
        return "TIM" if abfragen["n"] > 2 else ursprung(cmd)

    transport.responses[":INTEGRATE:STATE"] = zustand

    assert cfg.wait_until_finished(poll_interval_s=0) is IntegrationState.TIMEUP


def test_warten_ohne_lauf_ist_ein_ablauffehler_und_keine_geduldsfrage():
    cfg = integ(FakeTransport(integrate_responses(state="RES")), allow_changes=False)
    with pytest.raises(IntegrationStateError):
        cfg.wait_until_finished(poll_interval_s=0)


def test_warten_laeuft_in_die_zeitschranke():
    cfg = integ(FakeTransport(integrate_responses(state="STAR")), allow_changes=False)
    with pytest.raises(WTError) as fehler:
        cfg.wait_until_finished(timeout_s=0, poll_interval_s=0)
    # Die Meldung sagt ausdruecklich, dass das Geraet weiterlaeuft.
    assert "weiter" in str(fehler.value)


# ---------------------------------------------------------------------------
# Anbindung an die Fassade und an die Item-Tabelle
# ---------------------------------------------------------------------------


def test_fassade_reicht_die_gruppe_durch():
    responses = base_responses()
    responses.update(integrate_responses())
    with open_facade(FakeTransport(responses)) as wt:
        assert wt.integration.state() is IntegrationState.RESET
        # Zweimal abgefragt, einmal gebaut.
        assert wt.integration is wt.integration


def test_fassade_gibt_die_sperre_weiter():
    responses = base_responses()
    responses.update(integrate_responses())

    with open_facade(FakeTransport(responses)) as wt:
        assert wt.integration.allow_changes is False

    transport = FakeTransport(responses)
    with WT3000.from_transport(
        transport, WTConfig(use_remote=False), read_only=False, allow_changes=True
    ) as wt:
        assert wt.integration.allow_changes is True
        # RESET bleibt trotzdem geschuetzt.
        assert GROUP_RESET in wt.integration.protected_groups


def test_integrationsprofil_enthaelt_die_groessen_der_wh_messung():
    profile = build_integration_profile()
    functions = {spec.function for spec in profile}
    for gebraucht in ("TIME", "WH", "WHP", "WHM", "AH", "AHP", "AHM"):
        assert gebraucht in functions, gebraucht

    # TIME gilt geraeteweit und steht genau einmal drin.
    assert [spec.function for spec in profile].count("TIME") == 1

    # Die Integrationsgroessen sind als am Geraet ungeprueft gekennzeichnet.
    assert all(spec.verify for spec in profile if spec.function.startswith("WH"))

    # Und der Momentanwertkontext fehlt nicht.
    assert {"U", "I", "P"} <= functions


def test_profil_ist_ueber_die_fassade_erreichbar():
    responses = base_responses()
    responses.update(integrate_responses())
    with open_facade(FakeTransport(responses)) as wt:
        assert wt.items.integration_profile() == build_integration_profile()


def test_gruppennamen_sind_verschieden():
    """Trivial, aber die Sperre haengt daran."""
    assert len({GROUP_INTEGRATE, GROUP_RUN, GROUP_RESET}) == 3
