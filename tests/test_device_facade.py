# =============================================================================
# Datei: tests/test_device_facade.py
# Geraetefreie Tests der Fassade gegen FakeTransport. Dadurch laufen WTSession,
# Blockparser, Fehlerqueue und Item-Tabelle als zusammengebundene Schichten mit.
#
# 'ItemTableTransport' unten ist ein minimales Geraetemodell: es uebernimmt
# geschriebene ITEM<n>- und NUMber-Kommandos und beantwortet die Abfragen
# daraus. Ohne diese Rueckkopplung koennte man das Schreiben pruefen, aber
# nicht das Verifizieren und schon gar nicht die Wiederherstellung.
# =============================================================================

from __future__ import annotations


import pytest
# 'base_responses' und 'ItemTableTransport' liegen
# jetzt in conftest.py - die Stufenskripte brauchen dasselbe Geraetemodell.
from conftest import ItemTableTransport, base_responses

from wt3000_scpi import OPTION_REQUIREMENTS, WT3000, WTConfig, WTError
# Die reinen Funktionen der Optionserfassung werden einzeln
# geprueft, nicht nur ueber die Fassade.
from wt3000_scpi.wt3000_device import parse_options, required_options
from wt3000_scpi import wt3000_device  # fuer monkeypatch auf TmctlTransport
from wt3000_scpi.wt3000_core import ReadOnlyViolation, TmctlError
from wt3000_scpi.wt3000_input import ConfigLocked
from wt3000_scpi.wt3000_itemspec import ItemSpec
from wt3000_scpi.wt3000_numeric import ValueStatus
from wt3000_scpi.wt3000_transport import FakeTransport

def open_facade(transport: FakeTransport, **kwargs) -> WT3000:
    """Fassade auf einem Fake-Transport, ohne Fernsteuerung."""
    kwargs.setdefault("read_only", True)
    return WT3000.from_transport(transport, WTConfig(use_remote=False), **kwargs)


# ---------------------------------------------------------------------------
# Verbinden und Steckbrief
# ---------------------------------------------------------------------------


def test_steckbrief_wird_beim_verbinden_erhoben():
    with open_facade(FakeTransport(base_responses())) as wt:
        info = wt.device
        assert info.manufacturer == "YOKOGAWA"
        assert info.model == "WT3000"
        assert info.serial == "C1B234567"
        assert info.firmware == "F2.11"
        assert info.wiring == ("V3A3", "P1W2")
        assert info.elements == (1, 2, 3, 4)
        assert info.elements_assumed is False


def test_unbestueckte_elemente_fallen_aus_der_elementliste():
    """Die Elementliste wird gelesen, nicht gesetzt."""
    responses = base_responses(wiring="V3A3,NONE", modules="30,30,30,0")
    with open_facade(FakeTransport(responses)) as wt:
        assert wt.device.elements == (1, 2, 3)
        assert wt.device.has_element(4) is False
        assert wt.ranges.expand_scope("ALL") == (1, 2, 3)


def test_wiring_units_sind_ohne_zutun_verdrahtet():
    """Die Fassade buendelt den vollstaendigen Verbindungsablauf.

    Ohne Fassade muss der Aufrufer sigma_members_from_units(...) selbst
    einsetzen - in stage5b fehlt genau das, dort laeuft jeder SIGMA-Scope in
    einen Fehler.
    """
    with open_facade(FakeTransport(base_responses())) as wt:
        assert wt.ranges.expand_scope("SIGMA") == (1, 2, 3)
        assert wt.ranges.expand_scope("SIGMB") == (4,)
        # Und weiterhin kein Praefixmatching: SIGM != SIGMB.
        assert wt.ranges.expand_scope("SIGM") == (1, 2, 3)


def test_steckbrief_ohne_idn_bricht_nicht_ab():
    """'*IDN?' ist informativ - Verdrahtung und Modultypen sind es nicht."""
    responses = base_responses()
    del responses["*IDN"]
    transport = FakeTransport(responses, fail_commands=["*IDN?"])
    with open_facade(transport) as wt:
        assert wt.device.identity == "unbekannt"
        assert wt.device.elements == (1, 2, 3, 4)


