# =============================================================================
# Datei: tests/test_backup.py
# Geraetefreie Tests des Sitzungs-Sicherungspunkts.
#
# Ein Backup ist die einzige Sache in diesem Treiber, die man erst dann
# braucht, wenn schon etwas schiefgegangen ist - und dann muss sie stimmen.
# Entsprechend liegt der Schwerpunkt nicht auf den Gettern, sondern auf:
#
#   1. dem VOLLEN ZYKLUS: sichern, verstellen, zurueckschreiben, pruefen.
#      Ein Backup, das nur erfasst und nie zurueckgespielt wurde, ist eine
#      Behauptung.
#   2. der IDENTITAETSPRUEFUNG. Eine Konfiguration auf ein fremdes Geraet zu
#      schreiben ist der teuerste Einzelfehler, den dieses Modul zulassen
#      koennte.
#   3. der VERSIONSPRUEFUNG beim Laden - eine fremde Datei darf nicht erst
#      mitten im Wiederherstellen auffallen.
# =============================================================================

from __future__ import annotations

import json

import pytest
from conftest import (
    ItemTableTransport,
    base_responses,
    computation_responses,
    harmonics_responses,
    input_responses,
    integrate_responses,
)

from wt3000_scpi import WT3000, WTConfig, WTError
from wt3000_scpi.wt3000_backup import BACKUP_VERSION, SessionBackup, device_fingerprint
from wt3000_scpi.wt3000_deviceconfig import (
    AveragingType,
    FrequencyBand,
    IntegrationMode,
    IntegrationState,
    SQFormula,
    SyncMode,
    ThdFormula,
)
from wt3000_scpi.wt3000_transport import FakeTransport


def alle_antworten(**kwargs) -> dict:
    """Antworttabelle fuer eine Fassade, die ALLES sichern kann."""
    responses = base_responses(**kwargs)
    responses.update(input_responses())
    responses.update(integrate_responses())
    responses.update(computation_responses())
    responses.update(harmonics_responses())
    return responses


def facade(transport: FakeTransport, **kwargs) -> WT3000:
    kwargs.setdefault("read_only", True)
    return WT3000.from_transport(transport, WTConfig(use_remote=False), **kwargs)


def schreibfaehig(transport: FakeTransport) -> WT3000:
    return WT3000.from_transport(
        transport, WTConfig(use_remote=False), read_only=False, allow_changes=True
    )


# ---------------------------------------------------------------------------
# Erfassen
# ---------------------------------------------------------------------------


def test_backup_erfasst_alle_bausteine():
    transport = ItemTableTransport({1: "U,1", 2: "I,1"}, number=2)
    transport.responses.update(alle_antworten())
    with facade(transport) as wt:
        backup = wt.backup()

    assert backup.input is not None
    assert backup.ranges is not None
    assert backup.items is not None
    assert backup.integration is not None
    assert backup.computation is not None
    assert backup.harmonics is not None
    assert backup.version == BACKUP_VERSION
    assert backup.device["model"] == "WT3000"


def test_sichern_veraendert_nichts():
    """Reine Queries - deshalb geht es auch in einer Nur-Lesen-Sitzung."""
    transport = ItemTableTransport({1: "U,1", 2: "I,1"}, number=2)
    transport.responses.update(alle_antworten())
    with facade(transport, read_only=True) as wt:
        wt.backup()
    assert [c for c in transport.written if not c.endswith("?")] == []


def test_ohne_harmonics_option_wird_die_gruppe_ausgelassen():
    """Sonst liefe die Sicherung in einen Timeout - das Gegenteil eines Netzes."""
    transport = ItemTableTransport({1: "U,1"}, number=1)
    transport.responses.update(alle_antworten(options="B5,C7"))
    with facade(transport) as wt:
        backup = wt.backup()
    assert backup.harmonics is None
    # Der Rest steht trotzdem.
    assert backup.integration is not None
    assert backup.computation is not None


def test_teilbackup_ist_zulaessig():
    """Was nicht uebergeben wird, wird nicht erfasst - und nicht geraten."""
    backup = SessionBackup.capture(device={"model": "WT3000"})
    assert backup.parts() == []
    assert backup.input is None
    assert backup.items is None


