# =============================================================================
# Datei: tests/test_package_layout.py
# Tests des Paketlayouts: alle Module sind importierbar, Geschwisterimporte
# sind relativ und die Schichten zeigen nur nach unten.
# =============================================================================

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

import wt3000_scpi

PACKAGE_DIR = Path(wt3000_scpi.__file__).parent

# Erlaubte Importe je Modul - die Schichtung aus dem Kopf von __init__.py.
LAYERS: dict[str, set[str]] = {
    # Der Transport ist paketunabhaengig und damit geraetefrei testbar.
    "wt3000_transport": set(),
    # Der Kern darf nur den Transport importieren.
    "wt3000_core": {"wt3000_transport"},
    "wt3000_common": {"wt3000_core"},
    "wt3000_numeric": {"wt3000_core"},
    "wt3000_rangeio": {"wt3000_core", "wt3000_common"},
    "wt3000_input": {"wt3000_core", "wt3000_common"},
    # Geraetegruppen nutzen gemeinsame Regeln aus wt3000_common und importieren
    # keine Geschwister derselben Schicht.
    "wt3000_deviceconfig": {"wt3000_core", "wt3000_common"},
    "wt3000_itemspec": {"wt3000_core", "wt3000_common", "wt3000_numeric"},
    "wt3000_ranging": {"wt3000_core", "wt3000_common", "wt3000_rangeio"},
    "wt3000_measure": {
        "wt3000_core",
        "wt3000_common",
        "wt3000_numeric",
        "wt3000_itemspec",
    },
    # Ausgabeformate setzen SampleSink um. Die Messschleife darf sie nicht
    # zurueckimportieren und bleibt dadurch formatunabhaengig.
    "wt3000_sinks": {"wt3000_core", "wt3000_numeric", "wt3000_measure"},
    # Der Sitzungs-Sicherungspunkt steht auf Layer 3 und darf
    # deshalb aus den Fachmodulen darunter importieren - auch aus den beiden
    # Geschwistern 'wt3000_itemspec' und 'wt3000_ranging', genau wie
    # 'wt3000_measure' es mit 'wt3000_itemspec' tut.
    #
    # Bewusst NICHT enthalten: 'wt3000_device'. Der Steckbrief ('DeviceInfo')
    # ist Layer 4, und ein Backup, das ihn importierte, zoege die ganze Fassade
    # in Layer 3 hinein. Deshalb fuehrt 'SessionBackup.device' eine schlichte
    # Abbildung, die die Fassade ueber 'device_fingerprint()' fuellt.
    "wt3000_backup": {
        "wt3000_core",
        "wt3000_common",
        "wt3000_numeric",
        "wt3000_rangeio",
        "wt3000_input",
        "wt3000_deviceconfig",
        "wt3000_itemspec",
        "wt3000_ranging",
    },
    # Die Fassade buendelt alle tieferen Fachmodule, importiert aber weder
    # Stufenskripte noch ein anderes Layer-4-Modul.
    "wt3000_device": {
        "wt3000_transport",
        "wt3000_core",
        "wt3000_common",
        "wt3000_numeric",
        "wt3000_rangeio",
        "wt3000_input",
        "wt3000_deviceconfig",
        "wt3000_itemspec",
        "wt3000_ranging",
        "wt3000_measure",
        "wt3000_sinks",
        # Die Fassade buendelt den Sicherungspunkt.
        "wt3000_backup",
    },
    # Stufenskripte sind ebenfalls Teil der geprueften Schichtung.
    # Die Eintraege bilden den heutigen Bestand ab, sie sind also beim Anlegen
    # sofort gruen. Das ist Absicht - sie sichern, was schon gilt.
    #
    # Bewusst NICHT enthalten und der eigentliche Zweck dieser fuenf Zeilen:
    #
    #   * 'wt3000_device'. Die Fassade ist Layer 4, genau wie die Stufen. Ein
    #     Stufenskript, das sie importiert, waere ein Querimport innerhalb
    #     derselben Schicht. Der Eintrag 'wt3000_device' oben haelt dieselbe
    #     Regel fuer die Gegenrichtung fest ("aus keinem Stufenskript und aus
    #     keinem zweiten Layer-4-Modul"); ab hier gilt sie in beide Richtungen.
    #
    #   * jedes andere Stufenskript. Gemeinsames gehoert nach 'wt3000_common'
    #     (Layer 1) oder in die Fassade, nie quer. Das wird ab Schritt 8 des
    #     Plans wichtig, wenn alle sieben main() eine gemeinsame Signatur
    #     bekommen und die Versuchung entsteht, "gemeinsamen Code" zwischen
    #     zwei Stufen zu teilen.
    "stage2_read_numeric": {"wt3000_core", "wt3000_common", "wt3000_numeric"},
    "stage3_own_itemtable": {
        "wt3000_core",
        "wt3000_common",
        "wt3000_numeric",
        "wt3000_itemspec",
    },
    "stage4_measure": {
        "wt3000_core",
        "wt3000_common",
        "wt3000_numeric",
        "wt3000_itemspec",
        "wt3000_measure",
        "wt3000_sinks",
    },
    "stage5_input_config": {"wt3000_core", "wt3000_common", "wt3000_input"},
    "stage5b_range_probe": {
        "wt3000_core",
        "wt3000_common",
        "wt3000_rangeio",
        "wt3000_ranging",
    },
}


