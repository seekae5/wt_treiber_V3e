# =============================================================================
# Datei: tests/test_config_resolution.py
# Aufloesungskette der Verbindungsparameter. Entscheidend ist die Rangfolge:
#   Parameter > Umgebungsvariable > Konfigurationsdatei > Voreinstellung
# =============================================================================

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wt3000_scpi.wt3000_core import WTConfig, WTError
from wt3000_scpi.wt3000_transport import (
    CONFIG_FILE_NAME,
    config_search_paths,
    resolve_dll_path,
)

ALLE_VARIABLEN = (
    "WT3000_IP",
    "WT3000_DLL_PATH",
    "WT3000_USER",
    "WT3000_PASSWORD",
    "WT3000_TIMEOUT_MS",
    "WT3000_USE_REMOTE",
    "WT3000_CONFIG",
)


@pytest.fixture(autouse=True)
def saubere_umgebung(monkeypatch, tmp_path):
    """Keine WT3000_*-Variable und keine Datei aus der echten Umgebung.

    Ohne das haengt das Ergebnis davon ab, was auf dem Rechner des Pruefenden
    gesetzt ist - genau die Abhaengigkeit, die P-7 beseitigen soll.
    """
    for name in ALLE_VARIABLEN:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # './wt3000.json' zeigt ins Leere
    monkeypatch.setenv("HOME", str(tmp_path))  # '~/wt3000.json' ebenso


def datei_anlegen(tmp_path, **werte) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(json.dumps(werte), encoding="utf-8")


# ---------------------------------------------------------------------------
# Die Voreinstellung ist neutral
# ---------------------------------------------------------------------------


def test_voreinstellung_traegt_keine_zugangsdaten():
    """Der Kern von BF-M2: nichts Rechnerspezifisches mehr im Quelltext."""
    config = WTConfig()
    assert config.ip == ""
    assert config.user == ""
    assert config.password == ""
    assert config.dll_path == "tmctl64.dll"  # blosser Name, kein Pfad


def test_ohne_jede_quelle_bleibt_es_bei_der_voreinstellung():
    assert WTConfig.from_environment() == WTConfig()


# ---------------------------------------------------------------------------
# Die einzelnen Stufen
# ---------------------------------------------------------------------------


def test_umgebungsvariable_wird_uebernommen(monkeypatch):
    monkeypatch.setenv("WT3000_IP", "10.0.0.5")
    assert WTConfig.from_environment().ip == "10.0.0.5"


def test_konfigurationsdatei_wird_uebernommen(tmp_path):
    datei_anlegen(tmp_path, ip="192.168.1.7", user="LABOR")
    config = WTConfig.from_environment()
    assert config.ip == "192.168.1.7"
    assert config.user == "LABOR"


def test_parameter_schlaegt_umgebung(monkeypatch):
    monkeypatch.setenv("WT3000_IP", "10.0.0.5")
    assert WTConfig.from_environment(ip="10.0.0.9").ip == "10.0.0.9"


def test_umgebung_schlaegt_datei(monkeypatch, tmp_path):
    datei_anlegen(tmp_path, ip="192.168.1.7")
    monkeypatch.setenv("WT3000_IP", "10.0.0.5")
    assert WTConfig.from_environment().ip == "10.0.0.5"


def test_datei_schlaegt_voreinstellung(tmp_path):
    datei_anlegen(tmp_path, timeout_ms=9000)
    assert WTConfig.from_environment().timeout_ms == 9000


def test_die_stufen_mischen_sich_feldweise(monkeypatch, tmp_path):
    """Jedes Feld wird einzeln aufgeloest, nicht die Konfiguration als Ganzes."""
    datei_anlegen(tmp_path, ip="192.168.1.7", user="LABOR", timeout_ms=9000)
    monkeypatch.setenv("WT3000_USER", "AUS_UMGEBUNG")

    config = WTConfig.from_environment(timeout_ms=1234)
    assert config.ip == "192.168.1.7"          # aus der Datei
    assert config.user == "AUS_UMGEBUNG"       # aus der Umgebung
    assert config.timeout_ms == 1234           # als Parameter
    assert config.drain_timeout_ms == 500      # Voreinstellung


def test_none_als_parameter_zaehlt_nicht_als_angabe(monkeypatch):
    """Damit connect(ip=None) die Umgebung nicht ueberschreibt."""
    monkeypatch.setenv("WT3000_IP", "10.0.0.5")
    assert WTConfig.from_environment(ip=None).ip == "10.0.0.5"


def test_leere_umgebungsvariable_zaehlt_nicht_als_angabe(monkeypatch, tmp_path):
    """WT3000_IP= (leer) soll die Datei nicht verdraengen."""
    datei_anlegen(tmp_path, ip="192.168.1.7")
    monkeypatch.setenv("WT3000_IP", "")
    assert WTConfig.from_environment().ip == "192.168.1.7"


# ---------------------------------------------------------------------------
# Typen und Fehlerfaelle
# ---------------------------------------------------------------------------


