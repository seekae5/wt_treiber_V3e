# =============================================================================
# Datei: tests/test_takt_und_dubletten.py
# Taktkopplung und Dublettenerkennung.
#
# Der zugrunde liegende Fehler ist geraetefrei reproduzierbar: Bei
# ':RATE 2 s' und 'interval_s=0.1' lieferte die Messschleife vier Datensaetze,
# alle mit SampleMark.OK, alle mit identischen Werten - und keine einzige
# Warnung. Die entstandene Datei sah aus wie eine Messreihe mit zwanzigfacher
# Aufloesung und war eine mit zwanzigfacher Wiederholung.
#
# Diese Datei haelt beide Haelften der Abhilfe fest:
#
#   VORHER   check_sample_interval() benennt einen zu schnellen Takt.
#   WAEHREND run_measurement_loop() kennzeichnet den wiederholten Zyklus.
#
# Geprueft wird ausserdem, was NICHT passieren darf: ein fehlgeschlagenes
# ':RATE?' beendet keine Messreihe, und eine Dublette wird gekennzeichnet und
# nicht verworfen.
# =============================================================================

from __future__ import annotations

import logging

import pytest

from wt3000_scpi.wt3000_core import WTConfig, WTSession
from wt3000_scpi.wt3000_measure import (
    LoopStatistics,
    SampleMark,
    check_sample_interval,
    device_update_rate,
    run_measurement_loop,
)
from wt3000_scpi.wt3000_numeric import ItemTable, read_numeric_block
from wt3000_scpi.wt3000_transport import FakeTransport, float_block

FLOAT_NO_DATA = 0x7FC00000


class SammelSink:
    """Senke, die alles behaelt - der Pruefstand dieser Datei."""

    def __init__(self) -> None:
        self.columns: list[str] = []
        self.metadata: dict = {}
        self.samples: list = []
        self.geschlossen = False

    def open(self, columns, metadata=None) -> None:
        self.columns = list(columns)
        self.metadata = dict(metadata or {})

    def write(self, sample) -> None:
        self.samples.append(sample)

    def close(self) -> None:
        self.geschlossen = True


def antworten(bloecke: list[bytes], rate: str = "1.000E+00") -> dict:
    return {
        ":NUMeric:NORMal?": "3;U,1;I,1;P,1",
        ":NUMeric:HOLD?": "0",
        ":NUMeric:NORMal:VALue?": bloecke,
        ":STATus:CONDition?": "0",
        ":RATE?": rate,
    }


def messen(bloecke, rate="1.000E+00", interval_s=0.0, **kwargs):
    """Eine Messreihe ueber die angegebenen Bloecke fahren."""
    transport = FakeTransport(antworten(bloecke, rate))
    sess = WTSession(transport, WTConfig())
    tabelle = ItemTable.read_from_device(sess)
    senke = SammelSink()
    stats = run_measurement_loop(
        session=sess,
        table=tabelle,
        sink=senke,
        interval_s=interval_s,
        max_samples=len(bloecke),
        max_duration_s=None,
        use_hold=True,
        record_condition=False,
        log_every=0,
        **kwargs,
    )
    return stats, senke, transport


# ---------------------------------------------------------------------------
# 1 - Die Taktpruefung vorab
# ---------------------------------------------------------------------------


def test_zu_schneller_takt_wird_benannt(caplog):
    """Der Befund selbst: 0.1 s Takt gegen ':RATE 2 s'."""
    with caplog.at_level(logging.WARNING, logger="wt3000.measure"):
        check_sample_interval(0.1, 2.0)

    (eintrag,) = [r for r in caplog.records if "Takt" in r.getMessage()]
    text = eintrag.getMessage()
    # Die Meldung muss beide Zahlen nennen - eine Warnung ohne die Werte
    # zwingt zum Nachsehen an genau der Stelle, die man gerade nicht kennt.
    assert "0.100" in text and "2.000" in text
    # Und sie muss sagen, was zu tun ist.
    assert "set_update_rate" in text


def test_passender_takt_meldet_nichts(caplog):
    """Gleicher Takt wie die Rate ist der Normalfall und keine Auffaelligkeit."""
    with caplog.at_level(logging.WARNING, logger="wt3000.measure"):
        check_sample_interval(1.0, 1.0)
        check_sample_interval(2.0, 1.0)  # langsamer lesen ist ohnehin erlaubt
    assert caplog.records == []


