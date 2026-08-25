# =============================================================================
# Datei: tests/test_messsteuerung.py
# Steuerbare Messung: Measurement, stream() und Sitzungsbesitz.
#
# Diese Datei prueft VIER Zusagen, und drei davon lassen sich nur nebenlaeufig
# pruefen:
#
#   1. STEUERBARKEIT  Eine Messung ohne vorab bekanntes Ende laeuft im
#                     Hintergrund und endet auf ein Signal von aussen.
#   2. SOFORT         Das Signal greift SOFORT und nicht erst nach dem
#                     laufenden Intervall. Genau dafuer ist das Warten ein
#                     'Event.wait()' und kein 'time.sleep()' - der Test misst
#                     die Zeit und faellt, wenn jemand es zurueckbaut.
#   3. BESITZ         Waehrend des Laufs gehoert die Sitzung dem Mess-Thread.
#                     Ein Fremdzugriff endet in einer ConcurrentAccessError
#                     statt in vertauschten Antworten.
#   4. RUECKGABE      Nach dem Lauf ist die Sitzung frei, die Senke
#                     geschlossen, HOLD zurueckgenommen - auch nach einem
#                     Fehler im Mess-Thread.
#
# Zur Taktwahl in den Tests: 'interval_s=0' laesst die Schleife so schnell
# laufen, wie der FakeTransport antwortet. Wo eine Zeit gemessen wird, steht
# ein ausdruecklicher Takt - dort ist die Dauer der Gegenstand der Pruefung.
# =============================================================================

from __future__ import annotations

import threading
import time

import pytest

from wt3000_scpi import WT3000
from wt3000_scpi.wt3000_core import (
    ConcurrentAccessError,
    WTConfig,
    WTError,
    WTSession,
)
from wt3000_scpi.wt3000_measure import (
    LoopStatistics,
    Measurement,
    iter_samples,
    run_measurement_loop,
)
from wt3000_scpi.wt3000_numeric import ItemTable
from wt3000_scpi.wt3000_transport import FakeTransport, float_block

from conftest import base_responses


# ---------------------------------------------------------------------------
# Pruefstand
# ---------------------------------------------------------------------------


class SammelSink:
    """Senke, die alles behaelt - und mitschreibt, wer sie geschlossen hat.

    Der Thread-Name beim Schliessen ist hier kein Beiwerk: M3-1 verlangt
    'Cleanup im ausfuehrenden Ablauf, nicht nur beim Aufrufer'. Ohne diese
    Notiz liesse sich nicht unterscheiden, ob die Senke vom Mess-Thread oder
    erst vom wartenden Haupt-Thread geschlossen wurde.
    """

    def __init__(self) -> None:
        self.columns: list[str] = []
        self.metadata: dict = {}
        self.samples: list = []
        self.geschlossen = False
        self.geschlossen_von: str | None = None

    def open(self, columns, metadata=None) -> None:
        self.columns = list(columns)
        self.metadata = dict(metadata or {})

    def write(self, sample) -> None:
        self.samples.append(sample)

    def close(self) -> None:
        self.geschlossen = True
        self.geschlossen_von = threading.current_thread().name


def _zaehlende_bloecke():
    """Endlos verschiedene Messblocke - sonst waere jeder Zyklus eine Dublette."""
    zaehler = {"n": 0}

    def naechster(_command: str) -> bytes:
        zaehler["n"] += 1
        n = float(zaehler["n"])
        return float_block([n, n * 2, n * 3])

    return naechster


def sitzung(rate: str = "1.000E+00", **kwargs) -> tuple[WTSession, FakeTransport]:
    transport = FakeTransport(
        {
            ":NUMeric:NORMal?": "3;U,1;I,1;P,1",
            ":NUMeric:HOLD?": "0",
            ":NUMeric:NORMal:VALue?": _zaehlende_bloecke(),
            ":STATus:CONDition?": "0",
            ":RATE?": rate,
        },
        **kwargs,
    )
    return WTSession(transport, WTConfig()), transport


