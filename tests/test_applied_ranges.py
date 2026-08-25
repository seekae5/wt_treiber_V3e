# =============================================================================
# Datei: tests/test_applied_ranges.py
# Rueckstellpfad der Messbereiche: Anwenden, Verifizieren und Restaurieren
# muessen die Zusage einhalten, dass ein sauber verlassener Kontext den
# Ausgangszustand wiederherstellt.
#
# ZUM AUFBAU. Eine Antworttabelle genuegt hier nicht: Verifikation und
# Wiederherstellung LESEN ZURUECK, was sie geschrieben haben. Gegen eine
# starre Tabelle wuerde jeder Schreibvorgang scheinbar wirkungslos bleiben,
# und die Tests pruefeten die Tabelle statt den Ablauf. 'RangeGeraet' unten
# ist deshalb ein kleines Zustandsmodell: es nimmt Set-Kommandos an, merkt
# sie sich und beantwortet Queries daraus.
# =============================================================================

from __future__ import annotations

import json
import logging

import pytest

from wt3000_scpi.wt3000_core import WTError
from wt3000_scpi.wt3000_rangeio import Quantity, RangeAccess, RangeValue
from wt3000_scpi.wt3000_ranging import (
    AutoRangeSpec,
    RangeBackup,
    RangePlan,
    RangeSpec,
    applied_ranges,
    apply_plan,
    check_preconditions,
    probe_range_write_capability,
    restore_ranges,
    verify_plan,
)


class RangeGeraet:
    """Zustandsmodell der Bereichsknoten - beantwortet, was es angenommen hat.

    Bewusst NUR die Knoten, um die es hier geht. Alles andere fuehrt zu einem
    KeyError, genau wie bei 'FakeTransport': eine nicht vorgesehene Abfrage
    soll auffallen und nicht still beantwortet werden.
    """

    def __init__(
        self,
        elements: tuple[int, ...] = (1, 2, 3, 4),
        volt: float = 1000.0,
        strom: float = 5.0,
        independent: bool = True,
    ) -> None:
        self.elements = elements
        self.written: list[str] = []
        self.independent = independent
        #: Element -> (Bereich, Autorange) je Messgroesse.
        self.bereiche = {
            Quantity.VOLTAGE: {e: RangeValue(volt, False) for e in elements},
            Quantity.CURRENT: {e: RangeValue(strom, False) for e in elements},
        }
        self.autos = {
            Quantity.VOLTAGE: {e: False for e in elements},
            Quantity.CURRENT: {e: False for e in elements},
        }
        #: Kommandos, die das Geraet ablehnen soll (simulierter Fehler).
        self.verweigert: set[str] = set()
        #: Wenn gesetzt: Schreibvorgaenge werden angenommen, wirken aber nicht.
        self.taub = False

    # -- Sitzungsschnittstelle ---------------------------------------------

    def query(self, command: str) -> str:
        self.written.append(command)
        text = command.strip().rstrip("?")
        if text == ":INPut:INDependent":
            return "1" if self.independent else "0"
        if text == ":INPut:WIRing":
            return "V3A3,P1W2"
        if text == ":INPut:MODUle":
            return ",".join("30" for _ in self.elements)
        for quantity in Quantity:
            for element in self.elements:
                if text == f"{quantity.value}:RANGe:ELEMent{element}":
                    return self._range_antwort(self.bereiche[quantity][element])
                if text == f"{quantity.value}:AUTO:ELEMent{element}":
                    return "1" if self.autos[quantity][element] else "0"
        raise KeyError(f"RangeGeraet hat keine Antwort fuer {command!r}")

    def write(self, command: str) -> None:
        self.written.append(command)
        if command in self.verweigert:
            raise WTError(f"Geraet lehnt ab: {command}")
        if self.taub:
            return
        self._uebernehmen(command)

    def read_error_queue(self, max_entries: int = 20) -> list[str]:
        return []

    def assert_no_error(self, context: str = "") -> None:
        return None

    # -- Geraetemodell ------------------------------------------------------

    @staticmethod
    def _range_antwort(value: RangeValue) -> str:
        if value.sensor:
            return f"EXTERNAL,{value.value:.2E}"
        return f"{value.value:.3E}"

    def _ziele(self, suffix: str) -> tuple[int, ...]:
        if suffix == ":ALL":
            return self.elements
        if suffix == ":SIGMA":
            return tuple(e for e in self.elements if e in (1, 2, 3))
        if suffix == ":SIGMB":
            return tuple(e for e in self.elements if e == 4)
        return (int(suffix.removeprefix(":ELEMent")),)

    def _uebernehmen(self, command: str) -> None:
        knoten, _, parameter = command.strip().partition(" ")
        for quantity in Quantity:
            if knoten.startswith(f"{quantity.value}:RANGe"):
                suffix = knoten.removeprefix(f"{quantity.value}:RANGe")
                sensor = parameter.upper().startswith("EXTERNAL")
                zahl = float(parameter.split(",")[-1] if sensor else parameter)
                for element in self._ziele(suffix):
                    self.bereiche[quantity][element] = RangeValue(zahl, sensor)
                return
            if knoten.startswith(f"{quantity.value}:AUTO"):
                suffix = knoten.removeprefix(f"{quantity.value}:AUTO")
                for element in self._ziele(suffix):
                    self.autos[quantity][element] = parameter.upper() == "ON"
                return
        raise AssertionError(f"RangeGeraet kennt das Kommando nicht: {command!r}")


