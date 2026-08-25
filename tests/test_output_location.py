# =============================================================================
# Datei: tests/test_output_location.py
# Tests fuer die Ablage von Protokollen, Sicherungen und Messdateien. Ein- und
# Ausgabepfade duerfen nicht vom zufaelligen Arbeitsverzeichnis abhaengen.
# =============================================================================

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wt3000_scpi.wt3000_common import find_project_root, output_dir


@pytest.fixture
def projekt(tmp_path, monkeypatch) -> Path:
    """Ein Projekt mit Marker, dazu ein tiefes Unterverzeichnis als Startort."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    tief = tmp_path / "tools" / "hardware"
    tief.mkdir(parents=True)
    monkeypatch.chdir(tief)
    return tmp_path


# ---------------------------------------------------------------------------
# Die Wurzel finden
# ---------------------------------------------------------------------------


def test_wurzel_wird_aus_einem_unterverzeichnis_gefunden(projekt):
    """Der behobene Fall: Start in tools/hardware/, Marker zwei Ebenen darueber."""
    assert find_project_root() == projekt


def test_wurzel_ist_das_verzeichnis_mit_dem_marker_selbst(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert find_project_root() == tmp_path


@pytest.mark.parametrize("marker", ["pyproject.toml", ".git", "wt3000.json"])
def test_jeder_marker_taugt_als_anker(tmp_path, monkeypatch, marker):
    """'.git' fuer einen Klon ohne Installation, 'wt3000.json' als letzter Halt."""
    (tmp_path / marker).write_text("", encoding="utf-8")
    unten = tmp_path / "tief"
    unten.mkdir()
    monkeypatch.chdir(unten)
    assert find_project_root() == tmp_path


def test_git_als_verzeichnis_zaehlt_auch(tmp_path, monkeypatch):
    """In einem echten Klon ist '.git' ein Verzeichnis, keine Datei."""
    (tmp_path / ".git").mkdir()
    unten = tmp_path / "tief"
    unten.mkdir()
    monkeypatch.chdir(unten)
    assert find_project_root() == tmp_path


def test_naechstgelegene_wurzel_gewinnt(tmp_path, monkeypatch):
    """Ein eingebettetes Projekt gehoert nicht dem aeusseren."""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    inneres = tmp_path / "inneres"
    inneres.mkdir()
    (inneres / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(inneres)
    assert find_project_root() == inneres


def test_ohne_marker_gibt_es_keine_wurzel(tmp_path, monkeypatch):
    """Der Normalfall fuer ein installiertes Paket ausserhalb des Quellbaums."""
    unten = tmp_path / "irgendwo"
    unten.mkdir()
    monkeypatch.chdir(unten)
    # tmp_path traegt keinen Marker; oberhalb koennte auf einem Entwickler-
    # rechner einer liegen, deshalb wird hier ausdruecklich ab 'unten' gesucht
    # und nur geprueft, dass die Suche nicht bei 'unten' selbst haengenbleibt.
    assert find_project_root(unten) != unten


# ---------------------------------------------------------------------------
# Der Ablageort
# ---------------------------------------------------------------------------


def test_ablage_haengt_nicht_mehr_am_startverzeichnis(projekt):
    """Der Kern der Sache: derselbe Ort, egal von wo gestartet wird."""
    assert output_dir("konfiguration") == projekt / "konfiguration"
    assert output_dir("messungen") == projekt / "messungen"


def test_ohne_namen_kommt_die_wurzel_selbst(projekt):
    """stage2/stage3 legen flach ab - der Ablageort bleibt, nur stabil."""
    assert output_dir() == projekt


def test_ohne_projekt_bleibt_es_beim_arbeitsverzeichnis(tmp_path):
    """Rueckfall: wer ausserhalb eines Quellbaums misst, will es dort haben."""
    ohne_marker = tmp_path / "fremd"
    ohne_marker.mkdir()
    assert output_dir("messungen", start=ohne_marker).name == "messungen"


def test_verzeichnis_wird_nicht_angelegt(projekt):
    """Anlegen bleibt beim Aufrufer - sonst entstuenden Verzeichnisse beim Import."""
    ziel = output_dir("wird_nicht_erzeugt")
    assert not ziel.exists()


# ---------------------------------------------------------------------------
# Die Skripte benutzen es auch wirklich
# ---------------------------------------------------------------------------


def _ruft_path_cwd(quelle: str) -> bool:
    """Sucht einen echten Aufruf 'Path.cwd()' im Syntaxbaum.

    Ueber den Baum und nicht ueber die Zeichenkette: eine Textsuche findet
    auch jeden Kommentar und jeden Docstring, in dem der Name bloss erwaehnt
    wird - und die Erklaerung, warum eine Stelle NICHT mehr an 'Path.cwd()'
    haengt, enthaelt den Namen nun einmal. Denselben Weg geht
    tests/test_package_layout.py bei der Importrichtung.
    """
    for knoten in ast.walk(ast.parse(quelle)):
        if (
            isinstance(knoten, ast.Call)
            and isinstance(knoten.func, ast.Attribute)
            and knoten.func.attr == "cwd"
            and isinstance(knoten.func.value, ast.Name)
            and knoten.func.value.id == "Path"
        ):
            return True
    return False


def test_kein_skript_haengt_mehr_an_path_cwd():
    """Gegenprobe an der Quelle - sonst faellt ein Rueckfall erst am Geraet auf.

    'Path.cwd()' ist in einem Skript, das Dateien ablegt, praktisch immer ein
    Fehler: es macht den Ablageort davon abhaengig, wie der Anwender das
    Programm gestartet hat. Erlaubt bleibt es genau dort, wo das
    Arbeitsverzeichnis auch gemeint ist - in
    'wt3000_transport.config_search_paths()', das von dort aus nach oben
    sucht, und in 'wt3000_common.find_project_root()' selbst.
    """
    import wt3000_scpi

    paket = Path(wt3000_scpi.__file__).parent
    ausnahmen = {"wt3000_transport.py", "wt3000_common.py"}
    treffer = [
        pfad.name
        for pfad in sorted(paket.glob("*.py"))
        if pfad.name not in ausnahmen and _ruft_path_cwd(pfad.read_text(encoding="utf-8"))
    ]
    assert treffer == [], f"benutzen noch Path.cwd(): {treffer}"


def test_die_geraeteskripte_unter_tools_ebenso():
    """tools/hardware/ liegt ausserhalb des Pakets und faellt sonst durchs Raster."""
    import wt3000_scpi

    tools = Path(wt3000_scpi.__file__).parents[2] / "tools" / "hardware"
    if not tools.is_dir():  # pragma: no cover - nur im Quellbaum vorhanden
        pytest.skip("tools/hardware/ nicht vorhanden (installiertes Paket)")

    treffer = [
        pfad.name
        for pfad in sorted(tools.glob("*.py"))
        if _ruft_path_cwd(pfad.read_text(encoding="utf-8"))
    ]
    assert treffer == [], f"benutzen noch Path.cwd(): {treffer}"
