# =============================================================================
# Datei: tests/test_geraetebezug.py
# Der Treiber muss gegen den aktuellen Zustand dieses Geraets arbeiten. Die
# Tests decken zwei Varianten veralteter oder uneinheitlicher Geraetedaten ab:
#
#   1  Nach einer Umverdrahtung stand in 'wt.device' und in den
#             Wiring-Units von 'wt.ranges' weiterhin der alte Zustand.
#             'expand_scope("SIGMA")' loeste auf die Elemente der ALTEN
#             Verdrahtung auf - fehlerfrei, plausibel und falsch.
#
#   2  'InputConfig._elements_of("ALL")' lieferte fest (1, 2, 3, 4),
#             waehrend 'RangeAccess.expand_scope("ALL")' die bestueckten
#             Elemente kannte. Auf einem 3-Element-Geraet adressierten die
#             beiden Wege damit verschiedene Ziele.
#
# Beide Faelle sind geraetefrei nachstellbar.
# =============================================================================

from __future__ import annotations

from dataclasses import replace

import pytest

from wt3000_scpi import WT3000, WTConfig
from wt3000_scpi.wt3000_core import WTError, WTSession
from wt3000_scpi.wt3000_device import DeviceInfo
from wt3000_scpi.wt3000_input import GROUP_RANGE, GROUP_WIRING, InputConfig
from wt3000_scpi.wt3000_transport import FakeTransport

from conftest import base_responses


class VerdrahtbarerTransport(FakeTransport):
    """FakeTransport, der ':INPut:WIRing' tatsaechlich uebernimmt.

    Ohne das laeuft 'set_wiring()' in seine eigene Rueckleseprobe: es schreibt
    das neue Muster und liest das alte zurueck. Derselbe Kniff wie bei
    'ItemTableTransport' in conftest.py - so weit ausgebaut, wie der zu
    pruefende Ablauf es verlangt, und keinen Schritt weiter.
    """

    def __init__(self, wiring: str = "V3A3,P1W2", modules: str = "30,30,30,30") -> None:
        responses = base_responses(wiring=wiring, modules=modules)
        super().__init__(responses)

    def write(self, command: str) -> None:
        super().write(command)
        text = command.strip()
        if " " not in text:
            return
        knoten, wert = text.split(" ", 1)
        knoten, wert = self._key(knoten), wert.strip()

        if knoten == ":INPUT:WIRING":
            self.responses[knoten] = wert
        elif knoten == ":INPUT:VOLTAGE:RANGE:ALL":
            # Der Sammelknoten wirkt am Geraet auf jedes Element; ohne diese
            # Zeile liest die Gegenprobe den alten Wert und der Test scheitert
            # an der Antworttabelle statt an der Sache.
            for element in (1, 2, 3, 4):
                self.responses[f":INPUT:VOLTAGE:RANGE:ELEMENT{element}"] = f"{float(wert):.3E}"

    def am_bedienfeld_umverdrahten(self, wiring: str, modules: str | None = None) -> None:
        """Aenderung hinter dem Treiber vorbei - wie ein Griff ans Geraet."""
        self.responses[self._key(":INPut:WIRing")] = wiring
        if modules is not None:
            self.responses[self._key(":INPut:MODUle")] = modules


def fassade(transport: FakeTransport, **kwargs) -> WT3000:
    kwargs.setdefault("read_only", False)
    kwargs.setdefault("allow_changes", True)
    return WT3000.from_transport(transport, WTConfig(use_remote=False), **kwargs)


# ---------------------------------------------------------------------------
# Dieselbe Elementliste fuer beide Wege
# ---------------------------------------------------------------------------


def test_beide_wege_meinen_dieselben_elemente():
    """'ALL' muss an beiden Stellen dasselbe bedeuten."""
    transport = VerdrahtbarerTransport(wiring="V3A3,NONE", modules="30,30,30,0")
    with fassade(transport) as wt:
        assert wt.device.elements == (1, 2, 3)
        assert wt.ranges.expand_scope("ALL") == (1, 2, 3)
        # Genau hier stand vorher fest (1, 2, 3, 4).
        assert wt.input.elements == (1, 2, 3)
        assert wt.input._elements_of("ALL") == (1, 2, 3)


def test_sammelbereich_liest_kein_unbestuecktes_element_zurueck():
    """Rueckleseprobe an Element 4.

    Der Sammelknoten ':ALL' geht unveraendert ans Geraet - was 'ALL' dort
    bedeutet, entscheidet das Geraet. Falsch war die Gegenprobe danach: sie
    fragte einen Knoten ab, den es an diesem Geraet nicht gibt.
    """
    transport = VerdrahtbarerTransport(wiring="V3A3,NONE", modules="30,30,30,0")
    with fassade(transport) as wt:
        with wt.input.unlocked(GROUP_RANGE):
            wt.input.set_voltage_range(600.0, target="ALL")

    gefragt = [c for c in transport.written if ":VOLTAGE:RANGE:ELEMENT" in c.upper()]
    assert gefragt == [
        ":INPut:VOLTage:RANGe:ELEMent1?",
        ":INPut:VOLTage:RANGe:ELEMent2?",
        ":INPut:VOLTage:RANGe:ELEMent3?",
    ]
    # Der Sammelknoten selbst ist unveraendert einmal gesendet worden.
    assert ":INPut:VOLTage:RANGe:ALL 600" in transport.written


