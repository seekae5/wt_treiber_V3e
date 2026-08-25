# =============================================================================
# Datei: tests/conftest.py
# Gemeinsame Bausteine der Testsuite.
#
# Die gesamte Suite laeuft OHNE Geraet und ohne tmctl.dll. Wo ein Objekt eine
# WTSession erwartet, tritt FakeSession an ihre Stelle: sie beantwortet Queries
# aus einer Tabelle und merkt sich alles Geschriebene. Damit sind auch die
# Klassen pruefbar, die eine Sitzung nur als Datenquelle benutzen
# (RangeAccess, RangePlan.validate).
# =============================================================================

from __future__ import annotations

import importlib.util
import logging
import re
import sys
from pathlib import Path

import pytest

# Ohne Installation lauffaehig: src/ in den Suchpfad legen. Nach
# 'pip install -e .' ist die Zeile wirkungslos, aber nicht schaedlich.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# Sicherung: kein Geraetezugriff aus der Testsuite
# ---------------------------------------------------------------------------
#
# Die Zusage im Kopf dieser Datei war bisher nur eine Absichtserklaerung. Eine
# Zeit lang lag unter tests/ ein Skript, das eine echte TMCTL-Verbindung
# aufbaute und einen Messbereich schrieb (heute tools/hardware/). Es enthielt
# keine Testfunktion und blieb deshalb folgenlos - eine spaeter ergaenzte
# Testfunktion oder ein Aufruf auf Modulebene haette pytest aber unbemerkt an
# das eingemessene Geraet schreiben lassen.
#
# 'TmctlTransport' ist das einzige Tor, durch das eine echte Verbindung
# entsteht: WT3000.connect() und WT3000.from_config() gehen ebenfalls
# hindurch. Der Konstruktor wird deshalb hier stillgelegt.
#
# Bewusst auf MODULEBENE und nicht als Fixture: conftest.py wird vor dem
# Einsammeln der Testmodule importiert. Nur so greift die Sperre auch bei einem
# Geraeteaufruf auf Modulebene, der schon beim Import liefe - also genau in dem
# Fall, den eine Fixture zu spaet erwischen wuerde.
#
# Nicht betroffen: 'issubclass(TmctlTransport, Transport)' in
# test_fake_transport.py - der Protokollvertrag haengt an write/read/query/
# set_timeout/close, nicht am Konstruktor. Ebenso das monkeypatch auf
# wt3000_device.TmctlTransport in test_device_facade.py: dort wird der Name
# ersetzt, der echte Konstruktor also gar nicht erreicht.
from wt3000_scpi.wt3000_transport import (  # noqa: E402
    FakeTransport,
    TmctlTransport,
    float_block,
)


def _kein_geraetezugriff(self, *args, **kwargs):
    # Die Sperre entsteht durch den Import dieser Datei, nicht durch den
    # Ablageort des aufrufenden Skripts; die Meldung nennt beide Abhilfen.
    raise RuntimeError(
        "TmctlTransport() ist stillgelegt, weil tests/conftest.py importiert "
        "wurde: die Testsuite laeuft ohne Geraet und ohne tmctl.dll.\n"
        "  - In Tests: 'FakeTransport' benutzen "
        "(wt3000_scpi.wt3000_transport).\n"
        "  - In einem Geraeteskript unter tools/hardware/: pruefen, ob eine "
        "Zeile 'from tests...' oder 'import conftest' im Modulkopf steht - "
        "meist von der Entwicklungsumgebung automatisch ergaenzt. Aus tests/ "
        "darf ein Geraeteskript NICHTS importieren.\n"
        "  - Ein Skript, das wirklich mit dem Geraet spricht, gehoert nach "
        "tools/hardware/ und nicht unter tests/."
    )


#: Der echte Konstruktor, bevor er stillgelegt wird.
#
# Nur test_transport_fehlerpfade.py darf diesen Konstruktor fuer die Ladefehler
# betreten. ct.WinDLL wird dort ersetzt; eine Verbindung entsteht nie.
ECHTER_TMCTL_KONSTRUKTOR = TmctlTransport.__init__

