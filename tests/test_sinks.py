# =============================================================================
# Datei: tests/test_sinks.py
# Tests des Sink-Protocols und seiner Implementierungen. Auch eine externe
# Senke muss ohne Anpassung der Messschleife funktionieren.
# =============================================================================

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import pytest

from wt3000_scpi.wt3000_core import WTConfig, WTError, WTSession
from wt3000_scpi.wt3000_measure import Sample, SampleMark, SampleSink, run_measurement_loop
from wt3000_scpi.wt3000_numeric import ItemTable, NumericValue, ValueStatus
from wt3000_scpi.wt3000_sinks import (
    CallbackSink,
    CsvSink,
    JsonlSink,
    MultiSink,
    SinkNotOpen,
)
from wt3000_scpi.wt3000_transport import FakeTransport, float_block


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------

SPALTEN = ["U1", "I1", "P1"]


def wert(zahl: float, status: ValueStatus = ValueStatus.OK) -> NumericValue:
    return NumericValue(zahl, status, 0)


def datensatz(nummer: int = 1, *, values=None, mark=SampleMark.OK) -> Sample:
    return Sample(
        timestamp=datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc),
        elapsed_s=float(nummer),
        number=nummer,
        condition=16,
        values=list(values if values is not None else [wert(230.0), wert(1.0), wert(230.0)]),
        mark=mark,
    )


# ---------------------------------------------------------------------------
# 1 - Der Vertrag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "senke",
    [CsvSink, JsonlSink, CallbackSink, MultiSink],
    ids=lambda k: k.__name__,
)
def test_jede_mitgelieferte_senke_erfuellt_das_protocol(senke):
    """Strukturelle Typisierung: keine Basisklasse noetig, nur die drei Methoden."""
    assert issubclass(senke, SampleSink)


def test_protocol_deckt_genau_drei_methoden_ab():
    """Waechst der Vertrag, faellt es hier auf - jede fremde Senke muesste nach.

    Bewusst ueber vars() statt '__protocol_attrs__': letzteres gibt es erst ab
    Python 3.12, das Paket verlangt aber nur 3.10. Dieselbe Begruendung wie
    beim Transport-Protocol in test_fake_transport.py.
    """
    vertrag = {n for n in vars(SampleSink) if not n.startswith("_")}
    assert vertrag == {"open", "write", "close"}


@pytest.mark.parametrize("senke_bauen", [lambda p: CsvSink(p), lambda p: JsonlSink(p)])
def test_schreiben_ohne_open_ist_ein_klarer_fehler(tmp_path, senke_bauen):
    """Kein AttributeError auf None - der Aufrufer soll lesen, was er falsch macht."""
    senke = senke_bauen(tmp_path / "ohne_open.dat")
    with pytest.raises(SinkNotOpen, match="open"):
        senke.write(datensatz())


def test_sinknotopen_ist_eine_wterror():
    """Die Stufenskripte fangen nur WTError - alles andere reisst daran vorbei."""
    assert issubclass(SinkNotOpen, WTError)


@pytest.mark.parametrize("senke_bauen", [lambda p: CsvSink(p), lambda p: JsonlSink(p)])
def test_close_vertraegt_mehrfachen_aufruf(tmp_path, senke_bauen):
    """Die Schleife schliesst im finally, ein 'with' des Aufrufers ggf. noch einmal."""
    senke = senke_bauen(tmp_path / "doppelt.dat")
    senke.open(SPALTEN)
    senke.close()
    senke.close()  # darf nicht knallen


# ---------------------------------------------------------------------------
# 2 - Die Spaltenregel gilt fuer jede Senke, nicht nur fuer CSV
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("senke_bauen", [lambda p: CsvSink(p), lambda p: JsonlSink(p)])
@pytest.mark.parametrize("anzahl", [2, 4], ids=["zu_wenige", "zu_viele"])
def test_abweichende_werteanzahl_bricht_ab(tmp_path, senke_bauen, anzahl):
    """Eine verrutschte Spalte ist der teuerste Fehler - deshalb hart abbrechen."""
    senke = senke_bauen(tmp_path / "verrutscht.dat")
    senke.open(SPALTEN)
    with pytest.raises(WTError, match="Sample 5"):
        senke.write(datensatz(5, values=[wert(1.0)] * anzahl))
    senke.close()


# ---------------------------------------------------------------------------
# 3 - JSONL
# ---------------------------------------------------------------------------


