# =============================================================================
# Datei: tests/test_harmonics.py
# Geraetefreie Tests der Oberschwingungsanalyse ':HARMonics'.
#
# Drei Dinge unterscheiden diese Gruppe von den beiden vorherigen, und um sie
# geht es hier:
#
#   1. SIE VERLANGT EINE GERAETEOPTION ('/G5' oder '/G6'). Fehlt sie,
#      antwortet das Geraet auf kein Kommando der Gruppe - der Query laeuft in
#      den Timeout und sieht aus wie ein Verbindungsabbruch. Die Fassade faengt
#      das mit 'require_option()' ab; beide Richtungen werden geprueft.
#   2. Das Geraet MELDET BEI EINER PLL-QUELLE ETWAS ANDERES ZURUECK, als man
#      gesetzt hat ('SAMPle' -> 'EXTernal', Handbuch 6-58). Eine
#      Rueckleseprobe, die das nicht weiss, macht einen richtigen Aufruf
#      unbenutzbar.
#   3. Die Messwerte kommen ueber den <Order>-Parameter der gewoehnlichen
#      Item-Tabelle - ohne neue Maschinerie.
# =============================================================================

from __future__ import annotations

import pytest
from conftest import base_responses, harmonics_responses

from wt3000_scpi import WT3000, WTConfig, WTError
from wt3000_scpi.wt3000_core import WTSession
from wt3000_scpi.wt3000_deviceconfig import (
    ConfigLocked,
    FrequencyBand,
    HarmonicsConfig,
    IecGrouping,
    ThdFormula,
    parse_order,
)
from wt3000_scpi.wt3000_measure import build_harmonics_profile
from wt3000_scpi.wt3000_transport import FakeTransport


def harm(transport: FakeTransport, allow_changes: bool = True, **kwargs) -> HarmonicsConfig:
    """HarmonicsConfig auf einer schreibfaehigen Sitzung."""
    session = WTSession(transport, WTConfig(use_remote=False), read_only=False)
    return HarmonicsConfig(session, allow_changes=allow_changes, **kwargs)


def gesendet(transport: FakeTransport) -> list[str]:
    """Nur die Set-Kommandos."""
    return [c for c in transport.written if not c.endswith("?")]


def facade_responses(**kwargs) -> dict:
    responses = base_responses(**kwargs)
    responses.update(harmonics_responses())
    return responses


# ---------------------------------------------------------------------------
# Die Optionspruefung - der erste echte Nutzen von M1-3
# ---------------------------------------------------------------------------


def test_ohne_option_nennt_die_fassade_die_ursache():
    """Statt eines Timeouts, der wie ein Verbindungsabbruch aussieht."""
    # Ein Geraet ohne G5 und ohne G6.
    with WT3000.from_transport(
        FakeTransport(facade_responses(options="B5,C7")), WTConfig(use_remote=False)
    ) as wt:
        assert wt.device.supports(":HARMonics") is False
        with pytest.raises(WTError) as fehler:
            wt.harmonics  # noqa: B018 - der Zugriff selbst loest die Pruefung aus
        text = str(fehler.value)
        assert ":HARMonics" in text
        assert "G5" in text and "G6" in text


def test_g6_allein_genuegt():
    """Der Fall des eingemessenen Geraets: G6 vorhanden, G5 nicht."""
    with WT3000.from_transport(
        FakeTransport(facade_responses()), WTConfig(use_remote=False)
    ) as wt:
        assert wt.device.has_option("G5") is False
        assert wt.device.has_option("G6") is True
        assert wt.harmonics.band() is FrequencyBand.NORMAL


def test_unbekannte_optionen_sperren_die_gruppe_nicht():
    """Dieselbe Regel wie ueberall: unbekannt ist nicht dasselbe wie 'fehlt'."""
    responses = facade_responses()
    del responses["*OPT"]
    transport = FakeTransport(responses, fail_commands=["*OPT?"])
    with WT3000.from_transport(transport, WTConfig(use_remote=False)) as wt:
        assert wt.device.options_known is False
        assert wt.harmonics.band() is FrequencyBand.NORMAL


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