# ---------------------------------------------------------------------------
# Datei
# ---------------------------------------------------------------------------


def test_backup_geht_durch_die_datei_und_zurueck(tmp_path):
    transport = ItemTableTransport({1: "U,1", 2: "I,1"}, number=2)
    transport.responses.update(alle_antworten())
    pfad = tmp_path / "sitzung.json"

    with facade(transport) as wt:
        original = wt.backup(pfad)

    geladen = SessionBackup.load(pfad)
    assert geladen.to_dict() == original.to_dict()
    # Und die Fachobjekte sind wirklich wieder Objekte, nicht nur Dictionaries.
    assert geladen.integration.mode is IntegrationMode.NORMAL
    assert geladen.computation.averaging.type is AveragingType.EXPONENT
    assert geladen.harmonics.band is FrequencyBand.NORMAL
    assert geladen.items.number == 2


def test_datei_ist_mit_dem_auge_lesbar(tmp_path):
    """Ein Backup, das man nicht lesen kann, ist im Fehlerfall wertlos."""
    transport = ItemTableTransport({1: "U,1"}, number=1)
    transport.responses.update(alle_antworten())
    pfad = tmp_path / "sitzung.json"
    with facade(transport) as wt:
        wt.backup(pfad)

    text = pfad.read_text(encoding="utf-8")
    assert "\n" in text  # eingerueckt, nicht eine einzige Zeile
    daten = json.loads(text)
    assert daten["device"]["model"] == "WT3000"
    assert daten["integration"]["mode"] == "NORMal"
    assert daten["harmonics"]["order_min"] == 1


def test_fremde_formatversion_wird_beim_laden_abgewiesen(tmp_path):
    """Und nicht erst mitten im Wiederherstellen, wenn das Geraet halb steht."""
    pfad = tmp_path / "alt.json"
    pfad.write_text(json.dumps({"version": 99, "device": {}}), encoding="utf-8")
    with pytest.raises(WTError) as fehler:
        SessionBackup.load(pfad)
    assert "99" in str(fehler.value)


def test_kaputte_datei_nennt_den_pfad(tmp_path):
    pfad = tmp_path / "kaputt.json"
    pfad.write_text("{kein json", encoding="utf-8")
    with pytest.raises(WTError) as fehler:
        SessionBackup.load(pfad)
    assert "kaputt.json" in str(fehler.value)


# ---------------------------------------------------------------------------
# Identitaetspruefung
# ---------------------------------------------------------------------------


def test_backup_eines_fremden_geraets_wird_abgewiesen():
    """Der teuerste Einzelfehler, den dieses Modul zulassen koennte."""
    backup = SessionBackup(device=device_fingerprint(model="WT3000", serial="C1B234567"))
    fremd = device_fingerprint(model="WT3000", serial="XYZ999")

    assert backup.check_device(fremd) != []
    with pytest.raises(WTError) as fehler:
        backup.restore(device=fremd)
    assert "anderen Geraet" in str(fehler.value)
    assert "force" in str(fehler.value)


def test_fremdes_geraet_mit_force_geht_durch(caplog):
    backup = SessionBackup(device=device_fingerprint(model="WT3000", serial="C1B234567"))
    fremd = device_fingerprint(model="WT3000", serial="XYZ999")
    with caplog.at_level("WARNING"):
        backup.restore(device=fremd, force=True)
    assert any("force" in e.getMessage() for e in caplog.records)


def test_firmware_und_optionen_zaehlen_nicht_zur_identitaet():
    """Ein Firmware-Update macht ein Backup nicht ungueltig."""
    backup = SessionBackup(
        device=device_fingerprint(model="WT3000", serial="C1B", firmware="F2.11")
    )
    spaeter = device_fingerprint(model="WT3000", serial="C1B", firmware="F5.01")
    assert backup.check_device(spaeter) == []


def test_unbekannte_seriennummer_blockiert_nicht():
    """Ein von Hand gebautes Backup ohne Steckbrief soll benutzbar bleiben."""
    backup = SessionBackup(device={})
    assert backup.check_device(device_fingerprint(model="WT3000", serial="C1B")) == []


# ---------------------------------------------------------------------------
# Der volle Zyklus
# ---------------------------------------------------------------------------