def messung(
    sess: WTSession,
    senke: SammelSink,
    table: ItemTable | None = None,
    **kwargs,
) -> Measurement:
    """Ein 'Measurement' mit den Vorgaben dieser Datei.

    'table' ist ausdruecklich ein Parameter und wird nicht immer selbst
    gelesen: 'ItemTable.read_from_device()' ist ein GERAETEZUGRIFF. Wer ein
    zweites Measurement baut, waehrend das erste laeuft, darf ihn nicht mehr
    absetzen - die Sitzung gehoert dann dem Mess-Thread. Genau darueber ist
    die erste Fassung dieser Datei gestolpert.
    """
    vorgaben: dict = {
        "interval_s": 0.0,
        "max_samples": None,
        "max_duration_s": None,
        "use_hold": True,
        "record_condition": False,
        "log_every": 0,
        "check_update_rate": False,
    }
    vorgaben.update(kwargs)
    return Measurement(
        session=sess,
        table=table if table is not None else ItemTable.read_from_device(sess),
        sink=senke,
        **vorgaben,
    )


def warte_auf(bedingung, grenze: float = 5.0) -> bool:
    """Auf eine Bedingung warten, statt eine Dauer zu raten."""
    ende = time.monotonic() + grenze
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.005)
    return False


# ---------------------------------------------------------------------------
# 1 - Der Lebenszyklus
# ---------------------------------------------------------------------------


def test_start_kehrt_sofort_zurueck_und_die_messung_laeuft():
    """Der Kern von M3-1: eine Messung ohne Ende blockiert den Aufrufer nicht."""
    sess, _ = sitzung()
    senke = SammelSink()
    m = messung(sess, senke)

    assert m.is_running is False
    m.start()
    try:
        # Ohne Limit laeuft sie unbegrenzt - und der Aufrufer ist trotzdem hier.
        assert m.is_running is True
        assert warte_auf(lambda: len(senke.samples) >= 3)
    finally:
        m.stop()

    assert m.is_running is False


def test_stop_beendet_und_wait_liefert_die_statistik():
    sess, _ = sitzung()
    senke = SammelSink()
    m = messung(sess, senke).start()
    assert warte_auf(lambda: len(senke.samples) >= 2)

    stats = m.stop()

    assert isinstance(stats, LoopStatistics)
    assert stats.samples == len(senke.samples)
    assert stats.samples >= 2
    # wait() nach dem Ende ist unschaedlich und liefert dieselbe Statistik.
    assert m.wait() is stats


def test_die_statistik_waechst_waehrend_des_laufs():
    """'stats' ist der einzige Fortschrittsanzeiger, der den Besitz nicht bricht."""
    sess, _ = sitzung()
    senke = SammelSink()
    m = messung(sess, senke).start()
    try:
        assert warte_auf(lambda: m.stats.samples >= 1)
        zwischenstand = m.stats.samples
        assert warte_auf(lambda: m.stats.samples > zwischenstand)
    finally:
        m.stop()


def test_ein_limit_beendet_die_messung_von_selbst():
    """Auch im Hintergrundlauf gelten max_samples und max_duration_s."""
    sess, _ = sitzung()
    senke = SammelSink()
    m = messung(sess, senke, max_samples=4).start()

    stats = m.wait(timeout=5.0)

    assert stats.samples == 4
    assert len(senke.samples) == 4
    assert m.is_running is False


def test_zweiter_start_wird_abgelehnt():
    """'Measurement' ist Einweg - die Senke des ersten Laufs ist geschlossen."""
    sess, _ = sitzung()
    m = messung(sess, SammelSink(), max_samples=1).start()
    m.wait(timeout=5.0)

    with pytest.raises(WTError, match="einmal verwendbar"):
        m.start()


def test_stop_und_wait_ohne_start_sind_ein_fehler():
    sess, _ = sitzung()
    m = messung(sess, SammelSink())

    with pytest.raises(WTError, match="nie gestartet"):
        m.stop()
    with pytest.raises(WTError, match="nie gestartet"):
        m.wait()


