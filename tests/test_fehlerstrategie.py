# =============================================================================
# Datei: tests/test_fehlerstrategie.py
# Kommunikationsfehler waehrend der Messung: ErrorPolicy, MISSING und
# Wiederverbindung.
#
# Der Befund, der dazu gefuehrt hat: ein einziger fehlgeschlagener
# Lesevorgang beendete eine womoeglich stundenlange Messreihe. Verloren ging
# dabei nichts - die Senke flusht je Zeile -, aber die Reihe war zu Ende, und
# fuer einen unbeaufsichtigten Lauf ueber Tage ist das der Unterschied
# zwischen brauchbar und unbrauchbar.
#
# Vier Zusagen werden hier geprueft:
#
#   1. UNVERAENDERT  Ohne ErrorPolicy verhaelt sich alles wie bisher: der
#                    Fehler verlaesst die Schleife. Wer nichts vereinbart,
#                    bekommt keine stillschweigend veraenderte Semantik.
#   2. SICHTBAR      Mit Policy wird der Ausfall zu einer ZEILE mit
#                    mark=MISSING und voller Spaltenzahl - nicht zu einer
#                    fehlenden Zeile. Der Zielkonflikt aus S-08 loest sich
#                    ueber NO_DATA-Auffuellung.
#   3. BEGRENZT      Die Nachsicht hat Grenzen: max_consecutive und
#                    max_total beenden den Lauf mit einer Begruendung.
#   4. GEPRUEFT      Nach einem Neuaufbau wird der Geraetezustand nachgeprueft.
#                    Eine veraenderte Item-Tabelle beendet den Lauf, statt
#                    Zeilen unter falschen Spalten fortzuschreiben.
# =============================================================================

from __future__ import annotations

import json

import pytest

from wt3000_scpi.wt3000_core import ConcurrentAccessError, TmctlError, WTConfig, WTSession
from wt3000_scpi.wt3000_measure import (
    COMMUNICATION_ERRORS,
    ErrorPolicy,
    LoopStatistics,
    MeasurementAborted,
    Measurement,
    SampleMark,
    missing_values,
    run_measurement_loop,
    verify_after_reconnect,
)
from wt3000_scpi.wt3000_numeric import ItemTable, ValueStatus
from wt3000_scpi.wt3000_sinks import CsvSink, JsonlSink
from wt3000_scpi.wt3000_transport import FakeTransport, float_block


# ---------------------------------------------------------------------------
# Pruefstand
# ---------------------------------------------------------------------------


class SammelSink:
    """Senke, die alles behaelt."""

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


class StoerbarerTransport(FakeTransport):
    """FakeTransport, dessen Messwertabfrage sich gezielt stoeren laesst.

    'faellt_bei' nennt die Nummern der Lesevorgaenge, die scheitern sollen -
    'faellt_bei={2, 3}' heisst: der zweite und dritte Zyklus gehen schief, die
    uebrigen nicht. Damit laesst sich ein Aussetzer von einem Abriss
    unterscheiden, ohne auf Zeitverhalten zu bauen.

    'neuaufbau_hilft' entscheidet, ob ein 'reconnect()' die Stoerung behebt.
    Beides kommt am Geraet vor: ein gezogenes Kabel ist nach dem Einstecken
    weg, ein abgestuerzter Netzwerkstack nicht.
    """

    def __init__(
        self,
        *,
        faellt_bei: set[int] | None = None,
        neuaufbau_hilft: bool = True,
        items: str = "3;U,1;I,1;P,1",
    ) -> None:
        super().__init__(
            {
                ":NUMeric:NORMal?": items,
                ":NUMeric:HOLD?": "0",
                ":NUMeric:NORMal:VALue?": self._bloecke(),
                ":STATus:CONDition?": "0",
                ":RATE?": "1.000E+00",
                ":NUMeric:FORMat?": "FLOat",
                ":COMMunicate:HEADer?": "0",
            }
        )
        self.faellt_bei = set(faellt_bei or ())
        self.neuaufbau_hilft = neuaufbau_hilft
        self.lesevorgaenge = 0

    @staticmethod
    def _bloecke():
        zaehler = {"n": 0}

        def naechster(_command: str) -> bytes:
            zaehler["n"] += 1
            n = float(zaehler["n"])
            return float_block([n, n * 2, n * 3])

        return naechster

    def query(self, command: str) -> bytes:
        if self._key(command) == ":NUMERIC:NORMAL:VALUE":
            self.lesevorgaenge += 1
            if self.lesevorgaenge in self.faellt_bei:
                self.write(command)
                raise TmctlError("TmcReceive", 0xDEAD, command)
        return super().query(command)

    def reconnect(self) -> None:
        super().reconnect()
        if self.neuaufbau_hilft:
            self.faellt_bei.clear()