def test_momentaufnahme_liest_die_ganze_gruppe():
    cfg = harm(FakeTransport(harmonics_responses()), allow_changes=False)
    snapshot = cfg.capture()
    assert snapshot.band is FrequencyBand.NORMAL
    assert (snapshot.order_min, snapshot.order_max) == (1, 100)
    assert snapshot.pll_source == "U1"
    assert snapshot.pll_warning is True
    assert snapshot.thd is ThdFormula.TOTAL
    assert snapshot.iec_object == "ELEMENT1"
    assert snapshot.iec_voltage_grouping is IecGrouping.OFF
    assert snapshot.iec_current_grouping is IecGrouping.OFF


def test_kurzformen_werden_verstanden():
    cfg = harm(
        FakeTransport(harmonics_responses(band="NORM", thd="FUND")),
        allow_changes=False,
    )
    assert cfg.band() is FrequencyBand.NORMAL
    assert cfg.thd_formula() is ThdFormula.FUNDAMENTAL


def test_ordnungspaar_wird_zerlegt():
    assert parse_order("1,100") == (1, 100)
    assert parse_order("0,50") == (0, 50)
    assert parse_order(":HARMONICS:ORDER 1,40") == (1, 40)
    with pytest.raises(WTError):
        parse_order("1")


def test_beschreibung_nennt_ordnung_und_pll():
    cfg = harm(
        FakeTransport(harmonics_responses(order="1,40", pll="I3")),
        allow_changes=False,
    )
    text = "\n".join(cfg.capture().describe())
    assert "1..40" in text
    assert "I3" in text


# ---------------------------------------------------------------------------
# Ordnungsbereich
# ---------------------------------------------------------------------------


def test_ordnungsbereich_setzen():
    transport = FakeTransport(harmonics_responses(order="1,40"))
    harm(transport).set_order_range(1, 40)
    assert ":HARMonics:ORDer 1,40" in transport.written


def test_minimale_ordnung_kennt_nur_null_und_eins():
    """Handbuch 6-57: 0 oder 1, sonst nichts."""
    transport = FakeTransport(harmonics_responses())
    with pytest.raises(WTError) as fehler:
        harm(transport).set_order_range(2, 40)
    assert "0" in str(fehler.value)
    assert gesendet(transport) == []


def test_ordnung_null_ist_zulaessig_und_nimmt_den_gleichanteil_mit():
    transport = FakeTransport(harmonics_responses(order="0,40"))
    harm(transport).set_order_range(0, 40)
    assert ":HARMonics:ORDer 0,40" in transport.written


def test_maximale_ordnung_ueber_hundert_wird_nicht_gesendet():
    transport = FakeTransport(harmonics_responses())
    with pytest.raises(WTError):
        harm(transport).set_order_range(1, 150)
    assert gesendet(transport) == []


def test_maximale_ordnung_null_wird_abgewiesen():
    """0 ist als MINIMUM erlaubt (Gleichanteil), als Maximum nicht."""
    transport = FakeTransport(harmonics_responses())
    with pytest.raises(WTError) as fehler:
        harm(transport).set_order_range(1, 0)
    assert "ausserhalb" in str(fehler.value)
    assert gesendet(transport) == []


# ---------------------------------------------------------------------------
# PLL-Quelle - die Handbuchstelle, die man nicht uebersehen darf
# ---------------------------------------------------------------------------


def test_pll_quelle_setzen():
    transport = FakeTransport(harmonics_responses(pll="U3"))
    harm(transport).set_pll_source("U3")
    assert ":HARMonics:PLLSource U3" in transport.written


def test_sample_wird_als_external_zurueckgemeldet_und_ist_kein_fehler(caplog):
    """Handbuch 6-58: ausserhalb der Breitbandmessung meldet das Geraet EXTernal.

    Ohne diese Ausnahme meldete die Rueckleseprobe eine Abweichung und ein
    vollkommen richtiger Aufruf waere am Geraet unbenutzbar.
    """
    transport = FakeTransport(harmonics_responses(pll="EXTERNAL"))
    with caplog.at_level("INFO"):
        harm(transport).set_pll_source("SAMPle")
    assert ":HARMonics:PLLSource SAMPle" in transport.written
    # Und es passiert nicht still.
    assert any("EXTernal" in eintrag.getMessage() for eintrag in caplog.records)


def test_umgekehrt_gilt_die_nachsicht_nicht():
    """Wer EXTernal setzt und SAMPle zurueckbekommt, hat ein echtes Problem."""
    transport = FakeTransport(harmonics_responses(pll="SAMPLE"))
    with pytest.raises(WTError):
        harm(transport).set_pll_source("EXTernal")