def test_fehlende_verdrahtung_ist_ein_fehler():
    responses = base_responses()
    del responses[":INPUT:WIRING"]
    with pytest.raises(KeyError):
        open_facade(FakeTransport(responses))


# ---------------------------------------------------------------------------
# Geraeteoptionen
# ---------------------------------------------------------------------------
#
# Worum es geht: zehn Kommandogruppen des WT3000 sind an eine verbaute Option
# gebunden, und ein Kommando einer nicht verbauten Gruppe wird nicht etwa
# abgelehnt - es bleibt UNBEANTWORTET. Ohne die Optionserfassung faellt das
# erst im Timeout auf, mit einer Meldung, die nach Verbindungsabbruch aussieht.
#
# Die Antworttabelle des Modellgeraets (conftest.OPT) ist die des real
# eingemessenen Geraets: G6, B5, DT, C7, C5, CC verbaut - FL und DA nicht.
# Damit pruefen dieselben Saetze beide Richtungen.


def test_optionen_werden_beim_verbinden_erhoben():
    with open_facade(FakeTransport(base_responses())) as wt:
        assert wt.device.options_known is True
        assert wt.device.options == frozenset({"G6", "B5", "DT", "C7", "C5", "CC"})
        assert wt.device.has_option("G6") is True
        # Die Bestellschreibweise mit Schraegstrich meint denselben Code.
        assert wt.device.has_option("/g6") is True
        assert wt.device.has_option("FL") is False


def test_geraet_ohne_option_meldet_null():
    """'*OPT? -> 0' heisst 'keine verbaut' - und ist keine leere Antwort."""
    with open_facade(FakeTransport(base_responses(options="0"))) as wt:
        assert wt.device.options == frozenset()
        assert wt.device.options_known is True
        assert wt.device.supports(":HARMonics") is False


def test_optionsfreie_gruppen_brauchen_keine_pruefung():
    """Die groesste Luecke (':INTEGrate', Rang 1) haengt an keiner Option."""
    with open_facade(FakeTransport(base_responses(options="0"))) as wt:
        for gruppe in (":INTEGrate", ":MEASure", ":STORe", ":WAVeform", ":SYSTem"):
            assert wt.device.supports(gruppe) is True, gruppe


def test_supports_trennt_verbaute_von_fehlenden_gruppen():
    with open_facade(FakeTransport(base_responses())) as wt:
        info = wt.device
        # Verbaut: G6 allein genuegt fuer :HARMonics, G5 wird nicht gebraucht.
        assert info.supports(":HARMonics") is True
        assert info.supports(":ACQuisition") is True
        assert info.supports(":CBCycle") is True
        assert info.supports(":MEASure:DMeasure") is True
        # Nicht verbaut - genau die beiden Gruppen, die auch am realen Geraet
        # fehlen.
        assert info.supports(":FLICker") is False
        assert info.supports(":AOUTput") is False


def test_unterknoten_erbt_die_anforderung_seiner_gruppe():
    with open_facade(FakeTransport(base_responses(options="0"))) as wt:
        assert wt.device.supports(":HARMonics:ORDer") is False
        assert wt.device.supports(":harmonics:pllsource") is False
        # Und kein Praefixmatching in die andere Richtung: ':HARMonicsX' ist
        # keine Untergruppe von ':HARMonics'.
        assert wt.device.supports(":HARMonicsX") is True


def test_require_option_nennt_code_modell_und_rohantwort():
    with open_facade(FakeTransport(base_responses())) as wt:
        wt.device.require_option(":HARMonics")  # verbaut - kein Fehler
        with pytest.raises(WTError) as fehler:
            wt.device.require_option(":FLICker")
        text = str(fehler.value)
        assert ":FLICker" in text
        assert "FL" in text
        assert "WT3000" in text
        assert "G6,B5,DT,C7,C5,CC" in text


