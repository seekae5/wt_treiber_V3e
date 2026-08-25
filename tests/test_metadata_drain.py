# =============================================================================
# Datei: tests/test_metadata_drain.py
# write_metadata() muss nach einem fehlgeschlagenen Query aufraeumen. Die
# Funktion setzt nach einem Fehler mit der naechsten Abfrage fort:
#
#     for key, command in queries.items():
#         try:
#             device[key] = session.query(command)
#         except WTError as error:
#             device[key] = f"<Fehler: {error}>"   # und weiter zur naechsten
#
# Eine verspaetete Antwort darf dadurch nicht der naechsten Abfrage zugeordnet
# werden, etwa ':INPut?' dem Feld 'input_wiring'.
#
# Das Sidecar sieht dann plausibel aus und ist falsch - und es ist die Datei,
# aus der eine Messreihe spaeter interpretiert wird. Ein plausibel aussehendes,
# falsches Sidecar ist schlimmer als ein fehlendes.
#
# 'WTSession.drain_after_failure()' ist fuer genau diesen Fall gebaut, ist
# getestet und war im gesamten Produktivcode ungenutzt (Befund S-03). Es setzt
# das Timeout kurz herunter, liest einmal, verwirft und stellt das Timeout im
# finally wieder her.
#
# DIE VORRICHTUNG bildet den Fall ohne Kunstgriff nach: 'FakeTransport.prime()'
# gibt es genau dafuer - sein Docstring nennt den Fall woertlich ("eine
# verspaetete Antwort, die keiner Abfrage mehr zugeordnet ist"). Die Antwort
# auf ':INPut?' legt die verspaetete Antwort ab und scheitert dann; alles
# Weitere ergibt sich aus der Reihenfolge in _pending.
# =============================================================================

from __future__ import annotations

import json

import pytest

from wt3000_scpi.wt3000_core import WTSession
from wt3000_scpi.wt3000_measure import write_metadata
from wt3000_scpi.wt3000_numeric import ItemTable, NumericItem
from wt3000_scpi.wt3000_transport import FakeTransport, TmctlError, WTConfig

#: Rumpf, den das Geraet auf ':INPut?' liefert - lang, deshalb der Kandidat
#: fuer einen Timeout. Er darf in KEINEM anderen Feld auftauchen.
INPUT_RUMPF = "ELEMENT1,1000V,10A;ELEMENT2,1000V,10A;ELEMENT3,1000V,10A"

ANTWORTEN = {
    "*IDN": "YOKOGAWA,WT3000,0,F1.71",
    ":COMMUNICATE": "0,0,0",
    ":RATE": "1.000E+00",
    ":NUMERIC:FORMAT": "FLOAT",
    ":INPUT:WIRING": "V3A3,P1W2",
    ":INPUT:MODULE": "30,30,30,30",
    ":INPUT:SCALING": "0",
    ":INPUT:FILTER": "OFF",
    ":INPUT:CFACTOR": "3",
    ":MEASURE": "NORMAL",
}


def tabelle() -> ItemTable:
    return ItemTable(number=1, items=[NumericItem(index=1, function="U", element="1")])


@pytest.fixture
def lauf(tmp_path):
    """Sitzung, in der ':INPut?' scheitert und eine Antwort nachlaeuft."""
    transport = FakeTransport(dict(ANTWORTEN))

    def _scheitert_und_antwortet_zu_spaet(command: str):
        # Erst die verspaetete Antwort ablegen, dann scheitern - genau die
        # Reihenfolge, die am Geraet einen Timeout ausmacht: das Geraet HAT
        # geantwortet, nur zu spaet fuer diesen Query.
        transport.prime(INPUT_RUMPF)
        raise TmctlError("TmcReceive", 0x2, command)

    transport.responses[":INPUT"] = _scheitert_und_antwortet_zu_spaet
    sitzung = WTSession(transport, WTConfig(ip="10.0.0.5"), read_only=True)
    return transport, sitzung, tmp_path / "meta.json"


def gelesen(pfad) -> dict:
    return json.loads(pfad.read_text(encoding="utf-8"))["device"]


# ---------------------------------------------------------------------------
# Der Kern von A-07
# ---------------------------------------------------------------------------


