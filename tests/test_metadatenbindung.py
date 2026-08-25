# =============================================================================
# Datei: tests/test_metadatenbindung.py
# Verbindliche Metadaten und ihre Bindung an die Datendatei (M4-3).
#
# Der Befund: die Angaben zu einem Lauf gab es zweimal in halber Form. Die
# Laufparameter gingen an die Senken (und damit in die JSONL), der
# Geraetezustand in ein optionales Sidecar. Keines von beidem war
# vollstaendig - und nichts verband eine CSV mit ihrem Sidecar. Wer beide
# Dateien vor sich hatte, konnte nicht feststellen, ob sie zusammengehoeren.
#
# Vier Zusagen:
#
#   1. EINMAL       'RunMetadata' entsteht einmal je Lauf und geht an beide
#                   Stellen. Zwei halbe Beschreibungen gibt es nicht mehr.
#   2. VOLLSTAENDIG Eine JSONL traegt Geraet, Einheiten und Laufparameter
#                   selbst - sie ist ohne Sidecar interpretierbar.
#   3. GEBUNDEN     Das Sidecar nennt jede Datendatei mit Groesse und
#                   SHA-256. Die Zusammengehoerigkeit ist damit nachweisbar
#                   und nicht bloss behauptet.
#   4. NACHHER      Das Sidecar entsteht NACH dem Lauf und kennt deshalb
#                   Pruefsummen, Abschnitte und Ergebnis.
#
# Der Hash ist dabei der eigentliche Nachweis: ein gleicher Dateiname beweist
# nichts, zwei Laeufe heissen leicht gleich.
# =============================================================================

from __future__ import annotations

import json

import pytest

from wt3000_scpi import WT3000, WTConfig
from wt3000_scpi.wt3000_core import WTSession
from wt3000_scpi.wt3000_measure import (
    SIDECAR_VERSION,
    ErrorPolicy,
    LoopStatistics,
    RunMetadata,
    SidecarMismatch,
    file_digest,
    output_paths_of,
    read_device_context,
    run_measurement_loop,
    sidecar_path,
    verify_sidecar,
)
from wt3000_scpi.wt3000_numeric import ItemTable
from wt3000_scpi.wt3000_sinks import (
    CallbackSink,
    CsvSink,
    JsonlSink,
    MultiSink,
    RotatingSink,
    RotationPolicy,
)

from conftest import base_responses
from test_fehlerstrategie import StoerbarerTransport


# ---------------------------------------------------------------------------
# Pruefstand
# ---------------------------------------------------------------------------


def sitzung(**kwargs) -> tuple[WTSession, StoerbarerTransport]:
    transport = StoerbarerTransport(**kwargs)
    # Die elf Metadatenabfragen beantworten, sonst stuenden nur Fehlertexte drin.
    for knoten, antwort in base_responses().items():
        transport.responses.setdefault(knoten, antwort)
    return WTSession(transport, WTConfig()), transport


def fassade() -> WT3000:
    antworten = base_responses()
    antworten[":NUMERIC:NORMAL"] = "3;U,1;I,1;P,1"
    antworten[":NUMERIC:NORMAL:VALUE"] = StoerbarerTransport._bloecke()
    return WT3000.from_transport(
        StoerbarerTransport.__mro__[1](antworten),  # schlichter FakeTransport
        WTConfig(use_remote=False),
        read_only=False,
        allow_changes=True,
    )


def messreihe(sess, senke, **kwargs) -> LoopStatistics:
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
        session=sess, table=ItemTable.read_from_device(sess), sink=senke, **vorgaben
    )


# ---------------------------------------------------------------------------
# 1 - RunMetadata entsteht einmal
# ---------------------------------------------------------------------------