def test_gleichstand_scheitert_nicht_an_der_binaerdarstellung(caplog):
    """0.5 gegen 0.5 darf keine Warnung ergeben, auch nicht aus Rundung."""
    with caplog.at_level(logging.WARNING, logger="wt3000.measure"):
        check_sample_interval(0.1 + 0.2, 0.30000000000000004)
    assert caplog.records == []


def test_unbekannte_rate_wird_uebergangen(caplog):
    """Ohne ':RATE' gibt es nichts zu vergleichen - und nichts zu melden."""
    with caplog.at_level(logging.WARNING, logger="wt3000.measure"):
        check_sample_interval(0.1, None)
    assert caplog.records == []


def test_fehlgeschlagenes_rate_beendet_keine_messreihe():
    """Eine Plausibilitaetspruefung darf eine Messung nicht verhindern.

    Das ist die Rangfolge, die 'device_update_rate()' im Docstring zusagt:
    wer messen will, soll messen - er soll nur wissen, was er tut.
    """
    transport = FakeTransport(
        antworten([float_block([1.0, 2.0, 3.0])]),
        fail_commands=[":RATE?"],
    )
    sess = WTSession(transport, WTConfig())

    assert device_update_rate(sess) is None

    tabelle = ItemTable.read_from_device(sess)
    senke = SammelSink()
    stats = run_measurement_loop(
        session=sess,
        table=tabelle,
        sink=senke,
        interval_s=0.0,
        max_samples=1,
        max_duration_s=None,
        use_hold=True,
        record_condition=False,
        log_every=0,
    )
    assert stats.samples == 1
    assert stats.update_rate_s is None
    assert senke.geschlossen


# ---------------------------------------------------------------------------
# 2 - Die Dublettenerkennung waehrend des Laufs
# ---------------------------------------------------------------------------


def test_wiederholter_zyklus_wird_gekennzeichnet():
    """Der Kern von M3-3: dreimal derselbe Block, zweimal DUPLICATE."""
    block = float_block([230.0, 1.0, 230.0])
    stats, senke, _ = messen([block, block, block])

    assert [s.mark for s in senke.samples] == [
        SampleMark.OK,
        SampleMark.DUPLICATE,
        SampleMark.DUPLICATE,
    ]
    assert stats.duplicates == 2
    assert stats.samples == 3
    # Die Zahl, die den Anwender wirklich interessiert.
    assert stats.measured_samples == 1


def test_neue_werte_sind_keine_dublette():
    """Gegenprobe - sonst wuerde der Test oben auch bei 'immer DUPLICATE' gruen."""
    bloecke = [float_block([230.0 + n, 1.0, 230.0]) for n in range(3)]
    stats, senke, _ = messen(bloecke)

    assert all(s.mark is SampleMark.OK for s in senke.samples)
    assert stats.duplicates == 0
    assert stats.measured_samples == 3


def test_dublette_wird_aufgezeichnet_und_nicht_verworfen():
    """Zusage aus dem Docstring der Schleife.

    Eine Dublette ist eine Beobachtung: das Geraet hatte nichts Neues. Wer sie
    verwuerfe, koennte sie nie wieder herstellen - der Zeitpunkt, zu dem noch
    nichts Neues vorlag, waere aus den Daten verschwunden.
    """
    block = float_block([230.0, 1.0, 230.0])
    _, senke, _ = messen([block, block])

    assert len(senke.samples) == 2
    # Und sie ist im Ausgabeformat als solche erkennbar, ohne Zusatzwissen.
    assert senke.samples[1].status_flags(senke.columns) == ["mark=DUPLICATE"]


def test_no_data_verhindert_die_erkennung_nicht():
    """Der Grund fuer den Vergleich auf Rohbytes statt auf Werten.

    Ein Wert ohne Daten kommt als NaN heraus, und NaN != NaN. Ein Vergleich
    der geparsten Wertelisten wuerde zwei bitgleiche Bloecke fuer verschieden
    erklaeren, sobald einer davon ein NO_DATA enthaelt - also ausgerechnet
    dann versagen, wenn das Geraet gerade nichts liefert.
    """
    block = float_block([230.0, FLOAT_NO_DATA, 230.0])
    stats, senke, _ = messen([block, block])

    # Die Werte selbst sind ueber '==' NICHT vergleichbar - genau darum geht es.
    erster, zweiter = (s.values[1].value for s in senke.samples)
    assert erster != erster and zweiter != zweiter  # beide NaN

    assert senke.samples[1].mark is SampleMark.DUPLICATE
    assert stats.duplicates == 1