TmctlTransport.__init__ = _kein_geraetezugriff


class FakeSession:
    """Minimalersatz fuer WTSession - beantwortet Queries aus einer Tabelle.

    responses: Abbildung Kommando (ohne '?') -> Antwort. Der Zugriff ist
    unabhaengig von Gross-/Kleinschreibung. Fehlt ein Eintrag, wird ein
    KeyError geworfen statt still etwas zu erfinden: ein Test, der eine nicht
    hinterlegte Abfrage ausloest, soll auffallen.
    """

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.responses = {k.upper(): v for k, v in (responses or {}).items()}
        self.written: list[str] = []

    def query(self, command: str) -> str:
        key = command.strip().rstrip("?").upper()
        if key not in self.responses:
            raise KeyError(f"FakeSession hat keine Antwort fuer {command!r}")
        return self.responses[key]

    def write(self, command: str) -> None:
        self.written.append(command)

    def read_error_queue(self) -> list[str]:
        return []

    def assert_no_error(self, context: str = "") -> None:
        return None


# Bereichsantworten des vorliegenden Aufbaus: Elemente 1-3 haengen an externen
# Stromsensoren (10 V), Element 4 direkt (5 A). Genau die Konstellation, an der
# RangeBackup.capture() seine Sensorbehandlung zeigen muss.
SENSOR_ELEMENTS: tuple[int, ...] = (1, 2, 3)


def range_responses() -> dict[str, str]:
    """Antworttabelle fuer die Bereichsknoten aller vier Elemente."""
    table = {
        ":INPUT:WIRING": "V3A3,P1W2",
        ":INPUT:MODULE": "30,30,30,30",
        ":INPUT:INDEPENDENT": "1",
    }
    for element in (1, 2, 3, 4):
        table[f":INPUT:VOLTAGE:RANGE:ELEMENT{element}"] = "1.000E+03"
        table[f":INPUT:VOLTAGE:AUTO:ELEMENT{element}"] = "0"
        table[f":INPUT:CURRENT:AUTO:ELEMENT{element}"] = "0"
        table[f":INPUT:CURRENT:RANGE:ELEMENT{element}"] = (
            "EXTERNAL,10.00E+00" if element in SENSOR_ELEMENTS else "5.00E+00"
        )
    return table


@pytest.fixture
def fake_session() -> FakeSession:
    """Sitzung mit vollstaendiger Bereichs-Antworttabelle."""
    return FakeSession(range_responses())


@pytest.fixture
def access(fake_session: FakeSession):
    """Schreibfaehiger RangeAccess auf der FakeSession, Wiring V3A3,P1W2."""
    from wt3000_scpi.wt3000_rangeio import RangeAccess

    return RangeAccess(
        fake_session,
        allow_changes=True,
        sigma_members={"SIGMA": (1, 2, 3), "SIGMB": (4,)},
    )


def element_settings(**overrides):
    """ElementSettings des Aufbaus, einzelne Felder ueberschreibbar."""
    from wt3000_scpi.wt3000_input import ElementSettings

    base = dict(
        element=1,
        module=30,
        voltage_range=1000.0,
        voltage_auto=False,
        voltage_mode="RMS",
        current_direct=None,
        current_sensor=10.0,
        current_auto=False,
        current_mode="RMS",
        sensor_ratio=1.0,
        line_filter="OFF",
        frequency_filter=False,
        scaling=False,
        vt_ratio=1.0,
        ct_ratio=1.0,
        power_factor=1.0,
        sync_source="EXTERNAL",
    )
    base.update(overrides)
    return ElementSettings(**base)


def input_snapshot(*elements, **overrides):
    """InputSnapshot mit den uebergebenen Elementen."""
    from wt3000_scpi.wt3000_input import InputSnapshot

    base = dict(
        crest_factor=3,
        wiring=("V3A3", "P1W2"),
        independent=True,
        update_rate_s=1.0,
        elements=tuple(elements) or (element_settings(),),
        raw_dump="",
    )
    base.update(overrides)
    return InputSnapshot(**base)