class Geraetemodell(ItemTableTransport):
    """Fake-Geraet, das gesetzte Werte behaelt und zurueckmeldet.

    Ohne diese Rueckkopplung liesse sich ein Backup zwar erfassen und
    zurueckschreiben, aber nicht PRUEFEN: der Zustand bliebe konstant, und ob
    das Wiederherstellen etwas bewirkt hat, waere nicht sichtbar. Genau darum
    geht es bei einem Sicherungspunkt aber.

    Abgebildet wird nur, was der Zyklus unten anfasst - Averaging, THD-Bezug
    und Integrationsbetriebsart. Die uebrigen Knoten bleiben Tabellenantwort.
    """

    def __init__(self, **kwargs) -> None:
        self.zustand = {
            ":MEASURE:AVERAGING:STATE": "0",
            ":MEASURE:AVERAGING:TYPE": "EXPONENT",
            ":MEASURE:AVERAGING:COUNT": "8",
            ":HARMONICS:THD": "TOTAL",
            ":INTEGRATE:MODE": "NORM",
        }
        responses = alle_antworten()
        for knoten in self.zustand:
            responses[knoten] = self._responder(knoten)
        super().__init__({1: "U,1", 2: "I,1"}, number=2, responses=responses, **kwargs)

    def _responder(self, knoten: str):
        return lambda _cmd: self.zustand[knoten]

    def write(self, command: str) -> None:
        super().write(command)
        node, _, argument = command.strip().partition(" ")
        key = node.upper()
        if key in self.zustand:
            # Das Geraet meldet Boolesches als 0/1 zurueck, nicht als ON/OFF -
            # ohne diese Umsetzung meldete die Attrappe etwas, das ein echtes
            # Geraet nie schickt, und der Pruefsatz belegte nichts.
            gesetzt = argument.strip().upper()
            self.zustand[key] = {"ON": "1", "OFF": "0"}.get(gesetzt, gesetzt)


def test_voller_zyklus_sichern_verstellen_zurueckschreiben():
    """Das eigentliche Versprechen von M2-4, in einem Pruefsatz."""
    transport = Geraetemodell()

    with schreibfaehig(transport) as wt:
        backup = wt.backup()
        assert backup.computation.averaging.enabled is False
        assert backup.harmonics.thd is ThdFormula.TOTAL
        assert backup.integration.mode is IntegrationMode.NORMAL

        # Jemand verstellt das Geraet - drei Gruppen, drei Wege.
        wt.computation.set_averaging(True, AveragingType.EXPONENT, 32)
        wt.harmonics.set_thd_formula(ThdFormula.FUNDAMENTAL)
        wt.integration.set_mode(IntegrationMode.CONTINUOUS)

        veraendert = wt.backup()
        assert veraendert.computation.averaging.enabled is True
        assert veraendert.harmonics.thd is ThdFormula.FUNDAMENTAL
        assert veraendert.integration.mode is IntegrationMode.CONTINUOUS

        # Und zurueck.
        probleme = wt.restore_backup(backup)

    assert probleme == [], probleme
    assert transport.zustand[":MEASURE:AVERAGING:STATE"] == "0"
    assert transport.zustand[":MEASURE:AVERAGING:COUNT"] == "8"
    assert transport.zustand[":HARMONICS:THD"] == "TOTAL"
    assert transport.zustand[":INTEGRATE:MODE"] == "NORMAL"


def test_zyklus_ueber_die_datei(tmp_path):
    """Derselbe Weg, aber mit dem Umweg ueber die Platte - der Ernstfall."""
    transport = Geraetemodell()
    pfad = tmp_path / "vorher.json"

    with schreibfaehig(transport) as wt:
        wt.backup(pfad)
        wt.harmonics.set_thd_formula(ThdFormula.FUNDAMENTAL)
        assert transport.zustand[":HARMONICS:THD"] == "FUNDAMENTAL"

        probleme = wt.restore_backup(pfad)

    assert probleme == []
    assert transport.zustand[":HARMONICS:THD"] == "TOTAL"