def test_wait_mit_timeout_meldet_die_noch_laufende_messung():
    """Ein Timeout ist kein stilles 'fertig' - sonst laese der Aufrufer eine
    halbe Statistik als Endergebnis."""
    sess, _ = sitzung()
    m = messung(sess, SammelSink(), interval_s=0.05).start()
    try:
        with pytest.raises(WTError, match="laeuft nach"):
            m.wait(timeout=0.1)
    finally:
        m.stop()


# ---------------------------------------------------------------------------
# 2 - Das Stoppsignal greift SOFORT
# ---------------------------------------------------------------------------


def test_stop_wartet_das_laufende_intervall_nicht_ab():
    """Die Zusage, fuer die das Stoppsignal ein Event ist und kein Flag.

    Mit 'time.sleep(interval_s)' im Wartezweig kaeme stop() erst nach bis zu
    einem vollen Intervall zurueck - bei ':RATE 20 s' also zwanzig Sekunden
    spaeter. Hier sind es 3 s Takt, und stop() muss deutlich darunter bleiben.

    Der Test ist damit zugleich die Mutationsprobe: wer 'stop_event.wait()'
    wieder durch 'time.sleep()' ersetzt, laesst ihn fallen.
    """
    sess, _ = sitzung()
    senke = SammelSink()
    m = messung(sess, senke, interval_s=3.0).start()

    # Den ersten Zyklus abwarten - erst danach wartet die Schleife wirklich.
    assert warte_auf(lambda: len(senke.samples) >= 1)

    begonnen = time.monotonic()
    m.stop(timeout=5.0)
    gebraucht = time.monotonic() - begonnen

    assert gebraucht < 1.0, (
        f"stop() brauchte {gebraucht:.2f} s bei 3 s Takt - das Stoppsignal wird "
        "offenbar erst nach dem laufenden Intervall ausgewertet"
    )
    assert m.is_running is False


# ---------------------------------------------------------------------------
# 3 - Sitzungsbesitz (Maßnahme A2)
# ---------------------------------------------------------------------------


def test_fremdzugriff_waehrend_der_messung_wird_abgewiesen():
    """Der teuerste Fehler des Pakets, sichtbar gemacht.

    Ohne diese Sperre bekaeme der Haupt-Thread hier die Antwort des
    Mess-Threads - stillschweigend, weil beide plausibel aussehen.
    """
    sess, _ = sitzung()
    m = messung(sess, SammelSink()).start()
    try:
        with pytest.raises(ConcurrentAccessError) as fehler:
            sess.query(":NUMeric:HOLD?")

        text = str(fehler.value)
        # Die Meldung muss den Ausweg nennen, nicht nur das Verbot.
        assert "stop()" in text
        assert "stream()" in text
    finally:
        m.stop()


def test_der_besitz_gilt_ab_der_rueckkehr_von_start():
    """Kein Fenster zwischen 'start()' und dem ersten Takt.

    Deshalb traegt 'start()' den Besitz ein und nicht der Thread selbst: ein
    Vertrag, der erst ein paar Millisekunden spaeter gilt, ist keiner.
    """
    sess, _ = sitzung()
    m = messung(sess, SammelSink(), interval_s=5.0).start()
    try:
        # Unmittelbar nach der Rueckkehr, ohne jedes Abwarten.
        assert sess.owner is not None
        with pytest.raises(ConcurrentAccessError):
            sess.query(":RATE?")
    finally:
        m.stop()


def test_nach_dem_ende_ist_die_sitzung_wieder_frei():
    sess, _ = sitzung()
    m = messung(sess, SammelSink(), max_samples=2).start()
    m.wait(timeout=5.0)

    assert sess.owner is None
    # Und der naechste Zugriff geht wieder durch.
    assert sess.query(":NUMeric:HOLD?") == "0"


def test_die_sitzung_wird_auch_nach_einem_fehler_freigegeben():
    """Sonst gehoerte sie fuer immer einer Messung, die es nicht mehr gibt."""
    sess, _ = sitzung()

    class KaputteSenke(SammelSink):
        def write(self, sample) -> None:
            raise RuntimeError("Senke defekt")

    m = messung(sess, KaputteSenke()).start()
    with pytest.raises(RuntimeError, match="Senke defekt"):
        m.wait(timeout=5.0)

    assert sess.owner is None
    assert sess.query(":NUMeric:HOLD?") == "0"