def test_capture_erhebt_geraet_einheiten_und_spalten():
    sess, _ = sitzung()
    tabelle = ItemTable.read_from_device(sess)

    run = RunMetadata.capture(sess, tabelle, {"sample_interval_s": 1.0})

    assert run.columns == ["U1", "I1", "P1"]
    assert run.units == {"U1": "V", "I1": "A", "P1": "W"}
    assert "YOKOGAWA" in run.device["idn"]
    assert run.parameters["sample_interval_s"] == 1.0
    assert len(run.run_id) == 16


def test_jeder_lauf_bekommt_eine_eigene_kennung():
    sess, _ = sitzung()
    tabelle = ItemTable.read_from_device(sess)

    erste = RunMetadata.capture(sess, tabelle)
    zweite = RunMetadata.capture(sess, tabelle)

    assert erste.run_id != zweite.run_id


def test_ohne_geraeteabfragen_bleibt_der_block_leer():
    """Elf Queries kosten Zeit und setzen eine freie Sitzung voraus."""
    sess, transport = sitzung()
    tabelle = ItemTable.read_from_device(sess)
    vorher = len(transport.written)

    run = RunMetadata.capture(sess, tabelle, include_device=False)

    assert run.device == {}
    assert len(transport.written) == vorher
    # Die uebrigen Angaben sind trotzdem da.
    assert run.units and run.columns


def test_ein_fehlgeschlagener_query_wird_vermerkt_statt_verschwiegen():
    sess, transport = sitzung()
    transport.fail_commands.add(transport._key(":INPut:SCALing?"))

    device = read_device_context(sess)

    assert "<Fehler:" in device["input_scaling"]
    # Die uebrigen Felder stehen trotzdem.
    assert "YOKOGAWA" in device["idn"]


# ---------------------------------------------------------------------------
# 2 - Die Senken bekommen die vollen Metadaten
# ---------------------------------------------------------------------------


def test_die_sinkmetadaten_bleiben_flach_und_abwaertskompatibel():
    """Die bisherigen Schluessel bleiben, wo Auswertungen sie erwarten."""
    sess, _ = sitzung()
    tabelle = ItemTable.read_from_device(sess)
    run = RunMetadata.capture(sess, tabelle, {"sample_interval_s": 2.5})

    daten = run.as_sink_metadata()

    assert daten["sample_interval_s"] == 2.5     # wie bisher, oberste Ebene
    assert daten["units"] == run.units           # wie bisher
    assert daten["run_id"] == run.run_id         # neu
    assert daten["device"]["idn"]                # neu


def test_eine_jsonl_ist_ohne_sidecar_interpretierbar(tmp_path):
    """Die eigentliche Zusage von M4-3 fuer dieses Format."""
    wt = fassade()
    ziel = tmp_path / "messung.jsonl"
    wt.measure.record(
        JsonlSink(ziel), wt.items.read(), interval_s=0.0, max_samples=2,
        record_condition=False, check_update_rate=False,
        # Kein Sidecar - die JSONL soll sich selbst erklaeren.
        sidecar=False, include_device=True,
    )
    wt.close()

    kopf = json.loads(ziel.read_text(encoding="utf-8").splitlines()[0])
    meta = kopf["metadata"]
    # Alles, was zum Interpretieren noetig ist, steht in der Datei selbst.
    assert kopf["columns"] == ["U1", "I1", "P1"]
    assert meta["units"] == {"U1": "V", "I1": "A", "P1": "W"}
    assert "YOKOGAWA" in meta["device"]["idn"]
    assert meta["device"]["input_wiring"]
    assert meta["sample_interval_s"] == 0.0
    assert len(meta["run_id"]) == 16


def test_ohne_sidecar_und_ohne_metadata_path_bleibt_der_geraeteblock_weg(tmp_path):
    """Elf zusaetzliche Queries je Lauf sollen nicht ungefragt entstehen."""
    wt = fassade()
    ziel = tmp_path / "messung.jsonl"
    wt.measure.record(
        JsonlSink(ziel), wt.items.read(), interval_s=0.0, max_samples=1,
        record_condition=False, check_update_rate=False,
    )
    wt.close()

    meta = json.loads(ziel.read_text(encoding="utf-8").splitlines()[0])["metadata"]
    assert "device" not in meta
    # run_id und Einheiten kosten nichts und sind trotzdem da.
    assert meta["run_id"] and meta["units"]


