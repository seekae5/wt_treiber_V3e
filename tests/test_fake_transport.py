# =============================================================================
# Datei: tests/test_fake_transport.py
# Geraetefreie Gegenprobe zum Transport-Protocol. Sie deckt Query-Regeln,
# Nur-Lesen-Sperre, mehrteilige Blockdaten, Fehlerqueue und verspaetete
# Antworten auf der kompletten Kette ab:
#   FakeTransport -> WTSession -> ItemTable -> Messschleife -> CsvSink
# =============================================================================

from __future__ import annotations

import csv
from datetime import datetime, timezone

import pytest

from wt3000_scpi.wt3000_core import (
    DeviceError,
    ProtocolError,
    ReadOnlyViolation,
    TmctlError,
    Transport,
    WTConfig,
    WTError,
    WTSession,
)
from wt3000_scpi.wt3000_measure import (
    Sample,
    run_measurement_loop,
    write_metadata,
)
from wt3000_scpi.wt3000_sinks import CsvSink
from wt3000_scpi.wt3000_numeric import (
    FLOAT_NO_DATA,
    FLOAT_OVERRANGE,
    ItemTable,
    NumericValue,
    ValueStatus,
    parse_float_block,
    read_numeric_values,
)
from wt3000_scpi.wt3000_transport import FakeTransport, TmctlTransport, float_block

CONFIG = WTConfig(timeout_ms=5000, drain_timeout_ms=500)


def session(responses=None, read_only=False, **kwargs) -> tuple[FakeTransport, WTSession]:
    """Transport und Sitzung als Paar - beide werden in den Tests gebraucht."""
    transport = FakeTransport(responses or {}, **kwargs)
    return transport, WTSession(transport, CONFIG, read_only=read_only)


# ---------------------------------------------------------------------------
# Der Vertrag
# ---------------------------------------------------------------------------


def test_faketransport_erfuellt_das_protocol():
    """Strukturelle Typisierung: keine Basisklasse noetig, nur die fuenf Methoden."""
    assert isinstance(FakeTransport(), Transport)


def test_tmctltransport_erfuellt_das_protocol():
    """Die echte Implementierung muss denselben Vertrag erfuellen.

    Bewusst ueber issubclass geprueft: eine Instanz wuerde die tmctl.dll laden,
    und genau das soll die Testsuite nie tun.
    """
    assert issubclass(TmctlTransport, Transport)


def test_protocol_deckt_genau_die_benoetigten_methoden_ab():
    """Haelt fest, was WTSession voraussetzt - waechst der Vertrag, faellt das auf.

    Bewusst ueber vars() statt ueber '__protocol_attrs__': letzteres gibt es
    erst ab Python 3.12, das Paket verlangt aber nur 3.10.
    """
    vertrag = {n for n in vars(Transport) if not n.startswith("_")}
    assert vertrag == {"write", "read", "query", "set_timeout", "close"}


# ---------------------------------------------------------------------------
# Regeln, die WTSession selbst durchsetzt
# ---------------------------------------------------------------------------


def test_query_liefert_die_antwort_ohne_terminator():
    transport, sess = session({"*IDN?": "YOKOGAWA,WT3000,0,F1.23"})
    assert sess.query("*IDN?") == "YOKOGAWA,WT3000,0,F1.23"
    assert transport.written == ["*IDN?"]


def test_write_erreicht_den_transport():
    transport, sess = session()
    sess.write(":NUMeric:HOLD ON")
    assert transport.written == [":NUMeric:HOLD ON"]


def test_nur_lesen_session_sendet_kein_set_kommando():
    """Die Sperre muss greifen, BEVOR etwas auf der Leitung landet."""
    transport, sess = session(read_only=True)
    with pytest.raises(ReadOnlyViolation):
        sess.write(":NUMeric:HOLD ON")
    assert transport.written == []


def test_query_als_write_abgesetzt_wird_abgelehnt():
    transport, sess = session()
    with pytest.raises(ProtocolError):
        sess.write("*IDN?")
    assert transport.written == []