def test_zwei_messungen_auf_einer_sitzung_werden_abgelehnt():
    """Das Geraet hat genau eine Verbindung - also auch genau eine Messung."""
    sess, _ = sitzung()
    # Vor dem Start lesen: waehrend der Messung waere das selbst schon ein
    # abgewiesener Fremdzugriff.
    tabelle = ItemTable.read_from_device(sess)

    erste = messung(sess, SammelSink(), table=tabelle).start()
    try:
        zweite = messung(sess, SammelSink(), table=tabelle)
        with pytest.raises(ConcurrentAccessError, match="gehoert bereits"):
            zweite.start()
        # Die abgewiesene Messung darf keinen Thread hinterlassen.
        assert zweite.is_running is False
    finally:
        erste.stop()


def test_der_mess_thread_selbst_wird_nicht_ausgesperrt():
    """Gegenprobe zur Sperre: sie darf nicht den treffen, dem sie gehoert."""
    sess, _ = sitzung()
    senke = SammelSink()
    # record_condition=True heisst: die Schleife setzt je Zyklus einen
    # zusaetzlichen query() ab - aus dem Mess-Thread.
    m = messung(sess, senke, max_samples=3, record_condition=True).start()

    stats = m.wait(timeout=5.0)

    assert stats.samples == 3
    assert all(s.condition == 0 for s in senke.samples)


# ---------------------------------------------------------------------------
# 4 - Aufraeumen
# ---------------------------------------------------------------------------


def test_die_senke_wird_vom_mess_thread_geschlossen():
    """M3-1: 'Cleanup im ausfuehrenden Ablauf, nicht nur beim Aufrufer'."""
    sess, _ = sitzung()
    senke = SammelSink()
    m = messung(sess, senke, max_samples=2).start()
    m.wait(timeout=5.0)

    assert senke.geschlossen is True
    assert senke.geschlossen_von == "wt3000-measurement"


def test_hold_wird_nach_stop_zurueckgenommen():
    """Ein haengendes HOLD ist der unangenehmste Rest einer Messung: das
    Geraet liefert danach eingefrorene Werte, waehrend die Anzeige weiterlaeuft."""
    sess, transport = sitzung()
    m = messung(sess, SammelSink()).start()
    assert warte_auf(lambda: len(transport.written) > 3)
    m.stop()

    assert ":NUMeric:HOLD OFF" in transport.written


def test_hold_wird_auch_nach_einem_fehler_zurueckgenommen():
    sess, transport = sitzung()

    class KaputteSenke(SammelSink):
        def write(self, sample) -> None:
            raise RuntimeError("Senke defekt")

    m = messung(sess, KaputteSenke()).start()
    with pytest.raises(RuntimeError):
        m.wait(timeout=5.0)

    assert ":NUMeric:HOLD OFF" in transport.written


def test_ein_fehler_aus_dem_thread_erreicht_den_aufrufer():
    """Ohne wait() endete er als Textausgabe von 'threading' - also als etwas,
    das kein 'except' faengt und keine Ablaufsteuerung bemerkt."""
    sess, _ = sitzung()

    class KaputteSenke(SammelSink):
        def open(self, columns, metadata=None) -> None:
            raise RuntimeError("Datei nicht anlegbar")

    m = messung(sess, KaputteSenke()).start()

    with pytest.raises(RuntimeError, match="Datei nicht anlegbar"):
        m.wait(timeout=5.0)
    # Und er bleibt abrufbar, statt beim ersten wait() verbraucht zu sein.
    assert isinstance(m.error, RuntimeError)


def test_der_context_manager_stoppt_und_wartet_ab():
    """Damit keine Messung ihre Konfigurationsklammer ueberlebt."""
    sess, _ = sitzung()
    senke = SammelSink()

    with messung(sess, senke) as m:
        assert m.is_running is True
        assert warte_auf(lambda: len(senke.samples) >= 2)

    assert m.is_running is False
    assert senke.geschlossen is True
    assert sess.owner is None


