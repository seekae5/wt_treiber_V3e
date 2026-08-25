# =============================================================================
# Datei: tests/test_rotation.py
# Rotation und sicheres Fortsetzen (M4-4).
#
# Vier Zusagen, die zusammen den Langzeit-Dateibetrieb ausmachen:
#
#   1. ROTATION     Eine Messreihe verteilt sich nach Zeilen, Groesse oder
#                   Zeit auf mehrere Dateien. Jeder Abschnitt ist fuer sich
#                   auswertbar; Nummerierung und Laufzeit zaehlen durch.
#   2. FORTSETZEN   An eine vorhandene Datei laesst sich weiterschreiben -
#                   aber erst, nachdem Format und Spaltenkopf geprueft sind.
#   3. KOLLISION    Eine bestehende Datei wird nicht mehr wortlos
#                   ueberschrieben. 'error' und 'unique' verhindern es ganz.
#   4. GRENZEN      Eine Policy ohne Grenze und ein unpassender Spaltenkopf
#                   sind Fehler und keine stillen Sonderfaelle.
#
# Der gefaehrlichste Fall steht in Abschnitt 2: an eine Datei mit ANDEREM
# Spaltenkopf weiterzuschreiben erzeugt eine Datei, in der ab einer bestimmten
# Zeile etwas anderes steht, als der Kopf behauptet - und das laesst sich
# hinterher nicht mehr erkennen.
# =============================================================================

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone

import pytest

from wt3000_scpi.wt3000_measure import Sample, SampleMark
from wt3000_scpi.wt3000_numeric import NumericValue, ValueStatus
from wt3000_scpi.wt3000_sinks import (
    AppendMismatch,
    CsvSink,
    JsonlSink,
    RotatingSink,
    RotationPolicy,
    segment_path,
    unique_path,
)
from wt3000_scpi.wt3000_core import WTError


# ---------------------------------------------------------------------------
# Pruefstand
# ---------------------------------------------------------------------------


SPALTEN = ["U1", "I1", "P1"]


def sample(nummer: int) -> Sample:
    """Ein Datensatz mit drei brauchbaren Werten."""
    return Sample(
        timestamp=datetime(2026, 8, 25, 12, 0, nummer % 60, tzinfo=timezone.utc),
        elapsed_s=float(nummer),
        number=nummer,
        condition=0,
        values=[NumericValue(float(nummer) * f, ValueStatus.OK, 0) for f in (1, 2, 3)],
        mark=SampleMark.OK,
    )


def schreibe(sink, anzahl: int, spalten=SPALTEN, metadata=None) -> None:
    """Eine kurze Messreihe in eine Senke schreiben."""
    sink.open(spalten, metadata or {})
    try:
        for n in range(1, anzahl + 1):
            sink.write(sample(n))
    finally:
        sink.close()


def csv_zeilen(pfad) -> list[list[str]]:
    with pfad.open(newline="", encoding="utf-8") as datei:
        return list(csv.reader(datei))


def jsonl_zeilen(pfad) -> list[dict]:
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").strip().splitlines()]


# ---------------------------------------------------------------------------
# 1 - Rotation
# ---------------------------------------------------------------------------


def test_rotation_nach_zeilenzahl(tmp_path):
    basis = tmp_path / "messung.csv"
    sink = RotatingSink(CsvSink, basis, RotationPolicy(max_rows=2))

    schreibe(sink, 5)

    assert [p.name for p in sink.segments] == [
        "messung_0001.csv",
        "messung_0002.csv",
        "messung_0003.csv",
    ]
    # 2 + 2 + 1 Datensaetze, jeder Abschnitt mit eigenem Kopf.
    assert [len(csv_zeilen(p)) - 1 for p in sink.segments] == [2, 2, 1]


def test_die_basisdatei_bleibt_unangetastet(tmp_path):
    """Kein Abschnitt heisst anders als die uebrigen - sonst waere der erste
    ein Sonderfall, den jede Auswertung gesondert behandeln muesste."""
    basis = tmp_path / "messung.csv"
    schreibe(RotatingSink(CsvSink, basis, RotationPolicy(max_rows=2)), 3)

    assert not basis.exists()


def test_jeder_abschnitt_ist_fuer_sich_auswertbar(tmp_path):
    """Der Sinn der Rotation: eine Datei aus der Mitte muss ohne die anderen
    lesbar sein."""
    basis = tmp_path / "messung.csv"
    sink = RotatingSink(CsvSink, basis, RotationPolicy(max_rows=2))
    schreibe(sink, 5)

    for pfad in sink.segments:
        zeilen = csv_zeilen(pfad)
        assert zeilen[0][4:7] == SPALTEN  # eigener Spaltenkopf
        assert len(zeilen) >= 2