# ---------------------------------------------------------------------------
# Stufen- und Geraeteskripte vollstaendig durchspielen
# ---------------------------------------------------------------------------
#
# Gemeinsame Vorrichtung fuer vollstaendige main()-Laeufe mit FakeTransport.


def geraeteskript(name: str):
    """Ein Skript aus tools/hardware/ als Modul laden.

    Die beiden Geraeteskripte sind keine Paketmodule; ohne diesen Umweg sind
    sie aus der Suite heraus nicht erreichbar ('testpaths = ["tests"]', kein
    tools/__init__.py).

    Geladen wird ueber den DATEIPFAD und ausdruecklich NICHT ueber einen
    sys.path-Eintrag. Ein solcher Eintrag waere genau der Weg, ueber den eine
    automatische Import-Ergaenzung der Entwicklungsumgebung einmal
    'from tests.conftest import ...' in probe_current_range.py geschrieben hat -
    was die Sperre oben ausloeste und das Skript am Geraet unbrauchbar machte
    (siehe dessen Dateikopf). Hier entsteht kein Importweg, der das kann.

    Jeder Aufruf liefert ein FRISCHES Modulobjekt: die Tests setzen darin
    Modulkonstanten um, und ein geteiltes Objekt truege sie weiter.
    """
    pfad = Path(__file__).resolve().parents[1] / "tools" / "hardware" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"geraeteskript_{name}", pfad)
    assert spec is not None and spec.loader is not None, pfad
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture
def stufenlauf(monkeypatch, tmp_path):
    """main() eines Stufen- oder Geraeteskripts gegen FakeTransport fahren.

    Drei Ersetzungen im Modulnamensraum - mehr braucht es nicht:

      TmctlTransport   der einzige Weg zu einer echten Verbindung. Ersetzt wird
                       der Name IM MODUL, nicht global: der Konstruktor ist
                       oben stillgelegt, und daran soll sich auch diese
                       Vorrichtung nicht vorbeimogeln.
      OUTPUT_DIR       sonst landen Protokoll, Backup und Messdaten im
                       Arbeitsbaum - jeder Testlauf hinterliesse Dateien.
      setup_logging    setzt die Handler des Root-Loggers neu und raeumte damit
                       mitten im Testlauf pytests Log-Mitschnitt ab; alles nach
                       dem Aufruf fehlte dann in 'caplog.records'. Nachgestellt
                       und bestaetigt. Geprueft wird meist der Ablauf, nicht die
                       Protokolleinrichtung.

    'logging_stilllegen=False' laesst setup_logging() laufen - gebraucht fuer
    genau die Pruefsaetze, bei denen es auf die REIHENFOLGE ankommt: ob eine
    Meldung vor oder nach der Einrichtung des Protokolls entsteht (Befund
    A-08). Mit stillgelegtem setup_logging waere das nicht pruefbar, weil
    caplog unabhaengig davon mitschneidet - der Pruefsatz waere auch dann gruen,
    wenn die Meldung neben die Protokolldatei fiele. Er muss deshalb die DATEI
    lesen. Die Handler werden danach zurueckgesetzt; ohne das Schliessen des
    FileHandlers bliebe unter Windows ein Handle auf tmp_path offen.

    'use_remote' geht ausdruecklich ueber die Umgebung und nicht ueber die
    'wt3000.json' der Projektwurzel: stuende dort einmal 'use_remote: false',
    liefe ein Test, der die Ruecknahme der Fernsteuerung prueft, still ins
    Leere, statt rot zu werden. Die Umgebung hat in der Aufloesungskette
    Vorrang vor der Datei.
    """
    wurzel = logging.getLogger()
    handler_vorher = list(wurzel.handlers)
    level_vorher = wurzel.level

    def _vorbereiten(
        modul,
        responses: dict,
        *,
        ip: str = "10.0.0.5",
        use_remote: bool = True,
        logging_stilllegen: bool = True,
    ) -> FakeTransport:
        monkeypatch.setenv("WT3000_IP", ip)
        monkeypatch.setenv("WT3000_USE_REMOTE", "1" if use_remote else "0")

        transport = FakeTransport(responses)
        monkeypatch.setattr(modul, "TmctlTransport", lambda _config: transport)
        monkeypatch.setattr(modul, "OUTPUT_DIR", tmp_path)
        if logging_stilllegen:
            monkeypatch.setattr(modul, "setup_logging", lambda _pfad: None)
        return transport

    yield _vorbereiten

    for handler in list(wurzel.handlers):
        if handler not in handler_vorher:
            handler.close()
            wurzel.removeHandler(handler)
    for handler in handler_vorher:
        if handler not in wurzel.handlers:
            wurzel.addHandler(handler)
    wurzel.setLevel(level_vorher)