def test_erkennung_ist_abschaltbar():
    """'mark_duplicates=False' stellt das Verhalten von vor M3-3 wieder her."""
    block = float_block([230.0, 1.0, 230.0])
    stats, senke, _ = messen([block, block], mark_duplicates=False)

    assert all(s.mark is SampleMark.OK for s in senke.samples)
    assert stats.duplicates == 0


def test_taktpruefung_ist_abschaltbar():
    """'check_update_rate=False' fragt ':RATE?' gar nicht erst ab."""
    block = float_block([230.0, 1.0, 230.0])
    _, senke, transport = messen([block], check_update_rate=False)

    assert not any(c.strip().upper().startswith(":RATE") for c in transport.written)
    assert senke.metadata["update_rate_s"] is None


# ---------------------------------------------------------------------------
# 3 - Die Rate wandert mit in die Ausgabe
# ---------------------------------------------------------------------------


def test_geraeterate_steht_in_den_metadaten_der_senke():
    """Ohne sie ist eine Dublettenzahl in der fertigen Datei nicht zu deuten."""
    block = float_block([230.0, 1.0, 230.0])
    stats, senke, _ = messen([block], rate="5.000E-01")

    assert stats.update_rate_s == 0.5
    assert senke.metadata["update_rate_s"] == 0.5


def test_angabe_des_aufrufers_hat_vorrang():
    """Die Schleife legt die Rate dazu - sie ueberschreibt nichts.

    Eine Angabe des Aufrufers ist die aeltere Zusage; sie still zu ersetzen
    waere die Sorte Nebenwirkung, die niemand vermutet.
    """
    block = float_block([230.0, 1.0, 230.0])
    transport = FakeTransport(antworten([block], rate="5.000E-01"))
    sess = WTSession(transport, WTConfig())
    tabelle = ItemTable.read_from_device(sess)
    senke = SammelSink()

    run_measurement_loop(
        session=sess,
        table=tabelle,
        sink=senke,
        interval_s=0.0,
        max_samples=1,
        max_duration_s=None,
        use_hold=True,
        record_condition=False,
        log_every=0,
        metadata={"update_rate_s": "vom Aufrufer"},
    )
    assert senke.metadata["update_rate_s"] == "vom Aufrufer"


# ---------------------------------------------------------------------------
# 4 - Der Baustein darunter
# ---------------------------------------------------------------------------


def test_read_numeric_block_liefert_rohbytes_und_werte():
    """Die Rohbytes sind die Antwort des Geraets, nicht ihre Auswertung."""
    nutzlast = float_block([230.0, 1.0, 230.0])
    transport = FakeTransport({":NUMeric:NORMal:VALue?": nutzlast})
    sess = WTSession(transport, WTConfig())

    payload, values = read_numeric_block(sess, expected_count=3)

    assert len(values) == 3
    assert values[0].value == pytest.approx(230.0)
    # '#4NNNN' ist der Blockkopf; die Nutzlast steht dahinter.
    assert payload == nutzlast[6:]


def test_read_numeric_block_bricht_bei_falscher_anzahl_ab():
    """Die Zusage aus P-3 gilt fuer den neuen Einstieg unveraendert."""
    from wt3000_scpi.wt3000_core import ProtocolError

    transport = FakeTransport({":NUMeric:NORMal:VALue?": float_block([1.0, 2.0])})
    sess = WTSession(transport, WTConfig())

    with pytest.raises(ProtocolError):
        read_numeric_block(sess, expected_count=3)


def test_statistik_weist_dubletten_aus(caplog):
    """Die Zusammenfassung nennt Dubletten und Rate in einem Satz."""
    stats = LoopStatistics(samples=10, duplicates=4, update_rate_s=1.0)
    with caplog.at_level(logging.WARNING, logger="wt3000.measure"):
        stats.log_summary(interval_s=0.5)

    text = " ".join(r.getMessage() for r in caplog.records)
    assert "Dubletten: 4" in text
    assert "echte Messpunkte: 6" in text
    assert ":RATE 1 s" in text