def sitzung(**kwargs) -> tuple[WTSession, StoerbarerTransport]:
    transport = StoerbarerTransport(**kwargs)
    return WTSession(transport, WTConfig()), transport


def messreihe(sess: WTSession, senke, **kwargs) -> LoopStatistics:
    """Eine blockierende Messreihe mit den Vorgaben dieser Datei."""
    vorgaben: dict = {
        "interval_s": 0.0,
        "max_samples": 4,
        "max_duration_s": None,
        "use_hold": True,
        "record_condition": False,
        "log_every": 0,
        "check_update_rate": False,
    }
    vorgaben.update(kwargs)
    return run_measurement_loop(
        session=sess,
        table=ItemTable.read_from_device(sess),
        sink=senke,
        **vorgaben,
    )


# ---------------------------------------------------------------------------
# 1 - Ohne Policy bleibt alles, wie es war
# ---------------------------------------------------------------------------


def test_ohne_policy_beendet_ein_fehler_den_lauf():
    """Die Regressionssicherung: 'error_policy=None' ist die Voreinstellung.

    Eine Bibliothek darf Ausnahmen nicht ungefragt in Datenzeilen verwandeln.
    Wer Nachsicht will, vereinbart sie.
    """
    sess, _ = sitzung(faellt_bei={2})
    senke = SammelSink()

    with pytest.raises(TmctlError):
        messreihe(sess, senke)

    # Der erste Zyklus steht, der zweite hat den Lauf beendet.
    assert len(senke.samples) == 1
    # Und die Senke ist trotzdem geschlossen - das Aufraeumen haengt nicht am
    # guten Ausgang.
    assert senke.geschlossen is True


def test_ohne_policy_ist_die_voreinstellung():
    """Kein Aufrufer muss 'error_policy=None' hinschreiben."""
    sess, _ = sitzung(faellt_bei={1})
    with pytest.raises(TmctlError):
        messreihe(sess, SammelSink())


# ---------------------------------------------------------------------------
# 2 - Mit Policy: der Ausfall wird sichtbar statt toedlich
# ---------------------------------------------------------------------------


def test_ein_aussetzer_beendet_die_reihe_nicht_mehr():
    """Der Kern von A4."""
    sess, _ = sitzung(faellt_bei={2})
    senke = SammelSink()

    stats = messreihe(sess, senke, error_policy=ErrorPolicy())

    assert stats.samples == 4
    assert len(senke.samples) == 4
    assert stats.missing == 1
    # Drei echte Messpunkte - die Luecke zaehlt nicht mit.
    assert stats.measured_samples == 3


def test_der_ausgefallene_zyklus_steht_als_zeile_in_der_ausgabe():
    """Eine Luecke, die man sieht, ist einer stillschweigend fehlenden Zeile
    vorzuziehen: nur so laesst sich hinterher die Messdauer rekonstruieren."""
    sess, _ = sitzung(faellt_bei={2})
    senke = SammelSink()

    messreihe(sess, senke, error_policy=ErrorPolicy())

    marken = [s.mark for s in senke.samples]
    assert marken == [SampleMark.OK, SampleMark.MISSING, SampleMark.OK, SampleMark.OK]
    # Die Nummerierung laeuft durch - der Ausfall ist ein Zyklus, kein Loch.
    assert [s.number for s in senke.samples] == [1, 2, 3, 4]