def test_nummerierung_und_laufzeit_zaehlen_ueber_abschnitte_durch(tmp_path):
    """'sample' und 'elapsed_s' gehoeren zur Messreihe, nicht zur Datei.
    Beganne die Nummerierung je Abschnitt neu, waeren die Teile hinterher
    nicht mehr zusammensetzbar."""
    basis = tmp_path / "messung.csv"
    sink = RotatingSink(CsvSink, basis, RotationPolicy(max_rows=2))
    schreibe(sink, 5)

    nummern = [
        int(zeile[2]) for pfad in sink.segments for zeile in csv_zeilen(pfad)[1:]
    ]
    assert nummern == [1, 2, 3, 4, 5]


def test_rotation_nach_groesse(tmp_path):
    basis = tmp_path / "messung.csv"
    # Eine Datenzeile misst gut 60 Byte; 200 Byte reichen fuer wenige davon.
    sink = RotatingSink(CsvSink, basis, RotationPolicy(max_bytes=200))

    schreibe(sink, 12)

    assert len(sink.segments) > 1
    # Geprueft wird nach dem Schreiben - ein Abschnitt darf die Grenze um
    # hoechstens eine Zeile ueberschreiten, aber nicht beliebig.
    for pfad in sink.segments[:-1]:
        assert pfad.stat().st_size >= 200
        assert pfad.stat().st_size < 200 + 200


def test_rotation_nach_zeit(tmp_path, monkeypatch):
    """Die Uhr wird gestellt, statt im Test zu warten."""
    import wt3000_scpi.wt3000_sinks as sinks

    jetzt = {"t": 1000.0}
    monkeypatch.setattr(sinks.time, "monotonic", lambda: jetzt["t"])

    basis = tmp_path / "messung.csv"
    sink = RotatingSink(CsvSink, basis, RotationPolicy(max_seconds=60))
    sink.open(SPALTEN, {})
    try:
        sink.write(sample(1))
        assert len(sink.segments) == 1

        jetzt["t"] += 60
        # Der Datensatz, bei dem die Grenze reisst, steht noch im ALTEN
        # Abschnitt - erst der naechste beginnt den neuen. Sonst entstuende
        # am Ende eines Laufs eine Datei mit nichts als einem Spaltenkopf.
        sink.write(sample(2))
        assert len(sink.segments) == 1

        sink.write(sample(3))
        assert len(sink.segments) == 2
    finally:
        sink.close()

    assert [len(csv_zeilen(p)) - 1 for p in sink.segments] == [2, 1]


def test_am_ende_bleibt_kein_leerer_abschnitt_stehen(tmp_path):
    """Der Lauf endet genau auf einer Grenze.

    Bei eifriger Rotation entstuende hier eine dritte Datei mit nichts als
    einem Spaltenkopf - und wer die Abschnitte zusammenfuehrt, suchte darin
    die fehlenden Daten.
    """
    basis = tmp_path / "messung.csv"
    sink = RotatingSink(CsvSink, basis, RotationPolicy(max_rows=2))

    schreibe(sink, 4)

    assert len(sink.segments) == 2
    assert [len(csv_zeilen(p)) - 1 for p in sink.segments] == [2, 2]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "messung_0001.csv",
        "messung_0002.csv",
    ]


def test_rotation_wirkt_auch_fuer_jsonl(tmp_path):
    """Rotation ist formatunabhaengig - das ist der Grund fuer die Umhuellung."""
    basis = tmp_path / "messung.jsonl"
    sink = RotatingSink(JsonlSink, basis, RotationPolicy(max_rows=2))

    schreibe(sink, 5)

    assert len(sink.segments) == 3
    for pfad in sink.segments:
        zeilen = jsonl_zeilen(pfad)
        assert zeilen[0]["kind"] == "metadata"
        assert zeilen[0]["columns"] == SPALTEN


def test_die_metadaten_gehen_in_jeden_abschnitt(tmp_path):
    basis = tmp_path / "messung.jsonl"
    sink = RotatingSink(JsonlSink, basis, RotationPolicy(max_rows=2))

    schreibe(sink, 3, metadata={"update_rate_s": 1.0})

    for pfad in sink.segments:
        assert jsonl_zeilen(pfad)[0]["metadata"]["update_rate_s"] == 1.0


def test_eigene_namensgebung(tmp_path):
    basis = tmp_path / "messung.csv"

    def nach_zeitstempel(basis, index, started):
        return basis.with_name(f"{basis.stem}_{started:%Y%m%d}_{index}{basis.suffix}")

    sink = RotatingSink(CsvSink, basis, RotationPolicy(max_rows=1), namer=nach_zeitstempel)
    schreibe(sink, 2)

    assert all("_2026" in p.name or "_202" in p.name for p in sink.segments)
    assert len(sink.segments) == 2


