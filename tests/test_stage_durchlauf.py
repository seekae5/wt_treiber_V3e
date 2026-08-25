# =============================================================================
# Datei: tests/test_stage_durchlauf.py
# Vollstaendige Durchlaeufe der Stufenskripte gegen FakeTransport.
#
# Geprueft wird der Ablauf, nicht die separat abgedeckte Fachlogik: main()
# laeuft durch, REMOTE wird geloest, die Item-Tabelle restauriert und die
# Nur-Lesen-Zusage eingehalten.
#
# Fachdetails stehen in den jeweils zustaendigen Tests.
# =============================================================================

from __future__ import annotations

import json

import pytest
from conftest import ItemTableTransport

from wt3000_scpi import stage2_read_numeric as stage2
from wt3000_scpi import stage3_own_itemtable as stage3
from wt3000_scpi import stage4_measure as stage4
from wt3000_scpi import stage5_input_config as stage5

REMOTE_ON = ":COMMunicate:REMote ON"
REMOTE_OFF = ":COMMunicate:REMote OFF"

#: Ausgangstabelle des Geraets - bewusst ANDERS als die Zieltabellen von
#: Stufe 3 und 4, sonst waere ein fehlender Restore nicht zu erkennen.
AUSGANGSTABELLE = {1: "U,1", 2: "I,1", 3: "P,1"}


def geraet(**kwargs) -> ItemTableTransport:
    """Geraet mit der Ausgangstabelle und allen Antworten der Stufen 2-4."""
    return ItemTableTransport(dict(AUSGANGSTABELLE), number=len(AUSGANGSTABELLE), **kwargs)


def set_kommandos(transport) -> list[str]:
    """Alles, was kein Query war - also jeder echte Schreibzugriff."""
    return [c for c in transport.written if not c.strip().endswith("?")]


def ohne_remote(transport) -> list[str]:
    """Schreibzugriffe ohne die beiden REMOTE-Kommandos."""
    return [c for c in set_kommandos(transport) if ":COMMunicate:REMote" not in c]


# ---------------------------------------------------------------------------
# Stufe 2 - liest, schreibt nicht
# ---------------------------------------------------------------------------


@pytest.fixture
def stufe(stufenlauf, monkeypatch):
    """Ein Stufenskript gegen das ItemTableTransport-Geraetemodell fahren.

    Die Fixture 'stufenlauf' aus conftest.py setzt einen FakeTransport ein;
    hier wird stattdessen das zustandsfuehrende Geraetemodell gebraucht, sonst
    laesst sich die Frage "steht die Tabelle danach wieder wie vorher?" gar
    nicht stellen.
    """

    def _vorbereiten(modul, *, zyklen: int = 1, **kwargs) -> ItemTableTransport:
        transport = geraet(**kwargs)
        stufenlauf(modul, {})

        def _neue_verbindung(_config) -> ItemTableTransport:
            # Jeder Aufruf von TmctlTransport(config) ist am Geraet eine NEUE
            # Verbindung. Das Modell bleibt dasselbe Objekt - der Zustand des
            # Geraets ueberdauert ja auch das Schliessen -, aber 'closed' wird
            # zurueckgenommen.
            #
            # Ohne das scheitert Stufe 2: sie baut in ihrem aeusseren finally
            # eine ZWEITE Verbindung auf, um restore_to_device() aufzurufen
            # (Befund A-14). Gegen einen bereits geschlossenen Transport
            # endete das in "TmcSend fehlgeschlagen ... Transport ist
            # geschlossen" - ein Artefakt der Vorrichtung, nicht des Skripts.
            transport.closed = False
            return transport

        monkeypatch.setattr(modul, "TmctlTransport", _neue_verbindung)
        for name, wert in (("READ_CYCLES", zyklen), ("POLL_INTERVAL_S", 0)):
            if hasattr(modul, name):
                monkeypatch.setattr(modul, name, wert)
        return transport

    return _vorbereiten


def test_stufe2_gibt_null_zurueck(stufe):
    stufe(stage2)
    assert stage2.main() == 0


def test_stufe2_nimmt_remote_zurueck(stufe):
    transport = stufe(stage2)
    stage2.main()
    assert REMOTE_ON in transport.written
    assert REMOTE_OFF in transport.written


