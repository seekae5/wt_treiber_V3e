# =============================================================================
# Datei: tests/test_stage_startup.py
# Der Anfang der Aufrufkette ist abgesichert.
#
# 'WTConfig.from_environment()' muss bei allen ausfuehrbaren Skripten nach
# 'setup_logging()' innerhalb des try liegen. Nicht lesbare Dateien, falsche
# JSON-Struktur und ungueltige Feldwerte erscheinen so im Protokoll.
#
# Zwei Folgen, die dieser Test festhaelt:
#
#   1. Eine kaputte 'wt3000.json' - der haeufigste Konfigurationsfehler
#      ueberhaupt - endete als Traceback. Nicht als die Zeile "Abbruch: ...",
#      die jedes Skript fuer jeden anderen WTError ausgibt; der Rueckgabewert 1
#      kam aus dem Traceback statt aus dem Skript, und in der Protokolldatei
#      stand nichts, weil es sie noch gar nicht gab.
#
#   2. Die Warnung ueber unbekannte Schluessel in der Konfigurationsdatei
#      (wt3000_transport.py) fiel aus demselben Grund neben das Protokoll: sie
#      entstand, bevor setup_logging() einen Handler gesetzt hatte, und ging
#      ueber 'logging.lastResort' auf stderr - ohne Zeitstempel, ohne
#      Loggernamen, und NICHT in die archivierte Datei. Ein Tippfehler in
#      'wt3000.json' war im archivierten Lauf damit unsichtbar.
#
# WARUM DIESE TESTS DIE PROTOKOLLDATEI LESEN und nicht 'caplog': caplog
# schneidet unabhaengig von setup_logging() mit. Ein Pruefsatz auf caplog waere
# also auch dann gruen, wenn die Meldung neben die Datei faellt - er wuerde
# genau das nicht pruefen, wofuer man ihn baut. Die Fixture wird deshalb mit
# 'logging_stilllegen=False' angefordert und der Nachweis aus der Datei gezogen.
# =============================================================================

from __future__ import annotations

import json

import pytest
from conftest import geraeteskript

from wt3000_scpi import stage2_read_numeric as stage2
from wt3000_scpi import stage3_own_itemtable as stage3
from wt3000_scpi import stage4_measure as stage4
from wt3000_scpi import stage5_input_config as stage5
from wt3000_scpi import stage5b_range_probe as stage5b
from wt3000_scpi.wt3000_transport import WTConfig, config_file_in_use


def lauf_bis_zum_kopf(modul) -> None:
    """main() aufrufen und jeden Ausgang hinnehmen.

    Gebraucht fuer die Pruefsaetze, die nur den PROTOKOLLKOPF betreffen. Der
    steht, bevor die erste Abfrage hinausgeht; was danach passiert - hier ein
    KeyError aus FakeTransport, weil die Antworttabelle absichtlich leer ist -
    gehoert nicht zu ihrer Aussage. Eine vollstaendige Antworttabelle je Skript
    waere der falsche Preis dafuer, und sie wuerde diese Datei an Aenderungen
    binden, die mit A-08 nichts zu tun haben.
    """
    try:
        modul.main()
    except BaseException:  # noqa: BLE001 - der Ausgang ist hier gleichgueltig
        pass


def protokolltext(verzeichnis) -> str:
    """Inhalt der einzigen Protokolldatei im Ausgabeverzeichnis."""
    dateien = [p for p in verzeichnis.glob("*.txt") if p.stat().st_size or True]
    assert dateien, f"Keine Protokolldatei in {verzeichnis} angelegt"
    return "\n".join(p.read_text(encoding="utf-8") for p in dateien)


# Alle sieben ausfuehrbaren Skripte. Die beiden Geraeteskripte werden ueber den
# Dateipfad geladen (siehe conftest.geraeteskript), die fuenf Stufen sind
# Paketmodule - fuer diesen Test verhalten sie sich gleich, weil er nur den
# Kopf von main() betrifft und nie bis zur Verbindung kommt.
ALLE_SKRIPTE = [
    pytest.param(lambda: stage2, id="stage2"),
    pytest.param(lambda: stage3, id="stage3"),
    pytest.param(lambda: stage4, id="stage4"),
    pytest.param(lambda: stage5, id="stage5"),
    pytest.param(lambda: stage5b, id="stage5b"),
    pytest.param(lambda: geraeteskript("probe_voltage_range"), id="probe_voltage"),
    pytest.param(lambda: geraeteskript("probe_current_range"), id="probe_current"),
]