def test_schreiben_ohne_open_faellt_auf(tmp_path):
    from wt3000_scpi.wt3000_sinks import SinkNotOpen

    sink = RotatingSink(CsvSink, tmp_path / "m.csv", RotationPolicy(max_rows=2))
    with pytest.raises(SinkNotOpen):
        sink.write(sample(1))


def test_close_ist_mehrfach_unschaedlich(tmp_path):
    sink = RotatingSink(CsvSink, tmp_path / "m.csv", RotationPolicy(max_rows=2))
    schreibe(sink, 1)
    sink.close()
    sink.close()


def test_eine_policy_ohne_grenze_wird_abgelehnt():
    """Sie wuerde nie ausloesen - eine gewoehnliche Senke mit Umweg."""
    with pytest.raises(WTError, match="ohne Grenze"):
        RotationPolicy()


def test_unsinnige_grenzen_werden_abgelehnt():
    with pytest.raises(WTError, match="max_rows"):
        RotationPolicy(max_rows=0)
    with pytest.raises(WTError, match="max_bytes"):
        RotationPolicy(max_bytes=-1)
    with pytest.raises(WTError, match="max_seconds"):
        RotationPolicy(max_seconds=0)


# ---------------------------------------------------------------------------
# 2 - Fortsetzen, und wann es verweigert wird
# ---------------------------------------------------------------------------


def test_append_setzt_eine_vorhandene_csv_fort(tmp_path):
    ziel = tmp_path / "messung.csv"
    schreibe(CsvSink(ziel), 2)
    schreibe(CsvSink(ziel, if_exists="append"), 3)

    zeilen = csv_zeilen(ziel)
    # Ein Kopf, dann 2 + 3 Datenzeilen.
    assert len(zeilen) == 6
    assert zeilen[0][4:7] == SPALTEN
    assert [z[2] for z in zeilen[1:]] == ["1", "2", "1", "2", "3"]


def test_append_auf_eine_fehlende_datei_legt_sie_an(tmp_path):
    """Sonst muesste jeder Aufrufer den ersten Lauf gesondert behandeln."""
    ziel = tmp_path / "neu.csv"
    schreibe(CsvSink(ziel, if_exists="append"), 2)

    assert len(csv_zeilen(ziel)) == 3  # Kopf + 2


def test_ein_anderer_spaltenkopf_verhindert_das_fortsetzen(tmp_path):
    """Der gefaehrlichste Fall von M4-4.

    Ohne diese Pruefung entstuende eine Datei, in der ab einer bestimmten
    Zeile andere Groessen stehen als im Kopf - hinterher nicht erkennbar.
    """
    ziel = tmp_path / "messung.csv"
    schreibe(CsvSink(ziel), 2)

    with pytest.raises(AppendMismatch, match="Spaltenkopf"):
        schreibe(CsvSink(ziel, if_exists="append"), 1, spalten=["U1", "I1", "P1", "S1"])


def test_die_meldung_nennt_die_erste_abweichende_spalte(tmp_path):
    """Eine Zahl allein ('7 statt 8 Spalten') zwingt zum Nachsehen."""
    ziel = tmp_path / "messung.csv"
    schreibe(CsvSink(ziel), 1)

    with pytest.raises(AppendMismatch) as fehler:
        schreibe(CsvSink(ziel, if_exists="append"), 1, spalten=["U1", "I2", "P1"])

    assert "'I1'" in str(fehler.value) and "'I2'" in str(fehler.value)


def test_ein_anderes_trennzeichen_verhindert_das_fortsetzen(tmp_path):
    """Auch das ist ein Formatunterschied, nicht nur ein kosmetischer."""
    ziel = tmp_path / "messung.csv"
    schreibe(CsvSink(ziel), 2)

    with pytest.raises(AppendMismatch):
        schreibe(CsvSink(ziel, delimiter=";", if_exists="append"), 1)


def test_das_fortsetzen_wird_protokolliert(tmp_path, caplog):
    """'sample' und 'elapsed_s' beginnen erneut - wer das nicht weiss, haelt
    die Datei fuer widerspruechlich."""
    ziel = tmp_path / "messung.csv"
    schreibe(CsvSink(ziel), 1)

    with caplog.at_level(logging.WARNING, logger="wt3000.sinks"):
        schreibe(CsvSink(ziel, if_exists="append"), 1)

    meldungen = " ".join(r.getMessage() for r in caplog.records)
    assert "timestamp_iso" in meldungen


def test_append_setzt_eine_vorhandene_jsonl_fort(tmp_path):
    ziel = tmp_path / "messung.jsonl"
    schreibe(JsonlSink(ziel), 2)
    schreibe(JsonlSink(ziel, if_exists="append"), 2)

    zeilen = jsonl_zeilen(ziel)
    kopf = [z for z in zeilen if z["kind"] == "metadata"]
    # Zwei Metadatenzeilen - jede Zeile steht in JSONL fuer sich, und der
    # zweite Lauf hat eigene Laufparameter.
    assert len(kopf) == 2
    assert kopf[1]["continued"] is True
    assert len([z for z in zeilen if z["kind"] == "sample"]) == 4