# -- Motorvariante ----------------------------------------------------------
#
# Der Befund vom 21.08.2026 in einem Pruefsatz: am realen Geraet meldete
# '*OPT?' KEIN MTR, obwohl ':MOTor:PM?' antwortete. Zuverlaessig war der
# Modellcode '-MV'. Wer ':MOTor' spaeter doch in OPTION_REQUIREMENTS aufnimmt,
# faellt hier auf - und nicht erst am Geraet, wo der Treiber dann eine
# vorhandene Gruppe abweisen wuerde.

MOTORMODELL = "YOKOGAWA,760304-40-MV,0,F5.01"


def test_motorvariante_wird_am_modellcode_erkannt_nicht_an_mtr():
    responses = base_responses(options="G6,B5,DT,C7,C5,CC")
    responses["*IDN"] = MOTORMODELL
    with open_facade(FakeTransport(responses)) as wt:
        assert wt.device.has_option("MTR") is False
        assert wt.device.is_motor_model is True
        assert wt.device.supports(":MOTor") is True
        assert wt.device.supports(":MOTor:PM") is True


def test_ohne_motorvariante_und_ohne_mtr_ist_motor_gesperrt():
    with open_facade(FakeTransport(base_responses())) as wt:
        assert wt.device.is_motor_model is False
        assert wt.device.supports(":MOTor") is False


def test_mtr_allein_genuegt_ebenfalls():
    """Der umgekehrte Fall: ein Geraet, das MTR doch meldet."""
    with open_facade(FakeTransport(base_responses(options="MTR"))) as wt:
        assert wt.device.is_motor_model is False
        assert wt.device.supports(":MOTor") is True


# -- Wenn '*OPT?' nicht antwortet -------------------------------------------


def test_unbeantwortetes_opt_sperrt_nichts_und_bricht_nicht_ab(caplog):
    """Unbekannt ist nicht dasselbe wie 'fehlt'.

    Der Treiber darf eine Gruppe nur abweisen, wenn er WEISS, dass die Option
    fehlt. Ohne Antwort laeuft das Kommando im Zweifel ins Geraet und
    scheitert dort mit dessen eigener Meldung - geraten wird nicht.
    """
    responses = base_responses()
    del responses["*OPT"]
    transport = FakeTransport(responses, fail_commands=["*OPT?"])
    with caplog.at_level("WARNING"), open_facade(transport) as wt:
        info = wt.device
        assert info.options_known is False
        assert info.options == frozenset()
        assert info.options_raw == "unbekannt"
        assert info.supports(":FLICker") is True
        assert info.has_option("G6") is False
        # Der Rest des Steckbriefs steht trotzdem.
        assert info.model == "WT3000"
        assert info.wiring == ("V3A3", "P1W2")
    assert any("*OPT?" in eintrag.getMessage() for eintrag in caplog.records)


def test_nach_fehlgeschlagenem_opt_wird_abgeraeumt():
    """Sonst beantwortet eine verspaetete Antwort den NAECHSTEN Query.

    Der naechste ist ':INPut:WIRing?' - der Query, der die Verdrahtung traegt.
    Ohne Abraeumen waere der ganze Steckbrief um eine Position verschoben,
    ohne dass irgendwo ein Fehler auftraete. Sichtbar ist das Abraeumen an der
    kurzzeitig herabgesetzten Zeitschranke.
    """
    responses = base_responses()
    del responses["*OPT"]
    transport = FakeTransport(responses, fail_commands=["*OPT?"])
    with open_facade(transport) as wt:
        assert wt.device.wiring == ("V3A3", "P1W2")
    config = WTConfig()
    assert config.drain_timeout_ms in transport.timeouts_ms
    # Und die normale Zeitschranke steht danach wieder.
    assert transport.timeouts_ms[-1] == config.timeout_ms


