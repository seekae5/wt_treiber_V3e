# =============================================================================
# Datei: wt3000_deviceconfig.py
# NEU (ROADMAP M2-1 / M3-2, Rang 1 aus docs/ANALYSE_FEHLENDE_FUNKTIONEN.md):
# Layer 2 - Geraetegruppen ausserhalb von ':INPut' und ':NUMeric'.
#
# WARUM DIESES MODUL UND NICHT EIN EIGENES 'wt3000_integrate.py'
# --------------------------------------------------------------
# Die Frage war schon gestellt und beantwortet, bevor hier eine Zeile stand:
# der Klassenkopf von 'MeasureControl' (wt3000_device.py) haelt fest, dass
# ':INTEGrate' in der ROADMAP an ZWEI Stellen steht - unter M2-1 als Gruppe
# eines neuen Fachmoduls 'wt3000_deviceconfig.py' und unter M3-2 als Ablauf -
# und warnt: "Wird M3-2 vorab als eigenes Modul gebaut, entsteht genau die
# vierte Kopie derselben Parser, die M2-5 verhindern soll." Der dortige
# Vorschlag lautet: "die Knotenebene (MODE, TIMer, RTIMe, STATe?) gehoert nach
# unten in die Konfigurationsschicht". Genau das ist diese Datei; sie traegt
# deshalb den in ROADMAP Abschnitt 3 vorgesehenen Namen und nicht einen
# eigenen. Averaging, Frequenzmessquelle und die uebrigen Gruppen aus M2-1
# kommen hier hinein, nicht in ein drittes Modul.
#
# Kein einziger Parser ist dafuer neu geschrieben worden: Kopfentfernung,
# NR1/NR3, Boolean, NRf-Formatierung und die Aufzaehlungsregel kommen alle aus
# 'wt3000_common'. Die Aufzaehlungsregel lag bis zum 21.08.2026 in
# 'wt3000_input' und ist fuer dieses Modul eine Schicht tiefer gezogen worden
# (Geschwisterimporte verbietet LAYERS in tests/test_package_layout.py) - ein
# Stueck M2-5, nebenbei mit erledigt.
#
# WAS ':INTEGrate' IST
# --------------------
# Die Integrationsfunktion des WT3000: das Geraet summiert Leistung zu Energie
# (Wh) und Strom zu Ladung (Ah) auf. Ohne diese Gruppe kann der Treiber nur
# Momentanwerte lesen - laut Analyse 2.1 "die groesste funktionale Luecke
# gegenueber einem vollstaendigen Leistungsmessgeraetetreiber". Die Gruppe
# braucht KEINE Geraeteoption (Analyse 0.1, Gegenbeispiel); an diesem Geraet
# ist sie am 21.08.2026 lesend belegt worden.
#
# GRUNDREGEL DIESES MODULS - dieselbe wie in wt3000_input
# -------------------------------------------------------
# Das Geraet ist metrologisch eingemessen. Jeder Schreibzugriff ist doppelt
# gesperrt: 'allow_changes=False' (Voreinstellung) und zusaetzlich eine
# Gruppensperre. Geschuetzt ist per Voreinstellung genau eine Gruppe: RESET.
# ':INTEGrate:RESet' verwirft den aufgelaufenen Zaehlerstand unwiderruflich -
# also den Messwert selbst, nicht nur eine Einstellung. Eine versehentlich
# geloeschte Stundenmessung ist nicht wiederherstellbar, deshalb steht davor
# dieselbe ausdrueckliche Freigabe wie vor einem Bereichswechsel:
#
#     with cfg.unlocked(GROUP_RESET):
#         cfg.reset()
#
# WAS AM GERAET BELEGT IST UND WAS NICHT
# --------------------------------------
# Belegt (probe_capabilities.py, 21.08.2026, nur lesend):
#   ':INTEGrate:MODE?'  -> 'NORM'   (Kurzform! siehe canonical_enum_token)
#   ':INTEGrate:STATe?' -> 'RES'    (Kurzform)
#   ':INTEGrate:TIMer?' -> '0,0,0'
#   ':INTEGrate:RTIMe?' -> '2006,1,1,0,0,0;2006,1,1,1,0,0'
#
# NICHT belegt, weil Schreibkommandos: STARt, STOP, RESet, jedes Setzen. Sie
# sind hier nach Handbuch (6-74/6-75) gebaut und gegen FakeTransport geprueft;
# die Geraeteabnahme steht aus und haengt an ROADMAP M0-3 (nimmt das Geraet
# Set-Kommandos ueber Ethernet ohne ':COMMunicate:REMote ON' an?).
#
# WIDERLEGT und deshalb hier bewusst NICHT gebaut: ':INTEGrate:RTIMe?' als
# Restzeitanzeige. Zwei Abfragen im Abstand von 2 s lieferten denselben Wert -
# RTIMe ist das Wanduhrpaar des Echtzeitmodus, kein Zaehler. Der Fortschritt
# kommt aus 'remaining_seconds()': eingestellte Dauer minus verstrichene Zeit,
# und die verstrichene Zeit ist das NUMeric-Item TIME. Siehe dort.
# =============================================================================

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Iterator