# ---------------------------------------------------------------------------
# Geraetemodell fuer vollstaendige main()-Laeufe
# ---------------------------------------------------------------------------
#
# Beides stand in test_device_facade.py und wird ab Schritt 7 auch von den
# Stufenskripten gebraucht: Stufe 2, 3 und 4 lesen die Item-Tabelle, schreiben
# sie (3 und 4) und lesen anschliessend Messwerte dagegen. Ein FakeTransport
# mit fester Antworttabelle traegt das nicht - er wuesste nach einem
# ':NUMeric:NORMal:ITEM5 U,2' nichts davon.
#
# 'ItemTableTransport' fuehrt die Tabelle deshalb als ZUSTAND mit. Damit wird
# die Frage pruefbar, um die es bei Stufe 3 und 4 eigentlich geht: steht die
# Item-Tabelle nach main() wieder so da wie vorher?

IDN = "YOKOGAWA,WT3000,C1B234567,F2.11"

# Optionsantwort des Modellgeraets: G6 und CC sind verbaut, FL und DA nicht.
OPT = "G6,B5,DT,C7,C5,CC"

_ITEM_NODE = re.compile(r"^:NUMERIC:NORMAL:ITEM(\d+)$")


def base_responses(
    wiring: str = "V3A3,P1W2",
    modules: str = "30,30,30,30",
    header: str = "0",
    numeric_format: str = "FLOat",
    options: str = OPT,
) -> dict:
    """Antworten, die die Fassade beim Verbinden und Pruefen braucht."""
    table = dict(range_responses())
    table.update(
        {
            "*IDN": IDN,
            # DeviceInfo.read() fragt Identitaet und Optionen ab.
            "*OPT": options,
            ":INPUT:WIRING": wiring,
            ":INPUT:MODULE": modules,
            ":COMMUNICATE:HEADER": header,
            ":NUMERIC:FORMAT": numeric_format,
            ":STATUS:CONDITION": "0",
            ":NUMERIC:HOLD": "0",
            # Zusaetzlich Rate und Metadaten-Abfragen der Stufenskripte.
            ":RATE": "1.000E+00",
            ":COMMUNICATE": "0,0,0",
            ":INPUT": "ELEMENT1,1000V;ELEMENT2,1000V;ELEMENT3,1000V;ELEMENT4,1000V",
            ":INPUT:SCALING": "0,0,0,0",
            ":INPUT:FILTER": "OFF,OFF,OFF,OFF",
            ":INPUT:CFACTOR": "3",
            ":MEASURE": "NORMAL",
        }
    )
    return table


class ItemTableTransport(FakeTransport):
    """FakeTransport, der Schreibzugriffe auf die Item-Tabelle uebernimmt.

    Nur so weit ausgebaut, wie die Item-Tabelle es verlangt: ITEM<n> und
    NUMber werden uebernommen, alles andere bleibt Tabellenantwort.
    """

    MAX_INDEX = 64

    def __init__(self, items: dict[int, str], number: int, **kwargs) -> None:
        self.items = dict(items)
        self.number = number

        responses = base_responses()
        responses[":NUMERIC:NORMAL"] = lambda _cmd: self._table_response()
        for index in range(1, self.MAX_INDEX + 1):
            responses[f":NUMERIC:NORMAL:ITEM{index}"] = self._item_responder(index)
        responses[":NUMERIC:NORMAL:VALUE"] = lambda _cmd: self._value_block()
        responses.update(kwargs.pop("responses", {}))
        super().__init__(responses, **kwargs)

    # -- Geraetemodell ------------------------------------------------------

    def _item_responder(self, index: int):
        return lambda _cmd: self.items.get(index, "NONE")

    def _table_response(self) -> str:
        parts = [str(self.number)]
        parts += [self.items.get(i, "NONE") for i in range(1, self.number + 1)]
        return ";".join(parts)

    def _value_block(self) -> bytes:
        """Ein Messwert je Item - aufsteigend, damit die Zuordnung pruefbar ist."""
        return float_block(float(i) for i in range(1, self.number + 1))

    def write(self, command: str) -> None:
        super().write(command)
        node, _, argument = command.strip().partition(" ")
        key = node.upper()
        match = _ITEM_NODE.match(key)
        if match:
            self.items[int(match.group(1))] = argument.strip()
        elif key == ":NUMERIC:NORMAL:NUMBER":
            self.number = int(argument)