def test_auch_ein_fehlgeschlagenes_idn_wird_abgeraeumt():
    """Dasselbe eine Abfrage frueher - sonst faengt '*OPT?' die Kennung ein."""
    responses = base_responses()
    del responses["*IDN"]
    transport = FakeTransport(responses, fail_commands=["*IDN?"])
    with open_facade(transport) as wt:
        assert wt.device.identity == "unbekannt"
        # '*OPT?' hat trotzdem seine eigene Antwort bekommen.
        assert wt.device.options_known is True
        assert "G6" in wt.device.options
    assert WTConfig().drain_timeout_ms in transport.timeouts_ms


# -- Steckbrief -------------------------------------------------------------


def test_steckbrief_nennt_optionen_und_gesperrte_gruppen():
    with open_facade(FakeTransport(base_responses())) as wt:
        text = "\n".join(wt.device.describe())
    assert "Optionen:" in text
    assert "G6" in text
    # Was nicht geht, steht im Steckbrief und nicht erst im Timeout.
    gesperrt = text.split("Nicht ansprechbar", 1)[-1]
    assert ":FLICker" in gesperrt
    assert ":AOUTput" in gesperrt
    assert ":MOTor" in gesperrt
    # Was geht, steht dort nicht.
    assert ":HARMonics" not in gesperrt
    assert ":CBCycle" not in gesperrt


def test_steckbrief_unterscheidet_keine_optionen_von_unbekannt():
    with open_facade(FakeTransport(base_responses(options="0"))) as wt:
        assert "keine verbaut" in "\n".join(wt.device.describe())

    responses = base_responses()
    del responses["*OPT"]
    with open_facade(FakeTransport(responses, fail_commands=["*OPT?"])) as wt:
        text = "\n".join(wt.device.describe())
    assert "unbekannt" in text
    # Ohne Wissen wird nichts als gesperrt gemeldet.
    assert "Nicht ansprechbar" not in text


# -- Die reinen Funktionen --------------------------------------------------


def test_parse_options_vereinheitlicht_die_schreibweise():
    assert parse_options(" /G6, dt ,CC ") == frozenset({"G6", "DT", "CC"})
    assert parse_options("0") == frozenset()
    assert parse_options("") == frozenset()
    # Auch mit eingeschaltetem Antwortkopf lesbar.
    assert parse_options(":OPTION G6,DT") == frozenset({"G6", "DT"})


def test_required_options_kennt_optionsfreie_gruppen():
    assert required_options(":INTEGrate") is None
    assert required_options(":HARMonics") == ("G5", "G6")
    assert required_options(":harmonics:order") == ("G5", "G6")
    assert required_options(":MEASure:DMeasure") == ("DT",)
    # ':MEASure' selbst ist optionsfrei - nur der Delta-Zweig nicht.
    assert required_options(":MEASure") is None
    # ':MOTor' steht bewusst nicht in der Tabelle, siehe Kopf dieses
    # Abschnitts und OPTION_REQUIREMENTS.
    assert required_options(":MOTor") is None
    assert ":MOTor" not in OPTION_REQUIREMENTS


# ---------------------------------------------------------------------------
# Die beiden Schloesser
# ---------------------------------------------------------------------------


def test_voreinstellung_ist_nur_lesen():
    with open_facade(ItemTableTransport(three_items(), number=3)) as wt:
        assert wt.read_only is True
        assert wt.allow_changes is False

        with pytest.raises(ConfigLocked):
            wt.input.set_crest_factor(6)
        with pytest.raises(WTError):
            wt.items.apply(wt.items.read())
        with pytest.raises(ReadOnlyViolation):
            wt.session.write(":INPut:CFACtor 6")


def test_allow_changes_ohne_schreibsitzung_wird_abgelehnt():
    """Ein Widerspruch, der sonst erst beim ersten Set-Kommando auffiele."""
    with pytest.raises(WTError, match="widerspruechlich"):
        open_facade(FakeTransport(base_responses()), read_only=True, allow_changes=True)


def test_nur_lesen_sendet_kein_remote():
    transport = FakeTransport(base_responses())
    with WT3000.from_transport(transport, WTConfig(use_remote=True)) as wt:
        assert wt.read_only is True
    assert ":COMMunicate:REMote ON" not in transport.written


