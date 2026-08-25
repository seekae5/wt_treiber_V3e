# =============================================================================
# Datei: tests/test_protokollzustand.py
# Tests fuer Pruefung und optionales Herstellen des Protokollzustands.
#
# Der Fall ist heikler, als er aussieht, und deshalb ausdruecklich getestet:
# Steht der Header auf 1, antwortet das Geraet auf die Frage nach dem Header
# mit ':COMMUNICATE:HEADER 1' statt mit '1' (Handbuch IM WT3001E-17EN, 6-24).
# Die Erhebung des Ist-Zustands muss also genau die Antwortform verkraften,
# die es nur im Fehlerfall gibt.
# =============================================================================

from __future__ import annotations

import pytest

from wt3000_scpi import WT3000, WTConfig
from wt3000_scpi.wt3000_core import WTError
from wt3000_scpi.wt3000_transport import FakeTransport

from conftest import base_responses

HEADER = ":COMMUNICATE:HEADER"
VERBOSE = ":COMMUNICATE:VERBOSE"
FORMAT = ":NUMERIC:FORMAT"


class ProtokollGeraet(FakeTransport):
    """Transport, der die drei Protokollknoten wirklich fuehrt.

    Ahmt dabei das Geraet nach: steht HEADer auf 1, tragen ALLE Antworten
    einen Kopf. Ohne diese Eigenheit wuerde der Test den einen Fall nicht
    treffen, um den es geht.
    """

    def __init__(self, header: str = "0", verbose: str = "0", fmt: str = "FLOat") -> None:
        super().__init__(base_responses())
        self.zustand = {HEADER: header, VERBOSE: verbose, FORMAT: fmt}

    def _lookup(self, command: str) -> bytes:
        key = self._key(command)
        if key in self.zustand:
            wert = self.zustand[key]
            if self.zustand[HEADER] == "1":
                wert = f"{key} {wert}"
            return self._as_bytes(wert)
        return super()._lookup(command)

    def write(self, command: str) -> None:
        super().write(command)
        text = command.strip()
        # 'FakeTransport.query()' protokolliert jeden Query ueber write() -
        # ohne diese Zeile wuerde die Frage nach dem Header ihn auf '' setzen.
        if text.endswith("?") or " " not in text:
            return
        knoten, _, wert = text.partition(" ")
        key = self._key(knoten)
        if key in self.zustand:
            self.zustand[key] = wert.strip()

    @property
    def gesetzte(self) -> list[str]:
        """Nur die Set-Kommandos, in Reihenfolge."""
        return [c for c in self.written if not c.endswith("?")]


def fassade(transport: FakeTransport, **kwargs) -> WT3000:
    kwargs.setdefault("read_only", False)
    kwargs.setdefault("allow_changes", True)
    return WT3000.from_transport(transport, WTConfig(use_remote=False), **kwargs)


# ---------------------------------------------------------------------------
# Ist-Zustand erheben
# ---------------------------------------------------------------------------


def test_ist_zustand_wird_auch_mit_eingeschaltetem_header_gelesen():
    """Der Fall, den es nur im Fehlerfall gibt - und genau dort zaehlt er."""
    transport = ProtokollGeraet(header="1")
    with fassade(transport, read_only=True, allow_changes=False) as wt:
        ist = wt.protocol_state()

    # Ohne Kopfentfernung stuende hier ':COMMUNICATE:HEADER 1'.
    assert ist[":COMMunicate:HEADer"] == "1"
    assert ist[":NUMeric:FORMat"] == "FLOat"


def test_ist_zustand_veraendert_nichts():
    transport = ProtokollGeraet(header="1")
    with fassade(transport, read_only=True, allow_changes=False) as wt:
        wt.protocol_state()
    assert transport.gesetzte == []


# ---------------------------------------------------------------------------
# Herstellen und zuruecknehmen
# ---------------------------------------------------------------------------


def test_richtiger_zustand_wird_nicht_angefasst():
    """Der Normalfall darf kein einziges Set-Kommando ausloesen.

    Gemessen wird INNERHALB des Fassadenblocks: 'close()' schickt danach noch
    sein eigenes Aufraeumkommando (':NUMeric:HOLD OFF'), das mit dem
    Protokollzustand nichts zu tun hat.
    """
    transport = ProtokollGeraet()
    with fassade(transport) as wt:
        with wt.ensured_protocol_state() as geaendert:
            assert geaendert == {}
        assert transport.gesetzte == []