def test_zahlen_und_wahrheitswerte_werden_gewandelt(monkeypatch):
    monkeypatch.setenv("WT3000_TIMEOUT_MS", "1500")
    monkeypatch.setenv("WT3000_USE_REMOTE", "off")
    config = WTConfig.from_environment()
    assert config.timeout_ms == 1500 and isinstance(config.timeout_ms, int)
    assert config.use_remote is False


@pytest.mark.parametrize("text,erwartet", [("1", True), ("true", True), ("ja", True),
                                           ("0", False), ("nein", False), ("", None)])
def test_wahrheitswerte_aus_der_umgebung(monkeypatch, text, erwartet):
    monkeypatch.setenv("WT3000_USE_REMOTE", text)
    config = WTConfig.from_environment()
    # Leerer Text zaehlt nicht als Angabe - dann gilt die Voreinstellung True.
    assert config.use_remote is (WTConfig().use_remote if erwartet is None else erwartet)


def test_unbrauchbare_zahl_bricht_verstaendlich_ab(monkeypatch):
    monkeypatch.setenv("WT3000_TIMEOUT_MS", "bald")
    with pytest.raises(WTError, match="timeout_ms"):
        WTConfig.from_environment()


def test_ausdruecklich_benannte_datei_muss_existieren(tmp_path):
    """Ein Tippfehler im Pfad darf nicht still zur Voreinstellung fuehren."""
    with pytest.raises(WTError, match="nicht gefunden"):
        WTConfig.from_environment(config_file=tmp_path / "gibtsnicht.json")


def test_kaputte_datei_bricht_verstaendlich_ab(tmp_path):
    (tmp_path / CONFIG_FILE_NAME).write_text("{kein json", encoding="utf-8")
    with pytest.raises(WTError, match="nicht lesbar"):
        WTConfig.from_environment()


def test_kommentarschluessel_werden_stillschweigend_uebergangen(tmp_path, caplog):
    """JSON kennt keine Kommentare - '_'-Schluessel sind der Ersatz.

    Damit laesst sich 'wt3000.example.json' mit Erklaertext ausliefern, ohne
    dass eine Kopie davon bei jedem Start eine Warnung ausloest.
    """
    datei_anlegen(tmp_path, _hinweis="Vorlage, bitte anpassen", ip="10.0.0.5")
    with caplog.at_level("WARNING"):
        config = WTConfig.from_environment()
    assert config.ip == "10.0.0.5"
    assert not [r for r in caplog.records if "uebergangen" in r.getMessage()]