# ---------------------------------------------------------------------------
# 1 - Die kaputte Konfigurationsdatei
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("laden", ALLE_SKRIPTE)
def test_kaputte_konfigurationsdatei_endet_als_abbruch(stufenlauf, monkeypatch, tmp_path, laden):
    """Reproduktion 7.5 der Analyse, als Pruefsatz.

    Kein Traceback, sondern der Rueckgabewert 1 und die Zeile 'Abbruch: ...' -
    also dieselbe Behandlung, die jeder andere WTError schon bekam.
    """
    modul = laden()
    stufenlauf(modul, {}, logging_stilllegen=False)

    arbeitsverzeichnis = tmp_path / "lauf"
    arbeitsverzeichnis.mkdir()
    (arbeitsverzeichnis / "wt3000.json").write_text('{"ip: "1.2.3.4"}', encoding="utf-8")
    monkeypatch.chdir(arbeitsverzeichnis)
    # Ohne das gewinnt die Umgebungsvariable und die Datei wird nie gelesen.
    monkeypatch.delenv("WT3000_IP", raising=False)

    assert modul.main() == 1

    text = protokolltext(tmp_path)
    assert "Abbruch" in text, f"Protokoll nennt den Abbruch nicht:\n{text}"
    assert "wt3000.json" in text, "Die Meldung nennt die beanstandete Datei nicht"


@pytest.mark.parametrize("laden", ALLE_SKRIPTE)
def test_konfigurationsfehler_steht_in_der_protokolldatei(
    stufenlauf, monkeypatch, tmp_path, laden
):
    """Der Lauf ist archivierbar - vorher gab es die Datei noch gar nicht.

    Das ist der eigentliche Gewinn von A-08: 'setup_logging()' laeuft ZUERST,
    also existiert die Protokolldatei auch dann, wenn die Aufloesungskette
    scheitert.
    """
    modul = laden()
    stufenlauf(modul, {}, logging_stilllegen=False)

    arbeitsverzeichnis = tmp_path / "lauf"
    arbeitsverzeichnis.mkdir()
    (arbeitsverzeichnis / "wt3000.json").write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.chdir(arbeitsverzeichnis)
    monkeypatch.delenv("WT3000_IP", raising=False)

    lauf_bis_zum_kopf(modul)

    assert list(tmp_path.glob("*.txt")), "Keine Protokolldatei angelegt"
    assert "kein JSON-Objekt" in protokolltext(tmp_path)


# ---------------------------------------------------------------------------
# 2 - Die Warnung ueber unbekannte Schluessel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("laden", ALLE_SKRIPTE)
def test_tippfehler_in_der_konfiguration_landet_im_protokoll(
    stufenlauf, monkeypatch, tmp_path, laden
):
    """Reproduktion 7.6 der Analyse, als Pruefsatz.

    Die Warnung entsteht in der Aufloesungskette selbst. Lag diese vor
    setup_logging(), ging sie ueber 'logging.lastResort' auf stderr und stand
    NICHT in der archivierten Datei - ein Tippfehler in 'wt3000.json' war im
    Nachhinein unsichtbar.
    """
    modul = laden()
    stufenlauf(modul, {}, logging_stilllegen=False)

    arbeitsverzeichnis = tmp_path / "lauf"
    arbeitsverzeichnis.mkdir()
    (arbeitsverzeichnis / "wt3000.json").write_text(
        json.dumps({"ip": "10.0.0.5", "tippfehler_feld": 1}), encoding="utf-8"
    )
    monkeypatch.chdir(arbeitsverzeichnis)
    monkeypatch.delenv("WT3000_IP", raising=False)

    lauf_bis_zum_kopf(modul)

    text = protokolltext(tmp_path)
    assert "tippfehler_feld" in text, (
        "Die Warnung ueber unbekannte Schluessel steht nicht in der "
        f"Protokolldatei - sie entsteht vor setup_logging():\n{text}"
    )