@pytest.fixture
def geraet() -> RangeGeraet:
    return RangeGeraet()


@pytest.fixture
def access(geraet: RangeGeraet) -> RangeAccess:
    return RangeAccess(
        geraet,  # type: ignore[arg-type]  - erfuellt die benutzte Teilmenge
        allow_changes=True,
        elements=geraet.elements,
        sigma_members={"SIGMA": (1, 2, 3), "SIGMB": (4,)},
    )


def plan_600v_element4() -> RangePlan:
    return RangePlan.of(RangeSpec(Quantity.VOLTAGE, scope=4, value=600.0))


# ---------------------------------------------------------------------------
# 1 - Die Bausteine einzeln
# ---------------------------------------------------------------------------


def test_vorbedingungen_warnen_bei_gekoppelten_elementen(geraet, access, caplog):
    """':INPut:INDependent' AUS heisst: elementweise Kommandos wirken evtl. gekoppelt."""
    geraet.independent = False
    with caplog.at_level(logging.WARNING, logger="wt3000.ranging"):
        check_preconditions(access)
    assert any("INDependent" in r.getMessage() for r in caplog.records)


def test_schreibprobe_setzt_den_eigenen_wert(geraet, access):
    """Nulleffekt-Probe: beweist den Schreibpfad, ohne etwas zu veraendern."""
    backup = RangeBackup.capture(access)
    vorher = dict(geraet.bereiche[Quantity.VOLTAGE])

    probe_range_write_capability(access, backup)

    assert geraet.bereiche[Quantity.VOLTAGE] == vorher
    assert any(":VOLTage:RANGe:ELEMent1 " in c for c in geraet.written)


def test_schreibprobe_erkennt_einen_wirkungslosen_schreibpfad(geraet, access):
    """Der Fall, fuer den die Probe gebaut ist: das Geraet nimmt an und tut nichts.

    Am realen Geraet ist das der vermutete REMOTE-Fall (ROADMAP M0-3). Die
    Probe muss ihn VOR dem Plan bemerken - danach waere der Ausgangszustand
    bereits verlassen, ohne dass es jemand mitbekommen haette.
    """
    backup = RangeBackup.capture(access)
    geraet.bereiche[Quantity.VOLTAGE][1] = RangeValue(300.0, False)  # Drift
    geraet.taub = True

    with pytest.raises(WTError, match="Schreibprobe fehlgeschlagen"):
        probe_range_write_capability(access, backup)


def test_apply_plan_schaltet_autorange_vor_dem_bereich_ab(geraet, access):
    """Die Reihenfolge ist nicht beliebig - ein fester Bereich bei aktivem
    Autorange waere wirkungslos, sobald das Geraet neu skaliert."""
    geraet.autos[Quantity.VOLTAGE][4] = True

    geschrieben = apply_plan(access, plan_600v_element4())

    assert geschrieben == 2
    befehle = [c for c in geraet.written if not c.endswith("?")]
    assert befehle == [
        ":INPut:VOLTage:AUTO:ELEMent4 OFF",
        ":INPut:VOLTage:RANGe:ELEMent4 600",
    ]
    assert geraet.autos[Quantity.VOLTAGE][4] is False
    assert geraet.bereiche[Quantity.VOLTAGE][4] == RangeValue(600.0, False)