def test_der_context_manager_verdeckt_die_urspruengliche_ausnahme_nicht():
    sess, _ = sitzung()

    with pytest.raises(ValueError, match="eigentliche Ursache"):
        with messung(sess, SammelSink()):
            raise ValueError("eigentliche Ursache")

    assert sess.owner is None


# ---------------------------------------------------------------------------
# 5 - Der Generator
# ---------------------------------------------------------------------------


def test_stream_liefert_samples_im_eigenen_thread():
    sess, _ = sitzung()
    stats = LoopStatistics()

    gesehen = []
    for sample in iter_samples(
        session=sess,
        table=ItemTable.read_from_device(sess),
        stats=stats,
        interval_s=0.0,
        max_samples=3,
        max_duration_s=None,
        use_hold=True,
        record_condition=False,
        log_every=0,
    ):
        gesehen.append(sample)

    assert [s.number for s in gesehen] == [1, 2, 3]
    assert stats.samples == 3


def test_stream_laesst_die_sitzung_frei():
    """Der Unterschied zu start(), der diesen Weg rechtfertigt: zwischen zwei
    Samples darf der Aufrufer das Geraet anfassen."""
    sess, _ = sitzung()

    for _ in iter_samples(
        session=sess,
        table=ItemTable.read_from_device(sess),
        stats=LoopStatistics(),
        interval_s=0.0,
        max_samples=2,
        max_duration_s=None,
        use_hold=True,
        record_condition=False,
        log_every=0,
    ):
        # Genau das, was waehrend eines Hintergrundlaufs abgewiesen wuerde.
        assert sess.query(":NUMeric:HOLD?") == "0"


def test_ein_abgebrochener_stream_nimmt_hold_zurueck():
    """Der Aufrufer bricht mit 'break' ab - der Generator bekommt ein
    GeneratorExit, und sein 'finally' muss trotzdem laufen."""
    sess, transport = sitzung()

    strom = iter_samples(
        session=sess,
        table=ItemTable.read_from_device(sess),
        stats=LoopStatistics(),
        interval_s=0.0,
        max_samples=None,
        max_duration_s=None,
        use_hold=True,
        record_condition=False,
        log_every=0,
    )
    for _ in strom:
        break
    strom.close()

    assert ":NUMeric:HOLD OFF" in transport.written


def test_stream_reicht_strg_c_an_den_aufrufer_durch():
    """Im Thread des Aufrufers ist Strg+C der richtige Abbruchweg - anders als
    im Hintergrundlauf, wo SIGINT gar nicht ankaeme.

    Und zugleich die Grenze, die dieser Weg dem Aufrufer laesst: eine Ausnahme
    im SCHLEIFENRUMPF laesst den Generator am 'yield' ausgesetzt zurueck. Sein
    'finally' - und damit HOLD OFF - laeuft erst bei 'close()'. Wer 'stream()'
    benutzt, schliesst ihn deshalb selbst; 'record()' nimmt einem genau das ab.
    """
    sess, transport = sitzung()

    strom = iter_samples(
        session=sess,
        table=ItemTable.read_from_device(sess),
        stats=LoopStatistics(),
        interval_s=0.0,
        max_samples=None,
        max_duration_s=None,
        use_hold=True,
        record_condition=False,
        log_every=0,
    )

    with pytest.raises(KeyboardInterrupt):
        for _ in strom:
            raise KeyboardInterrupt

    # Ausgesetzt, nicht beendet - HOLD steht noch.
    assert ":NUMeric:HOLD OFF" not in transport.written
    strom.close()
    assert ":NUMeric:HOLD OFF" in transport.written


