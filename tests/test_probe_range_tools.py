# =============================================================================
# Datei: tests/test_probe_range_tools.py
# Maschinelle Tests der beiden schreibenden Geraeteskripte. Sie sichern die
# Rueckstellung auch bei Fehlern und Strg+C, Ruecklesevergleich, Fehlerqueue
# und den ausdruecklichen REMOTE-Zustand ab.
#
# WICHTIG - warum diese Datei die Skripte ueber den DATEIPFAD laedt und nicht
# ueber einen Import: tools/hardware/ ist kein Paket, und ein sys.path-Eintrag
# dorthin waere genau der Weg, ueber den eine automatische Import-Ergaenzung
# der Entwicklungsumgebung einmal 'from tests.conftest import ...' in
# probe_current_range.py geschrieben hat (siehe dessen Dateikopf). Der Ladeweg
# unten erzeugt keinen solchen Pfad.
#
# UEBERARBEITET (Schritt 0c): Vorrichtung und Ladefunktion liegen jetzt in
# conftest.py. Die Begruendung fuer den Ladeweg steht dort, bei geraeteskript().
# =============================================================================

from __future__ import annotations

import pytest

from conftest import geraeteskript

from wt3000_scpi.wt3000_common import format_nrf

ELEMENT = 4
REMOTE_ON = ":COMMunicate:REMote ON"
REMOTE_OFF = ":COMMunicate:REMote OFF"

#: Ausgangswerte des vorliegenden Aufbaus - Element 4 haengt direkt (5 A bzw.
#: 1000 V), nicht am Sensoreingang. Siehe conftest.range_responses().
AUSGANGSWERT = {"current": "5.00E+00", "voltage": "1.000E+03"}


def antworten(knoten: str, *bereichswerte: str) -> dict:
    """Antworttabelle mit einer Folge von Werten fuer denselben RANGe-Knoten.

    FakeTransport gibt bei einer Liste den ersten Eintrag heraus und laesst den
    letzten stehen. 'antworten(":INPUT:CURRENT", "5.00E+00", "500.0E-03")'
    beantwortet also das erste get_range() mit 5 A und jedes weitere mit 0,5 A -
    genau der Ablauf lesen / schreiben / zuruecklesen.
    """
    return {f"{knoten}:RANGE:ELEMENT{ELEMENT}": list(bereichswerte)}


def set_kommandos(transport) -> list[str]:
    """Alles, was kein Query war - also jeder echte Schreibzugriff."""
    return [c for c in transport.written if not c.strip().endswith("?")]


def bereichs_kommandos(transport) -> list[str]:
    """Nur die RANGe-Schreibzugriffe, ohne REMOTE."""
    return [c for c in set_kommandos(transport) if ":RANGe" in c]


WERKZEUGE = [
    pytest.param("probe_voltage_range", ":INPUT:VOLTAGE", "voltage", id="voltage"),
    pytest.param("probe_current_range", ":INPUT:CURRENT", "current", id="current"),
]


# ---------------------------------------------------------------------------
# Der Kern von A-02: die Rueckstellgarantie
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, knoten, groesse", WERKZEUGE)
def test_ausgangswert_wird_auch_bei_abbruch_zurueckgestellt(
    stufenlauf, monkeypatch, name, knoten, groesse
):
    """Der Befund in seiner reinen Form.

    Das Zuruecklesen bricht ab - eine Ausnahme zwischen dem Schreiben des
    Testwerts und der Rueckstellung, also genau die Luecke, die der Dateikopf
    nicht abdeckte. Danach muss der Ausgangswert trotzdem am Geraet stehen.
    """
    modul = geraeteskript(name)
    transport = stufenlauf(modul, antworten(knoten, AUSGANGSWERT[groesse]))

    # Der zweite get_range() findet keinen Eintrag mehr in der Antwortliste -
    # FakeTransport wirft dort. Kein kuenstlicher Fehler, sondern derselbe
    # Abbruch, den ein Timeout am Geraet erzeugt.
    original_get = modul.RangeAccess.get_range
    aufrufe = {"n": 0}

    def _zweiter_lesevorgang_scheitert(self, quantity, element):
        aufrufe["n"] += 1
        if aufrufe["n"] == 2:
            raise TimeoutError("Antwort blieb aus")
        return original_get(self, quantity, element)

    monkeypatch.setattr(modul.RangeAccess, "get_range", _zweiter_lesevorgang_scheitert)

    with pytest.raises(TimeoutError):
        modul.main()

    kommandos = bereichs_kommandos(transport)
    assert kommandos, "Es wurde ueberhaupt kein Bereich geschrieben"
    assert kommandos[-1].endswith(f" {format_nrf(float(AUSGANGSWERT[groesse]))}"), (
        f"Letzter Schreibzugriff war {kommandos[-1]!r} - der Testwert bleibt am "
        "Geraet stehen. Der Schreibteil gehoert in ein try/finally."
    )