def test_nicht_bestuecktes_element_wird_abgelehnt():
    """Dieselbe Elementpruefung wie in RangeAccess."""
    transport = VerdrahtbarerTransport(wiring="V3A3,NONE", modules="30,30,30,0")
    with fassade(transport) as wt:
        with wt.input.unlocked(GROUP_RANGE):
            with pytest.raises(WTError, match="nicht bestueckt"):
                wt.input.set_voltage_range(600.0, target=4)


def test_von_hand_gebautes_inputconfig_verhaelt_sich_wie_bisher():
    """Die Voreinstellung bleibt DEFAULT_ELEMENTS.

    Wichtig fuer die Stufenskripte und fuer tools/hardware/: sie bauen
    'InputConfig' selbst und duerfen von dieser Aenderung nichts merken.
    """
    transport = VerdrahtbarerTransport()
    sess = WTSession(transport, WTConfig())
    cfg = InputConfig(sess, allow_changes=False)

    assert cfg.elements == (1, 2, 3, 4)
    assert cfg._elements_of("ALL") == (1, 2, 3, 4)


# ---------------------------------------------------------------------------
# Der Steckbrief bleibt aktuell
# ---------------------------------------------------------------------------


def test_set_wiring_frischt_den_steckbrief_von_selbst_auf():
    """Die Aktualisierung braucht kein Zutun des Anwenders."""
    transport = VerdrahtbarerTransport()
    with fassade(transport) as wt:
        assert wt.device.wiring == ("V3A3", "P1W2")
        assert wt.ranges.expand_scope("SIGMA") == (1, 2, 3)

        with wt.input.unlocked(GROUP_WIRING):
            wt.input.set_wiring(["P1W2", "P1W2", "P1W2", "P1W2"])

        assert wt.device.wiring == ("P1W2", "P1W2", "P1W2", "P1W2")
        # Vier eigenstaendige Units: SIGMA ist jetzt nur noch Element 1.
        assert wt.ranges.expand_scope("SIGMA") == (1,)
        assert wt.ranges.expand_scope("SIGMB") == (2,)


def test_gehaltene_referenzen_ziehen_mit():
    """Die Fachobjekte werden geaendert, nicht ersetzt.

    Ein Anwender darf sich 'wt.ranges' in eine Variable legen. Wuerde die
    Auffrischung ein neues Objekt bauen, arbeitete er danach mit dem alten
    Stand weiter - der Befund waere nur verschoben.
    """
    transport = VerdrahtbarerTransport()
    with fassade(transport) as wt:
        bereiche = wt.ranges
        eingang = wt.input
        assert bereiche.expand_scope("SIGMA") == (1, 2, 3)

        with wt.input.unlocked(GROUP_WIRING):
            wt.input.set_wiring(["P1W2", "P1W2", "P1W2", "P1W2"])

        assert bereiche is wt.ranges
        assert eingang is wt.input
        assert bereiche.expand_scope("SIGMA") == (1,)


def test_eingriff_am_bedienfeld_wird_mit_refresh_device_nachgezogen():
    """Der zweite Weg: geaendert hat jemand anders, nicht dieser Treiber."""
    transport = VerdrahtbarerTransport()
    with fassade(transport) as wt:
        assert wt.ranges.expand_scope("SIGMA") == (1, 2, 3)

        transport.am_bedienfeld_umverdrahten("P1W2,P1W2,P1W2,P1W2")
        # Ohne Auffrischung bleibt der Stand des Verbindungsaufbaus stehen -
        # das ist keine Nachlaessigkeit, sondern die einzige ehrliche Antwort:
        # der Treiber hat von dem Eingriff nichts erfahren koennen.
        assert wt.device.wiring == ("V3A3", "P1W2")

        info = wt.refresh_device()

        assert info.wiring == ("P1W2", "P1W2", "P1W2", "P1W2")
        assert wt.device is info
        assert wt.ranges.expand_scope("SIGMA") == (1,)


def test_refresh_zieht_auch_die_elementliste_nach():
    """Ein Element faellt weg - beide Fachobjekte muessen es merken."""
    transport = VerdrahtbarerTransport()
    with fassade(transport) as wt:
        assert wt.input.elements == (1, 2, 3, 4)
        assert wt.ranges.elements == (1, 2, 3, 4)

        transport.am_bedienfeld_umverdrahten("V3A3,NONE", modules="30,30,30,0")
        wt.refresh_device()

        assert wt.device.elements == (1, 2, 3)
        assert wt.input.elements == (1, 2, 3)
        assert wt.ranges.elements == (1, 2, 3)