def test_record_nimmt_hold_zurueck_wenn_die_senke_abbricht():
    """Die Gegenprobe zum Test darueber - und der Grund fuer 'strom.close()'
    im 'finally' von 'run_measurement_loop()'.

    Trifft ein Strg+C waehrend 'sink.write()' ein, ist der Generator am
    'yield' ausgesetzt. Ohne den ausdruecklichen 'close()' haenge HOLD bis zur
    naechsten Speicherbereinigung - das Geraet liefert dann eingefrorene
    Werte, waehrend die Anzeige weiterlaeuft.
    """
    sess, transport = sitzung()

    class AbbrechendeSenke(SammelSink):
        def write(self, sample) -> None:
            raise KeyboardInterrupt

    stats = run_measurement_loop(
        session=sess,
        table=ItemTable.read_from_device(sess),
        sink=AbbrechendeSenke(),
        interval_s=0.0,
        max_samples=None,
        max_duration_s=None,
        use_hold=True,
        record_condition=False,
        log_every=0,
        check_update_rate=False,
    )

    assert ":NUMeric:HOLD OFF" in transport.written
    assert stats.samples == 1


# ---------------------------------------------------------------------------
# 6 - Das Lock der Sitzung
# ---------------------------------------------------------------------------


class VerschraenkenderTransport(FakeTransport):
    """FakeTransport mit einem Zeitfenster vor jedem Lesevorgang.

    Der Grund fuer diese Klasse ist ein Befund an der ERSTEN Fassung dieses
    Tests: sie lief mit vier Threads gegen den unveraenderten FakeTransport -
    und blieb gruen, auch als das Lock probeweise ausgebaut wurde. Die
    Operationen sind zu kurz, als dass sich die Threads zuverlaessig
    verschraenken; der Test prueft dann nichts.

    Das Fenster liegt genau dort, wo die Vertauschung entsteht. 'query()' legt
    die Antwort in einen gemeinsamen Puffer und liest sie danach ab. Kommt
    zwischen beidem ein zweiter Aufrufer, holt er sich das erste Haeppchen des
    ersten - und beide bauen aus fremden Bytes einen Block zusammen.
    """

    def read(self) -> bytes:
        time.sleep(0.002)
        return super().read()