# ---------------------------------------------------------------------------
# 3 - Die Bindung
# ---------------------------------------------------------------------------


def test_sidecar_path_wird_abgeleitet(tmp_path):
    assert sidecar_path(tmp_path / "messung.csv").name == "messung.csv.meta.json"
    assert sidecar_path(tmp_path / "messung.jsonl").name == "messung.jsonl.meta.json"


def test_record_legt_das_sidecar_neben_die_datei(tmp_path):
    wt = fassade()
    ziel = tmp_path / "messung.csv"
    wt.measure.record_csv(
        ziel, wt.items.read(), interval_s=0.0, max_samples=3,
        record_condition=False, check_update_rate=False, sidecar=True,
    )
    wt.close()

    neben = sidecar_path(ziel)
    assert neben.exists()
    daten = json.loads(neben.read_text(encoding="utf-8"))
    assert daten["sidecar_version"] == SIDECAR_VERSION
    assert daten["columns"] == ["U1", "I1", "P1"]
    assert daten["data_files"][0]["name"] == "messung.csv"


def test_verify_sidecar_bestaetigt_die_zugehoerigkeit(tmp_path):
    wt = fassade()
    ziel = tmp_path / "messung.csv"
    wt.measure.record_csv(
        ziel, wt.items.read(), interval_s=0.0, max_samples=3,
        record_condition=False, check_update_rate=False, sidecar=True,
    )
    wt.close()

    meta = verify_sidecar(ziel)

    # Der uebliche Aufruf ist zugleich das Einlesen.
    assert meta["units"] == {"U1": "V", "I1": "A", "P1": "W"}
    assert "YOKOGAWA" in meta["device"]["idn"]


def test_eine_veraenderte_datendatei_faellt_auf(tmp_path):
    """Der Hash ist der eigentliche Nachweis - er deckt auch Abschneiden ab."""
    wt = fassade()
    ziel = tmp_path / "messung.csv"
    wt.measure.record_csv(
        ziel, wt.items.read(), interval_s=0.0, max_samples=3,
        record_condition=False, check_update_rate=False, sidecar=True,
    )
    wt.close()

    zeilen = ziel.read_text(encoding="utf-8").splitlines()
    ziel.write_text("\n".join(zeilen[:-1]) + "\n", encoding="utf-8")

    with pytest.raises(SidecarMismatch, match="Bytes"):
        verify_sidecar(ziel)


def test_eine_gleich_lange_aenderung_faellt_ebenfalls_auf(tmp_path):
    """Der Fall, fuer den es die Pruefsumme ueberhaupt gibt.

    Eine geaenderte Ziffer laesst die Dateigroesse unveraendert - die
    Groessenpruefung allein liesse sie durch. Genau so sieht ein
    stillschweigend verfaelschter Messwert aus.
    """
    wt = fassade()
    ziel = tmp_path / "messung.csv"
    wt.measure.record_csv(
        ziel, wt.items.read(), interval_s=0.0, max_samples=3,
        record_condition=False, check_update_rate=False, sidecar=True,
    )
    wt.close()

    roh = ziel.read_bytes()
    vorher = len(roh)
    ziel.write_bytes(roh.replace(b"1.0", b"9.0", 1))
    assert ziel.stat().st_size == vorher, "Der Test muss die Laenge erhalten"

    with pytest.raises(SidecarMismatch, match="Pruefsumme"):
        verify_sidecar(ziel)