def modul_dateien() -> list[Path]:
    return sorted(p for p in PACKAGE_DIR.glob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize("name", wt3000_scpi.MODULES)
def test_jedes_fachmodul_ist_importierbar(name):
    """Importieren darf keine tmctl.dll und kein Geraet voraussetzen."""
    importlib.import_module(f"wt3000_scpi.{name}")


@pytest.mark.parametrize("pfad", modul_dateien(), ids=lambda p: p.stem)
def test_kein_absoluter_geschwisterimport(pfad):
    """Genau der Unterschied, der Wurzel und Build/-Klon getrennt hat."""
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ImportFrom) and knoten.level == 0:
            assert not (knoten.module or "").startswith("wt3000_"), (
                f"{pfad.name}: 'from {knoten.module} import ...' muss relativ sein"
            )
        if isinstance(knoten, ast.Import):
            for alias in knoten.names:
                assert not alias.name.startswith("wt3000_"), (
                    f"{pfad.name}: 'import {alias.name}' muss relativ sein"
                )


def test_layers_deckt_jedes_modul_ab():
    """NEU (Schritt 0a, Befund A-11): die Deckung selbst ist jetzt geprueft.

    Der Befund war nicht, dass fuenf Eintraege fehlten - sondern dass das
    niemandem auffiel. 'test_importrichtung_zeigt_nach_unten' ist ueber
    sorted(LAYERS) parametrisiert und prueft damit genau so viele Module, wie
    jemand eingetragen hat: ein neues Modul ohne Eintrag laesst die Suite
    gruen. Dieser Pruefsatz schliesst den Kreis, indem er LAYERS gegen den
    tatsaechlichen Bestand haelt.
    """
    vorhanden = {p.stem for p in modul_dateien()}
    fehlend = vorhanden - set(LAYERS)
    verwaist = set(LAYERS) - vorhanden
    assert not fehlend, (
        f"Ohne Eintrag in LAYERS und damit ungeprueft: {sorted(fehlend)}. "
        "Jedes Paketmodul braucht eine Zeile - auch ein Stufenskript."
    )
    assert not verwaist, f"LAYERS nennt Module, die es nicht gibt: {sorted(verwaist)}"