def test_zwei_queries_in_einer_nachricht_werden_abgelehnt():
    """Handbuch Kap. 5: genau ein Query pro Programmnachricht."""
    transport, sess = session()
    with pytest.raises(ProtocolError):
        sess.query(":INPut:WIRing?;:INPut:MODUle?")
    assert transport.written == []


def test_zu_lange_programmnachricht_wird_abgelehnt():
    """Grenze aus Kapitel 5 - geprueft im Transport, nicht in der Sitzung."""
    transport, sess = session()
    with pytest.raises(ProtocolError):
        sess.write(":NUMeric:NORMal:ITEM1 " + "X" * 1100)


# ---------------------------------------------------------------------------
# Blockdaten
# ---------------------------------------------------------------------------


def test_float_block_und_parser_sind_gegenstuecke():
    werte = [230.0, -1.5, 0.0]
    geparst = parse_float_block(float_block(werte)[6:])
    assert [round(v.value, 4) for v in geparst] == werte
    assert all(v.status is ValueStatus.OK for v in geparst)


def test_blockdaten_werden_ueber_mehrere_lesevorgaenge_zusammengesetzt():
    """Der Zweig in _assemble_block(), den vorher nichts erreicht hat.

    chunk_size=7 zerlegt die Antwort in Haeppchen, die weder auf der
    Headergrenze noch auf einer 4-Byte-Grenze enden - genau der unangenehme
    Fall.
    """
    werte = [float(i) for i in range(21)]
    transport, sess = session(
        {":NUMeric:NORMal:VALue?": float_block(werte)}, chunk_size=7
    )
    payload = sess.query_block(":NUMeric:NORMal:VALue?")
    assert len(payload) == 84
    assert transport.reads > 1, "Test prueft den Mehrfachlese-Zweig nicht"
    assert [v.value for v in parse_float_block(payload)] == werte


def test_antwort_ohne_blockheader_wird_als_protokollfehler_gemeldet():
    _, sess = session({":NUMeric:NORMal:VALue?": "1.234E+00"})
    with pytest.raises(ProtocolError, match="Kein Blockheader"):
        sess.query_block(":NUMeric:NORMal:VALue?")