def test_apply_plan_setzt_gewolltes_autorange_zuletzt(geraet, access):
    """Sonst wuerde Schritt 1 ein ausdrueckliches Autorange EIN wieder loeschen."""
    plan = RangePlan.of(
        RangeSpec(Quantity.VOLTAGE, scope=1, value=600.0),
        AutoRangeSpec(Quantity.CURRENT, scope=1, state=True),
    )
    apply_plan(access, plan)

    assert geraet.autos[Quantity.CURRENT][1] is True
    befehle = [c for c in geraet.written if not c.endswith("?")]
    assert befehle[-1] == ":INPut:CURRent:AUTO:ELEMent1 ON"


def test_verify_plan_meldet_eine_abweichung(geraet, access):
    """Das Geraet hat etwas anderes eingestellt als verlangt."""
    apply_plan(access, plan_600v_element4())
    geraet.bereiche[Quantity.VOLTAGE][4] = RangeValue(300.0, False)  # Geraet weicht ab

    probleme = verify_plan(access, plan_600v_element4())

    assert len(probleme) == 1
    assert "Element 4" in probleme[0]
    assert "600" in probleme[0] and "300" in probleme[0]


def test_verify_plan_ist_still_wenn_alles_stimmt(geraet, access):
    apply_plan(access, plan_600v_element4())
    assert verify_plan(access, plan_600v_element4()) == []


def test_allow_snapping_wertet_eine_anpassung_als_warnung(geraet, access, caplog):
    """Ob das Geraet rundet, ist ZU VERIFIZIEREN (M0-2) - der Schalter dafuer steht."""
    apply_plan(access, plan_600v_element4())
    geraet.bereiche[Quantity.VOLTAGE][4] = RangeValue(500.0, False)

    with caplog.at_level(logging.WARNING, logger="wt3000.ranging"):
        probleme = verify_plan(access, plan_600v_element4(), allow_snapping=True)

    assert probleme == []
    assert any("angepasst" in r.getMessage() for r in caplog.records)


def test_restore_schreibt_nur_was_abweicht(geraet, access):
    """Set-Kommandos kosten 100-250 ms - unnoetige gehoeren vermieden."""
    backup = RangeBackup.capture(access)
    apply_plan(access, plan_600v_element4())
    geraet.written.clear()

    restore_ranges(access, backup)

    assert geraet.bereiche[Quantity.VOLTAGE][4] == RangeValue(1000.0, False)
    # Nur Element 4 war verstellt - die uebrigen drei bleiben unberuehrt.
    gesetzt = [c for c in geraet.written if not c.endswith("?")]
    assert all("ELEMent4" in c for c in gesetzt)


def test_restore_mit_force_schreibt_alles(geraet, access):
    backup = RangeBackup.capture(access)
    geraet.written.clear()

    geschrieben = restore_ranges(access, backup, force=True)

    assert geschrieben > 4
    assert any("ELEMent1" in c for c in geraet.written if not c.endswith("?"))


# ---------------------------------------------------------------------------
# 2 - Der Context Manager: die Zusage der README
# ---------------------------------------------------------------------------


def test_ausgangszustand_steht_nach_dem_block_wieder(geraet, access):
    """Der Kern: sichern, verstellen, messen, zurueckstellen."""
    vorher_bereiche = dict(geraet.bereiche[Quantity.VOLTAGE])
    vorher_autos = dict(geraet.autos[Quantity.VOLTAGE])

    with applied_ranges(access, plan_600v_element4()) as report:
        # Im Block gilt der Plan.
        assert geraet.bereiche[Quantity.VOLTAGE][4] == RangeValue(600.0, False)
        assert report.problems == []

    assert geraet.bereiche[Quantity.VOLTAGE] == vorher_bereiche
    assert geraet.autos[Quantity.VOLTAGE] == vorher_autos
    assert report.restore_problems == []


def test_rueckstellung_auch_bei_einem_fehler_im_nutzblock(geraet, access):
    """Das 'finally' ist der ganze Punkt - es gilt auch bei Strg+C."""
    vorher = dict(geraet.bereiche[Quantity.VOLTAGE])

    with pytest.raises(RuntimeError, match="Messfehler"):
        with applied_ranges(access, plan_600v_element4()):
            raise RuntimeError("Messfehler mitten im Block")

    assert geraet.bereiche[Quantity.VOLTAGE] == vorher