def test_jsonl_legt_metadaten_in_die_datei_statt_in_einen_sidecar(tmp_path):
    ziel = tmp_path / "lauf.jsonl"
    with JsonlSink(ziel) as senke:
        senke.open(SPALTEN, {"sample_interval_s": 1.0})
        senke.write(datensatz(1))

    zeilen = [json.loads(z) for z in ziel.read_text(encoding="utf-8").splitlines()]
    assert zeilen[0]["kind"] == "metadata"
    assert zeilen[0]["columns"] == SPALTEN
    assert zeilen[0]["metadata"] == {"sample_interval_s": 1.0}
    assert zeilen[1]["kind"] == "sample"


def test_jsonl_benennt_werte_statt_sie_zu_positionieren(tmp_path):
    """Der eigentliche Gewinn gegenueber der CSV: keine Spalte kann verrutschen."""
    ziel = tmp_path / "benannt.jsonl"
    with JsonlSink(ziel) as senke:
        senke.open(SPALTEN)
        senke.write(datensatz(1))

    zeile = json.loads(ziel.read_text(encoding="utf-8").splitlines()[1])
    assert zeile["values"] == {"U1": 230.0, "I1": 1.0, "P1": 230.0}


def test_jsonl_schreibt_kein_nan_und_kein_infinity(tmp_path):
    """Beide sind in JSON nicht zulaessig - ein fremder Parser stolperte darueber."""
    ziel = tmp_path / "sonderfaelle.jsonl"
    with JsonlSink(ziel) as senke:
        senke.open(SPALTEN)
        senke.write(
            datensatz(
                1,
                values=[
                    wert(230.0),
                    wert(float("inf"), ValueStatus.OVERRANGE),
                    wert(float("nan"), ValueStatus.NO_DATA),
                ],
            )
        )

    roh = ziel.read_text(encoding="utf-8")
    assert "NaN" not in roh and "Infinity" not in roh
    zeile = json.loads(roh.splitlines()[1])
    assert zeile["values"] == {"U1": 230.0, "I1": None, "P1": None}
    assert zeile["status_flags"] == ["I1=OVERRANGE", "P1=NO_DATA"]


def test_jsonl_fuehrt_die_kennzeichnung_als_eigenes_feld(tmp_path):
    ziel = tmp_path / "markiert.jsonl"
    with JsonlSink(ziel) as senke:
        senke.open(SPALTEN)
        senke.write(datensatz(1, mark=SampleMark.DUPLICATE))

    assert json.loads(ziel.read_text(encoding="utf-8").splitlines()[1])["mark"] == "DUPLICATE"


# ---------------------------------------------------------------------------
# 4 - Rueckruf und Buendelung
# ---------------------------------------------------------------------------


def test_callbacksink_reicht_jeden_datensatz_weiter():
    gesehen: list[Sample] = []
    senke = CallbackSink(gesehen.append)
    senke.open(SPALTEN, {"quelle": "test"})
    senke.write(datensatz(1))
    senke.write(datensatz(2))
    senke.close()

    assert [s.number for s in gesehen] == [1, 2]
    assert senke.columns == SPALTEN
    assert senke.metadata == {"quelle": "test"}


def test_callbacksink_verschluckt_keinen_fehler():
    """Eine kaputte Anzeige soll die Messung anhalten, nicht stumm nichts tun."""

    def kaputt(_sample):
        raise RuntimeError("Anzeige weg")

    senke = CallbackSink(kaputt)
    senke.open(SPALTEN)
    with pytest.raises(RuntimeError, match="Anzeige weg"):
        senke.write(datensatz())


def test_multisink_bedient_alle_senken(tmp_path):
    csv_ziel = tmp_path / "beides.csv"
    jsonl_ziel = tmp_path / "beides.jsonl"
    gesehen: list[Sample] = []

    with MultiSink(CsvSink(csv_ziel), JsonlSink(jsonl_ziel), CallbackSink(gesehen.append)) as m:
        m.open(SPALTEN, {})
        m.write(datensatz(1))

    assert len(list(csv.reader(csv_ziel.open(encoding="utf-8")))) == 2  # Kopf + Zeile
    assert len(jsonl_ziel.read_text(encoding="utf-8").splitlines()) == 2
    assert [s.number for s in gesehen] == [1]


def test_multisink_ohne_senken_wird_abgelehnt():
    """Sonst laeuft eine Messreihe ins Leere, ohne dass es auffaellt."""
    with pytest.raises(WTError, match="ohne Senken"):
        MultiSink()