@pytest.mark.parametrize("name, knoten, groesse", WERKZEUGE)
def test_ausgangswert_wird_auch_bei_strg_c_zurueckgestellt(
    stufenlauf, monkeypatch, name, knoten, groesse
):
    """Strg+C zwischen Schreiben und Rueckstellen - der reale Fall.

    KeyboardInterrupt erbt von BaseException. Ein 'except Exception' als
    Reparatur wuerde diesen Pruefsatz nicht gruen bekommen; nur ein 'finally'
    traegt die Zusage aus dem Dateikopf.
    """
    modul = geraeteskript(name)
    transport = stufenlauf(
        modul, antworten(knoten, AUSGANGSWERT[groesse], AUSGANGSWERT[groesse])
    )

    original_get = modul.RangeAccess.get_range
    aufrufe = {"n": 0}

    def _strg_c_beim_ruecklesen(self, quantity, element):
        aufrufe["n"] += 1
        if aufrufe["n"] == 2:
            raise KeyboardInterrupt()
        return original_get(self, quantity, element)

    monkeypatch.setattr(modul.RangeAccess, "get_range", _strg_c_beim_ruecklesen)

    with pytest.raises(KeyboardInterrupt):
        modul.main()

    kommandos = bereichs_kommandos(transport)
    assert kommandos[-1].endswith(f" {format_nrf(float(AUSGANGSWERT[groesse]))}")


@pytest.mark.parametrize("name, knoten, groesse", WERKZEUGE)
def test_glatter_lauf_stellt_den_ausgangswert_zurueck(stufenlauf, name, knoten, groesse):
    """Auch ohne Zwischenfall endet der Lauf auf dem Ausgangswert."""
    modul = geraeteskript(name)
    transport = stufenlauf(
        modul,
        antworten(knoten, AUSGANGSWERT[groesse], format_nrf(modul.TEST_VALUE)),
    )

    assert modul.main() == 0

    kommandos = bereichs_kommandos(transport)
    assert len(kommandos) == 2, f"Erwartet: Testwert setzen und zuruecknehmen, war {kommandos}"
    assert kommandos[0].endswith(f" {format_nrf(modul.TEST_VALUE)}")
    assert kommandos[1].endswith(f" {format_nrf(float(AUSGANGSWERT[groesse]))}")


# ---------------------------------------------------------------------------
# Das maschinelle Urteil
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, knoten, groesse", WERKZEUGE)
def test_nicht_uebernommener_wert_ergibt_rueckgabewert_1(stufenlauf, name, knoten, groesse):
    """Der Pruefsatz, der heute nicht formulierbar waere.

    Das Geraet meldet einen ANDEREN Wert zurueck als den gesendeten - die
    Syntax wurde also nicht angenommen. Genau das ist die Frage, fuer die
    diese Skripte gebaut sind (ROADMAP M0-1). Vor der Reparatur gingen beide
    Werte nur ins Protokoll und main() lieferte auch dann 0.
    """
    modul = geraeteskript(name)
    abweichend = "9.99E+00"
    stufenlauf(modul, antworten(knoten, AUSGANGSWERT[groesse], abweichend))

    assert modul.main() == 1


@pytest.mark.parametrize("name, knoten, groesse", WERKZEUGE)
def test_uebernommener_wert_ergibt_rueckgabewert_0(stufenlauf, name, knoten, groesse):
    """Gegenprobe: stimmt der Rueckgabewert, ist der Lauf erfolgreich."""
    modul = geraeteskript(name)
    stufenlauf(
        modul,
        antworten(knoten, AUSGANGSWERT[groesse], format_nrf(modul.TEST_VALUE)),
    )

    assert modul.main() == 0


# ---------------------------------------------------------------------------
# REMOTE - ausdruecklich, nicht aus der Umgebung
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, knoten, groesse", WERKZEUGE)
def test_remote_wird_ein_und_wieder_ausgeschaltet(stufenlauf, name, knoten, groesse):
    """Beide Skripte schalten die Fernsteuerung ein und geben sie zurueck."""
    modul = geraeteskript(name)
    transport = stufenlauf(
        modul,
        antworten(knoten, AUSGANGSWERT[groesse], format_nrf(modul.TEST_VALUE)),
    )

    modul.main()

    assert REMOTE_ON in transport.written
    assert REMOTE_OFF in transport.written