def test_verstellter_header_wird_hergestellt_und_zurueckgenommen():
    """Der Kern von M1-4."""
    transport = ProtokollGeraet(header="1")
    with fassade(transport) as wt:
        with wt.ensured_protocol_state() as geaendert:
            assert geaendert == {":COMMunicate:HEADer": "1"}
            # Im Block gilt der Sollzustand.
            assert transport.zustand[HEADER] == "0"

        # Danach steht wieder, was vorgefunden wurde.
        assert transport.zustand[HEADER] == "1"


def test_alle_drei_knoten_gemeinsam():
    transport = ProtokollGeraet(header="1", verbose="1", fmt="ASCii")
    with fassade(transport) as wt:
        with wt.ensured_protocol_state() as geaendert:
            assert set(geaendert) == {
                ":COMMunicate:HEADer",
                ":COMMunicate:VERBose",
                ":NUMeric:FORMat",
            }
            assert transport.zustand == {HEADER: "0", VERBOSE: "0", FORMAT: "FLOat"}
            # Und der Sollzustand haelt jetzt auch der eigenen Pruefung stand.
            wt.check_protocol_state()

    assert transport.zustand == {HEADER: "1", VERBOSE: "1", FORMAT: "ASCii"}


def test_header_wird_zuerst_gesetzt_und_zuletzt_zurueckgenommen():
    """Reihenfolge mit Grund: solange der Header aus ist, kommen die
    Rueckleseproben der uebrigen Knoten ohne Kopf."""
    transport = ProtokollGeraet(header="1", verbose="1", fmt="ASCii")
    with fassade(transport) as wt:
        with wt.ensured_protocol_state():
            pass
        knoten = [c.split(" ")[0] for c in transport.gesetzte]

    assert knoten[0] == ":COMMunicate:HEADer"
    assert knoten[-1] == ":COMMunicate:HEADer"
    # Genau drei hin und drei zurueck.
    assert len(knoten) == 6


def test_rueckstellung_auch_bei_einem_fehler_im_block():
    transport = ProtokollGeraet(header="1")
    with pytest.raises(RuntimeError):
        with fassade(transport) as wt:
            with wt.ensured_protocol_state():
                raise RuntimeError("Messfehler")
    assert transport.zustand[HEADER] == "1"


def test_rueckstellung_auch_bei_strg_c():
    transport = ProtokollGeraet(header="1")
    with pytest.raises(KeyboardInterrupt):
        with fassade(transport) as wt:
            with wt.ensured_protocol_state():
                raise KeyboardInterrupt
    assert transport.zustand[HEADER] == "1"


def test_wirkungsloses_set_kommando_faellt_auf():
    """Rueckleseprobe wie ueberall sonst im Paket - das Geraet quittiert nicht."""

    class Taub(ProtokollGeraet):
        def write(self, command: str) -> None:
            FakeTransport.write(self, command)  # annehmen, aber nicht uebernehmen

    transport = Taub(header="1")
    with fassade(transport) as wt:
        with pytest.raises(WTError, match="liess sich nicht auf"):
            with wt.ensured_protocol_state():
                pass


# ---------------------------------------------------------------------------
# Nur-Lesen-Sitzungen
# ---------------------------------------------------------------------------


def test_lesende_sitzung_laeuft_durch_wenn_der_zustand_stimmt():
    """Eine lesende Sitzung an einem richtig eingestellten Geraet braucht
    diese Methode nicht zu fuerchten."""
    transport = ProtokollGeraet()
    with fassade(transport, read_only=True, allow_changes=False) as wt:
        with wt.ensured_protocol_state() as geaendert:
            assert geaendert == {}
    assert transport.gesetzte == []


def test_lesende_sitzung_bricht_klar_ab_wenn_geschrieben_werden_muesste():
    """Die Nur-Lesen-Zusage steht hoeher als die Bequemlichkeit.

    Wichtig ist die Meldung: sie muss sagen, WAS abweicht und WELCHE zwei Wege
    es gibt - sonst steht der Anwender vor einem Abbruch ohne Ausweg, und
    genau das war der Zustand vor M1-4.
    """
    transport = ProtokollGeraet(header="1")
    with fassade(transport, read_only=True, allow_changes=False) as wt:
        with pytest.raises(WTError) as fehler:
            with wt.ensured_protocol_state():
                pass

    text = str(fehler.value)
    assert ":COMMunicate:HEADer" in text
    assert "read_only=False" in text and "Bedienfeld" in text
    assert transport.gesetzte == []