def test_schreibsitzung_schaltet_remote_ein_und_beim_schliessen_ab():
    transport = FakeTransport(base_responses())
    with WT3000.from_transport(
        transport, WTConfig(use_remote=True), read_only=False, allow_changes=True
    ) as wt:
        assert wt.allow_changes is True
        assert ":COMMunicate:REMote ON" in transport.written

    assert ":NUMeric:HOLD OFF" in transport.written
    assert ":COMMunicate:REMote OFF" in transport.written
    assert transport.written.index(":NUMeric:HOLD OFF") < transport.written.index(
        ":COMMunicate:REMote OFF"
    )


# ---------------------------------------------------------------------------
# Fernsteuerung beim gescheiterten
# Verbindungsaufbau.
#
# Der Fall, den close() strukturell nicht abdecken kann: scheitert der
# Konstruktor, entsteht kein WT3000-Objekt, an dem sich close() aufrufen liesse.
# ':COMMunicate:REMote ON' ist da aber laengst gesendet - das Bedienfeld bliebe
# gesperrt zurueck. Geprueft wird deshalb fuer JEDEN Erzeugungsweg einzeln.
#
# 'fail_commands' laesst ':INPut:WIRing?' scheitern. Das ist eine der beiden
# Pflichtabfragen aus DeviceInfo.read() - genau der Fall, den der Kommentar in
# from_config() beschreibt.
# ---------------------------------------------------------------------------

WIRING_QUERY = ":INPut:WIRing?"


def test_gescheiterter_verbindungsaufbau_gibt_das_bedienfeld_frei():
    """REMote ON ohne passendes OFF waere ein gesperrtes Bedienfeld."""
    transport = FakeTransport(base_responses(), fail_commands={WIRING_QUERY})

    with pytest.raises(WTError):
        WT3000.from_transport(
            transport, WTConfig(use_remote=True), read_only=False, allow_changes=True
        )

    assert ":COMMunicate:REMote ON" in transport.written
    assert ":COMMunicate:REMote OFF" in transport.written
    assert transport.written.index(":COMMunicate:REMote ON") < transport.written.index(
        ":COMMunicate:REMote OFF"
    )


def test_gescheiterter_verbindungsaufbau_meldet_weiter_die_urspruengliche_ursache():
    """Das Aufraeumen darf die Ursache nicht verdecken."""
    transport = FakeTransport(base_responses(), fail_commands={WIRING_QUERY})

    with pytest.raises(WTError) as fehler:
        WT3000.from_transport(
            transport, WTConfig(use_remote=True), read_only=False, allow_changes=True
        )

    assert "WIRing" in str(fehler.value)


def test_gescheiterter_verbindungsaufbau_ohne_remote_sendet_kein_off():
    """Ohne vorheriges ON gibt es nichts zurueckzunehmen.

    Haengt an der Pruefung von '_remote_active' in disable_remote() - ein
    blindes OFF waere in einer Nur-Lesen-Sitzung ausserdem ein Set-Kommando
    und wuerde an der eigenen Sperre scheitern.
    """
    transport = FakeTransport(base_responses(), fail_commands={WIRING_QUERY})

    with pytest.raises(WTError):
        WT3000.from_transport(transport, WTConfig(use_remote=True), read_only=True)

    assert ":COMMunicate:REMote ON" not in transport.written
    assert ":COMMunicate:REMote OFF" not in transport.written


def test_from_config_gibt_bedienfeld_frei_und_schliesst_den_transport(monkeypatch):
    """Zweiter Erzeugungsweg: hier gehoert der Transport der Fassade.

    Reihenfolge ist entscheidend - nach transport.close() ginge ein
    'REMote OFF' ins Leere.
    """
    transport = FakeTransport(base_responses(), fail_commands={WIRING_QUERY})
    monkeypatch.setattr(wt3000_device, "TmctlTransport", lambda _config: transport)

    with pytest.raises(WTError):
        WT3000.from_config(
            WTConfig(use_remote=True), read_only=False, allow_changes=True
        )

    assert ":COMMunicate:REMote OFF" in transport.written
    assert transport.closed is True