def test_multisink_schliesst_alle_auch_wenn_eine_scheitert():
    """Dieselbe Regel wie in WT3000.close(): ein Fehlschlag ueberspringt nichts."""

    class Stoerenfried:
        def open(self, columns, metadata=None):
            pass

        def write(self, sample):
            pass

        def close(self):
            raise RuntimeError("Platte voll")

    class Mitschreiber:
        def __init__(self):
            self.geschlossen = False

        def open(self, columns, metadata=None):
            pass

        def write(self, sample):
            pass

        def close(self):
            self.geschlossen = True

    zweite = Mitschreiber()
    m = MultiSink(Stoerenfried(), zweite)
    m.open(SPALTEN, {})
    with pytest.raises(RuntimeError, match="Platte voll"):
        m.close()

    # Die zweite Senke ist trotzdem geschlossen worden - der erste Fehler
    # kommt erst danach heraus.
    assert zweite.geschlossen


# ---------------------------------------------------------------------------
# 5 - Erweiterbarkeit
# ---------------------------------------------------------------------------


def messschleifen_antworten(zyklen: int) -> dict:
    bloecke = [float_block([230.0 + n, 1.0 + n, 230.0 * (1 + n)]) for n in range(zyklen)]
    return {
        ":NUMeric:NORMal?": "3;U,1;I,1;P,1",
        ":NUMeric:HOLD?": "0",
        ":NUMeric:NORMal:VALue?": bloecke,
        ":STATus:CONDition?": "16",
        # Die Messschleife fragt die Geraeterate ab, um
        # den Takt dagegen zu pruefen. Der Eintrag fehlte hier - und dass er
        # gefehlt hat, hat FakeTransport gemeldet statt still etwas zu
        # erfinden. Genau dafuer gibt es die KeyError-Regel.
        ":RATE?": "1.000E+00",
    }


def test_zweites_format_ohne_eine_zeile_aenderung_an_der_schleife(tmp_path):
    """Eine externe Senke funktioniert ohne Aenderung der Messschleife.

    'ListeSink' gibt es im Paket nicht und wird von nichts importiert. Sie
    erfuellt nur den Vertrag - und die Messschleife bedient sie, oeffnet sie
    mit den Spalten aus der Item-Tabelle und schliesst sie am Ende. Genau das
    Die Schleife kennt dabei nur den SampleSink-Vertrag.
    """

    class ListeSink:
        def __init__(self):
            self.columns: list[str] = []
            self.metadata: dict = {}
            self.samples: list[Sample] = []
            self.geschlossen = False

        def open(self, columns, metadata=None):
            self.columns = list(columns)
            self.metadata = dict(metadata or {})

        def write(self, sample):
            self.samples.append(sample)

        def close(self):
            self.geschlossen = True

    transport = FakeTransport(messschleifen_antworten(3))
    sess = WTSession(transport, WTConfig())
    tabelle = ItemTable.read_from_device(sess)
    senke = ListeSink()

    stats = run_measurement_loop(
        session=sess,
        table=tabelle,
        sink=senke,
        interval_s=0.0,
        max_samples=3,
        max_duration_s=None,
        use_hold=True,
        record_condition=True,
        log_every=0,
        metadata={"sample_interval_s": 0.0},
    )

    assert stats.samples == 3
    # Die Schleife hat die Senke in Betrieb genommen und wieder abgeraeumt.
    assert senke.columns == ["U1", "I1", "P1"]
    # Die Angaben des Aufrufers gehen
    # unveraendert durch; die Schleife legt zwei Dinge dazu, die nur sie
    # kennt - die gelesene Geraeterate und die Einheiten der Spalten. Ohne
    # die Rate laesst sich eine Dublettenzahl nicht beurteilen, ohne die
    # Einheiten die Messwerte nicht.
    assert senke.metadata == {
        "sample_interval_s": 0.0,
        "update_rate_s": 1.0,
        "units": {"U1": "V", "I1": "A", "P1": "W"},
    }
    assert senke.geschlossen
    # Und sie hat genau die Datensaetze bekommen, die gemessen wurden.
    assert [s.number for s in senke.samples] == [1, 2, 3]
    assert [s.values[0].value for s in senke.samples] == [230.0, 231.0, 232.0]


def test_spaltenkopf_stammt_aus_der_item_tabelle_nicht_vom_aufrufer(tmp_path):
    """Dadurch koennen Kopf und Daten nicht auseinanderlaufen."""
    transport = FakeTransport(messschleifen_antworten(1))
    sess = WTSession(transport, WTConfig())
    tabelle = ItemTable.read_from_device(sess)
    ziel = tmp_path / "kopf.csv"

    run_measurement_loop(
        session=sess,
        table=tabelle,
        sink=CsvSink(ziel),
        interval_s=0.0,
        max_samples=1,
        max_duration_s=None,
        use_hold=True,
        record_condition=False,
        log_every=0,
    )

    kopf = next(csv.reader(ziel.open(encoding="utf-8")))
    assert kopf[4:-1] == [item.key for item in tabelle.items]