def test_verspaetete_antwort_landet_nicht_im_naechsten_feld(lauf):
    """Der eigentliche Befund.

    Ohne drain_after_failure() liest ':INPut:WIRing?' die nachlaufende Antwort
    auf ':INPut?' - das Feld 'input_wiring' enthaelt dann den INPut-Rumpf und
    sieht dabei voellig plausibel aus.
    """
    transport, sitzung, pfad = lauf
    write_metadata(pfad, sitzung, tabelle(), parameters={})

    geraet = gelesen(pfad)
    assert geraet["input_wiring"] == "V3A3,P1W2", (
        f"input_wiring traegt {geraet['input_wiring']!r} - die verspaetete "
        "Antwort auf ':INPut?' ist in die naechste Zeile gerutscht"
    )


def test_der_inputrumpf_taucht_in_keinem_feld_auf(lauf):
    """Strenger: die verspaetete Antwort darf NIRGENDS landen.

    Ohne die Reparatur verschieben sich potenziell alle folgenden Felder um
    eins - der Pruefsatz oben faende nur die erste Verschiebung.
    """
    transport, sitzung, pfad = lauf
    write_metadata(pfad, sitzung, tabelle(), parameters={})

    verschmutzt = [k for k, v in gelesen(pfad).items() if INPUT_RUMPF in str(v)]
    assert not verschmutzt, f"Verspaetete Antwort gelandet in: {verschmutzt}"


def test_alle_uebrigen_felder_stimmen(lauf):
    """Nach dem Aufraeumen steht jedes Feld auf seiner eigenen Antwort."""
    transport, sitzung, pfad = lauf
    write_metadata(pfad, sitzung, tabelle(), parameters={})

    geraet = gelesen(pfad)
    assert geraet["idn"] == "YOKOGAWA,WT3000,0,F1.71"
    assert geraet["rate"] == "1.000E+00"
    assert geraet["input_module"] == "30,30,30,30"
    assert geraet["measure"] == "NORMAL"


# ---------------------------------------------------------------------------
# Was sich NICHT aendern darf
# ---------------------------------------------------------------------------


def test_das_gescheiterte_feld_bleibt_als_fehler_vermerkt(lauf):
    """Die Metadaten sagen weiterhin, dass diese Abfrage nicht geklappt hat.

    Das Aufraeumen darf den Fehler nicht verschlucken - sonst sieht das
    Sidecar vollstaendig aus, obwohl ein Feld fehlt.
    """
    transport, sitzung, pfad = lauf
    write_metadata(pfad, sitzung, tabelle(), parameters={})

    assert gelesen(pfad)["input"].startswith("<Fehler:")


def test_der_lauf_bricht_nicht_ab(lauf):
    """write_metadata() ist bewusst nachsichtig - das bleibt so.

    Die Metadaten neben einer Messreihe sind eine Zugabe; ein fehlgeschlagener
    Query darf die Messung nicht verhindern. Genau deshalb ist dies ueberhaupt
    die einzige Stelle mit einem weiterlaufenden except.
    """
    transport, sitzung, pfad = lauf
    write_metadata(pfad, sitzung, tabelle(), parameters={"kommentar": "Probelauf"})

    assert pfad.is_file()
    inhalt = json.loads(pfad.read_text(encoding="utf-8"))
    assert inhalt["parameters"] == {"kommentar": "Probelauf"}
    assert inhalt["item_table"]["number"] == 1


def test_das_timeout_wird_wiederhergestellt(lauf):
    """drain_after_failure() senkt das Timeout und muss es zuruecksetzen.

    Bliebe das kurze Drain-Timeout stehen, liefe jede folgende Abfrage der
    Messreihe mit 500 ms statt mit 5000 ms - und die Reparatur erzeugte einen
    schlimmeren Fehler als den, den sie behebt.
    """
    transport, sitzung, pfad = lauf
    write_metadata(pfad, sitzung, tabelle(), parameters={})

    assert transport.timeouts_ms, "Es wurde ueberhaupt kein Timeout gesetzt"
    assert transport.timeouts_ms[-1] == WTConfig().timeout_ms


def test_ohne_fehler_wird_nicht_aufgeraeumt(tmp_path):
    """Der glatte Weg bleibt unberuehrt - kein zusaetzlicher Lesevorgang."""
    transport = FakeTransport(dict(ANTWORTEN, **{":INPUT": INPUT_RUMPF}))
    sitzung = WTSession(transport, WTConfig(ip="10.0.0.5"), read_only=True)

    write_metadata(tmp_path / "meta.json", sitzung, tabelle(), parameters={})

    assert transport.timeouts_ms == [], "Ohne Fehler darf kein Drain laufen"
    assert gelesen(tmp_path / "meta.json")["input"] == INPUT_RUMPF
