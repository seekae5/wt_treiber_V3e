# =============================================================================
# Datei: tests/test_probe_capabilities.py
# Maschinelle Pruefung von tools/hardware/probe_capabilities.py.
#
# Das Skript darf an einem eingemessenen Geraet kein Schreibkommando senden;
# diese Voraussetzung fuer unbeaufsichtigte Laeufe wird hier durchgesetzt.
#
# Der zweite Schwerpunkt: eine nicht verbaute Option ist fuer dieses Skript
# ein ERGEBNIS und kein Fehler. Ein Query auf eine fehlende Gruppe laeuft am
# Geraet in den Timeout; das Skript muss ihn abraeumen und weiterlaufen, statt
# abzubrechen. 'fail_commands' des FakeTransport bildet genau diesen Timeout
# nach.
#
# WICHTIG - warum diese Datei das Skript ueber den DATEIPFAD laedt: siehe
# conftest.geraeteskript() und den Kopf von test_probe_range_tools.py.
# =============================================================================

from __future__ import annotations

import pytest

from conftest import geraeteskript

#: '*IDN?'-Antwort des Handbuchbeispiels (6-114). Kein '-MV' im Modellcode -
#: die Motorfrage muss sich hier also am '*OPT?'-Code MTR entscheiden.
IDN = "YOKOGAWA,WT3004E-2A0-30A4,0,F6.01"

#: '*OPT?'-Antwort des Handbuchbeispiels (6-115). Enthaelt MTR, aber KEIN CC -
#: damit sind beide interessanten Faelle in einer Antwort: eine vorhandene und
#: eine fehlende optionsgebundene Gruppe.
OPT = "G6,B5,FQ,DA,V1,C2,C7,C5,FL,MTR"

#: Kommandos der :CBCycle-Gruppe. Ohne /CC antwortet das Geraet darauf nicht.
CBCYCLE = (
    ":CBCycle:SYNChronize:SOURce?",
    ":CBCycle:SYNChronize:SLOPe?",
    ":CBCycle:TRIGger:MODE?",
    ":CBCycle:TRIGger:SOURce?",
    ":CBCycle:TRIGger:LEVel?",
    ":CBCycle:TIMEout?",
    ":CBCycle:STATe?",
)


@pytest.fixture
def probe():
    """Frisches Modulobjekt - die Pruefsaetze setzen Modulkonstanten um."""
    return geraeteskript("probe_capabilities")


def antworten(**abweichungen: object) -> dict:
    """Antworttabelle fuer einen vollstaendigen Lauf.

    FakeTransport vergleicht ohne '?' und in Grossschrift (siehe dessen
    '_key()'), die Schluessel stehen deshalb so da.
    """
    tabelle: dict[str, object] = {
        ":COMMUNICATE:HEADER": "0",
        "*IDN": IDN,
        "*OPT": OPT,
        ":COMMUNICATE:LOCKOUT": "0",
        ":SYSTEM:KLOCK": "0",
        ":SYSTEM:SLOCK": "0",
        ":INTEGRATE:MODE": "NORMAL",
        ":INTEGRATE:STATE": "RESET",
        ":INTEGRATE:TIMER": "1,0,0",
        # Zweimal derselbe Wert: RTIMe ist die Start-/Stoppzeit des
        # Echtzeitmodus und zaehlt nicht herunter - der Fall, den Frage 4
        # belegen soll.
        ":INTEGRATE:RTIME": "2005,1,1,0,0,0;2005,1,1,1,0,0",
        ":RATE": "500.0E-03",
        ":STATUS:CONDITION": "0",
        # Handbuchbeispiel 6-81: die Gruppe antwortet, wenn sie ansprechbar
        # ist. Passend zur Basisantwort oben (MTR in '*OPT?' vorhanden).
        ":MOTOR:PM": 'SCALING 1.0000;UNIT "W"',
    }
    tabelle.update(abweichungen)
    return tabelle