def test_query_block_ist_als_ganzes_geschuetzt():
    """Das Lock muss beide Haelften umschliessen.

    'query_block()' liest ueber '_assemble_block()' nach; ein zweiter
    Aufrufer, der sich dazwischenschiebt, laese mitten in einen fremden Block
    hinein. Mit 'chunk_size' antwortet der FakeTransport haeppchenweise -
    genau der Fall, fuer den die Nachlese-Schleife da ist.

    MUTATIONSPROBE: Wird 'self._lock' in WTSession durch einen
    nullcontext ersetzt, faellt dieser Test - mit vertauschten Blocklaengen
    oder einer ProtocolError aus einem halb fremden Blockkopf.
    """
    transport = VerschraenkenderTransport(
        {
            ":NUMeric:NORMal?": "3;U,1;I,1;P,1",
            ":NUMeric:HOLD?": "0",
            ":NUMeric:NORMal:VALue?": _zaehlende_bloecke(),
            ":STATus:CONDition?": "0",
            ":RATE?": "1.000E+00",
        }
    )
    sess = WTSession(transport, WTConfig())
    # Die Item-Tabelle ZUERST und ungestueckelt lesen: 'chunk_size' zerlegt
    # jede Antwort, auch eine gewoehnliche Textantwort - und die liest
    # 'query()' nur einmal. Erst danach umschalten, sonst pruefte der Test
    # eine Eigenheit des FakeTransport statt des Locks.
    tabelle = ItemTable.read_from_device(sess)
    # 8 und nicht 4: der Blockkopf '#40012' ist 6 Byte lang und muss im ERSTEN
    # Lesevorgang vollstaendig ankommen - '_assemble_block()' liest die
    # Nutzlast nach, nicht den Kopf. Der ganze Block misst 18 Byte, es bleiben
    # also zwei Nachlesevorgaenge je Abfrage.
    transport.chunk_size = 8

    ergebnisse: list[int] = []
    fehler: list[BaseException] = []

    def lesen() -> None:
        try:
            for _ in range(10):
                roh = sess.query_block(":NUMeric:NORMal:VALue?")
                ergebnisse.append(len(roh))
        except BaseException as error:  # pragma: no cover - schlaegt sonst fehl
            fehler.append(error)

    threads = [threading.Thread(target=lesen) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert fehler == []
    # Jeder Block traegt drei Werte zu je vier Byte. Waere ein Aufrufer in
    # einen fremden Block geraten, staende hier eine andere Laenge.
    assert set(ergebnisse) == {4 * len(tabelle.items)}


# ---------------------------------------------------------------------------
# 7 - Ueber die Fassade
# ---------------------------------------------------------------------------


def fassade_mit_messwerten() -> WT3000:
    """Eine verbundene Fassade, die auf Messwertabfragen antwortet."""
    antworten = base_responses()
    antworten[":NUMERIC:NORMAL"] = "3;U,1;I,1;P,1"
    antworten[":NUMERIC:NORMAL:VALUE"] = _zaehlende_bloecke()
    transport = FakeTransport(antworten)
    return WT3000.from_transport(
        transport, WTConfig(use_remote=False), read_only=False, allow_changes=True
    )


def test_die_fassade_startet_und_stoppt_eine_messung():
    """Der Weg, den ein Ablauf tatsaechlich geht."""
    with fassade_mit_messwerten() as wt:
        tabelle = wt.items.read()
        senke = SammelSink()

        m = wt.measure.start(senke, tabelle, interval_s=0.0, check_update_rate=False)
        assert m.is_running is True
        assert wt.measure.active is m
        assert warte_auf(lambda: len(senke.samples) >= 3)

        stats = m.stop()

    assert stats.samples >= 3
    assert senke.geschlossen is True
    # Die Laufparameter gehen wie bei record() in die Metadaten der Senke.
    assert senke.metadata["sample_interval_s"] == 0.0
    assert "units" in senke.metadata


def test_die_fassade_weist_fremdzugriff_waehrend_der_messung_ab():
    """Der Vertrag aus Sicht des Anwenders: 'wt.input' ist waehrenddessen zu."""
    with fassade_mit_messwerten() as wt:
        tabelle = wt.items.read()
        m = wt.measure.start(SammelSink(), tabelle, interval_s=0.0, check_update_rate=False)
        try:
            with pytest.raises(ConcurrentAccessError):
                wt.input.get_wiring()
        finally:
            m.stop()

        # Und danach geht es wieder.
        assert wt.input.get_wiring() is not None


def test_close_beendet_eine_laufende_messung():
    """Sonst liefe close() in die eigene Sperre.

    close() sendet ':NUMeric:HOLD OFF'. Gehoert die Sitzung noch dem
    Mess-Thread, scheiterte ausgerechnet der Aufraeumpfad an dem Schutz, der
    die Messung sauber halten soll - und die Messung liefe als Daemon-Thread
    weiter, waehrend der Transport unter ihr geschlossen wird.
    """
    wt = fassade_mit_messwerten()
    tabelle = wt.items.read()
    senke = SammelSink()
    m = wt.measure.start(senke, tabelle, interval_s=0.0, check_update_rate=False)
    assert warte_auf(lambda: len(senke.samples) >= 2)

    wt.close()

    assert m.is_running is False
    assert senke.geschlossen is True


def test_stream_ueber_die_fassade():
    with fassade_mit_messwerten() as wt:
        stats = LoopStatistics()
        gesehen = [
            s.number
            for s in wt.measure.stream(
                wt.items.read(), interval_s=0.0, max_samples=3, check_update_rate=False, stats=stats
            )
        ]

        assert gesehen == [1, 2, 3]
        assert stats.samples == 3
        # Der Unterschied zu start(): die Sitzung war nie vergeben.
        assert wt.input.get_wiring() is not None


def test_owned_by_current_thread_gibt_den_besitz_zurueck():
    sess, _ = sitzung()

    with sess.owned_by_current_thread("Testabschnitt"):
        assert sess.owner == threading.get_ident()
        # Der Eigentuemer selbst kommt durch.
        assert sess.query(":NUMeric:HOLD?") == "0"

    assert sess.owner is None


def test_der_besitz_wird_auch_bei_einem_fehler_zurueckgegeben():
    sess, _ = sitzung()

    with pytest.raises(ValueError):
        with sess.owned_by_current_thread("Testabschnitt"):
            raise ValueError

    assert sess.owner is None