def test_andere_spalten_verhindern_das_fortsetzen_der_jsonl(tmp_path):
    ziel = tmp_path / "messung.jsonl"
    schreibe(JsonlSink(ziel), 1)

    with pytest.raises(AppendMismatch, match="Spalten"):
        schreibe(JsonlSink(ziel, if_exists="append"), 1, spalten=["U1", "I1"])


def test_eine_fremde_datei_wird_nicht_fortgesetzt(tmp_path):
    """Weiterzuschreiben ergaebe eine Datei, die weder das eine noch das
    andere Format ist."""
    ziel = tmp_path / "fremd.jsonl"
    ziel.write_text("kein json\n", encoding="utf-8")

    with pytest.raises(AppendMismatch, match="JSON"):
        schreibe(JsonlSink(ziel, if_exists="append"), 1)


def test_eine_jsonl_ohne_metadatenzeile_wird_nicht_fortgesetzt(tmp_path):
    ziel = tmp_path / "ohne_kopf.jsonl"
    ziel.write_text(json.dumps({"kind": "sample"}) + "\n", encoding="utf-8")

    with pytest.raises(AppendMismatch, match="Metadatenzeile"):
        schreibe(JsonlSink(ziel, if_exists="append"), 1)


# ---------------------------------------------------------------------------
# 3 - Kollisionen
# ---------------------------------------------------------------------------


def test_ueberschreiben_bleibt_die_voreinstellung_wird_aber_gemeldet(tmp_path, caplog):
    """Der frueher Befund lautete 'ueberschreibt wortlos'. Wortlos ist es
    nicht mehr; die Voreinstellung bleibt, damit kein bestehendes Skript
    stillschweigend anders laeuft."""
    ziel = tmp_path / "messung.csv"
    schreibe(CsvSink(ziel), 3)

    with caplog.at_level(logging.WARNING, logger="wt3000.sinks"):
        schreibe(CsvSink(ziel), 1)

    assert len(csv_zeilen(ziel)) == 2  # neu angelegt
    assert any("ueberschrieben" in r.getMessage() for r in caplog.records)


def test_error_verhindert_das_ueberschreiben(tmp_path):
    ziel = tmp_path / "messung.csv"
    schreibe(CsvSink(ziel), 2)

    with pytest.raises(WTError, match="existiert bereits"):
        schreibe(CsvSink(ziel, if_exists="error"), 1)

    # Die alte Datei ist unangetastet.
    assert len(csv_zeilen(ziel)) == 3


def test_unique_weicht_auf_einen_freien_namen_aus(tmp_path):
    ziel = tmp_path / "messung.csv"
    schreibe(CsvSink(ziel), 2)

    zweite = CsvSink(ziel, if_exists="unique")
    schreibe(zweite, 1)

    assert zweite.path.name == "messung_0001.csv"
    assert len(csv_zeilen(ziel)) == 3        # unveraendert
    assert len(csv_zeilen(zweite.path)) == 2


def test_unique_zaehlt_weiter(tmp_path):
    ziel = tmp_path / "messung.csv"
    for _ in range(3):
        schreibe(CsvSink(ziel, if_exists="unique"), 1)

    namen = sorted(p.name for p in tmp_path.iterdir())
    assert namen == ["messung.csv", "messung_0001.csv", "messung_0002.csv"]


def test_unique_path_laesst_einen_freien_namen_unveraendert(tmp_path):
    frei = tmp_path / "frei.csv"
    assert unique_path(frei) == frei


def test_eine_leere_datei_gilt_nicht_als_vorhanden(tmp_path):
    """Eine angelegte, aber nie beschriebene Datei ist kein Messergebnis -
    sie zu schuetzen waere Umstand ohne Nutzen."""
    ziel = tmp_path / "leer.csv"
    ziel.touch()

    schreibe(CsvSink(ziel, if_exists="error"), 1)  # darf nicht werfen
    assert len(csv_zeilen(ziel)) == 2


def test_ein_unbekanntes_if_exists_wird_abgelehnt(tmp_path):
    with pytest.raises(WTError, match="if_exists"):
        schreibe(CsvSink(tmp_path / "m.csv", if_exists="vielleicht"), 1)  # type: ignore[arg-type]


def test_segment_path_ist_sortierbar(tmp_path):
    """Vier Stellen, damit eine Dateiliste in der richtigen Reihenfolge steht."""
    basis = tmp_path / "messung.csv"
    namen = [segment_path(basis, i, datetime.now()).name for i in (1, 2, 10, 100)]
    assert namen == sorted(namen)