def test_unbekannte_schluessel_werden_uebergangen_und_gemeldet(tmp_path, caplog):
    datei_anlegen(tmp_path, ip="10.0.0.5", tippfehler="egal")
    with caplog.at_level("WARNING"):
        config = WTConfig.from_environment()
    assert config.ip == "10.0.0.5"
    assert any("tippfehler" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# DLL-Aufloesung
# ---------------------------------------------------------------------------


def test_blosser_dateiname_wird_durchgereicht():
    """Windows sucht dann selbst in PATH - der Weg fuer eine installierte TMCTL."""
    assert resolve_dll_path("tmctl64.dll") == "tmctl64.dll"


def test_vorhandener_pfad_wird_angenommen(tmp_path):
    dll = tmp_path / "tmctl64.dll"
    dll.write_bytes(b"")
    assert resolve_dll_path(str(dll)) == dll


def test_fehlender_pfad_nennt_die_wege_zur_abhilfe(tmp_path):
    with pytest.raises(WTError) as fehler:
        resolve_dll_path(str(tmp_path / "weg" / "tmctl64.dll"))
    meldung = str(fehler.value)
    assert "WT3000_DLL_PATH" in meldung and CONFIG_FILE_NAME in meldung


# ---------------------------------------------------------------------------
# Hilfsmittel
# ---------------------------------------------------------------------------


def test_with_values_ueberschreibt_nur_das_angegebene():
    config = WTConfig.from_environment(ip="10.0.0.5")
    assert config.with_values(ip=None, user="X") == WTConfig(ip="10.0.0.5", user="X")


def test_describe_zeigt_kein_passwort():
    config = WTConfig(ip="10.0.0.5", user="TEST", password="geheim")
    assert "geheim" not in config.describe()
    assert "10.0.0.5" in config.describe()


# ---------------------------------------------------------------------------
# UEBERARBEITET: Suche nach oben statt nur im Arbeitsverzeichnis
#
# Der Fall aus der Praxis: 'wt3000.json' liegt in der Projektwurzel, das
# Skript liegt unter tools/hardware/ und wird von der Entwicklungsumgebung in
# SEINEM Verzeichnis gestartet. Vorher war das Ergebnis "Keine IP-Adresse
# gesetzt", obwohl die Datei zwei Ebenen darueber lag und derselbe Aufruf aus
# der Projektwurzel anstandslos lief.
# ---------------------------------------------------------------------------


def test_datei_im_elternverzeichnis_wird_gefunden(monkeypatch, tmp_path):
    """Der behobene Fall: Start aus einem Unterverzeichnis."""
    datei_anlegen(tmp_path, ip="192.168.10.20")
    unterverzeichnis = tmp_path / "tools" / "hardware"
    unterverzeichnis.mkdir(parents=True)
    monkeypatch.chdir(unterverzeichnis)

    assert WTConfig.from_environment().ip == "192.168.10.20"


def test_naechstgelegene_datei_gewinnt(monkeypatch, tmp_path):
    """Ein Unterverzeichnis darf eine eigene Konfiguration mitbringen."""
    datei_anlegen(tmp_path, ip="10.0.0.1")
    unten = tmp_path / "labor"
    unten.mkdir()
    (unten / CONFIG_FILE_NAME).write_text(json.dumps({"ip": "10.0.0.2"}), encoding="utf-8")
    monkeypatch.chdir(unten)

    assert WTConfig.from_environment().ip == "10.0.0.2"


def test_umgebungsvariable_schlaegt_auch_eine_datei_weiter_oben(monkeypatch, tmp_path):
    """Die Rangfolge bleibt unveraendert - die Suche nach oben aendert nur, WO gesucht wird."""
    datei_anlegen(tmp_path, ip="192.168.10.20")
    unten = tmp_path / "tief" / "tiefer"
    unten.mkdir(parents=True)
    monkeypatch.chdir(unten)
    monkeypatch.setenv("WT3000_IP", "10.0.0.5")

    assert WTConfig.from_environment().ip == "10.0.0.5"


def test_suchpfade_beginnen_beim_arbeitsverzeichnis_und_gehen_nach_oben(monkeypatch, tmp_path):
    unten = tmp_path / "a" / "b"
    unten.mkdir(parents=True)
    monkeypatch.chdir(unten)

    pfade = config_search_paths()
    assert pfade[0] == unten / CONFIG_FILE_NAME
    assert (tmp_path / "a" / CONFIG_FILE_NAME) in pfade
    assert (tmp_path / CONFIG_FILE_NAME) in pfade
    # Bis zur Wurzel des Dateisystems, nicht nur eine Ebene.
    assert pfade[-1].parent in (Path.home(), *unten.parents)


def test_suchpfade_enthalten_keine_dubletten(monkeypatch, tmp_path):
    """Liegt das Arbeitsverzeichnis unter dem Home-Verzeichnis, stand es sonst zweimal drin."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    unten = tmp_path / "projekt"
    unten.mkdir()
    monkeypatch.chdir(unten)

    pfade = config_search_paths()
    assert len(pfade) == len(set(pfade))


def test_ausdruecklich_benannte_datei_hat_weiter_vorrang(monkeypatch, tmp_path):
    """Der explizite Pfad steht vor jeder gefundenen Datei - unveraendert."""
    datei_anlegen(tmp_path, ip="192.168.10.20")
    eigene = tmp_path / "andere.json"
    eigene.write_text(json.dumps({"ip": "10.0.0.9"}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert WTConfig.from_environment(config_file=eigene).ip == "10.0.0.9"


def test_relativer_dll_pfad_aus_der_datei_gilt_relativ_zur_datei(monkeypatch, tmp_path):
    """Sonst bricht der Lauf aus einem Unterverzeichnis an der DLL ab.

    'wt3000.json' in der Projektwurzel traegt "tools/tmctl64.dll". Vorher
    loeste das gegen das Arbeitsverzeichnis auf - aus tools/hardware/ heraus
    also gegen 'tools/hardware/tools/tmctl64.dll'.
    """
    (tmp_path / "tools").mkdir()
    dll = tmp_path / "tools" / "tmctl64.dll"
    dll.write_bytes(b"\x00")
    datei_anlegen(tmp_path, ip="10.0.0.5", dll_path="tools/tmctl64.dll")

    unten = tmp_path / "tools" / "hardware"
    unten.mkdir()
    monkeypatch.chdir(unten)

    assert Path(WTConfig.from_environment().dll_path) == dll.resolve()


def test_blosser_dateiname_bleibt_unangetastet(monkeypatch, tmp_path):
    """Ihn soll Windows in PATH suchen - ein Verzeichnis davor verhinderte das."""
    datei_anlegen(tmp_path, ip="10.0.0.5", dll_path="tmctl64.dll")
    monkeypatch.chdir(tmp_path)
    assert WTConfig.from_environment().dll_path == "tmctl64.dll"


def test_absoluter_dll_pfad_bleibt_unangetastet(monkeypatch, tmp_path):
    absolut = tmp_path / "woanders" / "tmctl64.dll"
    datei_anlegen(tmp_path, ip="10.0.0.5", dll_path=str(absolut))
    monkeypatch.chdir(tmp_path)
    assert WTConfig.from_environment().dll_path == str(absolut)


def test_dll_pfad_aus_der_umgebung_bleibt_arbeitsverzeichnisrelativ(monkeypatch, tmp_path):
    """Umgebungsvariablen kommen vom Aufrufer - sie behalten dessen Bezugspunkt."""
    datei_anlegen(tmp_path, ip="10.0.0.5", dll_path="tools/tmctl64.dll")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WT3000_DLL_PATH", "eigene/tmctl64.dll")
    assert WTConfig.from_environment().dll_path == "eigene/tmctl64.dll"