# ---------------------------------------------------------------------------
# 3 - Der Protokollkopf beantwortet, wogegen gemessen wurde
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("laden", ALLE_SKRIPTE)
def test_protokollkopf_nennt_verbindung_und_herkunft(stufenlauf, monkeypatch, tmp_path, laden):
    """A-09, erste Haelfte: 'describe()' wird von keinem Skript aufgerufen.

    Aus dem Aufruf 'python -m wt3000_scpi.stage4_measure' laesst sich nicht
    ablesen, gegen welches Geraet gemessen wurde. Die Zeile kostet nichts und
    macht den archivierten Lauf nachvollziehbar. Die zweite Haelfte - die
    Parameter am Aufruf statt aus verstecktem Prozesszustand - kommt in
    Schritt 8.
    """
    modul = laden()
    stufenlauf(modul, {}, ip="192.0.2.77", logging_stilllegen=False)
    monkeypatch.chdir(tmp_path)

    lauf_bis_zum_kopf(modul)

    text = protokolltext(tmp_path)
    assert "192.0.2.77" in text, "Der Protokollkopf nennt die Gegenstelle nicht"
    assert "Konfigurationsdatei" in text, "Der Protokollkopf nennt die Herkunft nicht"


# ---------------------------------------------------------------------------
# 4 - describe() traegt die Parameter, die den Lauf bestimmen
# ---------------------------------------------------------------------------


def test_describe_nennt_use_remote():
    """'use_remote' ist der stille Schalter aus A-09.

    Er entscheidet, ob das Bedienfeld waehrend des Laufs gesperrt ist, kommt
    aus der Umgebung oder aus 'wt3000.json' - und tauchte in describe() nicht
    auf. Damit half auch die neue Protokollzeile aus Schritt 3 nur halb.
    """
    an = WTConfig(ip="10.0.0.5", use_remote=True).describe()
    aus = WTConfig(ip="10.0.0.5", use_remote=False).describe()
    assert an != aus, f"describe() unterscheidet use_remote nicht: {an!r}"


def test_describe_nennt_timeout():
    """Der zweite Wert, der den Lauf bestimmt und im Protokoll fehlte."""
    assert "7500" in WTConfig(ip="10.0.0.5", timeout_ms=7500).describe()


def test_describe_zeigt_weiterhin_kein_passwort():
    """Die Erweiterung darf die Zusage aus test_config_resolution nicht brechen.

    Ein Protokoll wird archiviert und weitergereicht - es ist der falsche Ort
    fuer Zugangsdaten.
    """
    text = WTConfig(ip="10.0.0.5", user="TEST", password="geheim").describe()
    assert "geheim" not in text


# ---------------------------------------------------------------------------
# 5 - Die Herkunft ist ohne privaten Zugriff feststellbar
# ---------------------------------------------------------------------------


def test_config_file_in_use_findet_die_datei(tmp_path, monkeypatch):
    """Ohne diese Funktion muesste ein Skript '_config_file_path' benutzen.

    Also einen privaten Namen aus Layer 0 - genau die Sorte Zugriff, die die
    Schichtung verhindern soll.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WT3000_CONFIG", raising=False)
    assert config_file_in_use() is None

    datei = tmp_path / "wt3000.json"
    datei.write_text(json.dumps({"ip": "10.0.0.5"}), encoding="utf-8")
    assert config_file_in_use() == datei


def test_config_file_in_use_liest_die_datei_nicht(tmp_path, monkeypatch):
    """Sie stellt nur fest, WELCHE Datei gelesen wuerde.

    Waere es anders, meldete sie einen Syntaxfehler ein zweites Mal - einmal
    hier und einmal in from_environment() - und die Protokollzeile, die die
    Herkunft nennen soll, braeche selbst ab.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WT3000_CONFIG", raising=False)
    datei = tmp_path / "wt3000.json"
    datei.write_text("{kaputt", encoding="utf-8")

    assert config_file_in_use() == datei