def test_strg_c_waehrend_des_verbindungsaufbaus_gibt_das_bedienfeld_frei():
    """KeyboardInterrupt ist kein WTError - deshalb faengt der Konstruktor
    BaseException. Ein abgebrochener Verbindungsaufbau darf das Geraet nicht
    gesperrt zuruecklassen."""

    def abbruch(_command: str) -> str:
        raise KeyboardInterrupt

    responses = base_responses()
    responses[":INPUT:WIRING"] = abbruch
    transport = FakeTransport(responses)

    with pytest.raises(KeyboardInterrupt):
        WT3000.from_transport(
            transport, WTConfig(use_remote=True), read_only=False, allow_changes=True
        )

    assert ":COMMunicate:REMote OFF" in transport.written


def test_erfolgreicher_aufbau_sendet_kein_vorzeitiges_off():
    """Gegenprobe: der Aufraeumpfad darf im Regelfall nicht anspringen."""
    transport = FakeTransport(base_responses())
    wt = WT3000.from_transport(
        transport, WTConfig(use_remote=True), read_only=False, allow_changes=True
    )
    try:
        assert ":COMMunicate:REMote OFF" not in transport.written
    finally:
        wt.close()
    assert ":COMMunicate:REMote OFF" in transport.written


# ---------------------------------------------------------------------------
# Beenden
# ---------------------------------------------------------------------------


def test_context_manager_schliesst_auch_bei_einem_fehler_im_block():
    transport = FakeTransport(base_responses())
    with pytest.raises(ZeroDivisionError):
        with WT3000.from_transport(
            transport, WTConfig(use_remote=True), read_only=False, allow_changes=True
        ):
            raise ZeroDivisionError("Fehler im Nutzblock")
    assert ":NUMeric:HOLD OFF" in transport.written
    assert ":COMMunicate:REMote OFF" in transport.written


def test_close_ist_mehrfach_aufrufbar_und_sperrt_danach():
    wt = open_facade(FakeTransport(base_responses()))
    wt.close()
    wt.close()
    with pytest.raises(WTError, match="geschlossen"):
        _ = wt.input


def test_mitgebrachter_transport_wird_nicht_geschlossen():
    """Wer den Transport mitbringt, schliesst ihn auch - Voreinstellung von from_transport."""
    transport = FakeTransport(base_responses())
    with open_facade(transport):
        pass
    assert transport.closed is False


def test_eigener_transport_wird_geschlossen():
    transport = FakeTransport(base_responses())
    with WT3000.from_transport(transport, WTConfig(use_remote=False), owns_transport=True):
        pass
    assert transport.closed is True


# ---------------------------------------------------------------------------
# Sollzustand der Kommunikation
# ---------------------------------------------------------------------------


def test_protokollzustand_in_ordnung():
    with open_facade(FakeTransport(base_responses())) as wt:
        wt.check_protocol_state()


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"header": "1"}, "HEADer"),
        ({"numeric_format": "ASCii"}, "FORMat"),
    ],
)
def test_protokollzustand_faellt_auf(kwargs, fragment):
    with open_facade(FakeTransport(base_responses(**kwargs))) as wt:
        with pytest.raises(WTError, match=fragment):
            wt.check_protocol_state()


def test_condition_bits_werden_gemeldet(caplog):
    responses = base_responses()
    responses[":STATUS:CONDITION"] = str((1 << 4) | (1 << 7))
    with open_facade(FakeTransport(responses)) as wt:
        with caplog.at_level("WARNING"):
            assert wt.log_condition() == 0x90
    assert "FOV" in caplog.text
    assert "PLLE" in caplog.text


# ---------------------------------------------------------------------------
# Item-Tabelle und Messwerte
# ---------------------------------------------------------------------------


def three_items() -> dict[int, str]:
    return {1: "U,1", 2: "I,1", 3: "P,1"}