def test_ein_fremdes_sidecar_wird_erkannt(tmp_path):
    """Gleicher Name beweist nichts - zwei Laeufe heissen leicht gleich."""
    wt = fassade()
    erste = tmp_path / "a.csv"
    zweite = tmp_path / "b.csv"
    tabelle = wt.items.read()
    for ziel in (erste, zweite):
        wt.measure.record_csv(
            ziel, tabelle, interval_s=0.0, max_samples=2,
            record_condition=False, check_update_rate=False, sidecar=True,
        )
    wt.close()

    # Das Sidecar der zweiten Datei gegen die erste halten.
    with pytest.raises(SidecarMismatch, match="beschreibt"):
        verify_sidecar(erste, sidecar_path(zweite))


def test_ein_fehlendes_sidecar_wird_benannt(tmp_path):
    daten = tmp_path / "messung.csv"
    daten.write_text("x\n", encoding="utf-8")

    with pytest.raises(SidecarMismatch, match="kein Sidecar"):
        verify_sidecar(daten)


def test_eine_fremde_json_datei_ist_kein_sidecar(tmp_path):
    daten = tmp_path / "messung.csv"
    daten.write_text("x\n", encoding="utf-8")
    sidecar_path(daten).write_text('{"etwas": 1}', encoding="utf-8")

    with pytest.raises(SidecarMismatch, match="sidecar_version"):
        verify_sidecar(daten)


def test_kaputtes_json_wird_gemeldet(tmp_path):
    daten = tmp_path / "messung.csv"
    daten.write_text("x\n", encoding="utf-8")
    sidecar_path(daten).write_text("{kein json", encoding="utf-8")

    with pytest.raises(SidecarMismatch, match="JSON"):
        verify_sidecar(daten)


def test_file_digest_stimmt_mit_hashlib_ueberein(tmp_path):
    import hashlib

    datei = tmp_path / "x.bin"
    inhalt = b"beliebige Bytes" * 10000      # ueber eine Blockgroesse hinaus
    datei.write_bytes(inhalt)

    assert file_digest(datei) == hashlib.sha256(inhalt).hexdigest()


# ---------------------------------------------------------------------------
# 4 - Das Sidecar entsteht nachher und kennt das Ergebnis
# ---------------------------------------------------------------------------


def test_das_sidecar_traegt_das_ergebnis_des_laufs(tmp_path):
    """Der frueher Weg schrieb VOR dem Lauf und konnte den Ausgang nicht kennen."""
    sess, _ = sitzung(faellt_bei={2})
    tabelle = ItemTable.read_from_device(sess)
    ziel = tmp_path / "messung.csv"
    senke = CsvSink(ziel)
    run = RunMetadata.capture(sess, tabelle)

    stats = messreihe(sess, senke, error_policy=ErrorPolicy())
    run.write_sidecar(sidecar_path(ziel), [ziel], stats)

    ergebnis = json.loads(sidecar_path(ziel).read_text(encoding="utf-8"))["result"]
    assert ergebnis["samples"] == 4
    assert ergebnis["missing"] == 1
    assert ergebnis["measured_samples"] == 3


def test_das_sidecar_deckt_alle_rotationsabschnitte_ab(tmp_path):
    """Ein Lauf, viele Dateien, eine Beschreibung."""
    wt = fassade()
    basis = tmp_path / "lang.csv"
    wt.measure.record_csv(
        basis, wt.items.read(), interval_s=0.0, max_samples=5,
        record_condition=False, check_update_rate=False,
        rotation=RotationPolicy(max_rows=2), sidecar=True,
    )
    wt.close()

    # Abgelegt neben dem ERSTEN Abschnitt, nicht neben dem leeren Basisnamen.
    neben = sidecar_path(tmp_path / "lang_0001.csv")
    assert neben.exists()
    daten = json.loads(neben.read_text(encoding="utf-8"))
    namen = [e["name"] for e in daten["data_files"]]
    assert namen == ["lang_0001.csv", "lang_0002.csv", "lang_0003.csv"]

    # Und jeder Abschnitt laesst sich gegen dasselbe Sidecar nachweisen.
    for name in namen:
        verify_sidecar(tmp_path / name, neben)