def test_identitaet_und_optionen_ueberleben_den_refresh():
    """'DeviceInfo.read(previous=...)' fragt beides nicht erneut ab.

    Modell, Seriennummer, Firmware und verbaute Optionen aendern sich waehrend
    einer Verbindung nicht. Sie erneut abzufragen waere nicht nur unnoetig -
    ein diesmal fehlschlagendes '*IDN?' wuerde eine bekannte Identitaet gegen
    'unbekannt' tauschen und mit options_known=False die Optionspruefung
    stilllegen. Die Auffrischung machte den Steckbrief dann schlechter.
    """
    transport = VerdrahtbarerTransport()
    with fassade(transport) as wt:
        vorher = wt.device
        vor_refresh = [c for c in transport.written if c.upper().startswith(("*IDN", "*OPT"))]

        wt.refresh_device()

        nach_refresh = [c for c in transport.written if c.upper().startswith(("*IDN", "*OPT"))]
        assert vor_refresh == nach_refresh  # kein zweites Mal gefragt
        assert wt.device.serial == vorher.serial
        assert wt.device.firmware == vorher.firmware
        assert wt.device.options == vorher.options
        assert wt.device.options_known is True


def test_read_mit_previous_fragt_idn_und_opt_gar_nicht_erst():
    """Derselbe Nachweis eine Ebene tiefer, ohne Fassade.

    Aufgebaut als Vorher-Nachher an EINER Sitzung, in der '*IDN?' und '*OPT?'
    verweigert werden: ohne 'previous' bleibt der Steckbrief duenn, mit
    'previous' ist er vollstaendig - weil die beiden Abfragen nicht stattfinden
    und deshalb auch nicht scheitern koennen.
    """
    transport = FakeTransport(base_responses(), fail_commands=["*IDN?", "*OPT?"])
    sess = WTSession(transport, WTConfig())

    ohne = DeviceInfo.read(sess)
    assert ohne.identity == "unbekannt"
    assert ohne.options_known is False

    # Ein vollstaendiger Steckbrief, wie ihn ein gelungener Verbindungsaufbau
    # hinterlaesst - hier von Hand gebaut, damit der Test nicht von einer
    # zweiten Sitzung abhaengt.
    vollstaendig = replace(
        ohne,
        identity="YOKOGAWA,WT3000,C1B234567,F2.11",
        serial="C1B234567",
        firmware="F2.11",
        options=frozenset({"G6", "DT"}),
        options_raw="G6,DT",
        options_known=True,
    )

    vorher = len(transport.written)
    aufgefrischt = DeviceInfo.read(sess, previous=vollstaendig)
    gefragt = [c for c in transport.written[vorher:] if c.upper().startswith(("*IDN", "*OPT"))]

    assert gefragt == []
    assert aufgefrischt.identity == vollstaendig.identity
    assert aufgefrischt.options == frozenset({"G6", "DT"})
    assert aufgefrischt.options_known is True
    # Und die geraeteabhaengigen Teile sind frisch gelesen.
    assert aufgefrischt.wiring == ("V3A3", "P1W2")


def test_fehlgeschlagene_auffrischung_wird_nicht_verschluckt():
    """Wenn die Verdrahtung steht, der Steckbrief aber nicht nachziehbar ist.

    Das ist genau der Zustand, den diese Aenderung beseitigen soll - er darf
    deshalb nicht als Protokollzeile enden. Der Aufrufer erfaehrt, dass
    geschrieben WURDE und dass sein Bild jetzt veraltet ist.
    """
    transport = VerdrahtbarerTransport()
    with fassade(transport) as wt:
        transport.fail_commands.add(transport._key(":INPut:MODUle?"))

        with wt.input.unlocked(GROUP_WIRING):
            with pytest.raises(WTError, match="wurde am Geraet gesetzt"):
                wt.input.set_wiring(["P1W2", "P1W2", "P1W2", "P1W2"])

    # Das Kommando ist tatsaechlich hinausgegangen - die Meldung sagt die
    # Wahrheit und nicht das Beruhigende.
    assert any("WIRing P1W2" in c for c in transport.written)


def test_ohne_rueckruf_gebautes_inputconfig_meldet_nichts():
    """Wer 'InputConfig' selbst baut, bekommt keine Nebenwirkung.

    Der Rueckruf ist ein Parameter und keine eingebaute Kopplung: Layer 2
    weiss weiterhin nichts von Layer 4.
    """
    transport = VerdrahtbarerTransport()
    sess = WTSession(transport, WTConfig())
    cfg = InputConfig(sess, allow_changes=True, protected_groups=frozenset())

    cfg.set_wiring(["P1W2", "P1W2", "P1W2", "P1W2"])  # darf einfach durchlaufen

    assert cfg.get_wiring() == ("P1W2", "P1W2", "P1W2", "P1W2")