def test_stufe2_beruehrt_die_item_tabelle_nicht(stufe):
    """Der Dateikopf sagt: 'Diese Stufe veraendert die Item-Tabelle NICHT.'

    Das ist die maschinelle Fassung dieser Zusage. Sie ist heute schon wahr -
    der Pruefsatz schreibt sie fest, BEVOR Schritt 9 die Stufe auf
    read_only=True umstellt (Befund A-14).
    """
    transport = stufe(stage2)
    stage2.main()

    assert transport.items == AUSGANGSTABELLE
    assert transport.number == len(AUSGANGSTABELLE)
    assert ohne_remote(transport) == [], (
        f"Stufe 2 hat geschrieben: {ohne_remote(transport)}"
    )


def test_stufe2_legt_ein_backup_an(stufe, tmp_path):
    stufe(stage2)
    stage2.main()

    sicherungen = list(tmp_path.glob("wt3000_itemtable_backup_*.json"))
    assert len(sicherungen) == 1
    # Stufe 2 benutzt ItemTable.save() und schreibt to_dict() direkt; Stufe 3
    # und 4 gehen ueber save_backup_bundle() und legen einen 'table'-Rahmen
    # darum. Zwei Formate fuer dieselbe Sache - das ist Befund S-06 (Stufe 2
    # sichert keinen Tail, weil sie nie ueber NUMber hinaus schreibt) und
    # gehoert zu ROADMAP M2-4, "Ein gemeinsames Backup".
    inhalt = json.loads(sicherungen[0].read_text(encoding="utf-8"))
    assert inhalt["number"] == len(AUSGANGSTABELLE)


# ---------------------------------------------------------------------------
# Stufe 3 - schreibt die Item-Tabelle und stellt sie zurueck
# ---------------------------------------------------------------------------


def test_stufe3_gibt_null_zurueck(stufe):
    stufe(stage3)
    assert stage3.main() == 0


def test_stufe3_stellt_die_item_tabelle_zurueck(stufe):
    """Die Kernzusage von Stufe 3 - und der Grund, warum sie schreibt.

    Ohne diesen Pruefsatz ist 'restore_item_table()' nur eine Behauptung: der
    Lauf schreibt 33 Items an ein eingemessenes Geraet und verlaesst sich
    darauf, dass er sie danach wieder findet.
    """
    transport = stufe(stage3)
    assert stage3.main() == 0

    assert transport.number == len(AUSGANGSTABELLE)
    for index, argument in AUSGANGSTABELLE.items():
        assert transport.items[index] == argument, f"ITEM{index} nicht zurueckgestellt"


def test_stufe3_hat_zwischendurch_wirklich_geschrieben(stufe):
    """Gegenprobe zum Pruefsatz oben.

    Ein Restore, der nichts zurueckstellt, weil nie etwas geschrieben wurde,
    waere ebenfalls gruen. Dieser Pruefsatz schliesst das aus.
    """
    transport = stufe(stage3)
    stage3.main()

    geschrieben = [c for c in ohne_remote(transport) if ":NUMeric:NORMal:ITEM" in c]
    assert geschrieben, "Stufe 3 hat gar keine Items geschrieben"


def test_stufe3_nimmt_remote_zurueck(stufe):
    transport = stufe(stage3)
    stage3.main()
    assert REMOTE_OFF in transport.written


def test_stufe3_legt_ein_backup_an(stufe, tmp_path):
    stufe(stage3)
    stage3.main()
    assert list(tmp_path.glob("wt3000_itemtable_backup_*.json"))


# ---------------------------------------------------------------------------
# Stufe 4 - Messschleife, CSV und Metadaten
# ---------------------------------------------------------------------------


@pytest.fixture
def stufe4(stufe, monkeypatch):
    """Stufe 4 mit einer sehr kurzen Messreihe."""
    transport = stufe(stage4)
    monkeypatch.setattr(stage4, "MAX_SAMPLES", 3)
    monkeypatch.setattr(stage4, "MAX_DURATION_S", None)
    monkeypatch.setattr(stage4, "SAMPLE_INTERVAL_S", 0.0)
    return transport


def test_stufe4_gibt_null_zurueck(stufe4):
    assert stage4.main() == 0