from .wt3000_common import (
    canonical_enum_token,
    enum_match,
    parse_boolean,
    strip_response_header,
)
from .wt3000_core import WTError, WTSession

_log = logging.getLogger("wt3000.deviceconfig")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConfigLocked(WTError):
    """Ein Schreibzugriff wurde von der Sicherung dieses Moduls abgewiesen.

    Traegt denselben Namen wie die Klasse in wt3000_input und ist bewusst
    NICHT dieselbe: ein Geschwisterimport ist auf dieser Schicht nicht
    erlaubt. Beide erben von WTError, ein Aufrufer faengt also mit
    'except WTError' oder mit dem Namen aus dem Modul, das er benutzt.
    """


class IntegrationStateError(WTError):
    """Der verlangte Uebergang passt nicht zum aktuellen Integrationszustand."""


# ---------------------------------------------------------------------------
# Aufzaehlungen
# ---------------------------------------------------------------------------


class IntegrationMode(Enum):
    """Betriebsarten aus ':INTEGrate:MODE' (Handbuch 6-74).

    NORMAL         zaehlt bis Timer-Ablauf oder Stopp und haelt dann an
    CONTINUOUS     zaehlt nach Timer-Ablauf weiter (fortlaufende Messung)
    RNORMAL        wie NORMAL, aber Start/Stopp nach Wanduhr (':RTIMe')
    RCONTINUOUS    wie CONTINUOUS, aber Start/Stopp nach Wanduhr
    """

    NORMAL = "NORMal"
    CONTINUOUS = "CONTinuous"
    RNORMAL = "RNORmal"
    RCONTINUOUS = "RCONtinuous"


class IntegrationState(Enum):
    """Antworten auf ':INTEGrate:STATe?' (Handbuch 6-74).

    RESET    Zaehler steht auf null, kein Lauf
    READY    wartet auf die Startzeit (nur Echtzeitmodus)
    START    Integration laeuft
    STOP     angehalten, Zaehlerstand bleibt erhalten
    ERROR    unnormal beendet (Ueberlauf, Spannungsausfall)
    TIMEUP   durch Ablauf des Integrationstimers beendet
    """

    RESET = "RESET"
    READY = "READY"
    START = "START"
    STOP = "STOP"
    ERROR = "ERROR"
    TIMEUP = "TIMEUP"


MODE_TOKENS: frozenset[str] = frozenset(m.value.upper() for m in IntegrationMode)
STATE_TOKENS: frozenset[str] = frozenset(s.value for s in IntegrationState)

#: Zustaende, in denen kein Lauf mehr aussteht - das Ende einer Messung.
FINISHED_STATES: frozenset[IntegrationState] = frozenset(
    {IntegrationState.STOP, IntegrationState.ERROR, IntegrationState.TIMEUP}
)


# ---------------------------------------------------------------------------
# Gruppen fuer die Schreibsperre
# ---------------------------------------------------------------------------

#: Betriebsart, Timer, Echtzeitfenster, Autokalibrierung - Einstellungen.
GROUP_INTEGRATE: str = "INTEGRATE"
#: Starten und Stoppen - veraendert den Geraetezustand, aber keine Messwerte.
GROUP_RUN: str = "RUN"
#: Zuruecksetzen - verwirft den aufgelaufenen Zaehlerstand unwiderruflich.
GROUP_RESET: str = "RESET"

ALL_GROUPS: frozenset[str] = frozenset({GROUP_INTEGRATE, GROUP_RUN, GROUP_RESET})

#: Per Voreinstellung zusaetzlich gesperrt - Begruendung im Dateikopf.
DEFAULT_PROTECTED: frozenset[str] = frozenset({GROUP_RESET})


# ---------------------------------------------------------------------------
# Knoten
# ---------------------------------------------------------------------------