def test_der_ausfall_behaelt_die_volle_spaltenzahl():
    """Die Aufloesung des Zielkonflikts aus S-08.

    'require_matching_columns()' besteht zu Recht auf der festen Spaltenzahl.
    Ein Ausfall traegt deshalb NO_DATA in jeder Spalte statt gar keine Werte.
    """
    sess, _ = sitzung(faellt_bei={2})
    senke = SammelSink()

    messreihe(sess, senke, error_policy=ErrorPolicy())

    ausfall = senke.samples[1]
    assert len(ausfall.values) == len(senke.columns) == 3
    assert all(v.status is ValueStatus.NO_DATA for v in ausfall.values)


def test_der_ausfall_ist_von_geraeteseitigem_no_data_unterscheidbar():
    """Beide tragen NO_DATA-Werte; nur 'mark' trennt sie."""
    sess, _ = sitzung(faellt_bei={2})
    senke = SammelSink()
    messreihe(sess, senke, error_policy=ErrorPolicy())

    ausfall = senke.samples[1]
    assert ausfall.mark is SampleMark.MISSING
    assert "mark=MISSING" in ausfall.status_flags(senke.columns)


def test_ein_ausfall_zaehlt_die_spalten_nicht_einzeln_auf():
    """Bei einem Ausfall traegt jede Spalte NO_DATA - das folgt aus
    'mark=MISSING' und ist keine zusaetzliche Beobachtung.

    Ohne diese Kuerzung stuende bei einer Item-Tabelle mit 100 Eintraegen in
    jeder Ausfallzeile eine Statusspalte von ueber tausend Zeichen.
    """
    sess, _ = sitzung(faellt_bei={2})
    senke = SammelSink()
    messreihe(sess, senke, error_policy=ErrorPolicy())

    assert senke.samples[1].status_flags(senke.columns) == ["mark=MISSING"]


def test_die_zaehlung_setzt_nach_einem_erfolg_zurueck():
    """Zwei Aussetzer mit einem guten Zyklus dazwischen sind kein Abriss.

    Ohne das Zuruecksetzen liefe jede lange Messreihe mit gelegentlichen
    Stoerungen irgendwann in 'max_consecutive' - ein Fehler pro Stunde reichte.
    """
    sess, _ = sitzung(faellt_bei={2, 4})
    senke = SammelSink()

    stats = messreihe(
        sess, senke, max_samples=5, error_policy=ErrorPolicy(max_consecutive=2)
    )

    assert stats.samples == 5
    assert stats.missing == 2
    assert [s.mark for s in senke.samples] == [
        SampleMark.OK,
        SampleMark.MISSING,
        SampleMark.OK,
        SampleMark.MISSING,
        SampleMark.OK,
    ]


def test_nach_dem_fehler_wird_nachgeraeumt():
    """Sonst bekaeme der naechste Query die verspaetete Antwort des
    fehlgeschlagenen Zyklus - und damit dessen Werte unter neuem Zeitstempel."""
    sess, transport = sitzung(faellt_bei={2})

    vorher = len(transport.timeouts_ms)
    messreihe(sess, SammelSink(), error_policy=ErrorPolicy())

    # drain_after_failure() verstellt den Timeout und stellt ihn zurueck.
    assert len(transport.timeouts_ms) >= vorher + 2


# ---------------------------------------------------------------------------
# 3 - Die Grenzen der Nachsicht
# ---------------------------------------------------------------------------


def test_zu_viele_fehler_in_folge_beenden_den_lauf():
    sess, _ = sitzung(faellt_bei={2, 3, 4})
    senke = SammelSink()

    with pytest.raises(MeasurementAborted, match="max_consecutive=2"):
        messreihe(sess, senke, max_samples=9, error_policy=ErrorPolicy(max_consecutive=2))

    assert senke.geschlossen is True


def test_die_ausloesende_zeile_steht_noch_in_der_datei():
    """Sonst fehlte ausgerechnet der Datensatz, der das Ende erklaert."""
    sess, _ = sitzung(faellt_bei={2, 3})
    senke = SammelSink()

    with pytest.raises(MeasurementAborted):
        messreihe(sess, senke, max_samples=9, error_policy=ErrorPolicy(max_consecutive=2))

    assert [s.mark for s in senke.samples] == [
        SampleMark.OK,
        SampleMark.MISSING,
        SampleMark.MISSING,
    ]