def test_item_tabelle_wird_mitgesichert_und_zurueckgeschrieben():
    transport = Geraetemodell()
    with schreibfaehig(transport) as wt:
        backup = wt.backup()
        assert [it.argument for it in backup.items.items] == ["U,1", "I,1"]

        wt.items.apply(wt.items.build(wt.items.standard_profile()))
        assert transport.number > 2

        wt.restore_backup(backup)

    assert transport.number == 2
    assert transport.items[1] == "U,1"
    assert transport.items[2] == "I,1"


def test_restore_verlangt_allow_changes():
    transport = Geraetemodell()
    with facade(transport) as wt:
        backup = wt.backup()
        with pytest.raises(WTError) as fehler:
            wt.restore_backup(backup)
    assert "allow_changes" in str(fehler.value)


# ---------------------------------------------------------------------------
# Endkontrolle
# ---------------------------------------------------------------------------


def test_endkontrolle_meldet_eine_abweichung():
    """Sie prueft wirklich - sie sagt nicht immer 'in Ordnung'."""
    transport = Geraetemodell()
    with schreibfaehig(transport) as wt:
        backup = wt.backup()
        wt.harmonics.set_thd_formula(ThdFormula.FUNDAMENTAL)

        probleme = backup.verify(harmonics=wt.harmonics)

    assert len(probleme) == 1
    assert "Oberschwingungen" in probleme[0]


def test_endkontrolle_prueft_nur_was_uebergeben_wurde():
    transport = Geraetemodell()
    with facade(transport) as wt:
        backup = wt.backup()
        assert backup.verify() == []


def test_bereichsteil_wird_nicht_geschrieben_sondern_gegengelesen(caplog):
    """Die Ueberschneidung mit dem Input-Snapshot, als Regel festgehalten."""
    transport = Geraetemodell()
    with schreibfaehig(transport) as wt:
        backup = wt.backup()
        with caplog.at_level("INFO"):
            wt.restore_backup(backup)
    assert any("Bereichsteil" in e.getMessage() for e in caplog.records)


# ---------------------------------------------------------------------------
# Inhalt beschreiben
# ---------------------------------------------------------------------------


def test_beschreibung_nennt_geraet_und_inhalt():
    transport = Geraetemodell()
    with facade(transport) as wt:
        text = "\n".join(wt.backup().describe())
    assert "WT3000" in text
    assert "C1B234567" in text
    for baustein in ("Eingangskonfiguration", "Messbereiche", "Item-Tabelle"):
        assert baustein in text


def test_leeres_backup_beschreibt_sich_als_leer():
    text = "\n".join(SessionBackup().describe())
    assert "<leer>" in text


def test_fingerprint_hat_eine_feste_form():
    """Ein Steckbriefteil, der je nach Aufrufer anders aussieht, taugt nicht."""
    daten = device_fingerprint(
        model="760304-40-MV", serial="0", firmware="F5.01", options=("G6", "DT")
    )
    assert daten["model"] == "760304-40-MV"
    assert daten["options"] == ["DT", "G6"]  # sortiert, damit vergleichbar
    assert set(daten) == {
        "identity",
        "manufacturer",
        "model",
        "serial",
        "firmware",
        "options",
        "elements",
        "wiring",
    }


# ---------------------------------------------------------------------------
# Die drei neuen Gruppen serialisieren
# ---------------------------------------------------------------------------


def test_settings_gehen_durch_dict_und_zurueck():
    transport = Geraetemodell()
    with facade(transport) as wt:
        fuer_alle = (wt.integration.capture(), wt.computation.capture(), wt.harmonics.capture())

    for settings in fuer_alle:
        wieder = type(settings).from_dict(settings.to_dict())
        assert wieder == settings


def test_zeitpunkte_ueberleben_die_datei():
    transport = Geraetemodell()
    with facade(transport) as wt:
        settings = wt.integration.capture()
    wieder = type(settings).from_dict(json.loads(json.dumps(settings.to_dict())))
    assert wieder.real_time_start == settings.real_time_start
    assert wieder.state is IntegrationState.RESET


def test_aufzaehlungen_stehen_als_klartext_in_der_datei():
    transport = Geraetemodell()
    with facade(transport) as wt:
        daten = wt.computation.capture().to_dict()
    assert daten["sq_formula"] == SQFormula.TYPE1.value
    assert daten["sync_mode"] == SyncMode.MASTER.value