def test_items_lesen_und_werte_zuordnen():
    transport = ItemTableTransport(three_items(), number=3)
    with open_facade(transport) as wt:
        table = wt.items.read()
        assert [item.key for item in table.items] == ["U1", "I1", "P1"]

        mapped = wt.measure.read_mapped(table)
        assert list(mapped) == ["U1", "I1", "P1"]
        assert mapped["U1"].value == pytest.approx(1.0)
        assert mapped["P1"].value == pytest.approx(3.0)
        assert all(v.status is ValueStatus.OK for v in mapped.values())


def test_hold_wird_in_der_nur_lesen_sitzung_stillgelegt():
    transport = ItemTableTransport(three_items(), number=3)
    with open_facade(transport) as wt:
        with wt.measure.hold() as hold:
            hold.refresh()
    assert not [c for c in transport.written if c.startswith(":NUMeric:HOLD")]


def test_applied_schreibt_verifiziert_und_stellt_zurueck():
    """Der Ablauf, den Stufe 3 und Stufe 4 heute jeweils von Hand nachbauen."""
    transport = ItemTableTransport(three_items(), number=3)
    specs = [ItemSpec("U", "1"), ItemSpec("I", "1"), ItemSpec("P", "1"), ItemSpec("U", "SIGMA")]

    with WT3000.from_transport(
        transport, WTConfig(use_remote=False), read_only=False, allow_changes=True
    ) as wt:
        with wt.items.applied(specs) as target:
            assert target.number == 4
            # Zustand am 'Geraet' waehrend des Blocks.
            assert transport.number == 4
            assert transport.items[4] == "U,SIGMA"
            assert wt.items.verify(target) == []

        # Nach dem Block ist der Ausgangszustand wiederhergestellt.
        assert transport.number == 3
        assert wt.items.read().items[0].argument == "U,1"


def test_applied_stellt_auch_nach_einem_fehler_zurueck():
    transport = ItemTableTransport(three_items(), number=3)
    specs = [ItemSpec("U", "1"), ItemSpec("I", "1"), ItemSpec("P", "1"), ItemSpec("U", "SIGMA")]

    with WT3000.from_transport(
        transport, WTConfig(use_remote=False), read_only=False, allow_changes=True
    ) as wt:
        with pytest.raises(ZeroDivisionError):
            with wt.items.applied(specs):
                raise ZeroDivisionError("Fehler im Nutzblock")
        assert transport.number == 3
        assert 4 not in [item.index for item in wt.items.read().items]


# ---------------------------------------------------------------------------
# Misslungene Wiederherstellung.
#
# 'applied()' verspricht "Ausgangszustand garantiert zurueck". Bisher wurde ein
# Fehler im Restore nur protokolliert und dann verschluckt - der Aufrufer
# verliess den Block ohne Ausnahme und ohne jeden Hinweis darauf, dass die
# Item-Tabelle noch verstellt war.
#
# Die beiden Transporte unten stellen die zwei Arten des Misslingens nach:
# das Kommando kommt gar nicht durch, oder es kommt durch und wirkt nicht.
# ---------------------------------------------------------------------------


class BreakableItemTransport(ItemTableTransport):
    """Ab 'break_writes = True' scheitert jeder Schreibzugriff auf die Tabelle.

    Der abgerissene Verbindungsweg: das Kommando erreicht das Geraet nicht.
    """

    break_writes = False

    def write(self, command: str) -> None:
        if self.break_writes and command.upper().startswith(":NUMERIC:NORMAL"):
            raise TmctlError("TmcSend", 0xDEAD, command)
        super().write(command)


class IgnoringItemTransport(ItemTableTransport):
    """Ab 'ignore_writes = True' werden Schreibzugriffe angenommen, aber nicht uebernommen.

    Der heimtueckischere Fall: kein Fehler, kein Hinweis - der Zustand stimmt
    trotzdem nicht. Ohne Gegenprobe faellt das nirgends auf, weil das Geraet
    Set-Kommandos nicht quittiert.
    """

    ignore_writes = False

    def write(self, command: str) -> None:
        if self.ignore_writes:
            FakeTransport.write(self, command)  # nur protokollieren
            return
        super().write(command)