def test_pll_quelle_auf_unbestuecktem_element_wird_abgewiesen():
    transport = FakeTransport(harmonics_responses())
    cfg = harm(transport, elements=(1, 2, 3))
    with pytest.raises(WTError):
        cfg.set_pll_source("U4")
    assert gesendet(transport) == []


def test_unsinnige_pll_quelle_wird_abgewiesen():
    transport = FakeTransport(harmonics_responses())
    for quelle in ("P1", "TAKT", "U"):
        with pytest.raises(WTError):
            harm(transport).set_pll_source(quelle)
    assert gesendet(transport) == []


def test_pll_warnung_schalten():
    transport = FakeTransport(harmonics_responses(pll_warning="0"))
    harm(transport).set_pll_warning(False)
    assert ":HARMonics:PLLWarning:STATe OFF" in transport.written


# ---------------------------------------------------------------------------
# THD und Bandbreite
# ---------------------------------------------------------------------------


def test_thd_bezug_umstellen():
    transport = FakeTransport(harmonics_responses(thd="FUNDAMENTAL"))
    harm(transport).set_thd_formula(ThdFormula.FUNDAMENTAL)
    assert ":HARMonics:THD FUNDamental" in transport.written


def test_bandbreite_umstellen():
    transport = FakeTransport(harmonics_responses(band="WIDE"))
    harm(transport).set_band(FrequencyBand.WIDE)
    assert ":HARMonics:FBANd WIDE" in transport.written


def test_unbekannte_bandbreite_erreicht_das_geraet_nicht():
    transport = FakeTransport(harmonics_responses())
    with pytest.raises(WTError):
        harm(transport).set_band("SEHRWEIT")
    assert gesendet(transport) == []


# ---------------------------------------------------------------------------
# IEC-Teil
# ---------------------------------------------------------------------------


def test_iec_objekt_nimmt_element_als_zahl():
    transport = FakeTransport(harmonics_responses(iec_object="ELEMENT2"))
    harm(transport).set_iec_object(2)
    assert ":HARMonics:IEC:OBJect ELEMENT2" in transport.written


def test_iec_objekt_nimmt_auch_die_wiring_unit():
    transport = FakeTransport(harmonics_responses(iec_object="SIGMA"))
    harm(transport).set_iec_object("SIGMA")
    assert ":HARMonics:IEC:OBJect SIGMA" in transport.written


def test_iec_objekt_auf_unbestuecktem_element_wird_abgewiesen():
    transport = FakeTransport(harmonics_responses())
    cfg = harm(transport, elements=(1, 2, 3))
    with pytest.raises(WTError):
        cfg.set_iec_object("ELEMENT4")
    assert gesendet(transport) == []


def test_nicht_vorhandene_wiring_unit_wird_abgewiesen():
    """Bei einer Verdrahtung ohne SigmaB gibt es kein SIGMB."""
    transport = FakeTransport(harmonics_responses())
    cfg = harm(transport, sigma_units=("SIGMA",))
    with pytest.raises(WTError) as fehler:
        cfg.set_iec_object("SIGMB")
    assert "SIGMB" in str(fehler.value)
    assert gesendet(transport) == []


def test_gruppierung_getrennt_fuer_spannung_und_strom():
    transport = FakeTransport(harmonics_responses(ugrouping="TYPE1", igrouping="TYPE2"))
    cfg = harm(transport)
    cfg.set_iec_grouping("U", IecGrouping.TYPE1)
    cfg.set_iec_grouping("I", IecGrouping.TYPE2)
    assert ":HARMonics:IEC:UGRouping TYPE1" in transport.written
    assert ":HARMonics:IEC:IGRouping TYPE2" in transport.written
    assert cfg.iec_grouping("U") is IecGrouping.TYPE1
    assert cfg.iec_grouping("I") is IecGrouping.TYPE2


def test_unbekannte_groesse_bei_der_gruppierung():
    cfg = harm(FakeTransport(harmonics_responses()), allow_changes=False)
    with pytest.raises(WTError) as fehler:
        cfg.iec_grouping("P")
    assert "'U'" in str(fehler.value)


# ---------------------------------------------------------------------------
# Sperre und Wiederherstellung
# ---------------------------------------------------------------------------


