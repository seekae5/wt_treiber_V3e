# =============================================================================
# Datei: tests/test_stage5b_write_probe.py
# Die Nur-Lesen-Voreinstellung von Stufe 5b ist maschinell abgesichert.
#
# Ein frueherer Widerspruch: Der Dateikopf sagte "SCHREIBT NICHTS", die Konstante
# ENABLE_NOOP_WRITE_PROBE stand auf True. Ein Widerspruch, den nichts bemerkt
# haette - Stufenskripte waren bis hierher ungetestet, weil sie eine echte
# Verbindung aufbauen.
#
# Mit 'FakeTransport' (M1-2) laesst sich main() vollstaendig durchspielen. Der
# Transport wird im Modul ersetzt, nicht global: 'TmctlTransport' ist seit P-6
# in der Testsuite stillgelegt (tests/conftest.py), und genau daran soll sich
# auch dieser Test nicht vorbeimogeln.
# =============================================================================

from __future__ import annotations

import pytest
from conftest import range_responses

from wt3000_scpi import stage5b_range_probe as stage5b
from wt3000_scpi.wt3000_transport import FakeTransport


def geraeteantworten() -> dict:
    """Alles, was Stufe 5b abfragt - Bereiche, Umfeld, Rohabzuege."""
    table = dict(range_responses())
    table.update(
        {
            ":INPUT:POVER": "0,0,0,0",
            ":INPUT:VOLTAGE": "1.000E+03",
            ":INPUT:CURRENT": "EXTERNAL,10.00E+00",
        }
    )
    return table


@pytest.fixture
def lauf(stufenlauf):
    """main() auf einem FakeTransport, Ausgabedateien im tmp-Verzeichnis.

    UEBERARBEITET (Schritt 0c aus MarkDowns/PLAN_AUFRUFKETTE.md, Befund A-13):
    die drei monkeypatch-Zeilen, die hier standen, liegen jetzt als Fixture
    'stufenlauf' in conftest.py - samt der Begruendung, warum setup_logging()
    stillgelegt werden muss. Sie werden inzwischen von drei Testmodulen
    gebraucht; diese Datei war die Vorlage dafuer.
    """
    return stufenlauf(stage5b, geraeteantworten())


def gesendete_kommandos(transport: FakeTransport) -> list[str]:
    """Alles, was kein Query war - also jeder echte Schreibzugriff."""
    return [c for c in transport.written if not c.strip().endswith("?")]


# ---------------------------------------------------------------------------
# Die Voreinstellung
# ---------------------------------------------------------------------------


def test_voreinstellung_sendet_kein_einziges_set_kommando(lauf):
    """Der Kern von BF-H4: 'SCHREIBT NICHTS' gilt jetzt nachweislich."""
    assert stage5b.main() == 0
    assert gesendete_kommandos(lauf) == []


def test_voreinstellung_liest_trotzdem_den_bereichszustand(lauf):
    """Ohne Schreibprobe bleibt das Skript vollstaendig brauchbar."""
    stage5b.main()
    abgefragt = [c for c in lauf.written if c.strip().endswith("?")]
    assert ":INPut:WIRing?" in abgefragt
    assert ":INPut:VOLTage:RANGe:ELEMent1?" in abgefragt


def test_voreinstellung_schreibt_das_backup_auf_platte(lauf, tmp_path):
    stage5b.main()
    assert list(tmp_path.glob("wt3000_ranges_*.json"))


# ---------------------------------------------------------------------------
# Der Schalter
# ---------------------------------------------------------------------------


def test_write_probe_sendet_genau_ein_set_kommando(lauf):
    assert stage5b.main(enable_write_probe=True) == 0
    gesendet = gesendete_kommandos(lauf)
    assert len(gesendet) == 1
    assert gesendet[0].startswith(":INPut:VOLTage:RANGe:ELEMent1 ")


def test_write_probe_meldet_sich_vor_dem_ersten_kommando(lauf, caplog):
    """Der Schalter soll unangenehm sein - die Warnung steht vor dem Zugriff."""
    with caplog.at_level("WARNING"):
        stage5b.main(enable_write_probe=True)
    warnungen = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("SCHREIBPROBE AKTIV" in m for m in warnungen)
    assert any(":INPut:VOLTage:RANGe:ELEMent1" in m for m in warnungen)


# ---------------------------------------------------------------------------
# Kommandozeile
# ---------------------------------------------------------------------------


def test_ohne_schalter_bleibt_die_kommandozeile_lesend():
    assert stage5b._parse_args([]).write_probe is False


def test_der_schalter_setzt_das_flag():
    assert stage5b._parse_args(["--write-probe"]).write_probe is True


def test_unbekannter_schalter_bricht_ab():
    """Ein Tippfehler darf nicht stillschweigend zu 'nur Lesen' fuehren."""
    with pytest.raises(SystemExit):
        stage5b._parse_args(["--writeprobe"])