_NODE_MODE: str = ":INTEGrate:MODE"
_NODE_ACAL: str = ":INTEGrate:ACAL"
_NODE_TIMER: str = ":INTEGrate:TIMer"
_NODE_STATE: str = ":INTEGrate:STATe"
_NODE_RTIME_START: str = ":INTEGrate:RTIMe:STARt"
_NODE_RTIME_END: str = ":INTEGrate:RTIMe:END"

#: Grenzen des Integrationstimers (Handbuch 6-75): 0,0,0 bis 10000,0,0.
TIMER_MAX_HOURS: int = 10000
TIMER_MAX_SECONDS: int = TIMER_MAX_HOURS * 3600

#: Grenzen des Echtzeitfensters (Handbuch 6-74).
RTIME_MIN_YEAR: int = 2001
RTIME_MAX_YEAR: int = 2099


# ---------------------------------------------------------------------------
# Parser der Gruppe
# ---------------------------------------------------------------------------


def parse_timer(response: str) -> int:
    """'1,30,0' -> 5400 Sekunden.

    Das Geraet fuehrt den Timer als Tripel Stunde/Minute/Sekunde. Nach aussen
    ist eine Sekundenzahl handlicher - sie laesst sich rechnen, vergleichen
    und gegen das NUMeric-Item TIME halten, das ebenfalls in Sekunden kommt.
    """
    text = strip_response_header(response)
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3:
        raise WTError(f"Kein Timer-Tripel in der Antwort {response!r}")
    try:
        hours, minutes, seconds = (int(float(p)) for p in parts)
    except ValueError as exc:
        raise WTError(f"Timer-Tripel {text!r} enthaelt keine Zahlen") from exc
    return hours * 3600 + minutes * 60 + seconds