@pytest.mark.parametrize("name", sorted(LAYERS))
def test_importrichtung_zeigt_nach_unten(name):
    quelle = (PACKAGE_DIR / f"{name}.py").read_text(encoding="utf-8")
    genutzt = {
        knoten.module
        for knoten in ast.walk(ast.parse(quelle))
        if isinstance(knoten, ast.ImportFrom)
        and knoten.level == 1
        and knoten.module is not None
    }
    unerlaubt = genutzt - LAYERS[name]
    assert not unerlaubt, f"{name} importiert aus einer hoeheren Schicht: {unerlaubt}"


STUFENSKRIPTE = (
    "stage2_read_numeric",
    "stage3_own_itemtable",
    "stage4_measure",
    "stage5_input_config",
    "stage5b_range_probe",
)


def test_stufenskripte_fuehren_beim_import_nichts_aus():
    """Layer 4 darf erst ueber main() aktiv werden, nicht beim Import."""
    for name in STUFENSKRIPTE:
        modul = importlib.import_module(f"wt3000_scpi.{name}")
        assert callable(modul.main)


@pytest.mark.parametrize("name", STUFENSKRIPTE)
def test_import_legt_keine_datei_an(name, tmp_path, monkeypatch):
    """UEBERARBEITET (Schritt 0b, Befund A-10): die Zusage ist jetzt geprueft.

    Bis hierher stellte der Test darueber nur fest, dass 'main' aufrufbar ist -
    das ist keine Aussage darueber, ob der Import etwas TUT. Und er tut etwas:
    seit Schritt 0b legt jedes der fuenf Skripte 'OUTPUT_DIR = output_dir(...)'
    als Modulkonstante an, und output_dir() laeuft ueber find_project_root(),
    das vom Arbeitsverzeichnis aus aufwaerts 'exists()' auf drei Marker prueft.

    Das ist ein LESENDER Dateisystemzugriff und ausdruecklich zugelassen - die
    Alternative waere gewesen, den Pfad erst in main() aufzuloesen, und dann
    liesse er sich nicht mehr durch einen einzigen setattr ersetzen (siehe die
    Begruendung in Schritt 0b des Plans). Was NICHT passieren darf, ist ein
    schreibender Zugriff: kein mkdir, keine Protokolldatei, kein Backup. Genau
    diese Grenze haelt dieser Pruefsatz fest.
    """
    monkeypatch.chdir(tmp_path)
    for modul in list(sys.modules):
        if modul == f"wt3000_scpi.{name}":
            del sys.modules[modul]

    vorher = set(tmp_path.rglob("*"))
    importlib.import_module(f"wt3000_scpi.{name}")
    neu = set(tmp_path.rglob("*")) - vorher

    assert not neu, f"{name} hat beim Import angelegt: {sorted(p.name for p in neu)}"


# ---------------------------------------------------------------------------
# Die Suite bleibt geraetefrei
# ---------------------------------------------------------------------------


def test_testsuite_kann_keine_geraeteverbindung_oeffnen():
    """Belegt die Sperre aus tests/conftest.py.

    Der Kopf von conftest.py sagt zu, dass die Suite ohne Geraet und ohne
    tmctl.dll laeuft. Diese Zusage war lange nur Absicht: unter tests/ lag ein
    Skript, das eine echte Verbindung aufbaute und einen Messbereich schrieb.
    Seit es nach tools/hardware/ umgezogen ist, sichert conftest.py die Zusage
    aktiv ab - dieser Test haelt fest, dass die Sperre auch wirklich greift.
    """
    from wt3000_scpi.wt3000_transport import TmctlTransport, WTConfig

    with pytest.raises(RuntimeError, match="ohne Geraet"):
        TmctlTransport(WTConfig())


def test_die_sperre_laesst_den_protokollvertrag_unberuehrt():
    """Der stillgelegte Konstruktor darf die Typpruefung nicht beschaedigen.

    'issubclass(TmctlTransport, Transport)' in test_fake_transport.py haengt an
    write/read/query/set_timeout/close - nicht am Konstruktor.
    """
    from wt3000_scpi.wt3000_transport import TmctlTransport, Transport

    assert issubclass(TmctlTransport, Transport)