@pytest.mark.parametrize("name, knoten, groesse", WERKZEUGE)
def test_remote_wird_auch_bei_abbruch_zurueckgenommen(
    stufenlauf, monkeypatch, name, knoten, groesse
):
    """Dieselbe Zusage wie A-01 in Stufe 3 und 4, hier fuer die Werkzeuge."""
    modul = geraeteskript(name)
    transport = stufenlauf(modul, antworten(knoten, AUSGANGSWERT[groesse]))
    monkeypatch.setattr(
        modul.RangeAccess,
        "set_range",
        lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        modul.main()

    assert REMOTE_OFF in transport.written


@pytest.mark.parametrize("name, knoten, groesse", WERKZEUGE)
def test_use_remote_haengt_nicht_an_der_konfiguration(
    stufenlauf, name, knoten, groesse
):
    """Der Versuchsparameter steht im Skript, nicht in 'wt3000.json'.

    Begruendung siehe Plan, Schritt 2d: M0-1 fragt nach der Syntax, M0-3 nach
    der Notwendigkeit von REMOTE. Haenge diese Skripte an config.use_remote,
    entscheidet eine Umgebungsvariable ueber den Versuchsaufbau, ohne im
    Protokoll aufzutauchen - und ein fehlgeschlagener Rueckleseversuch waere
    keiner der beiden Ursachen mehr zuzuordnen.
    """
    modul = geraeteskript(name)
    # Die Konfiguration sagt ausdruecklich NEIN - das Skript muss REMOTE
    # trotzdem einschalten, weil USE_REMOTE eine Modulkonstante ist.
    transport = stufenlauf(
        modul,
        antworten(knoten, AUSGANGSWERT[groesse], format_nrf(modul.TEST_VALUE)),
        use_remote=False,
    )

    modul.main()

    assert REMOTE_ON in transport.written, (
        "config.use_remote hat den Versuchsaufbau veraendert - USE_REMOTE "
        "gehoert als Modulkonstante ins Skript"
    )


# ---------------------------------------------------------------------------
# Die Fehlerqueue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, knoten, groesse", WERKZEUGE)
def test_fehlerqueue_wird_nach_der_rueckstellung_geprueft(stufenlauf, name, knoten, groesse):
    """':STATus:ERRor?' muss NACH dem letzten Schreibzugriff kommen.

    Sonst deckt die Pruefung die Rueckstellung nicht mit ab - und die
    Rueckstellung ist der Schreibzugriff, bei dem ein Fehler am meisten weh
    tut.
    """
    modul = geraeteskript(name)
    transport = stufenlauf(
        modul,
        antworten(knoten, AUSGANGSWERT[groesse], format_nrf(modul.TEST_VALUE)),
    )

    modul.main()

    letzter_bereich = max(i for i, c in enumerate(transport.written) if ":RANGe" in c)
    erste_fehlerabfrage = min(
        i for i, c in enumerate(transport.written) if c.strip().upper().startswith(":STAT")
    )
    assert erste_fehlerabfrage > letzter_bereich


@pytest.mark.parametrize("name, knoten, groesse", WERKZEUGE)
def test_geraetefehler_ergibt_rueckgabewert_1(stufenlauf, name, knoten, groesse):
    """Ein Eintrag in der Fehlerqueue faellt als WTError auf und wird gefangen."""
    modul = geraeteskript(name)
    transport = stufenlauf(
        modul,
        antworten(knoten, AUSGANGSWERT[groesse], format_nrf(modul.TEST_VALUE)),
    )
    transport.error_queue.append("-113,\"Undefined header\"")

    assert modul.main() == 1


# ---------------------------------------------------------------------------
# Der Laufparameter selbst (Schritt 2a)
# ---------------------------------------------------------------------------


def test_teststrom_ist_eine_gueltige_bereichsstufe():
    """TEST_VALUE muss in JEDER Bereichstabelle des Handbuchs vorkommen.

    Sonst stellt der Lauf zwei Fragen auf einmal - Syntax (M0-1) und
    Rundungsverhalten (M0-2) - und ein abweichender Rueckgabewert liesse sich
    keiner von beiden zuordnen. Genau das begruendet der Kommentar ueber
    TEST_VALUE in probe_current_range.py; dieser Pruefsatz haelt ihn fest.
    """
    from wt3000_scpi.wt3000_input import CURRENT_RANGES

    modul = geraeteskript("probe_current_range")
    tabellen = [
        werte
        for werte in CURRENT_RANGES.values()
        if isinstance(werte, (list, tuple)) and werte
    ]
    assert tabellen, "CURRENT_RANGES hat eine andere Form als erwartet"
    fehlend = [t for t in tabellen if modul.TEST_VALUE not in t]
    assert not fehlend, (
        f"TEST_VALUE = {modul.TEST_VALUE} fehlt in {len(fehlend)} von "
        f"{len(tabellen)} Bereichstabellen"
    )