def test_stufe4_stellt_die_item_tabelle_zurueck(stufe4):
    """Dieselbe Zusage wie Stufe 3, nach einer echten Messschleife."""
    assert stage4.main() == 0

    assert stufe4.number == len(AUSGANGSTABELLE)
    for index, argument in AUSGANGSTABELLE.items():
        assert stufe4.items[index] == argument


def test_stufe4_schreibt_messdaten(stufe4, tmp_path):
    stage4.main()

    csv = list(tmp_path.glob("wt3000_measurement_*.csv"))
    assert len(csv) == 1
    zeilen = csv[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(zeilen) == 1 + 3, f"Kopfzeile + 3 Messwerte erwartet, war {len(zeilen)}"


def test_stufe4_schreibt_metadaten(stufe4, tmp_path):
    """Das Sidecar, um das es in Schritt 6 ging - hier im vollen Lauf."""
    stage4.main()

    meta = list(tmp_path.glob("wt3000_measurement_*_meta.json"))
    assert len(meta) == 1
    inhalt = json.loads(meta[0].read_text(encoding="utf-8"))
    assert inhalt["device"]["idn"].startswith("YOKOGAWA")
    assert inhalt["parameters"]["max_samples"] == 3
    assert inhalt["item_table"]["number"] >= 1


def test_stufe4_nimmt_hold_zurueck(stufe4):
    """':NUMeric:HOLD' ist der zweite Schreibzugriff von Stufe 4.

    Er friert den Datensatz fuer die Dauer eines Zyklus ein. Bleibt HOLD am
    Ende auf ON stehen, liefert das Geraet einem spaeteren Anwender immer
    denselben Datensatz - ein Fehler, der wie ein defektes Geraet aussieht.
    """
    stage4.main()

    hold = [c for c in set_kommandos(stufe4) if ":NUMeric:HOLD" in c]
    if hold:
        assert hold[-1].upper().endswith("OFF"), f"HOLD bleibt stehen: {hold}"


def test_stufe4_nimmt_remote_zurueck(stufe4):
    stage4.main()
    assert REMOTE_OFF in stufe4.written


# ---------------------------------------------------------------------------
# Stufe 5 - liest die Eingangskonfiguration, schreibt nichts
# ---------------------------------------------------------------------------


def test_stufe5_gibt_null_zurueck(stufenlauf, eingangsantworten):
    stufenlauf(stage5, eingangsantworten)
    assert stage5.main() == 0


def test_stufe5_schreibt_kein_einziges_kommando(stufenlauf, eingangsantworten):
    """Der Dateikopf sagt 'SCHREIBT NICHTS' - hier steht der Beleg.

    Stufe 5 setzt das mit read_only=True durch, WTSession lehnt also jedes
    Nicht-Query-Kommando ab. Der Pruefsatz haelt fest, dass die Sperre auch
    nie ausgeloest wird: ein Lauf, der bei jedem zweiten Kommando gegen die
    Sperre laeuft, waere formal ebenfalls 'schreibt nichts'.

    ZU BEACHTEN - die Zusage hat eine Grenze, die Befund A-15 benennt:
    'assert_no_error()' sendet ':STATus:ERRor?' und LEERT damit die
    Fehlerqueue. Ein reiner Lesevorgang veraendert also sehr wohl etwas am
    Geraet. Das ist kein Fehler, sondern eine Grenze der Zusage; sie wird in
    Schritt 9 im Dateikopf benannt.
    """
    transport = stufenlauf(stage5, eingangsantworten)
    stage5.main()

    assert set_kommandos(transport) == []


def test_stufe5_nimmt_remote_nicht_in_anspruch(stufenlauf, eingangsantworten):
    """Stufe 5 schaltet REMOTE bewusst nie ein - mit Warnung im Dateikopf.

    Der Pruefsatz laeuft ausdruecklich mit use_remote=True: die Konfiguration
    sagt ja, das Skript tut es trotzdem nicht.
    """
    transport = stufenlauf(stage5, eingangsantworten, use_remote=True)
    stage5.main()

    assert REMOTE_ON not in transport.written


def test_stufe5_sichert_snapshot_und_rohabzug(stufenlauf, eingangsantworten, tmp_path):
    stufenlauf(stage5, eingangsantworten)
    stage5.main()

    assert len(list(tmp_path.glob("wt3000_inputconfig_*.json"))) == 1
    assert len(list(tmp_path.glob("wt3000_inputdump_*.txt"))) == 1