def test_das_gesamtbudget_beendet_den_lauf():
    """Eine Leitung, die jede Minute einmal zuckt, reisst nie
    'max_consecutive' - und liefert am Ende trotzdem mehr Luecken als Werte."""
    sess, _ = sitzung(faellt_bei={2, 4, 6})
    senke = SammelSink()

    with pytest.raises(MeasurementAborted, match="max_total=2"):
        messreihe(sess, senke, max_samples=9, error_policy=ErrorPolicy(max_total=2))

    assert sum(1 for s in senke.samples if s.mark is SampleMark.MISSING) == 2


def test_der_abbruch_nennt_die_urspruengliche_ursache():
    """'MeasurementAborted' sagt 'Grenze erreicht', nicht 'Leitung weg'.
    Der eigentliche Fehler haengt als __cause__ daran."""
    sess, _ = sitzung(faellt_bei={1, 2})
    with pytest.raises(MeasurementAborted) as fehler:
        messreihe(sess, SammelSink(), max_samples=9, error_policy=ErrorPolicy(max_consecutive=2))

    assert isinstance(fehler.value.__cause__, TmctlError)


# ---------------------------------------------------------------------------
# 4 - Was NICHT unter die Fehlerstrategie faellt
# ---------------------------------------------------------------------------


def test_nur_kommunikationsfehler_sind_gemeint():
    """Ein Programmierfehler darf nicht zu einer MISSING-Zeile werden."""
    assert ConcurrentAccessError not in COMMUNICATION_ERRORS


def test_ein_fehler_der_senke_wird_nicht_verschluckt():
    """Die Policy gilt fuer die Leitung zum Geraet, nicht fuer die Ausgabe."""
    sess, _ = sitzung()

    class KaputteSenke(SammelSink):
        def write(self, sample) -> None:
            raise RuntimeError("Platte voll")

    with pytest.raises(RuntimeError, match="Platte voll"):
        messreihe(sess, KaputteSenke(), error_policy=ErrorPolicy())


# ---------------------------------------------------------------------------
# 5 - Wiederverbindung
# ---------------------------------------------------------------------------


def test_nach_zwei_fehlern_wird_neu_verbunden():
    sess, transport = sitzung(faellt_bei={2, 3})
    senke = SammelSink()

    stats = messreihe(
        sess,
        senke,
        max_samples=5,
        error_policy=ErrorPolicy(max_consecutive=5, reconnect_after=2),
    )

    assert transport.reconnects == 1
    assert stats.reconnects == 1
    # Der Neuaufbau hat geholfen: ab Zyklus 4 laeuft es wieder.
    assert stats.missing == 2
    assert [s.mark for s in senke.samples][-2:] == [SampleMark.OK, SampleMark.OK]


def test_ohne_reconnect_after_wird_nie_neu_verbunden():
    sess, transport = sitzung(faellt_bei={2})
    messreihe(sess, SammelSink(), error_policy=ErrorPolicy())
    assert transport.reconnects == 0


def test_die_zahl_der_neuaufbauten_ist_begrenzt():
    """Sonst liefe eine tote Leitung bis in alle Ewigkeit im Neuaufbau."""
    sess, transport = sitzung(
        faellt_bei=set(range(1, 100)), neuaufbau_hilft=False
    )

    with pytest.raises(MeasurementAborted):
        messreihe(
            sess,
            SammelSink(),
            max_samples=50,
            error_policy=ErrorPolicy(max_consecutive=9, reconnect_after=2, max_reconnects=3),
        )

    assert transport.reconnects == 3