def test_eine_fehlende_datendatei_wird_vermerkt(tmp_path):
    """Dass sie fehlt, ist selbst eine Aussage."""
    sess, _ = sitzung()
    run = RunMetadata.capture(sess, ItemTable.read_from_device(sess))
    ziel = tmp_path / "sidecar.json"

    run.write_sidecar(ziel, [tmp_path / "gibtsnicht.csv"])

    eintrag = json.loads(ziel.read_text(encoding="utf-8"))["data_files"][0]
    assert eintrag["missing"] is True


def test_das_sidecar_deckt_alle_senken_eines_buendels_ab(tmp_path):
    wt = fassade()
    csv_ziel = tmp_path / "messung.csv"
    jsonl_ziel = tmp_path / "messung.jsonl"
    senke = MultiSink(CsvSink(csv_ziel), JsonlSink(jsonl_ziel), CallbackSink(lambda s: None))

    wt.measure.record(
        senke, wt.items.read(), interval_s=0.0, max_samples=2,
        record_condition=False, check_update_rate=False, sidecar=True,
    )
    wt.close()

    neben = sidecar_path(csv_ziel)
    namen = [e["name"] for e in json.loads(neben.read_text(encoding="utf-8"))["data_files"]]
    assert namen == ["messung.csv", "messung.jsonl"]
    verify_sidecar(jsonl_ziel, neben)


def test_output_paths_sammelt_aus_buendel_und_rotation(tmp_path):
    einzeln = CsvSink(tmp_path / "a.csv")
    rotierend = RotatingSink(CsvSink, tmp_path / "b.csv", RotationPolicy(max_rows=5))
    buendel = MultiSink(einzeln, rotierend, CallbackSink(lambda s: None))

    assert output_paths_of(einzeln) == [tmp_path / "a.csv"]
    assert output_paths_of(CallbackSink(lambda s: None)) == []
    # Vor open() hat die Rotation noch keinen Abschnitt.
    assert output_paths_of(buendel) == [tmp_path / "a.csv"]

    rotierend.open(["U1"], {})
    rotierend.close()
    assert output_paths_of(buendel) == [tmp_path / "a.csv", tmp_path / "b_0001.csv"]


def test_eine_senke_ohne_dateien_erzeugt_kein_sidecar(tmp_path, caplog):
    import logging

    wt = fassade()
    with caplog.at_level(logging.WARNING, logger="wt3000.device"):
        wt.measure.record(
            CallbackSink(lambda s: None), wt.items.read(), interval_s=0.0,
            max_samples=1, record_condition=False, check_update_rate=False,
            sidecar=True,
        )
    wt.close()

    assert not list(tmp_path.glob("*.meta.json"))
    assert any("keine Datei" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 5 - Auch im Hintergrundlauf
# ---------------------------------------------------------------------------


def test_die_steuerbare_messung_legt_das_sidecar_ab(tmp_path):
    """Im Mess-Thread und nach dem Schliessen der Senke - sonst stimmten die
    Pruefsummen nicht."""
    wt = fassade()
    ziel = tmp_path / "hintergrund.csv"

    m = wt.measure.start(
        CsvSink(ziel), wt.items.read(), interval_s=0.0, max_samples=3,
        record_condition=False, check_update_rate=False, sidecar=True,
    )
    stats = m.wait(timeout=5.0)
    wt.close()

    assert stats.samples == 3
    meta = verify_sidecar(ziel)
    assert meta["result"]["samples"] == 3


def test_ein_sidecar_ohne_geraetekontext_ist_trotzdem_gebunden(tmp_path):
    """'metadata_path' ohne 'sidecar': der Ablageort ist gewaehlt, die
    Bindung gilt genauso."""
    wt = fassade()
    ziel = tmp_path / "messung.csv"
    eigenes = tmp_path / "woanders.json"

    wt.measure.record_csv(
        ziel, wt.items.read(), interval_s=0.0, max_samples=2,
        record_condition=False, check_update_rate=False, metadata_path=eigenes,
    )
    wt.close()

    assert eigenes.exists()
    verify_sidecar(ziel, eigenes)