def test_ohne_allow_changes_geht_kein_kommando_raus():
    transport = FakeTransport(harmonics_responses())
    cfg = harm(transport, allow_changes=False)
    for aufruf in (
        lambda: cfg.set_band(FrequencyBand.WIDE),
        lambda: cfg.set_order_range(1, 40),
        lambda: cfg.set_pll_source("U2"),
        lambda: cfg.set_pll_warning(False),
        lambda: cfg.set_thd_formula(ThdFormula.FUNDAMENTAL),
        lambda: cfg.set_iec_object(2),
        lambda: cfg.set_iec_grouping("U", IecGrouping.TYPE1),
    ):
        with pytest.raises(ConfigLocked):
            aufruf()
    assert gesendet(transport) == []


def test_momentaufnahme_laesst_sich_zurueckschreiben():
    tabelle = harmonics_responses(
        band="WIDE", order="0,50", pll="I2", pll_warning="0", thd="FUNDAMENTAL",
        iec_object="SIGMA", ugrouping="TYPE1", igrouping="TYPE1",
    )
    transport = FakeTransport(tabelle)
    cfg = harm(transport)
    cfg.restore(cfg.capture())

    befehle = gesendet(transport)
    assert ":HARMonics:FBANd WIDE" in befehle
    assert ":HARMonics:ORDer 0,50" in befehle
    assert ":HARMonics:PLLSource I2" in befehle
    assert ":HARMonics:THD FUNDamental" in befehle
    assert ":HARMonics:IEC:OBJect SIGMA" in befehle


# ---------------------------------------------------------------------------
# Messprofil - die Leseseite
# ---------------------------------------------------------------------------


def test_profil_nutzt_den_ordnungsparameter_der_item_tabelle():
    """Ohne neue Maschinerie: 'U,1,5' ist die 5. Oberschwingung an Element 1."""
    profile = build_harmonics_profile(orders=(3, 5), elements=("1",))
    argumente = {spec.argument for spec in profile}
    assert "U,1,3" in argumente
    assert "U,1,5" in argumente
    assert "P,1,5" in argumente
    # Der Gesamtwert als Bezug fehlt nicht.
    assert "U,1,TOTAL" in argumente


def test_profil_enthaelt_die_summengroessen():
    profile = build_harmonics_profile(orders=(3,), elements=("1",))
    functions = {spec.function for spec in profile}
    for gebraucht in ("UTHD", "ITHD", "PTHD", "UTHF", "HVF"):
        assert gebraucht in functions, gebraucht


def test_profil_kennzeichnet_alles_als_am_geraet_ungeprueft():
    assert all(spec.verify for spec in build_harmonics_profile())


def test_profil_weist_unzulaessige_ordnungen_ab():
    with pytest.raises(WTError):
        build_harmonics_profile(orders=(1, 150))
    with pytest.raises(WTError):
        build_harmonics_profile(orders=())


def test_profil_ist_ueber_die_fassade_erreichbar():
    with WT3000.from_transport(
        FakeTransport(facade_responses()), WTConfig(use_remote=False)
    ) as wt:
        assert wt.items.harmonics_profile(orders=(3,), elements=("1",)) == (
            build_harmonics_profile(orders=(3,), elements=("1",))
        )


# ---------------------------------------------------------------------------
# Fassade
# ---------------------------------------------------------------------------


def test_fassade_verdrahtet_elemente_und_wiring_units():
    with WT3000.from_transport(
        FakeTransport(facade_responses()), WTConfig(use_remote=False)
    ) as wt:
        assert wt.harmonics.elements == (1, 2, 3, 4)
        assert wt.harmonics is wt.harmonics
        # SIGMA und SIGMB kommen aus dem Steckbrief, nicht aus einer Konstante:
        # beide werden als IEC-Objekt angenommen, ein drittes nicht.
        assert wt.device.sigma_members.keys() == {"SIGMA", "SIGMB"}
        with pytest.raises(WTError):
            wt.harmonics.set_iec_object("SIGMC")


def test_fassade_gibt_die_schreibsperre_weiter():
    with WT3000.from_transport(
        FakeTransport(facade_responses()), WTConfig(use_remote=False)
    ) as wt:
        assert wt.harmonics.allow_changes is False

    with WT3000.from_transport(
        FakeTransport(facade_responses()),
        WTConfig(use_remote=False),
        read_only=False,
        allow_changes=True,
    ) as wt:
        assert wt.harmonics.allow_changes is True