def test_ein_transport_ohne_reconnect_bricht_nicht_ab(caplog):
    """FakeTransport kann es; ein selbstgeschriebener Transport muss nicht.
    Die Messung laeuft dann ohne Wiederverbindung weiter."""
    import logging

    class OhneNeuaufbau:
        """Nur die fuenf Methoden des Transport-Protocols."""

        def __init__(self, echt: FakeTransport) -> None:
            self._echt = echt

        def write(self, command: str) -> None:
            self._echt.write(command)

        def read(self) -> bytes:
            return self._echt.read()

        def query(self, command: str) -> bytes:
            return self._echt.query(command)

        def set_timeout(self, timeout_ms: int) -> None:
            self._echt.set_timeout(timeout_ms)

        def close(self) -> None:
            self._echt.close()

    innen = StoerbarerTransport(faellt_bei={2})
    sess = WTSession(OhneNeuaufbau(innen), WTConfig())
    assert sess.can_reconnect is False

    with caplog.at_level(logging.ERROR, logger="wt3000.measure"):
        stats = messreihe(
            sess, SammelSink(), error_policy=ErrorPolicy(reconnect_after=1)
        )

    assert stats.samples == 4
    assert stats.missing == 1
    assert any("Transport kann es nicht" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 6 - Pruefung nach dem Neuaufbau
# ---------------------------------------------------------------------------


def test_nach_dem_neuaufbau_wird_der_geraetezustand_geprueft():
    sess, transport = sitzung()
    tabelle = ItemTable.read_from_device(sess)

    verify_after_reconnect(sess, tabelle)  # darf nicht werfen

    transport.responses[transport._key(":NUMeric:FORMat?")] = "ASCii"
    with pytest.raises(MeasurementAborted, match="FORMat"):
        verify_after_reconnect(sess, tabelle)


def test_ein_eingeschalteter_header_nach_dem_neuaufbau_beendet_den_lauf():
    sess, transport = sitzung()
    tabelle = ItemTable.read_from_device(sess)

    transport.responses[transport._key(":COMMunicate:HEADer?")] = "1"
    with pytest.raises(MeasurementAborted, match="HEADer"):
        verify_after_reconnect(sess, tabelle)


def test_eine_veraenderte_item_tabelle_beendet_den_lauf():
    """Der gefaehrlichste Fall im ganzen A4-Umfang.

    War das Geraet zwischendurch stromlos, kann die Item-Tabelle anders
    stehen. Jede weitere Zeile bedeutete dann etwas anderes als ihr
    Spaltenkopf behauptet - und das ist hinterher nicht mehr zu erkennen.
    Solche Daten sind schlimmer als keine.
    """
    sess, transport = sitzung()
    tabelle = ItemTable.read_from_device(sess)

    transport.responses[transport._key(":NUMeric:NORMal?")] = "3;P,1;I,1;U,1"
    with pytest.raises(MeasurementAborted, match="Reihenfolge"):
        verify_after_reconnect(sess, tabelle)


def test_der_lauf_endet_wenn_das_geraet_nach_dem_neuaufbau_anders_steht():
    """Dieselbe Pruefung, aber im Lauf - und ohne weitere Zeile danach."""

    class UmgestecktesGeraet(StoerbarerTransport):
        def reconnect(self) -> None:
            super().reconnect()
            # Nach dem Neuaufbau steht eine andere Item-Tabelle im Geraet.
            self.responses[self._key(":NUMeric:NORMal?")] = "2;U,1;I,1"

    transport = UmgestecktesGeraet(faellt_bei={2, 3})
    sess = WTSession(transport, WTConfig())
    senke = SammelSink()

    with pytest.raises(MeasurementAborted, match="Item-Tabelle"):
        run_measurement_loop(
            session=sess,
            table=ItemTable.read_from_device(sess),
            sink=senke,
            interval_s=0.0,
            max_samples=9,
            max_duration_s=None,
            use_hold=True,
            record_condition=False,
            log_every=0,
            check_update_rate=False,
            error_policy=ErrorPolicy(max_consecutive=5, reconnect_after=2),
        )

    assert senke.geschlossen is True


# ---------------------------------------------------------------------------
# 7 - Die Ausgabeformate nehmen den Ausfall an
# ---------------------------------------------------------------------------


def test_die_csv_schreibt_den_ausfall_als_leere_zellen(tmp_path):
    """Der Beweis, dass S-08 geloest ist: die strenge Spaltenregel greift
    nicht mehr gegen einen MISSING-Datensatz."""
    sess, _ = sitzung(faellt_bei={2})
    ziel = tmp_path / "messung.csv"

    messreihe(sess, CsvSink(ziel), error_policy=ErrorPolicy())

    zeilen = ziel.read_text(encoding="utf-8").strip().splitlines()
    assert len(zeilen) == 5  # Kopf + 4 Datensaetze
    ausfall = zeilen[2].split(",")
    # timestamp, elapsed_s, sample, condition, U1, I1, P1, status_flags
    assert ausfall[2] == "2"
    assert ausfall[4:7] == ["", "", ""]
    assert ausfall[7] == "mark=MISSING"


def test_jsonl_schreibt_den_ausfall_als_null(tmp_path):
    sess, _ = sitzung(faellt_bei={2})
    ziel = tmp_path / "messung.jsonl"

    messreihe(sess, JsonlSink(ziel), error_policy=ErrorPolicy())

    zeilen = [json.loads(z) for z in ziel.read_text(encoding="utf-8").strip().splitlines()]
    datensaetze = [z for z in zeilen if z.get("kind") == "sample"]
    assert len(datensaetze) == 4
    ausfall = datensaetze[1]
    assert ausfall["mark"] == "MISSING"
    assert ausfall["values"] == {"U1": None, "I1": None, "P1": None}


# ---------------------------------------------------------------------------
# 8 - Die Policy selbst
# ---------------------------------------------------------------------------


def test_unbrauchbare_policies_werden_abgelehnt():
    with pytest.raises(Exception, match="max_consecutive"):
        ErrorPolicy(max_consecutive=0)
    with pytest.raises(Exception, match="max_total"):
        ErrorPolicy(max_total=0)
    with pytest.raises(Exception, match="reconnect_after"):
        ErrorPolicy(reconnect_after=0)


def test_reconnect_after_ueber_max_consecutive_ist_wirkungslos_und_wird_abgelehnt():
    """Der Lauf braeche ab, bevor je ein Neuaufbau versucht wuerde - eine
    Einstellung, die verspricht, was sie nicht halten kann."""
    with pytest.raises(Exception, match="bevor je ein Neuaufbau"):
        ErrorPolicy(max_consecutive=2, reconnect_after=3)


def test_unattended_ist_eine_brauchbare_voreinstellung():
    policy = ErrorPolicy.unattended()
    assert policy.reconnect_after is not None
    assert policy.reconnect_after <= policy.max_consecutive
    assert policy.max_total is None  # kein Abbruch wegen Aussetzern ueber Tage


def test_missing_values_liefert_die_richtige_laenge():
    werte = missing_values(7)
    assert len(werte) == 7
    assert all(v.status is ValueStatus.NO_DATA for v in werte)


# ---------------------------------------------------------------------------
# 9 - Auch im Hintergrundlauf
# ---------------------------------------------------------------------------


def test_die_policy_gilt_auch_fuer_die_steuerbare_messung():
    """A4 und A3 muessen zusammenspielen - der unbeaufsichtigte Langzeitlauf
    braucht beides."""
    sess, _ = sitzung(faellt_bei={2})
    senke = SammelSink()

    m = Measurement(
        session=sess,
        table=ItemTable.read_from_device(sess),
        sink=senke,
        interval_s=0.0,
        max_samples=4,
        record_condition=False,
        check_update_rate=False,
        error_policy=ErrorPolicy(),
    ).start()

    stats = m.wait(timeout=5.0)

    assert stats.samples == 4
    assert stats.missing == 1
    assert sess.owner is None


def test_ein_abbruch_im_hintergrundlauf_erreicht_den_aufrufer():
    sess, _ = sitzung(faellt_bei={1, 2, 3})
    m = Measurement(
        session=sess,
        table=ItemTable.read_from_device(sess),
        sink=SammelSink(),
        interval_s=0.0,
        max_samples=9,
        record_condition=False,
        check_update_rate=False,
        error_policy=ErrorPolicy(max_consecutive=2),
    ).start()

    with pytest.raises(MeasurementAborted):
        m.wait(timeout=5.0)

    assert sess.owner is None