def lauf(
    probe,
    stufenlauf,
    *,
    responses: dict | None = None,
    schweigt: tuple[str, ...] = CBCYCLE,
    error_queue: list[str] | None = None,
    **kwargs,
):
    """main() gegen den FakeTransport fahren. Liefert (rueckgabe, transport).

    'schweigt' sind die Kommandos, auf die das Geraetemodell NICHT antwortet -
    der Timeout einer nicht verbauten Option. Voreingestellt ist die
    :CBCycle-Gruppe, denn die '*OPT?'-Antwort oben meldet kein /CC; ein Modell,
    das die Gruppe trotzdem beantwortet, waere kein Modell dieses Geraets.
    Gesetzt wird das NACH stufenlauf(): die Fixture baut den FakeTransport
    selbst und reicht 'fail_commands' nicht durch.

    Die Messdauer von Frage 5 wird auf einen Wimpernschlag gesetzt: gegen
    einen FakeTransport braucht ein Query Mikrosekunden, die vollen 10 s der
    Voreinstellung waeren Millionen Proben. Dasselbe fuer den Abstand der
    beiden RTIMe-Abfragen - hier wird der Vergleich geprueft, nicht das Warten.
    """
    transport = stufenlauf(probe, responses if responses is not None else antworten(), **kwargs)
    # Schluesselform des FakeTransport: ohne '?', ohne Rand, in Grossschrift.
    transport.fail_commands.update(c.strip().rstrip("?").upper() for c in schweigt)
    if error_queue is not None:
        transport.error_queue = list(error_queue)
    probe.UPD_PROBE_SECONDS = 0.02
    probe.RTIME_ABSTAND_S = 0.0
    return probe.main(), transport


def geschrieben(transport) -> list[str]:
    """Alles, was kein Query war - also jeder echte Schreibzugriff."""
    return [c for c in transport.written if not c.strip().endswith("?")]


# ---------------------------------------------------------------------------
# Die zentrale Zusage: nur lesen
# ---------------------------------------------------------------------------


def test_lauf_endet_ohne_fehler(probe, stufenlauf):
    rueckgabe, _ = lauf(probe, stufenlauf)
    assert rueckgabe == 0


def test_kein_einziges_schreibkommando(probe, stufenlauf):
    """Der Dateikopf sagt 'FRAGT NUR AB' - hier steht der Beleg.

    Das Skript setzt das mit 'read_only=True' durch; WTSession lehnt dann jede
    Nachricht ohne '?' ab. Dieser Pruefsatz haelt zusaetzlich fest, dass die
    Sperre nie AUSGELOEST wird: ein Lauf, der bei jedem zweiten Kommando gegen
    sie laeuft, waere formal ebenfalls 'schreibt nichts', aber kaputt.

    Grenze der Zusage, dieselbe wie bei Stufe 5 (Befund A-15):
    ':STATus:ERRor?' LEERT die Fehlerqueue. Ein reiner Lesevorgang veraendert
    also sehr wohl etwas am Geraet - nur eben nichts an der Messkonfiguration.
    """
    _, transport = lauf(probe, stufenlauf)
    assert geschrieben(transport) == []


def test_fernsteuerung_wird_nicht_eingeschaltet(probe, stufenlauf):
    """Kein ':COMMunicate:REMote ON' - auch nicht bei use_remote=True.

    Die Fixture setzt 'WT3000_USE_REMOTE=1'. Die beiden Schreibproben
    schalten die Fernsteuerung daraufhin ein; dieses Skript darf es nicht,
    weil REMote ON selbst ein Schreibkommando ist und das Bedienfeld ohne Not
    sperren wuerde.
    """
    _, transport = lauf(probe, stufenlauf, use_remote=True)
    assert not any("REMOTE" in c.upper() for c in transport.written)


# ---------------------------------------------------------------------------
# Frage 1 - *IDN? und *OPT?
# ---------------------------------------------------------------------------


def test_beide_kernabfragen_werden_gesendet(probe, stufenlauf):
    """Der Anlass des Skripts: '*OPT?' UND '*IDN?'."""
    _, transport = lauf(probe, stufenlauf)
    gesendet = [c.strip().upper() for c in transport.written]
    assert "*IDN?" in gesendet
    assert "*OPT?" in gesendet