def test_rueckstellung_auch_bei_strg_c(geraet, access):
    """KeyboardInterrupt erbt nicht von Exception - ein 'except Exception'
    wuerde ihn nicht fangen. Deshalb ausdruecklich geprueft."""
    vorher = dict(geraet.bereiche[Quantity.VOLTAGE])

    with pytest.raises(KeyboardInterrupt):
        with applied_ranges(access, plan_600v_element4()):
            raise KeyboardInterrupt

    assert geraet.bereiche[Quantity.VOLTAGE] == vorher


def test_abweichung_beim_anwenden_bricht_ab_und_stellt_zurueck(geraet, access):
    """Ein Plan, den das Geraet nicht uebernimmt, darf nicht gemessen werden."""
    vorher = dict(geraet.bereiche[Quantity.VOLTAGE])
    betreten = False

    class Stur(RangeGeraet):
        """Nimmt den Plan an, stellt aber etwas anderes ein."""

        def _uebernehmen(self, command: str) -> None:
            super()._uebernehmen(command)
            if "RANGe:ELEMent4 600" in command:
                self.bereiche[Quantity.VOLTAGE][4] = RangeValue(300.0, False)

    stur = Stur()
    zugriff = RangeAccess(
        stur,  # type: ignore[arg-type]
        allow_changes=True,
        elements=stur.elements,
        sigma_members={"SIGMA": (1, 2, 3), "SIGMB": (4,)},
    )

    with pytest.raises(WTError, match="Abweichung"):
        with applied_ranges(zugriff, plan_600v_element4()):
            betreten = True

    assert betreten is False, "der Nutzblock darf gar nicht erst laufen"
    assert stur.bereiche[Quantity.VOLTAGE] == vorher


def test_nur_lesender_zugriff_wird_abgelehnt(geraet):
    """Zwei Schloesser - dieses hier greift vor dem ersten Kommando."""
    lesend = RangeAccess(
        geraet,  # type: ignore[arg-type]
        allow_changes=False,
        elements=geraet.elements,
        sigma_members={"SIGMA": (1, 2, 3)},
    )
    with pytest.raises(WTError, match="allow_changes=True"):
        with applied_ranges(lesend, plan_600v_element4()):
            pass
    assert geraet.written == [], "es darf kein einziges Kommando hinausgehen"


def test_backup_datei_wird_vor_der_ersten_aenderung_geschrieben(geraet, access, tmp_path):
    """Sie ist das Netz fuer den Fall, dass die Rueckstellung selbst scheitert -
    also muss sie stehen, BEVOR geschrieben wird."""
    ziel = tmp_path / "bereiche.json"

    with applied_ranges(access, plan_600v_element4(), backup_file=ziel):
        assert ziel.exists(), "das Backup muss im Block bereits auf Platte liegen"

    daten = json.loads(ziel.read_text(encoding="utf-8"))
    assert daten["wiring"] == "V3A3,P1W2"
    element4 = [s for s in daten["states"] if s["element"] == 4][0]
    assert element4["voltage_range"] == 1000.0


def test_gescheiterte_rueckstellung_kommt_als_ausnahme_heraus(geraet, access, caplog):
    """Kein stiller Fehlschlag.

    Wer den Block ohne Ausnahme verlaesst, soll sich auf den Ausgangszustand
    verlassen duerfen. Misslingt die Wiederherstellung, muss das laut Docstring
    als Ausnahme herauskommen und nicht nur ins Protokoll.
    """
    with pytest.raises(WTError):
        with applied_ranges(access, plan_600v_element4()):
            # Ab jetzt lehnt das Geraet genau das Rueckstellkommando ab.
            geraet.verweigert.add(":INPut:VOLTage:RANGe:ELEMent4 1000")

    assert any("Wiederherstellung" in r.getMessage() for r in caplog.records)


def test_report_nennt_die_geschriebenen_kommandos(geraet, access):
    with applied_ranges(access, plan_600v_element4()) as report:
        pass
    assert report.commands_written == 2
    assert report.backup.state_of(4).voltage_range == RangeValue(1000.0, False)