# ---------------------------------------------------------------------------
# Antworttabelle der Integrationsgruppe
# ---------------------------------------------------------------------------
#
# Die Voreinstellungen sind KEINE Erfindung: es sind die Werte, die das reale
# Geraet am 21.08.2026 gemeldet hat (docs/ANALYSE_FEHLENDE_FUNKTIONEN.md,
# Abschnitt 0.3) - einschliesslich der Kurzformen 'RES' und 'NORM', an denen
# ein Treiber scheitert, der nur die Langform kennt.


def integrate_responses(
    mode: str = "NORM",
    state: str = "RES",
    timer: str = "0,0,0",
    acal: str = "0",
    rtime_start: str = "2006,1,1,0,0,0",
    rtime_end: str = "2006,1,1,1,0,0",
) -> dict[str, str]:
    """Alles, was 'IntegrationConfig.capture()' abfragt."""
    return {
        ":INTEGRATE:MODE": mode,
        ":INTEGRATE:STATE": state,
        ":INTEGRATE:TIMER": timer,
        ":INTEGRATE:ACAL": acal,
        ":INTEGRATE:RTIME:START": rtime_start,
        ":INTEGRATE:RTIME:END": rtime_end,
    }


def computation_responses(
    avg_state: str = "0",
    avg_type: str = "EXPONENT",
    avg_count: str = "8",
    freq1: str = "U3",
    freq2: str = "I3",
    eta1: str = "OFF",
    eta2: str = "OFF",
    eta3: str = "OFF",
    eta4: str = "OFF",
    sq: str = "TYPE1",
    sync: str = "MASTER",
) -> dict[str, str]:
    """Alles, was 'ComputationConfig.capture()' abfragt.

    Die Frequenzquellen stehen auf U3/I3 - genau die Einstellung, die der
    Kommentar in 'build_standard_profile()' beschreibt und die dort erklaert,
    warum FU nur fuer Element 3 gefuehrt wird.
    """
    return {
        ":MEASURE:AVERAGING:STATE": avg_state,
        ":MEASURE:AVERAGING:TYPE": avg_type,
        ":MEASURE:AVERAGING:COUNT": avg_count,
        ":MEASURE:FREQUENCY:ITEM1": freq1,
        ":MEASURE:FREQUENCY:ITEM2": freq2,
        ":MEASURE:EFFICIENCY:ETA1": eta1,
        ":MEASURE:EFFICIENCY:ETA2": eta2,
        ":MEASURE:EFFICIENCY:ETA3": eta3,
        ":MEASURE:EFFICIENCY:ETA4": eta4,
        ":MEASURE:SQFORMULA": sq,
        ":MEASURE:SYNCHRONIZE": sync,
    }


def harmonics_responses(
    band: str = "NORMAL",
    order: str = "1,100",
    pll: str = "U1",
    pll_warning: str = "1",
    thd: str = "TOTAL",
    iec_object: str = "ELEMENT1",
    ugrouping: str = "OFF",
    igrouping: str = "OFF",
) -> dict[str, str]:
    """Alles, was 'HarmonicsConfig.capture()' abfragt.

    Die Voreinstellungen sind die des Handbuchbeispiels zu ':HARMonics?'
    (Seite 6-57): FBAND NORMAL, PLLSOURCE U1, ORDER 1,100, THD TOTAL,
    IEC:OBJECT ELEMENT1, beide Gruppierungen OFF, PLLWARNING 1.
    """
    return {
        ":HARMONICS:FBAND": band,
        ":HARMONICS:ORDER": order,
        ":HARMONICS:PLLSOURCE": pll,
        ":HARMONICS:PLLWARNING:STATE": pll_warning,
        ":HARMONICS:THD": thd,
        ":HARMONICS:IEC:OBJECT": iec_object,
        ":HARMONICS:IEC:UGROUPING": ugrouping,
        ":HARMONICS:IEC:IGROUPING": igrouping,
    }