def test_opt_kommt_nach_idn(probe, stufenlauf):
    """Handbuch 6-115: '*OPT?' muss der letzte Query der Nachricht sein.

    WTSession sendet ohnehin einen Query je Nachricht, die Regel ist also
    durch die Bauart eingehalten. Die Reihenfolge haelt das Skript aber auch
    dann richtig, wenn jemand spaeter buendelt - und genau das soll nicht
    unbemerkt umgedreht werden.
    """
    _, transport = lauf(probe, stufenlauf)
    gesendet = [c.strip().upper() for c in transport.written]
    assert gesendet.index("*IDN?") < gesendet.index("*OPT?")


def test_optionstabelle_trennt_vorhanden_von_fehlend(probe, stufenlauf, caplog):
    """Aus '*OPT?' muss eine Aussage je Kommandogruppe werden.

    Die Antwort enthaelt G6 und MTR, aber kein CC. Genau diese Unterscheidung
    ist der Zweck von Frage 1 - eine blosse Wiedergabe der Rohantwort haette
    das Raetselraten aus Abschnitt 5 nicht beendet.
    """
    with caplog.at_level("INFO"):
        lauf(probe, stufenlauf)
    zeilen = [r.getMessage() for r in caplog.records]

    assert any("G6" in z and "VORHANDEN" in z for z in zeilen)
    assert any("MTR" in z and "VORHANDEN" in z for z in zeilen)
    assert any("CC" in z and "fehlt" in z for z in zeilen)


def test_motorbefund_meldet_den_widerspruch(probe, stufenlauf, caplog):
    """Modellcode ohne '-MV', '*OPT?' aber mit MTR.

    Die Analyse (Rang 8) haelt die Motorvariante fuer NUR ueber '*IDN?'
    erkennbar, das Handbuch nennt MTR als '*OPT?'-Code. Widersprechen sich die
    beiden Indizien, muss das Protokoll das sagen und darf sich nicht still
    fuer eines der beiden entscheiden - der direkte Query fällt danach das
    eigentliche Urteil.
    """
    with caplog.at_level("INFO"):
        lauf(probe, stufenlauf)
    zeilen = [r.getMessage() for r in caplog.records]
    assert any("widersprechen sich" in z for z in zeilen)
    assert any(":MOTor:PM?" in z and "ANSPRECHBAR" in z for z in zeilen)


def test_motor_direkter_query_wird_immer_gesendet(probe, stufenlauf, caplog):
    """':MOTor:PM?' faellt das Urteil unabhaengig davon, ob die Indizien
    sich einig sind - nicht nur im Widerspruchsfall.
    """
    eindeutig = antworten(**{"*IDN": "YOKOGAWA,WT3004E-2A0-30A4,0,F6.01"})
    with caplog.at_level("INFO"):
        _, transport = lauf(probe, stufenlauf, responses=eindeutig)
    gesendet = [c.strip().upper() for c in transport.written]
    assert ":MOTOR:PM?" in gesendet


def test_motor_ansprechbar_trotz_fehlendem_mtr_code(probe, stufenlauf, caplog):
    """Genau der Fall aus dem realen Gerätecheck vom 21.08.2026:

    Modellcode traegt '-MV', '*OPT?' meldet aber kein MTR. Antwortet
    ':MOTor:PM?' trotzdem, ist die Gruppe ansprechbar - und das Protokoll muss
    einordnen, dass der '*OPT?'-Code hier NICHT der zuverlaessige Indikator
    war, sondern der Modellcode.
    """
    real_geraet = antworten(
        **{
            "*IDN": "YOKOGAWA,760304-40-MV,0,F5.01",
            "*OPT": "G6,B5,DT,C7,C5,CC",  # kein MTR, wie am realen Geraet
            ":MOTOR:PM": 'SCALING 1.0000;UNIT "W"',
        }
    )
    with caplog.at_level("INFO"):
        lauf(probe, stufenlauf, responses=real_geraet)
    zeilen = [r.getMessage() for r in caplog.records]
    indizien = next(z for z in zeilen if "Motor-Indizien" in z)
    assert "MV vorhanden" in indizien and "kein MTR" in indizien
    assert any(":MOTor:PM?" in z and "ANSPRECHBAR" in z for z in zeilen)
    assert any("MTR ist an" in z and "kein zuverlaessiger Indikator" in z for z in zeilen)