def format_timer(total_seconds: int) -> str:
    """5400 -> '1,30,0'. Die Umkehrung von parse_timer()."""
    if total_seconds < 0:
        raise WTError(f"Integrationsdauer {total_seconds} s ist negativ")
    if total_seconds > TIMER_MAX_SECONDS:
        raise WTError(
            f"Integrationsdauer {total_seconds} s ueberschreitet das Maximum "
            f"von {TIMER_MAX_SECONDS} s ({TIMER_MAX_HOURS} h)"
        )
    hours, rest = divmod(int(total_seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours},{minutes},{seconds}"


def parse_datetime(response: str) -> datetime:
    """'2006,1,1,0,0,0' -> datetime(2006, 1, 1, 0, 0, 0).

    Ohne Zeitzone, und das ist kein Versehen: das Geraet fuehrt eine eigene
    Uhr (':SYSTem:DATE'/':TIME') ohne Zonenangabe. Ihr eine Zone anzudichten,
    waere eine Annahme ueber den Aufstellort. Wer PC- und Geraetezeit
    abgleichen will, tut das ausdruecklich - siehe Analyse 2.8.
    """
    text = strip_response_header(response)
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 6:
        raise WTError(f"Keine Zeitangabe aus sechs Feldern in {response!r}")
    try:
        year, month, day, hour, minute, second = (int(float(p)) for p in parts)
        return datetime(year, month, day, hour, minute, second)
    except ValueError as exc:
        raise WTError(f"Zeitangabe {text!r} ist unzulaessig: {exc}") from exc


def format_datetime(moment: datetime) -> str:
    """datetime(2006, 1, 1) -> '2006,1,1,0,0,0'."""
    if not RTIME_MIN_YEAR <= moment.year <= RTIME_MAX_YEAR:
        raise WTError(
            f"Jahr {moment.year} liegt ausserhalb {RTIME_MIN_YEAR}..{RTIME_MAX_YEAR}"
        )
    return (
        f"{moment.year},{moment.month},{moment.day},"
        f"{moment.hour},{moment.minute},{moment.second}"
    )


def parse_state(response: str) -> IntegrationState:
    """Antwort auf ':INTEGrate:STATe?' auswerten - Kurzform eingeschlossen.

    Das Geraet antwortet mit der Kurzform ('RES' statt 'RESET', am 21.08.2026
    so gemessen). 'canonical_enum_token' bildet das auf die Langform ab, ohne
    dass hier eine zweite Tabelle mit Kurzformen entsteht.
    """
    token = canonical_enum_token(response, STATE_TOKENS)
    try:
        return IntegrationState(token)
    except ValueError as exc:
        raise WTError(
            f"Unbekannter Integrationszustand {token!r} (Antwort {response!r}); "
            f"erwartet: {', '.join(sorted(STATE_TOKENS))}"
        ) from exc


def parse_mode(response: str) -> IntegrationMode:
    """Antwort auf ':INTEGrate:MODE?' auswerten - 'NORM' -> NORMAL."""
    token = canonical_enum_token(response, MODE_TOKENS)
    for mode in IntegrationMode:
        if mode.value.upper() == token:
            return mode
    raise WTError(
        f"Unbekannte Integrationsbetriebsart {token!r} (Antwort {response!r}); "
        f"erwartet: {', '.join(sorted(MODE_TOKENS))}"
    )


# ---------------------------------------------------------------------------
# Momentaufnahme
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntegrationSettings:
    """Alles, was ':INTEGrate' ueber sich preisgibt - in einem Datensatz.

    Gedacht als Sicherungspunkt vor einem Lauf und als Vorlage fuer den
    gemeinsamen 'SessionBackup' aus M2-4: 'restore()' schreibt genau die
    Felder zurueck, die eingestellt werden koennen. 'state' gehoert bewusst
    dazu, ist aber nicht wiederherstellbar - er beschreibt, was das Geraet
    gerade tut, nicht wie es eingestellt ist.
    """

    mode: IntegrationMode
    timer_seconds: int
    auto_calibration: bool
    state: IntegrationState
    real_time_start: datetime | None = None
    real_time_end: datetime | None = None

    def describe(self) -> list[str]:
        """Als Zeilenliste fuer Protokoll und Konsole."""
        lines = [
            f"Integration: {self.state.value}  (Betriebsart {self.mode.value})",
            f"  Timer:     {format_timer(self.timer_seconds)} (h,min,s)"
            + ("  - nicht gesetzt" if self.timer_seconds == 0 else ""),
            f"  Autokal.:  {'ein' if self.auto_calibration else 'aus'}",
        ]
        if self.real_time_start is not None or self.real_time_end is not None:
            lines.append(
                f"  Echtzeit:  {self.real_time_start} bis {self.real_time_end}"
                "  (nur in den R-Betriebsarten wirksam)"
            )
        return lines


# ---------------------------------------------------------------------------
# Die Gruppe als Objekt
# ---------------------------------------------------------------------------


class IntegrationConfig:
    """Lesen und (gesichertes) Steuern der Integrationsfunktion.

    Lesen ist immer erlaubt. Schreiben verlangt 'allow_changes=True' UND eine
    Gruppe, die nicht in 'protected_groups' steht - dieselbe doppelte Sperre
    wie in wt3000_input, mit derselben Begruendung.

    Der uebliche Ablauf einer Wh-Messung, und zugleich das Abnahmekriterium
    von ROADMAP M3-2 ("eine Wh-Messung ueber definierte Dauer sicher
    gestartet, beendet und ausgelesen"):

        with WT3000.connect(read_only=False, allow_changes=True) as wt:
            integ = wt.integration
            with integ.unlocked(GROUP_RESET):
                integ.reset()                      # Zaehler auf null
            integ.set_mode(IntegrationMode.NORMAL)
            integ.set_timer(minutes=15)            # definierte Dauer
            with integ.running():                  # STARt ... STOP im finally
                integ.wait_until_finished()
            werte = wt.measure.read_mapped()       # WH, AH, TIME auslesen

    Zum Auslesen gehoert die passende Item-Tabelle: die Integrationsgroessen
    (WH, WHP, WHM, AH, AHP, AHM, TIME) stehen nicht im Standardprofil.
    'wt3000_measure.build_integration_profile()' liefert sie.
    """

    def __init__(
        self,
        session: WTSession,
        allow_changes: bool = False,
        protected_groups: frozenset[str] = DEFAULT_PROTECTED,
        verify: bool = True,
        check_errors: bool = True,
    ) -> None:
        self._session = session
        self._allow_changes = allow_changes
        self._protected = set(protected_groups)
        self._verify = verify
        self._check_errors = check_errors

    # -- Sperre -------------------------------------------------------------

    @property
    def allow_changes(self) -> bool:
        """True, wenn dieses Objekt ueberhaupt schreiben darf."""
        return self._allow_changes

    @property
    def protected_groups(self) -> frozenset[str]:
        """Aktuell zusaetzlich gesperrte Gruppen."""
        return frozenset(self._protected)

    @contextmanager
    def unlocked(self, *groups: str) -> Iterator["IntegrationConfig"]:
        """Gruppen fuer die Dauer des Blocks freigeben.

        Wortgleich zu 'InputConfig.unlocked()' - wer das eine kennt, kennt das
        andere. Die Freigabe wird protokolliert, weil sie den Unterschied
        zwischen "aus Versehen" und "mit Absicht" ausmacht.
        """
        unknown = {g for g in groups if g not in ALL_GROUPS}
        if unknown:
            raise WTError(f"Unbekannte Gruppe(n): {sorted(unknown)}")

        previous_allow = self._allow_changes
        previous_protected = set(self._protected)
        self._allow_changes = True
        self._protected -= set(groups)
        _log.warning("Schreibzugriff freigegeben fuer: %s", ", ".join(groups))
        try:
            yield self
        finally:
            self._allow_changes = previous_allow
            self._protected = previous_protected
            _log.info("Schreibzugriff wieder gesperrt")

    def _require_writable(self, group: str) -> None:
        """Vor jedem Set-Kommando pruefen, ob geschrieben werden darf."""
        if not self._allow_changes:
            raise ConfigLocked(
                f"Schreibzugriff auf '{group}' abgelehnt: IntegrationConfig wurde "
                "mit allow_changes=False erzeugt. Freigabe ueber unlocked()."
            )
        if group in self._protected:
            raise ConfigLocked(
                f"Gruppe '{group}' ist geschuetzt. Freigabe ausdruecklich ueber: "
                f"with integ.unlocked('{group}'): ..."
            )

    # -- Basisoperationen ---------------------------------------------------

    def _query(self, node: str) -> str:
        """Query absetzen und den Kopf entfernen."""
        return strip_response_header(self._session.query(f"{node}?"))

    def _write(
        self,
        group: str,
        command: str,
        query_node: str | None,
        matches: Callable[[str], bool] | None,
        label: str,
    ) -> None:
        """Set-Kommando senden, zuruecklesen, Fehlerqueue pruefen.

        Derselbe Dreischritt wie in 'InputConfig._write_scalar()'. Fuer die
        drei Aktionen (STARt/STOP/RESet) gibt es keinen Knoten, den man
        zuruecklesen koennte - dort steht 'query_node=None', und die Kontrolle
        macht der Aufrufer ueber 'state()'.
        """
        self._require_writable(group)
        _log.info("SET %s", command)
        self._session.write(command)

        if self._verify and query_node is not None and matches is not None:
            actual = self._query(query_node)
            if not matches(actual):
                raise WTError(f"{label}: Geraet meldet {actual!r} nach '{command}'")
            _log.info("  verifiziert: %s = %s", query_node, actual)

        if self._check_errors:
            self._session.assert_no_error(label)

    # =======================================================================
    # Lesen
    # =======================================================================

    def state(self) -> IntegrationState:
        """Aktueller Integrationszustand (':INTEGrate:STATe?')."""
        return parse_state(self._query(_NODE_STATE))

    def is_running(self) -> bool:
        """True, solange die Integration laeuft."""
        return self.state() is IntegrationState.START

    def mode(self) -> IntegrationMode:
        """Eingestellte Betriebsart (':INTEGrate:MODE?')."""
        return parse_mode(self._query(_NODE_MODE))

    def timer_seconds(self) -> int:
        """Eingestellte Integrationsdauer in Sekunden. 0 = kein Timer."""
        return parse_timer(self._query(_NODE_TIMER))

    def auto_calibration(self) -> bool:
        """Zustand der Autokalibrierung (':INTEGrate:ACAL?')."""
        return parse_boolean(self._query(_NODE_ACAL), ":INTEGrate:ACAL")

    def real_time_window(self) -> tuple[datetime, datetime]:
        """Start- und Stoppzeit des Echtzeitmodus.

        Abgefragt werden die beiden Einzelknoten ':RTIMe:STARt?' und
        ':RTIMe:END?' und nicht das zusammengesetzte ':RTIMe?'. Grund: dessen
        Antwort ist im Handbuch nur MIT eingeschaltetem Kopf abgedruckt
        ('START 2005,...;END 2005,...'), und wie sie bei ':COMMunicate:HEADer
        0' - dem Sollzustand dieses Treibers - aussieht, ist nicht belegt.
        Zwei belegte Abfragen sind besser als eine geratene.
        """
        return (
            parse_datetime(self._query(_NODE_RTIME_START)),
            parse_datetime(self._query(_NODE_RTIME_END)),
        )

    def capture(self, include_real_time: bool = True) -> IntegrationSettings:
        """Vollstaendige Momentaufnahme der Gruppe.

        'include_real_time=False' laesst die beiden Wanduhrknoten aus - sie
        wirken nur in den R-Betriebsarten und kosten sonst zwei Abfragen.
        """
        start: datetime | None = None
        end: datetime | None = None
        if include_real_time:
            start, end = self.real_time_window()
        return IntegrationSettings(
            mode=self.mode(),
            timer_seconds=self.timer_seconds(),
            auto_calibration=self.auto_calibration(),
            state=self.state(),
            real_time_start=start,
            real_time_end=end,
        )

    def log_summary(self) -> None:
        """Momentaufnahme ins Protokoll schreiben."""
        for line in self.capture().describe():
            _log.info("%s", line)

    # =======================================================================
    # Einstellen
    # =======================================================================

    def set_mode(self, mode: IntegrationMode | str) -> None:
        """Betriebsart setzen (':INTEGrate:MODE').

        BEWUSST OHNE ZUSTANDSVORBEHALT: es liegt nahe, das Setzen waehrend
        eines laufenden Zaehlvorgangs vorab abzuweisen - das Bedienfeld
        verhaelt sich so. Belegen laesst sich das aber nicht: das Handbuch
        (6-74) nennt keine solche Bedingung, und am Geraet geprueft ist sie
        nicht. Ein erfundener Vorbehalt wuerde einen Aufruf blockieren, der
        vielleicht zulaessig ist. Weist das Geraet das Kommando ab, kommt der
        Fall ueber die Fehlerqueue heraus - 'assert_no_error()' steht am Ende
        jedes Schreibpfades dieses Moduls.
        """
        token = mode.value if isinstance(mode, IntegrationMode) else str(mode)
        canonical = canonical_enum_token(token, MODE_TOKENS)
        if canonical not in MODE_TOKENS:
            raise WTError(
                f"Betriebsart {mode!r} unzulaessig; erlaubt: "
                f"{', '.join(m.value for m in IntegrationMode)}"
            )
        self._write(
            GROUP_INTEGRATE,
            f"{_NODE_MODE} {token}",
            _NODE_MODE,
            lambda actual: enum_match(token, actual, MODE_TOKENS),
            "Integrationsbetriebsart setzen",
        )

    def set_timer(
        self, hours: int = 0, minutes: int = 0, seconds: int = 0
    ) -> None:
        """Integrationsdauer setzen (':INTEGrate:TIMer').

        Die drei Angaben werden addiert, nicht auf ihre Feldgrenzen geprueft:
        'set_timer(minutes=90)' ist zulaessig und wird als '1,30,0' gesendet.
        Das Geraet selbst laesst in Minuten und Sekunden nur 0..59 zu - ohne
        diese Umrechnung waere die bequemste Angabe die fehleranfaellige.

        0,0,0 heisst laut Handbuch: kein Timer. Der Lauf endet dann nur durch
        'stop()' oder eine Stoerung.

        Zum fehlenden Zustandsvorbehalt siehe 'set_mode()' - dieselbe
        Begruendung.
        """
        total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        parameter = format_timer(total)  # prueft die Grenzen
        self._write(
            GROUP_INTEGRATE,
            f"{_NODE_TIMER} {parameter}",
            _NODE_TIMER,
            lambda actual: parse_timer(actual) == total,
            "Integrationstimer setzen",
        )

    def set_auto_calibration(self, enabled: bool) -> None:
        """Autokalibrierung waehrend der Integration ein- oder ausschalten.

        Wirkt auf die Messung selbst: eingeschaltet unterbricht das Geraet die
        Erfassung regelmaessig fuer den Nullabgleich. Fuer eine lueckenlose
        Energiebilanz ist das unerwuenscht, fuer eine Langzeitmessung mit
        Temperaturgang dagegen erwuenscht - deshalb hier stellbar und nicht
        vorbelegt.
        """
        parameter = "ON" if enabled else "OFF"
        self._write(
            GROUP_INTEGRATE,
            f"{_NODE_ACAL} {parameter}",
            _NODE_ACAL,
            lambda actual: parse_boolean(actual, ":INTEGrate:ACAL") is enabled,
            "Autokalibrierung setzen",
        )

    def set_real_time_window(self, start: datetime, end: datetime) -> None:
        """Start- und Stoppzeit des Echtzeitmodus setzen (':INTEGrate:RTIMe').

        Nur in den Betriebsarten RNORMAL und RCONTINUOUS wirksam. Das Fenster
        wird hier auf Plausibilitaet geprueft (Ende nach Start), bevor es
        gesendet wird - ein umgekehrtes Fenster nimmt das Geraet zwar an, der
        Lauf startet dann aber nie.
        """
        if end <= start:
            raise WTError(
                f"Echtzeitfenster: Ende {end} liegt nicht nach dem Start {start}"
            )
        for node, moment, label in (
            (_NODE_RTIME_START, start, "Echtzeit-Startzeit setzen"),
            (_NODE_RTIME_END, end, "Echtzeit-Stoppzeit setzen"),
        ):
            parameter = format_datetime(moment)

            def _matches(actual: str, wanted: datetime = moment) -> bool:
                return parse_datetime(actual) == wanted

            self._write(GROUP_INTEGRATE, f"{node} {parameter}", node, _matches, label)

    def restore(self, settings: IntegrationSettings) -> None:
        """Eine Momentaufnahme zurueckschreiben.

        'state' wird ausdruecklich NICHT wiederhergestellt: ob das Geraet
        laeuft, ist kein Einstellwert. Die Vorlage fuer M2-4 (SessionBackup)
        ist damit vollstaendig - alles, was 'capture()' liest und was sich
        setzen laesst, geht hier auch wieder hinein.
        """
        self.set_mode(settings.mode)
        self.set_timer(seconds=settings.timer_seconds)
        self.set_auto_calibration(settings.auto_calibration)
        if settings.real_time_start is not None and settings.real_time_end is not None:
            self.set_real_time_window(settings.real_time_start, settings.real_time_end)

    # =======================================================================
    # Steuern
    # =======================================================================

    def start(self) -> None:
        """Integration starten (':INTEGrate:STARt').

        Zulaessig aus RESET (neuer Lauf), READY (Echtzeitmodus wartet) und
        STOP (angehaltenen Lauf fortsetzen - der Zaehlerstand bleibt erhalten
        und zaehlt weiter).

        Die beiden Vorbehalte unten sind ENTSCHEIDUNGEN DIESES TREIBERS und
        keine Behauptungen ueber das Geraet:

        * aus START heraus waere ein zweiter Start wirkungslos - wer ihn
          aufruft, hat sich in seinem Ablauf vertan, und das soll auffallen
          statt still durchzugehen;
        * aus ERROR oder TIMEUP heraus bliebe unklar, ob der neue Lauf auf dem
          alten Zaehlerstand aufsetzt. Ein ausdrueckliches 'reset()' macht die
          Absicht eindeutig - und kostet den Aufrufer eine Zeile.
        """
        current = self.state()
        if current is IntegrationState.START:
            raise IntegrationStateError(
                "Integration laeuft bereits - start() waere wirkungslos"
            )
        if current in {IntegrationState.ERROR, IntegrationState.TIMEUP}:
            raise IntegrationStateError(
                f"Integration steht auf {current.value}; vor einem neuen Lauf "
                "ist reset() noetig (Freigabe ueber unlocked(GROUP_RESET))."
            )
        self._write(GROUP_RUN, ":INTEGrate:STARt", None, None, "Integration starten")
        _log.info("Integration gestartet")

    def stop(self) -> None:
        """Integration anhalten (':INTEGrate:STOP'). Mehrfachaufruf unschaedlich.

        Absichtlich nachsichtig, im Gegensatz zu 'start()': dieser Aufruf
        steht typischerweise in einem 'finally' (siehe 'running()'), und ein
        Aufraeumpfad, der seinerseits eine Ausnahme wirft, verdeckt die
        eigentliche Ursache. Laeuft nichts, wird nichts gesendet.
        """
        current = self.state()
        if current is not IntegrationState.START:
            _log.info("stop(): Integration steht auf %s - kein Kommando noetig", current.value)
            return
        self._write(GROUP_RUN, ":INTEGrate:STOP", None, None, "Integration stoppen")
        _log.info("Integration gestoppt")

    def reset(self) -> None:
        """Zaehlerstand verwerfen (':INTEGrate:RESet').

        Der unwiderrufliche Schritt: die aufgelaufene Energie ist danach weg.
        Deshalb steht GROUP_RESET per Voreinstellung in 'protected_groups' und
        verlangt eine ausdrueckliche Freigabe.

        Waehrend eines laufenden Zaehlvorgangs wird das Kommando gar nicht
        erst gesendet. Auch das ist eine Entscheidung dieses Treibers und
        keine Aussage darueber, ob das Geraet es annaehme: Messdaten
        wegzuwerfen, waehrend sie entstehen, ist kein Vorgang, den eine
        Bibliothek stillschweigend ausfuehren sollte.
        """
        current = self.state()
        if current is IntegrationState.START:
            raise IntegrationStateError(
                "reset() waehrend eines laufenden Zaehlvorgangs abgelehnt - "
                "erst stop() aufrufen."
            )
        self._write(GROUP_RESET, ":INTEGrate:RESet", None, None, "Integration zuruecksetzen")
        _log.info("Integrationszaehler zurueckgesetzt")

    @contextmanager
    def running(self) -> Iterator["IntegrationConfig"]:
        """Starten, Block ausfuehren, in jedem Fall stoppen.

        Dasselbe Muster wie 'NumericHold' und 'applied_ranges()': der
        Rueckweg steht im 'finally' und laeuft auch bei Strg+C oder einem
        Fehler im Block. Ein Zaehlvorgang, der nach einem Abbruch
        weiterlaeuft, waere sonst der Normalfall - das Geraet zaehlt ohne PC
        munter weiter.
        """
        self.start()
        try:
            yield self
        finally:
            try:
                self.stop()
            except WTError as error:
                # Nicht verschlucken, aber auch nicht die Ursache verdecken:
                # der Block hat womoeglich schon eine Ausnahme im Gepaeck.
                _log.error("Integration konnte nicht gestoppt werden: %s", error)
                raise

    # =======================================================================
    # Fortschritt und Warten
    # =======================================================================

    def remaining_seconds(self, elapsed_seconds: float) -> float | None:
        """Restlaufzeit aus eingestellter Dauer und verstrichener Zeit.

        WARUM NICHT ':INTEGrate:RTIMe?': die naheliegende Annahme, RTIMe sei
        ein Restzeitzaehler, ist am Geraet WIDERLEGT worden (21.08.2026, zwei
        Abfragen im Abstand von 2 s lieferten denselben Wert). RTIMe ist das
        Start-/Stopp-Paar des Echtzeitmodus. Der Fortschritt kommt deshalb aus
        dieser Rechnung.

        'elapsed_seconds' ist das NUMeric-Item TIME - die verstrichene
        Integrationszeit in Sekunden. Es steht im Profil aus
        'build_integration_profile()' und kommt bei ':NUMeric:FORMat FLOat'
        als gewoehnlicher Gleitkommawert (Handbuch: 1 Stunde -> 3600.0).

        Rueckgabe None, wenn kein Timer gesetzt ist (0,0,0): dann gibt es
        keine Restzeit, weil es kein Ende gibt.
        """
        total = self.timer_seconds()
        if total <= 0:
            return None
        return max(0.0, float(total) - float(elapsed_seconds))

    def wait_until_finished(
        self,
        timeout_s: float | None = None,
        poll_interval_s: float = 1.0,
    ) -> IntegrationState:
        """Warten, bis der Lauf endet. Rueckgabe: der erreichte Zustand.

        Beendet heisst STOP, TIMEUP oder ERROR (FINISHED_STATES). Der
        uebliche Fall ist TIMEUP - der Integrationstimer ist abgelaufen.

        Dies ist bewusst ein POLLING-Warten und kein Warten auf ein
        Geraeteereignis. Der Grund steht in Analyse 0.3, Frage 5: das
        naheliegende UPD-Bit des Extended Event Register ist am Geraet
        gemessen worden und trug nicht (0 Treffer in 3556 Proben). Der Weg
        ueber ':STATus:FILTer1'/':STATus:EESE' und Service-Request ist
        ungeprueft und braucht Schreibzugriff auf die Statusregister; bis er
        belegt ist, ist eine Zustandsabfrage im Sekundentakt das ehrlichere
        Mittel. Sie kostet eine Abfrage je Intervall und nichts weiter.

        'timeout_s=None' wartet unbegrenzt - richtig fuer einen Lauf, dessen
        Dauer der Timer bestimmt. Mit gesetztem Timeout kommt bei Ablauf eine
        WTError; der Lauf am Geraet wird dabei NICHT gestoppt, das entscheidet
        der Aufrufer (in 'running()' erledigt es das finally).
        """
        if poll_interval_s < 0:
            raise WTError(f"poll_interval_s={poll_interval_s} ist negativ")

        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            current = self.state()
            if current in FINISHED_STATES:
                _log.info("Integration beendet mit Zustand %s", current.value)
                return current
            if current is IntegrationState.RESET:
                # Kein Lauf angestossen - endloses Warten waere hier ein Fehler
                # im Ablauf des Aufrufers und keine Geduldsfrage.
                raise IntegrationStateError(
                    "wait_until_finished(): Integration steht auf RESET - "
                    "es laeuft nichts, worauf zu warten waere."
                )
            if deadline is not None and time.monotonic() >= deadline:
                raise WTError(
                    f"Integration nicht beendet nach {timeout_s} s "
                    f"(Zustand {current.value}). Der Lauf laeuft am Geraet weiter."
                )
            if poll_interval_s:
                time.sleep(poll_interval_s)