@pytest.fixture
def oberschwingungsantworten() -> dict[str, str]:
    """Antworttabelle der Oberschwingungsgruppe im Ausgangszustand."""
    return harmonics_responses()


@pytest.fixture
def rechenantworten() -> dict[str, str]:
    """Antworttabelle der Rechengruppe im Ausgangszustand."""
    return computation_responses()


@pytest.fixture
def integrationsantworten() -> dict[str, str]:
    """Antworttabelle der Integrationsgruppe im Ausgangszustand."""
    return integrate_responses()


# ---------------------------------------------------------------------------
# Antworttabelle fuer die Eingangskonfiguration (Stufe 5)
# ---------------------------------------------------------------------------


def input_responses(elemente: tuple[int, ...] = (1, 2, 3, 4)) -> dict[str, str]:
    """Alles, was 'InputSnapshot.capture()' abfragt - 17 Knoten je Element.

    Die Knotennamen werden aus den Konstanten von 'wt3000_input' GEBAUT und
    nicht abgeschrieben. Das ist der Unterschied zwischen einer Tabelle, die
    mitwandert, und einer, die beim naechsten Umbau still veraltet: benennt
    jemand '_BASE_SCAL_SFACTOR' um oder aendert den Pfad, faellt das hier auf,
    statt sich in einem KeyError aus FakeTransport zu verstecken.

    Abgebildet ist der vorliegende Aufbau: Elemente 1-3 an externen
    Stromsensoren (10 V), Element 4 direkt (5 A) - dieselbe Konstellation wie
    in range_responses().
    """
    from wt3000_scpi import wt3000_input as wi

    tabelle: dict[str, str] = {
        ":INPUT": "ELEMENT1,1000V;ELEMENT2,1000V;ELEMENT3,1000V;ELEMENT4,1000V",
        ":INPUT:CFACTOR": "3",
        ":INPUT:WIRING": "V3A3,P1W2",
        ":INPUT:INDEPENDENT": "1",
        ":INPUT:MODULE": ",".join("30" if e in elemente else "0" for e in (1, 2, 3, 4)),
        ":RATE": "1.000E+00",
    }

    je_element = {
        wi._BASE_VOLT_RANGE: "1.000E+03",
        wi._BASE_VOLT_AUTO: "0",
        wi._BASE_VOLT_MODE: "RMS",
        wi._BASE_CURR_AUTO: "0",
        wi._BASE_CURR_MODE: "RMS",
        wi._BASE_SRATIO: "1.000E+00",
        wi._BASE_FILTER_LINE: "OFF",
        wi._BASE_FILTER_FREQ: "0",
        wi._BASE_SCAL_STATE: "0",
        wi._BASE_SCAL_VT: "1.000E+00",
        wi._BASE_SCAL_CT: "1.000E+00",
        wi._BASE_SCAL_SFACTOR: "1.000E+00",
        wi._BASE_SYNC: "EXTERNAL",
    }

    for element in elemente:
        for basis, wert in je_element.items():
            tabelle[wi._node(basis, element).upper()] = wert
        # Elemente 1-3 haengen am Sensoreingang, Element 4 direkt.
        tabelle[wi._node(wi._BASE_CURR_RANGE, element).upper()] = (
            "EXTERNAL,10.00E+00" if element in SENSOR_ELEMENTS else "5.00E+00"
        )

    return tabelle


@pytest.fixture
def eingangsantworten() -> dict[str, str]:
    """Antworttabelle des vorliegenden Aufbaus fuer Stufe 5."""
    return input_responses()