def test_motor_nicht_ansprechbar_trotz_mv_modellcode(probe, stufenlauf, caplog):
    """Umgekehrter Fall: '-MV' im Modell, aber ':MOTor:PM?' antwortet nicht.

    Dann war umgekehrt der Modellcode kein zuverlaessiger Indikator - auch
    das muss das Protokoll ausdruecklich sagen, nicht nur den Timeout melden.
    """
    with caplog.at_level("INFO"):
        lauf(
            probe,
            stufenlauf,
            responses=antworten(**{"*IDN": "YOKOGAWA,760304-40-MV,0,F5.01"}),
            schweigt=CBCYCLE + (":MOTor:PM?",),
        )
    zeilen = [r.getMessage() for r in caplog.records]
    assert any(":MOTor:PM?" in z and "NICHT ansprechbar" in z for z in zeilen)
    assert any(
        "Modellcode allein war hier kein zuverlaessiger Indikator" in z for z in zeilen
    )


def test_ohne_optionen_bleibt_die_menge_leer(probe, stufenlauf, caplog):
    """'*OPT? -> 0' heisst laut Handbuch: keine einzige Option verbaut.

    Die '0' darf nicht als Optionscode durchgehen - sonst stuende sie in der
    Auswertung als vorhandene Option.
    """
    with caplog.at_level("INFO"):
        lauf(probe, stufenlauf, responses=antworten(**{"*OPT": "0"}))
    zeilen = [r.getMessage() for r in caplog.records]
    assert any("Erkannte Optionscodes: <keine>" in z for z in zeilen)


# ---------------------------------------------------------------------------
# Fehlende Gruppen sind ein Ergebnis, kein Abbruchgrund
# ---------------------------------------------------------------------------


def test_fehlende_gruppe_bricht_den_lauf_nicht_ab(probe, stufenlauf):
    """Ohne /CC antwortet das Geraet auf :CBCycle nicht - der Lauf geht weiter.

    'fail_commands' bildet den Timeout nach. Wuerde das Skript hier
    abbrechen, blieben die Fragen 4 und 5 unbeantwortet, obwohl sie mit
    :CBCycle nichts zu tun haben.
    """
    rueckgabe, transport = lauf(probe, stufenlauf)
    assert rueckgabe == 0
    # Die spaeteren Abschnitte wurden trotzdem erreicht.
    gesendet = [c.strip().upper() for c in transport.written]
    assert ":INTEGRATE:STATE?" in gesendet
    assert ":RATE?" in gesendet


def test_fehlgeschlagener_query_raeumt_die_fehlerqueue_ab(probe, stufenlauf, caplog):
    """Nach einem Timeout muss der Eintrag aus der Queue geholt werden.

    Sonst steht er am Ende des Laufs noch drin und faellt der naechsten
    Sitzung zur Last - das Skript meldete dann einen Fehler, den es selbst
    ausgeloest hat.
    """
    with caplog.at_level("INFO"):
        lauf(probe, stufenlauf, error_queue=['113,"Undefined header"'])
    zeilen = [r.getMessage() for r in caplog.records]
    assert any("Undefined header" in z for z in zeilen)
    assert any("Fehlerqueue am Ende leer" in z for z in zeilen)