def test_misslungener_restore_wird_gemeldet_statt_verschluckt():
    """Der Wiederherstellungsfehler darf nicht verloren gehen."""
    transport = BreakableItemTransport(three_items(), number=3)
    specs = [ItemSpec("U", "1"), ItemSpec("I", "1"), ItemSpec("P", "1"), ItemSpec("U", "SIGMA")]

    with WT3000.from_transport(
        transport, WTConfig(use_remote=False), read_only=False, allow_changes=True
    ) as wt:
        with pytest.raises(WTError):
            with wt.items.applied(specs):
                # Der Nutzblock selbst laeuft sauber durch - die Ausnahme kommt
                # ausschliesslich aus der Wiederherstellung.
                transport.break_writes = True
        transport.break_writes = False


def test_stiller_restore_ohne_wirkung_wird_von_der_gegenprobe_gefunden():
    """Restore laeuft ohne Fehler, der Ausgangszustand steht trotzdem nicht."""
    transport = IgnoringItemTransport(three_items(), number=3)
    specs = [ItemSpec("U", "1"), ItemSpec("I", "1"), ItemSpec("P", "1"), ItemSpec("U", "SIGMA")]

    with WT3000.from_transport(
        transport, WTConfig(use_remote=False), read_only=False, allow_changes=True
    ) as wt:
        with pytest.raises(WTError, match="Abweichung"):
            with wt.items.applied(specs):
                transport.ignore_writes = True
        transport.ignore_writes = False

        # Beleg dafuer, dass die Meldung berechtigt war: das 'Geraet' steht
        # noch auf der Zieltabelle, nicht auf dem Ausgangszustand.
        assert transport.number == 4


def test_fehler_im_nutzblock_und_im_restore_bleiben_beide_erhalten():
    """Befund.md verlangt ausdruecklich, dass keiner der beiden verloren geht.

    Python leistet das von selbst: die im finally ausgeloeste Ausnahme traegt
    die urspruengliche als '__context__'.
    """
    transport = BreakableItemTransport(three_items(), number=3)
    specs = [ItemSpec("U", "1"), ItemSpec("I", "1"), ItemSpec("P", "1"), ItemSpec("U", "SIGMA")]

    with WT3000.from_transport(
        transport, WTConfig(use_remote=False), read_only=False, allow_changes=True
    ) as wt:
        with pytest.raises(WTError) as fehler:
            with wt.items.applied(specs):
                transport.break_writes = True
                raise ZeroDivisionError("Fehler im Nutzblock")
        transport.break_writes = False

    assert isinstance(fehler.value.__context__, ZeroDivisionError)
    assert "Nutzblock" in str(fehler.value.__context__)


def test_gelungener_restore_wird_durch_die_gegenprobe_bestaetigt(caplog):
    """Gegenprobe: im Regelfall laeuft die Kontrolle durch und meldet Erfolg."""
    transport = ItemTableTransport(three_items(), number=3)
    specs = [ItemSpec("U", "1"), ItemSpec("I", "1"), ItemSpec("P", "1"), ItemSpec("U", "SIGMA")]

    with WT3000.from_transport(
        transport, WTConfig(use_remote=False), read_only=False, allow_changes=True
    ) as wt:
        with caplog.at_level("INFO"):
            with wt.items.applied(specs):
                pass

    assert any("Restore-Kontrolle" in r.message for r in caplog.records)
    assert transport.number == 3


def test_applied_verlangt_eine_schreibsitzung():
    transport = ItemTableTransport(three_items(), number=3)
    with open_facade(transport) as wt:
        with pytest.raises(WTError, match="allow_changes"):
            with wt.items.applied([ItemSpec("U", "1")]):
                pass
    assert not [c for c in transport.written if c.startswith(":NUMeric:NORMal:ITEM")]


def test_standardprofil_ist_ueber_die_fassade_erreichbar():
    with open_facade(ItemTableTransport(three_items(), number=3)) as wt:
        specs = wt.items.standard_profile()
        table = wt.items.build(specs)
        assert table.number == len(specs) == 31
        assert table.items[0].key == "U1"