# ---------------------------------------------------------------------------
# Vollstaendiger Blockheader.
#
# Bisher war nur die Ziffernanzahl abgesichert. Das Laengenfeld stand
# ungeschuetzt - eine abgerissene oder verstuemmelte Antwort kam als nackter
# ValueError heraus, an dem jedes 'except WTError' vorbeilaeuft. Die
# Stufenskripte fangen genau nur WTError.
#
# Jeder Fall hier muss ein ProtocolError sein, keiner ein ValueError.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "antwort,was",
    [
        (b"#", "Ziffernanzahl fehlt"),
        (b"#X0012", "Ziffernanzahl ist keine Zahl"),
        (b"#0", "unbestimmte Laenge"),
        (b"#4", "Laengenfeld fehlt ganz"),
        (b"#412", "Laengenfeld abgeschnitten"),
        (b"#4AB12", "Laengenfeld ist keine Zahl"),
        (b"#4-100" + b"\x00" * 8, "negative Laenge"),
        (b"#9999999999" + b"\x00" * 8, "unplausibel grosse Laenge"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_kaputter_blockheader_kommt_als_protokollfehler(antwort, was):
    _transport, sess = session({":NUMeric:NORMal:VALue?": antwort})
    with pytest.raises(ProtocolError):
        sess.query_block(":NUMeric:NORMal:VALue?")


def test_negative_laenge_liefert_keinen_still_gekuerzten_block():
    """Der unangenehmste der Faelle: '#4-100' sieht wie ein Ergebnis aus.

    'payload[:-100]' wuerde am ENDE schneiden statt am Anfang - die
    Nachlese-Schleife laeuft gar nicht erst an, und heraus kaeme ein
    stillschweigend zu kurzer Block ohne jeden Hinweis.
    """
    _transport, sess = session({":NUMeric:NORMal:VALue?": b"#4-100" + b"\x01" * 40})
    with pytest.raises(ProtocolError, match="Unplausible Nutzlastlaenge"):
        sess.query_block(":NUMeric:NORMal:VALue?")


def test_zu_grosse_laenge_nennt_den_kopf_und_nicht_die_leitung():
    """Ohne die Vorpruefung meldete das Geraet '64 Lesevorgaenge' - falsche Spur."""
    _transport, sess = session({":NUMeric:NORMal:VALue?": b"#9999999999" + b"\x01" * 8})
    with pytest.raises(ProtocolError, match="Unplausible Nutzlastlaenge") as fehler:
        sess.query_block(":NUMeric:NORMal:VALue?")
    assert "Lesevorgaengen" in str(fehler.value)  # nennt die Grenze, nicht den Abbruch


def test_gueltiger_block_bleibt_unberuehrt():
    """Gegenprobe: die zusaetzlichen Pruefungen duerfen den Regelfall nicht stoeren."""
    _transport, sess = session({":NUMeric:NORMal:VALue?": float_block([1.0, 2.0, 3.0])})
    werte = parse_float_block(sess.query_block(":NUMeric:NORMal:VALue?"))
    assert [v.value for v in werte] == [1.0, 2.0, 3.0]


def test_sentinel_bitmuster_ueberleben_den_transport():
    """NAN/INF sind endliche IEEE-Singles - sie duerfen unterwegs nicht kippen."""
    _, sess = session(
        {":NUMeric:NORMal:VALue?": float_block([FLOAT_NO_DATA, FLOAT_OVERRANGE, 12.5])}
    )
    werte = parse_float_block(sess.query_block(":NUMeric:NORMal:VALue?"))
    assert [v.status for v in werte] == [
        ValueStatus.NO_DATA,
        ValueStatus.OVERRANGE,
        ValueStatus.OK,
    ]


# ---------------------------------------------------------------------------
# Fehlerqueue und Fehlerpfade
# ---------------------------------------------------------------------------


def test_leere_fehlerqueue_laesst_assert_no_error_durch():
    _, sess = session()
    sess.assert_no_error("Testkontext")


def test_eintrag_in_der_fehlerqueue_wird_zur_deviceerror():
    _, sess = session(error_queue=['-113,"Undefined header"'])
    with pytest.raises(DeviceError, match="Undefined header"):
        sess.assert_no_error("Bereichswechsel")


def test_read_error_queue_leert_bis_zum_ruhewert():
    """Die Queue leert sich beim Lesen - wie am Geraet."""
    _, sess = session(error_queue=['-113,"Undefined header"', '-222,"Data out of range"'])
    eintraege = sess.read_error_queue()
    assert len(eintraege) == 3
    assert eintraege[-1].startswith("0,")


def test_drain_nach_fehler_raeumt_die_verspaetete_antwort_ab():
    """Ohne dieses Abraeumen verschiebt eine Nachzuegler-Antwort alle Folgewerte."""
    transport, sess = session()
    transport.prime("1.234E+00")
    sess.drain_after_failure()
    # Timeout runter und wieder hoch - der Ablauf aus WTSession.
    assert transport.timeouts_ms == [CONFIG.drain_timeout_ms, CONFIG.timeout_ms]
    # Danach ist nichts mehr da: der naechste Lesevorgang laeuft ins Leere.
    with pytest.raises(TmctlError):
        transport.read()


def test_simulierter_verbindungsabbruch_kommt_als_tmctlerror_an():
    """Der Abbruch muss an der oeffentlichen Grenze als WTError ankommen."""
    _, sess = session({":RATE?": "1"}, fail_commands=[":RATE?"])
    with pytest.raises(TmctlError):
        sess.query(":RATE?")


# ---------------------------------------------------------------------------
# Item-Tabelle
# ---------------------------------------------------------------------------

ITEM_ANTWORT = "4;U,1;I,1;P,1;FU,3"


def test_item_tabelle_wird_ueber_den_transport_gelesen():
    _, sess = session({":NUMeric:NORMal?": ITEM_ANTWORT})
    tabelle = ItemTable.read_from_device(sess)
    assert tabelle.number == 4
    assert [it.key for it in tabelle.items] == ["U1", "I1", "P1", "FU3"]


def test_wiederherstellung_schreibt_nur_die_abweichenden_items():
    """restore_to_device() liest erst, schreibt dann die Differenz."""
    transport, sess = session({":NUMeric:NORMal?": ITEM_ANTWORT})
    soll = ItemTable.from_response("4;U,1;I,1;P,1;S,1")
    geschrieben = soll.restore_to_device(sess)
    assert geschrieben == 1
    assert ":NUMeric:NORMal:ITEM4 S,1" in transport.written
    # NUMber ist unveraendert und darf deshalb nicht mitgeschrieben werden.
    assert not any(w.startswith(":NUMeric:NORMal:NUMber") for w in transport.written)


# ---------------------------------------------------------------------------
# Die vollstaendige Messschleife
# ---------------------------------------------------------------------------


def messschleifen_antworten(zyklen: int) -> dict:
    """Antworttabelle fuer eine Messreihe mit wechselnden Werten.

    Zyklus 2 enthaelt absichtlich ein NO_DATA und ein OVERRANGE, damit die
    Statuskodierung der CSV mitgeprueft wird.
    """
    bloecke = []
    for n in range(zyklen):
        if n == 1:
            bloecke.append(float_block([230.0, FLOAT_NO_DATA, FLOAT_OVERRANGE, 50.0]))
        else:
            bloecke.append(float_block([230.0 + n, 1.0 + n, 230.0 * (1 + n), 50.0]))
    return {
        ":NUMeric:NORMal?": ITEM_ANTWORT,
        ":NUMeric:HOLD?": "0",
        ":NUMeric:NORMal:VALue?": bloecke,
        ":STATus:CONDition?": "16",
        # Geraeterate fuer die Taktpruefung der Schleife.
        ":RATE?": "1.000E+00",
    }


def test_vollstaendige_messschleife_bis_in_die_csv(tmp_path):
    """Geraetefreier Durchlauf: Item-Tabelle, HOLD, Blockdaten, CSV, Statistik."""
    zyklen = 3
    transport, sess = session(messschleifen_antworten(zyklen))
    tabelle = ItemTable.read_from_device(sess)
    ziel = tmp_path / "messung.csv"

    # Die Messschleife oeffnet und schliesst die Senke.
    stats = run_measurement_loop(
        session=sess,
        table=tabelle,
        sink=CsvSink(ziel),
        interval_s=0.0,  # kein sleep - der Test soll nicht warten
        max_samples=zyklen,
        max_duration_s=None,
        use_hold=True,
        record_condition=True,
        log_every=0,
    )

    assert stats.samples == zyklen
    assert stats.status_counts[ValueStatus.NO_DATA] == 1
    assert stats.status_counts[ValueStatus.OVERRANGE] == 1
    assert stats.status_counts[ValueStatus.OK] == zyklen * 4 - 2

    # HOLD wurde je Zyklus neu gesetzt und am Ende garantiert abgeschaltet.
    assert transport.written.count(":NUMeric:HOLD ON") == zyklen
    assert transport.written[-1] == ":NUMeric:HOLD OFF"

    zeilen = list(csv.reader(ziel.open(encoding="utf-8")))
    kopf, daten = zeilen[0], zeilen[1:]
    assert kopf[:4] == ["timestamp_iso", "elapsed_s", "sample", "condition"]
    assert kopf[4:8] == ["U1", "I1", "P1", "FU3"]
    assert kopf[-1] == "status_flags"
    assert len(daten) == zyklen

    # Statuskodierung: NO_DATA -> leere Zelle, OVERRANGE -> 'INF'.
    zweite = daten[1]
    assert zweite[5] == ""
    assert zweite[6] == "INF"
    assert zweite[-1] == "I1=NO_DATA;P1=OVERRANGE"
    assert zweite[3] == "16"


# ---------------------------------------------------------------------------
# CSV-Zeile gegen den Spaltenkopf pruefen.
#
# Der teuerste Fehler dieser Codebasis waere eine stillschweigend verrutschte
# Spalte: jede Zeile sieht fuer sich plausibel aus, die Verschiebung zeigt sich
# erst im Vergleich mit dem Kopf - meist erst bei der Auswertung.
# ---------------------------------------------------------------------------


def messwerte(anzahl: int) -> list[NumericValue]:
    """Eine Liste gueltiger Messwerte gewuenschter Laenge."""
    return [NumericValue(float(i), ValueStatus.OK, 0) for i in range(1, anzahl + 1)]


def test_zu_wenige_werte_werden_nicht_geschrieben(tmp_path):
    """Sonst rutscht 'status_flags' unter eine Messwertspalte."""
    ziel = tmp_path / "kurz.csv"
    with CsvSink(ziel) as recorder:
        recorder.open(["U1", "I1", "P1"])
        with pytest.raises(WTError, match="Sample 1"):
            recorder.write(
                Sample(
                    timestamp=datetime.now(timezone.utc),
                    elapsed_s=0.0,
                    number=1,
                    condition=None,
                    values=messwerte(2),
                )
            )

    # Nur der Kopf steht in der Datei - keine halbe Zeile.
    zeilen = list(csv.reader(ziel.open(encoding="utf-8")))
    assert len(zeilen) == 1
    assert zeilen[0][-1] == "status_flags"


def test_zu_viele_werte_werden_nicht_geschrieben(tmp_path):
    """Der Gegenfall: sonst entstehen unbenannte Spalten hinter 'status_flags'."""
    ziel = tmp_path / "lang.csv"
    with CsvSink(ziel) as recorder:
        recorder.open(["U1", "I1", "P1"])
        with pytest.raises(WTError, match="4 Messwerte"):
            recorder.write(
                Sample(
                    timestamp=datetime.now(timezone.utc),
                    elapsed_s=0.0,
                    number=7,
                    condition=None,
                    values=messwerte(4),
                )
            )

    assert len(list(csv.reader(ziel.open(encoding="utf-8")))) == 1


def test_passende_anzahl_wird_unveraendert_geschrieben(tmp_path):
    """Gegenprobe: die Pruefung darf den Regelfall nicht behindern."""
    ziel = tmp_path / "passend.csv"
    with CsvSink(ziel) as recorder:
        recorder.open(["U1", "I1", "P1"])
        recorder.write(
            Sample(
                timestamp=datetime.now(timezone.utc),
                elapsed_s=1.5,
                number=1,
                condition=16,
                values=messwerte(3),
            )
        )

    kopf, zeile = list(csv.reader(ziel.open(encoding="utf-8")))
    assert len(zeile) == len(kopf)
    assert zeile[3] == "16"
    assert zeile[-1] == ""  # keine Statusflags, alle Werte OK


# ---------------------------------------------------------------------------
# Abweichende Werteanzahl faellt bereits beim Lesen auf.
# ---------------------------------------------------------------------------


def test_abweichende_werteanzahl_bricht_beim_lesen_ab():
    """Eine Abfrage frueher als in der CSV - dort ist die Ursache noch sichtbar."""
    _transport, sess = session({":NUMeric:NORMal:VALue?": float_block([1.0, 2.0])})
    with pytest.raises(ProtocolError, match="Erwartet: 4"):
        read_numeric_values(sess, expected_count=4)


def test_strict_false_liefert_die_werte_mit_warnung(caplog):
    """Diagnoseweg: wer nachsehen will, was das Geraet liefert, kommt heran."""
    _transport, sess = session({":NUMeric:NORMal:VALue?": float_block([1.0, 2.0])})
    with caplog.at_level("WARNING"):
        werte = read_numeric_values(sess, expected_count=4, strict=False)
    assert len(werte) == 2
    assert any("Erwartet" in r.message for r in caplog.records)


def test_passende_anzahl_geht_ohne_beanstandung_durch():
    _transport, sess = session({":NUMeric:NORMal:VALue?": float_block([1.0, 2.0])})
    assert len(read_numeric_values(sess, expected_count=2)) == 2


def test_messschleife_bricht_bei_verschobener_item_tabelle_ab(tmp_path):
    """Der Fall aus der Praxis: jemand stellt am Bedienfeld die Tabelle um.

    Die Schleife laeuft dann nicht mit falsch beschrifteten Spalten weiter,
    sondern bricht ab - und HOLD wird trotzdem abgeschaltet.
    """
    antworten = messschleifen_antworten(1)
    antworten[":NUMeric:NORMal:VALue?"] = float_block([1.0, 2.0])  # statt vier Werten
    transport, sess = session(antworten)
    tabelle = ItemTable.read_from_device(sess)

    with pytest.raises(ProtocolError):
        run_measurement_loop(
            session=sess,
            table=tabelle,
            sink=CsvSink(tmp_path / "abbruch.csv"),
            interval_s=0.0,
            max_samples=1,
            max_duration_s=None,
            use_hold=True,
            record_condition=False,
            log_every=0,
        )

    assert transport.written[-1] == ":NUMeric:HOLD OFF"


def test_hold_wird_auch_bei_einem_fehler_mitten_im_zyklus_abgeschaltet():
    """Bleibt HOLD aktiv, liefert das Geraet in der Folgesitzung Altwerte."""
    transport, sess = session(
        {
            ":NUMeric:HOLD?": "0",
            ":NUMeric:NORMal:VALue?": float_block([1.0]),
            # Taktpruefung der Schleife.
            ":RATE?": "1.000E+00",
        },
        fail_commands=[":NUMeric:NORMal:VALue?"],
    )
    tabelle = ItemTable.from_response("1;U,1")

    class Verweigerer:
        """Eine Senke, die den ganzen Vertrag erfuellt und beim Schreiben knallt.

        Minimaler Sink fuer den Lebenszyklustest.
        Jetzt braucht sie open() und close(), weil die Messschleife beides
        ruft - womit der Test nebenbei belegt, dass eine voellig fremde Klasse
        als SampleSink taugt, ohne von irgendetwas zu erben.
        """

        def __init__(self):
            self.geoeffnet = False
            self.geschlossen = False

        def open(self, columns, metadata=None):
            self.geoeffnet = True

        def write(self, sample):  # pragma: no cover - wird nie erreicht
            raise AssertionError("es darf keine Zeile entstehen")

        def close(self):
            self.geschlossen = True

    senke = Verweigerer()
    with pytest.raises(TmctlError):
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
        )
    assert transport.written[-1] == ":NUMeric:HOLD OFF"
    # Die Schleife raeumt die Senke auch dann ab, wenn der Zyklus
    # mitten im Lesen scheitert - sonst bliebe eine Datei offen zurueck.
    assert senke.geoeffnet and senke.geschlossen


def test_metadaten_sidecar_haelt_fehlgeschlagene_abfragen_fest(tmp_path):
    """write_metadata() darf an einer stummen Abfrage nicht scheitern."""
    antworten = {
        "*IDN?": "YOKOGAWA,WT3000,0,F1.23",
        ":COMMunicate?": "0;0;0",
        ":RATE?": "1",
        ":NUMeric:FORMat?": "FLOAT",
        ":INPut?": "<roh>",
        ":INPut:WIRing?": "V3A3,P1W2",
        ":INPut:MODUle?": "30,30,30,30",
        ":INPut:SCALing?": "0",
        ":INPut:FILTer?": "OFF",
        ":INPut:CFACtor?": "3",
        ":MEASure?": "<roh>",
    }
    _, sess = session(antworten, fail_commands=[":MEASure?"])
    ziel = tmp_path / "messung.json"
    write_metadata(ziel, sess, ItemTable.from_response("1;U,1"), {"rate": 1})

    import json

    inhalt = json.loads(ziel.read_text(encoding="utf-8"))
    assert inhalt["device"]["idn"] == "YOKOGAWA,WT3000,0,F1.23"
    assert inhalt["device"]["measure"].startswith("<Fehler:")
    assert inhalt["item_table"]["number"] == 1