def test_vorhandene_gruppe_wird_ausgewertet(probe, stufenlauf, caplog):
    """Mit /CC muss aus den :CBCycle-Antworten eine Aussage werden.

    Frage 3 will wissen, ob eine EXTERNE Beschaltung noetig ist. Sync-Quelle
    U1 und Trigger-Modus AUTO heissen: nein. Die Rohantworten allein sagen das
    nicht - die Uebersetzung ist der Zweck des Abschnitts.
    """
    mit_cc = antworten(
        **{
            "*OPT": "G6,CC,MTR",
            ":CBCYCLE:SYNCHRONIZE:SOURCE": "U1",
            ":CBCYCLE:SYNCHRONIZE:SLOPE": "RISE",
            ":CBCYCLE:TRIGGER:MODE": "AUTO",
            ":CBCYCLE:TRIGGER:SOURCE": "U1",
            ":CBCYCLE:TRIGGER:LEVEL": "0.0",
            ":CBCYCLE:TIMEOUT": "10",
            ":CBCYCLE:STATE": "RESET",
        }
    )
    with caplog.at_level("INFO"):
        rueckgabe, _ = lauf(probe, stufenlauf, responses=mit_cc, schweigt=())
    assert rueckgabe == 0
    zeilen = [r.getMessage() for r in caplog.records]
    assert any("interner Messkanal genuegt" in z for z in zeilen)
    assert any("laeuft frei (AUTO)" in z for z in zeilen)


# ---------------------------------------------------------------------------
# Frage 4 - RTIMe ist keine Restzeit
# ---------------------------------------------------------------------------


def test_rtime_unveraendert_widerlegt_die_restzeitannahme(probe, stufenlauf, caplog):
    with caplog.at_level("INFO"):
        lauf(probe, stufenlauf)
    zeilen = [r.getMessage() for r in caplog.records]
    assert any("kein Restzeitzaehler" in z for z in zeilen)


# ---------------------------------------------------------------------------
# Frage 5 - UPD-Bit
# ---------------------------------------------------------------------------


def test_ohne_flanke_lautet_der_befund_nicht_ausreichend(probe, stufenlauf, caplog):
    """Condition-Register bleibt 0 - das UPD-Bit wird nie getroffen.

    Das ist der Fall, in dem Frage 5 mit "Polling allein traegt M3-3 nicht"
    zu beantworten ist. Er darf nicht als "keine Aussage moeglich"
    durchgehen: das Ausbleiben der Flanke IST die Aussage.
    """
    with caplog.at_level("INFO"):
        lauf(probe, stufenlauf)
    zeilen = [r.getMessage() for r in caplog.records]
    assert any("keine einzige 1->0-Flanke" in z for z in zeilen)


def test_flanken_werden_gezaehlt_und_bewertet(probe, stufenlauf, caplog):
    """Wechselndes UPD-Bit - die Flankenauswertung muss greifen.

    Der FakeTransport gibt bei jedem zweiten Aufruf UPD=1 zurueck. Die
    Abstaende sind dann winzig (kein echter Bus dazwischen) und liegen damit
    innerhalb der Toleranz zur Update-Rate; geprueft wird hier die Rechnung,
    nicht das Zeitverhalten eines echten Geraets.
    """
    zaehler = {"n": 0}

    def wechselnd(_cmd: str) -> str:
        zaehler["n"] += 1
        return "1" if zaehler["n"] % 2 else "0"

    with caplog.at_level("INFO"):
        lauf(probe, stufenlauf, responses=antworten(**{":STATUS:CONDITION": wechselnd}))
    zeilen = [r.getMessage() for r in caplog.records]
    assert any("Flanken, Abstand min" in z for z in zeilen)
    assert any("UPD-Bit traegt den Takt" in z for z in zeilen)


def test_upd_wird_ueber_condition_gemessen_nicht_ueber_eesr(probe, stufenlauf):
    """':STATus:EESR?' LOESCHT das Register beim Lesen.

    Das Skript begruendet im Docstring von frage_5, warum es stattdessen das
    Condition-Register pollt. Diese Entscheidung soll nicht unbemerkt
    umgedreht werden - ein Messskript, das nebenbei ein Register leert, ist
    kein reines Messskript mehr.
    """
    _, transport = lauf(probe, stufenlauf)
    gesendet = [c.strip().upper() for c in transport.written]
    assert ":STATUS:CONDITION?" in gesendet
    assert not any("EESR" in c for c in gesendet)
