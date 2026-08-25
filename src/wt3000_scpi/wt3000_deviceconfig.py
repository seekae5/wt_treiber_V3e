# Konfiguration der Gruppen ':INTEGrate', ':MEASure' und ':HARMonics'. Sie
# teilen Parser, Schreibpfad und die doppelte Schreibsperre aus wt3000_input.
# Die Fassade prueft optionsgebundene Gruppen, weil nur sie den Geraetesteckbrief
# kennt.
#
# ':INTEGrate:RESet' loescht einen Messwert unwiderruflich und verlangt daher
# die ausdrueckliche Freigabe von GROUP_RESET. RTIMe ist das Wanduhrpaar des
# Echtzeitmodus, keine Restzeitanzeige; remaining_seconds() verwendet deshalb
# die konfigurierte Dauer und das NUMeric-Item TIME.

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Iterator

from .wt3000_common import (
    DEFAULT_ELEMENTS,
    canonical_enum_token,
    enum_match,
    parse_boolean,
    parse_nr1,
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


def _optional_datetime(text: str | None) -> datetime | None:
    """ISO-Zeichenkette oder None in einen Zeitpunkt wandeln."""
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise WTError(f"Zeitangabe {text!r} im Backup ist nicht lesbar: {exc}") from exc


@dataclass(frozen=True)
class IntegrationSettings:
    """Alles, was ':INTEGrate' ueber sich preisgibt - in einem Datensatz.

    'restore()' schreibt nur einstellbare Felder zurueck. 'state' gehoert zur
    Momentaufnahme, ist aber nicht wiederherstellbar: Er beschreibt den
    laufenden Zustand und keine Einstellung.
    """

    mode: IntegrationMode
    timer_seconds: int
    auto_calibration: bool
    state: IntegrationState
    real_time_start: datetime | None = None
    real_time_end: datetime | None = None

    # -- Serialisieren -----------------------------------------------------
    #
    # Enums gehen als ihr Wert in die Datei ('NORMal', 'RESET'), Zeitpunkte als
    # ISO-Zeichenkette. Beides ist im JSON lesbar - ein Backup, das man nicht
    # mit dem Auge pruefen kann, ist im Fehlerfall wertlos.

    def to_dict(self) -> dict:
        """Serialisierbare Darstellung fuer das Sitzungs-Backup."""
        return {
            "mode": self.mode.value,
            "timer_seconds": self.timer_seconds,
            "auto_calibration": self.auto_calibration,
            "state": self.state.value,
            "real_time_start": (
                None if self.real_time_start is None else self.real_time_start.isoformat()
            ),
            "real_time_end": (
                None if self.real_time_end is None else self.real_time_end.isoformat()
            ),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IntegrationSettings":
        """Gegenstueck zu to_dict()."""
        return cls(
            mode=IntegrationMode(data["mode"]),
            timer_seconds=int(data["timer_seconds"]),
            auto_calibration=bool(data["auto_calibration"]),
            state=IntegrationState(data["state"]),
            real_time_start=_optional_datetime(data.get("real_time_start")),
            real_time_end=_optional_datetime(data.get("real_time_end")),
        )

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

    Ueblicher Ablauf einer zeitlich definierten Wh-Messung:

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
        laeuft, ist kein Einstellwert. Alle von 'capture()' gelesenen und
        einstellbaren Werte werden dagegen zurueckgeschrieben.
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


# ===========================================================================
# Rechengruppe ':MEASure'
# ===========================================================================
#
# Averaging beeinflusst jede Messreihe und muss daher zusammen mit
# Wirkungsgradformeln, Frequenzquellen, SQFormula und Synchronrolle erfassbar
# sein. Benutzerdefinierte Rechenkanäle und weitere MEASure-Untergruppen sind
# nicht Teil dieser API.


class AveragingType(Enum):
    """Mittelungsart aus ':MEASure:AVERaging:TYPE' (Handbuch 6-77).

    EXPONENT  exponentiell gleitend - der Wert im Zaehler ist die
              Daempfungskonstante
    LINEAR    linearer gleitender Mittelwert - der Wert ist die Anzahl der
              gemittelten Messungen
    """

    EXPONENT = "EXPonent"
    LINEAR = "LINear"


class SQFormula(Enum):
    """Formelsatz fuer Schein- und Blindleistung (':MEASure:SQFormula')."""

    TYPE1 = "TYPE1"
    TYPE2 = "TYPE2"
    TYPE3 = "TYPE3"


class SyncMode(Enum):
    """Rolle bei synchronisierter Mehrgeraetemessung (':MEASure:SYNChronize')."""

    MASTER = "MASTer"
    SLAVE = "SLAVe"


AVERAGING_TYPE_TOKENS: frozenset[str] = frozenset(a.value.upper() for a in AveragingType)
SQFORMULA_TOKENS: frozenset[str] = frozenset(s.value for s in SQFormula)
SYNC_TOKENS: frozenset[str] = frozenset(s.value.upper() for s in SyncMode)

#: Zulaessige Werte von ':MEASure:AVERaging:COUNt' - je nach TYPE verschieden.
#
# Das ist der Grund, warum 'set_averaging()' Art und Zahl GEMEINSAM setzt und
# nicht als zwei unabhaengige Setter: 128 ist bei LINear richtig und bei
# EXPonent falsch. Wer beides einzeln setzt, kann durch einen Zwischenzustand
# laufen, den das Geraet ablehnt - und zwar je nach Reihenfolge.
AVERAGING_COUNTS: dict[AveragingType, tuple[int, ...]] = {
    AveragingType.EXPONENT: (2, 4, 8, 16, 32, 64),
    AveragingType.LINEAR: (8, 16, 32, 64, 128, 256),
}

#: Marke fuer "kein Wirkungsgrad berechnen" in ':MEASure:EFFiciency:ETA<x>'.
EFFICIENCY_OFF: str = "OFF"

#: Anzahl der Wirkungsgradgleichungen (eta1..eta4) und der Frequenzmessitems.
EFFICIENCY_EQUATIONS: int = 4
FREQUENCY_ITEMS: int = 2

_NODE_AVG_STATE: str = ":MEASure:AVERaging:STATe"
_NODE_AVG_TYPE: str = ":MEASure:AVERaging:TYPE"
_NODE_AVG_COUNT: str = ":MEASure:AVERaging:COUNt"
_NODE_ETA: str = ":MEASure:EFFiciency:ETA"
_NODE_FREQ_ITEM: str = ":MEASure:FREQuency:ITEM"
_NODE_SQFORMULA: str = ":MEASure:SQFormula"
_NODE_SYNC: str = ":MEASure:SYNChronize"

#: Gruppe fuer die Schreibsperre der Rechenfunktionen.
GROUP_COMPUTATION: str = "COMPUTATION"


@dataclass(frozen=True)
class AveragingSettings:
    """Zustand der Mittelung - die drei Knoten in einem Datensatz."""

    enabled: bool
    type: AveragingType
    count: int

    def to_dict(self) -> dict:
        """Serialisierbare Darstellung."""
        return {"enabled": self.enabled, "type": self.type.value, "count": self.count}

    @classmethod
    def from_dict(cls, data: dict) -> "AveragingSettings":
        """Gegenstueck zu to_dict()."""
        return cls(
            enabled=bool(data["enabled"]),
            type=AveragingType(data["type"]),
            count=int(data["count"]),
        )

    def describe(self) -> str:
        """Eine Zeile, die auch im ausgeschalteten Fall etwas aussagt."""
        if not self.enabled:
            return f"Averaging:   aus (eingestellt: {self.type.value}, {self.count})"
        einheit = "Daempfungskonstante" if self.type is AveragingType.EXPONENT else "Messungen"
        return f"Averaging:   EIN - {self.type.value}, {self.count} ({einheit})"


@dataclass(frozen=True)
class EfficiencyEquation:
    """Eine Wirkungsgradgleichung eta<x> = Zaehler / Nenner.

    Die Schreibweise des Geraets (Handbuch 6-78) hat zwei Eigenheiten, die
    hier abgebildet sind statt sie dem Aufrufer zu ueberlassen:

    * 'OFF' heisst: diese Gleichung wird nicht berechnet. Dann ist
      'denominator' None, und 'enabled' ist False.
    * Der Zaehler darf fehlen; das Geraet setzt ihn dann auf 1 und LAESST IHN
      IN DER ANTWORT AUCH WEG ("The numerator is omitted when the numerator is
      1 in the response to a query"). 'numerator=None' heisst deshalb genau
      das: der Zaehler ist 1, nicht "unbekannt".
    """

    numerator: str | None = None
    denominator: str | None = None

    @property
    def enabled(self) -> bool:
        """False, wenn diese Gleichung auf OFF steht."""
        return self.denominator is not None

    @classmethod
    def off(cls) -> "EfficiencyEquation":
        """Die abgeschaltete Gleichung."""
        return cls(numerator=None, denominator=None)

    def as_parameter(self) -> str:
        """Als Parameter fuer ':MEASure:EFFiciency:ETA<x>'."""
        if not self.enabled:
            return EFFICIENCY_OFF
        if self.numerator is None:
            return str(self.denominator)
        return f"{self.numerator},{self.denominator}"

    def describe(self) -> str:
        if not self.enabled:
            return "aus"
        zaehler = self.numerator if self.numerator is not None else "1"
        return f"{zaehler} / {self.denominator}"

    def to_dict(self) -> dict:
        """Serialisierbare Darstellung."""
        return {"numerator": self.numerator, "denominator": self.denominator}

    @classmethod
    def from_dict(cls, data: dict) -> "EfficiencyEquation":
        """Gegenstueck zu to_dict()."""
        return cls(numerator=data.get("numerator"), denominator=data.get("denominator"))


@dataclass(frozen=True)
class ComputationSettings:
    """Momentaufnahme der Rechengruppe - Sicherungspunkt und Protokollzeile."""

    averaging: AveragingSettings
    frequency_items: tuple[str, ...]
    efficiency: tuple[EfficiencyEquation, ...]
    sq_formula: SQFormula
    sync_mode: SyncMode

    def to_dict(self) -> dict:
        """Serialisierbare Darstellung fuer das Sitzungs-Backup."""
        return {
            "averaging": self.averaging.to_dict(),
            "frequency_items": list(self.frequency_items),
            "efficiency": [eq.to_dict() for eq in self.efficiency],
            "sq_formula": self.sq_formula.value,
            "sync_mode": self.sync_mode.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ComputationSettings":
        """Gegenstueck zu to_dict()."""
        return cls(
            averaging=AveragingSettings.from_dict(data["averaging"]),
            frequency_items=tuple(data["frequency_items"]),
            efficiency=tuple(
                EfficiencyEquation.from_dict(d) for d in data["efficiency"]
            ),
            sq_formula=SQFormula(data["sq_formula"]),
            sync_mode=SyncMode(data["sync_mode"]),
        )

    def describe(self) -> list[str]:
        """Als Zeilenliste fuer Protokoll und Konsole."""
        lines = [self.averaging.describe()]
        for index, quelle in enumerate(self.frequency_items, start=1):
            lines.append(f"  Freq{index}:     {quelle}")
        aktive = [
            f"eta{i}={eq.describe()}"
            for i, eq in enumerate(self.efficiency, start=1)
            if eq.enabled
        ]
        lines.append(
            "  Wirkungsgrad: " + (", ".join(aktive) if aktive else "keine Gleichung aktiv")
        )
        lines.append(f"  S/Q-Formel:  {self.sq_formula.value}    Sync: {self.sync_mode.value}")
        return lines


def parse_efficiency(response: str) -> EfficiencyEquation:
    """Antwort auf ':MEASure:EFFiciency:ETA<x>?' auswerten.

    'PB,PA' -> Zaehler PB, Nenner PA.  'PA' -> Zaehler 1, Nenner PA.
    'OFF'   -> abgeschaltet. Die Regel steht im Klassendocstring.
    """
    text = strip_response_header(response).strip()
    if not text:
        raise WTError(f"Leere Antwort auf eine Wirkungsgradgleichung ({response!r})")
    parts = [teil.strip().upper() for teil in text.split(",")]
    if parts[0] == EFFICIENCY_OFF:
        return EfficiencyEquation.off()
    if len(parts) == 1:
        return EfficiencyEquation(numerator=None, denominator=parts[0])
    if len(parts) == 2:
        return EfficiencyEquation(numerator=parts[0], denominator=parts[1])
    raise WTError(f"Wirkungsgradgleichung {text!r} hat mehr als zwei Glieder")


class ComputationConfig:
    """Lesen und (gesichertes) Einstellen der Rechenfunktionen (':MEASure').

    Dieselbe doppelte Sperre wie bei 'IntegrationConfig' und wt3000_input:
    Lesen immer, Schreiben nur mit 'allow_changes=True'. Eine zusaetzlich
    geschuetzte Gruppe gibt es hier NICHT - anders als ':INTEGrate:RESet'
    verwirft kein Kommando dieser Gruppe Messwerte. Wer schreiben darf, darf
    alles darin.

    Zwei Konstruktorangaben bilden Geraeteeigenschaften ab, die dieses Modul
    nicht selbst erfragen kann (es kennt 'DeviceInfo' nicht - Layer 2):

        elements                bestueckte Elemente, fuer 'P<x>'/'U<x>'/'I<x>'
        advanced_computation    ist '/G6' verbaut? Nur damit ist SQFormula
                                TYPE3 waehlbar (Handbuch 6-80)
        motor                   traegt das Geraet die Motorvariante? Nur dann
                                ist 'PM' als Wirkungsgradglied zulaessig

    Fuer die beiden Faehigkeiten gilt dieselbe Regel wie in 'DeviceInfo':
    None heisst UNBEKANNT und wird nicht zur Ablehnung benutzt - lieber laeuft
    das Kommando ins Geraet und scheitert dort mit dessen eigener Meldung, als
    dass der Treiber eine vorhandene Faehigkeit sperrt. Die Fassade fuellt
    beide aus dem Steckbrief.
    """

    def __init__(
        self,
        session: WTSession,
        allow_changes: bool = False,
        elements: tuple[int, ...] = DEFAULT_ELEMENTS,
        advanced_computation: bool | None = None,
        motor: bool | None = None,
        verify: bool = True,
        check_errors: bool = True,
    ) -> None:
        self._session = session
        self._allow_changes = allow_changes
        self._elements = tuple(elements)
        self._advanced = advanced_computation
        self._motor = motor
        self._verify = verify
        self._check_errors = check_errors

    # -- Sperre -------------------------------------------------------------

    @property
    def allow_changes(self) -> bool:
        """True, wenn dieses Objekt schreiben darf."""
        return self._allow_changes

    @property
    def elements(self) -> tuple[int, ...]:
        """Die bestueckten Elemente, gegen die Parameter geprueft werden."""
        return self._elements

    def _require_writable(self) -> None:
        if not self._allow_changes:
            raise ConfigLocked(
                "Schreibzugriff auf die Rechenfunktionen abgelehnt: "
                "ComputationConfig wurde mit allow_changes=False erzeugt."
            )

    # -- Basisoperationen ---------------------------------------------------

    def _query(self, node: str) -> str:
        return strip_response_header(self._session.query(f"{node}?"))

    def _write(
        self,
        command: str,
        query_node: str,
        matches: Callable[[str], bool],
        label: str,
    ) -> None:
        """Derselbe Dreischritt wie in IntegrationConfig: senden, lesen, pruefen."""
        self._require_writable()
        _log.info("SET %s", command)
        self._session.write(command)

        if self._verify:
            actual = self._query(query_node)
            if not matches(actual):
                raise WTError(f"{label}: Geraet meldet {actual!r} nach '{command}'")
            _log.info("  verifiziert: %s = %s", query_node, actual)

        if self._check_errors:
            self._session.assert_no_error(label)

    # =======================================================================
    # Averaging
    # =======================================================================

    def averaging(self) -> AveragingSettings:
        """Zustand der Mittelung: ein/aus, Art, Zahl."""
        return AveragingSettings(
            enabled=parse_boolean(self._query(_NODE_AVG_STATE), _NODE_AVG_STATE),
            type=self._averaging_type(),
            count=parse_nr1(self._query(_NODE_AVG_COUNT), _NODE_AVG_COUNT),
        )

    def _averaging_type(self) -> AveragingType:
        token = canonical_enum_token(self._query(_NODE_AVG_TYPE), AVERAGING_TYPE_TOKENS)
        for kind in AveragingType:
            if kind.value.upper() == token:
                return kind
        raise WTError(
            f"Unbekannte Mittelungsart {token!r}; erwartet: "
            f"{', '.join(a.value for a in AveragingType)}"
        )

    def set_averaging(
        self,
        enabled: bool,
        type: AveragingType | str | None = None,
        count: int | None = None,
    ) -> None:
        """Mittelung setzen - Art und Zahl GEMEINSAM.

        Warum nicht drei einzelne Setter: die zulaessigen Werte von COUNt
        haengen von TYPE ab (Handbuch 6-76; EXPonent 2..64, LINear 8..256).
        Wer beides getrennt setzt, laeuft je nach Reihenfolge durch einen
        Zwischenzustand, den das Geraet ablehnt - '128' ist bei LINear richtig
        und bei EXPonent falsch. Hier wird das Paar vorher geprueft und dann
        in der Reihenfolge TYPE, COUNt, STATe geschrieben.

        'type' und 'count' duerfen fehlen; dann bleibt der jeweils
        eingestellte Wert stehen und nur der Rest wird angefasst.
        'set_averaging(False)' schaltet also einfach ab.
        """
        # Die Sperre wird hier ausnahmsweise VOR der Wertpruefung abgefragt und
        # nicht erst in '_write()' wie in den uebrigen Settern: fehlt 'type',
        # muss zuerst die eingestellte Art vom Geraet gelesen werden. Auf einem
        # gesperrten Objekt waere das eine Abfrage fuer einen Aufruf, der
        # ohnehin nicht durchgeht.
        self._require_writable()

        ziel_typ = self._averaging_type() if type is None else self._canonical_type(type)
        ziel_zahl = count if count is not None else None

        if ziel_zahl is not None:
            erlaubt = AVERAGING_COUNTS[ziel_typ]
            if ziel_zahl not in erlaubt:
                raise WTError(
                    f"Averaging-Zahl {ziel_zahl} ist bei {ziel_typ.value} unzulaessig; "
                    f"erlaubt: {', '.join(str(w) for w in erlaubt)}"
                )

        if type is not None:
            self._write(
                f"{_NODE_AVG_TYPE} {ziel_typ.value}",
                _NODE_AVG_TYPE,
                lambda actual: enum_match(ziel_typ.value, actual, AVERAGING_TYPE_TOKENS),
                "Mittelungsart setzen",
            )
        if ziel_zahl is not None:
            self._write(
                f"{_NODE_AVG_COUNT} {ziel_zahl}",
                _NODE_AVG_COUNT,
                lambda actual: parse_nr1(actual, _NODE_AVG_COUNT) == ziel_zahl,
                "Averaging-Zahl setzen",
            )
        self._write(
            f"{_NODE_AVG_STATE} {'ON' if enabled else 'OFF'}",
            _NODE_AVG_STATE,
            lambda actual: parse_boolean(actual, _NODE_AVG_STATE) is enabled,
            "Averaging schalten",
        )

    @staticmethod
    def _canonical_type(type: AveragingType | str) -> AveragingType:
        if isinstance(type, AveragingType):
            return type
        token = canonical_enum_token(str(type), AVERAGING_TYPE_TOKENS)
        for kind in AveragingType:
            if kind.value.upper() == token:
                return kind
        raise WTError(
            f"Mittelungsart {type!r} unzulaessig; erlaubt: "
            f"{', '.join(a.value for a in AveragingType)}"
        )

    @contextmanager
    def averaging_disabled(self) -> Iterator["ComputationConfig"]:
        """Mittelung fuer die Dauer des Blocks abschalten und danach zurueck.

        Der Fall, fuer den es gebaut ist: eine Bereichs- oder Einschwingprobe
        mitten in einer Messreihe, bei der eine ueber 64 Zyklen gemittelte
        Anzeige nur stoert. Der Ausgangszustand - ein/aus, Art UND Zahl - wird
        vorher gelesen und im 'finally' vollstaendig zurueckgeschrieben.

        War die Mittelung ohnehin aus, wird nichts geschrieben.
        """
        vorher = self.averaging()
        if not vorher.enabled:
            _log.info("Averaging war bereits aus - kein Kommando noetig")
            yield self
            return

        self.set_averaging(False)
        try:
            yield self
        finally:
            self.set_averaging(vorher.enabled, vorher.type, vorher.count)
            _log.info("Averaging wiederhergestellt: %s", vorher.describe())

    # =======================================================================
    # Frequenzmessquelle
    # =======================================================================

    def frequency_item(self, index: int = 1) -> str:
        """Messquelle von Freq1 bzw. Freq2 - z.B. 'U3' oder 'I1'.

        WICHTIG fuer die Auswertung: das Geraet misst die Frequenz nur an
        diesen ein bis zwei Quellen. Ein NUMeric-Item 'FU1' liefert deshalb
        strukturell NAN, wenn Freq1 auf U3 steht - kein Messfehler, sondern
        eine Folge dieser Einstellung. 'build_standard_profile()' fuehrt FU
        genau deswegen nur fuer ein Element; bis hierher war diese Zuordnung
        ein Kommentar, jetzt ist sie abfragbar.

        Auf Geraeten mit der Frequenz-Zusatzoption '/FQ' ist das Kommando
        laut Handbuch (6-79) UNGUELTIG, weil dort ohnehin alle Elemente
        gleichzeitig gemessen werden. Das eingemessene Geraet hat '/FQ' nicht
        ('*OPT?' -> G6,B5,DT,C7,C5,CC), fuer dieses Geraet gilt die Einschraenkung
        also nicht. Geprueft wird sie hier trotzdem nicht: dieses Modul kennt
        den Steckbrief nicht, und eine falsche Sperre waere schlimmer als eine
        Geraetemeldung.
        """
        self._require_frequency_index(index)
        return strip_response_header(self._query(f"{_NODE_FREQ_ITEM}{index}")).upper()

    def set_frequency_item(self, index: int, source: str) -> None:
        """Messquelle von Freq<index> setzen. Zulaessig: 'U<x>' oder 'I<x>'."""
        self._require_frequency_index(index)
        token = self._canonical_frequency_source(source)
        node = f"{_NODE_FREQ_ITEM}{index}"
        self._write(
            f"{node} {token}",
            node,
            lambda actual: strip_response_header(actual).strip().upper() == token,
            f"Frequenzmessquelle {index} setzen",
        )

    @staticmethod
    def _require_frequency_index(index: int) -> None:
        if index not in range(1, FREQUENCY_ITEMS + 1):
            raise WTError(
                f"Frequenzmessitem {index} gibt es nicht; das Geraet fuehrt "
                f"Freq1 bis Freq{FREQUENCY_ITEMS}."
            )

    def _canonical_frequency_source(self, source: str) -> str:
        token = str(source).strip().upper()
        if len(token) < 2 or token[0] not in {"U", "I"} or not token[1:].isdigit():
            raise WTError(
                f"Frequenzmessquelle {source!r} unzulaessig; erwartet 'U<x>' oder "
                "'I<x>' mit <x> = Elementnummer"
            )
        self._require_element(int(token[1:]), f"Frequenzmessquelle {source!r}")
        return token

    # =======================================================================
    # Wirkungsgrad
    # =======================================================================

    def efficiency(self, index: int = 1) -> EfficiencyEquation:
        """Wirkungsgradgleichung eta<index> lesen (1..4)."""
        self._require_efficiency_index(index)
        return parse_efficiency(self._query(f"{_NODE_ETA}{index}"))

    def set_efficiency(
        self,
        index: int,
        numerator: str | None = None,
        denominator: str | None = None,
    ) -> None:
        """Wirkungsgradgleichung eta<index> = Zaehler / Nenner setzen.

        Beide Glieder sind eines von: 'P<x>' (Element), 'PA' (P Sigma A),
        'PB' (P Sigma B), 'PM' (Motorausgang), 'UDEF1'/'UDEF2'.

        'denominator=None' schaltet die Gleichung ab (sendet 'OFF').
        'numerator=None' bei gesetztem Nenner heisst laut Handbuch: Zaehler
        ist 1 - dann wird nur der Nenner gesendet.
        """
        self._require_efficiency_index(index)
        if denominator is None:
            equation = EfficiencyEquation.off()
        else:
            equation = EfficiencyEquation(
                numerator=None if numerator is None else self._canonical_power_term(numerator),
                denominator=self._canonical_power_term(denominator),
            )
        node = f"{_NODE_ETA}{index}"
        parameter = equation.as_parameter()
        self._write(
            f"{node} {parameter}",
            node,
            lambda actual: parse_efficiency(actual) == equation,
            f"Wirkungsgradgleichung {index} setzen",
        )

    @staticmethod
    def _require_efficiency_index(index: int) -> None:
        if index not in range(1, EFFICIENCY_EQUATIONS + 1):
            raise WTError(
                f"Wirkungsgradgleichung {index} gibt es nicht; das Geraet fuehrt "
                f"eta1 bis eta{EFFICIENCY_EQUATIONS}."
            )

    def _canonical_power_term(self, term: str) -> str:
        """Ein Glied einer Wirkungsgradgleichung pruefen und normieren.

        Die Einschraenkungen stehen im Handbuch (6-78) und sind
        geraeteabhaengig: 'PB' gibt es nur auf Vierelementgeraeten, 'PM' nur
        mit Motorauswertung. Was dieses Modul nicht wissen kann, weist es auch
        nicht ab - siehe Klassendocstring.
        """
        token = str(term).strip().upper()
        if token in {"UDEF1", "UDEF2"}:
            return token
        if token == "PA":
            if len(self._elements) < 2:
                raise WTError("'PA' (P SigmaA) gibt es erst ab zwei bestueckten Elementen")
            return token
        if token == "PB":
            if len(self._elements) < 4:
                raise WTError("'PB' (P SigmaB) gibt es nur auf Vierelementgeraeten")
            return token
        if token == "PM":
            if self._motor is False:
                raise WTError(
                    "'PM' (Motorausgang) verlangt die Motorvariante; dieses "
                    "Geraet meldet sie nicht"
                )
            return token
        if token.startswith("P") and token[1:].isdigit():
            self._require_element(int(token[1:]), f"Wirkungsgradglied {term!r}")
            return token
        raise WTError(
            f"Wirkungsgradglied {term!r} unzulaessig; erlaubt: P<x>, PA, PB, PM, "
            "UDEF1, UDEF2"
        )

    # =======================================================================
    # Formelsatz und Synchronisation
    # =======================================================================

    def sq_formula(self) -> SQFormula:
        """Formelsatz fuer Schein- und Blindleistung."""
        token = canonical_enum_token(self._query(_NODE_SQFORMULA), SQFORMULA_TOKENS)
        try:
            return SQFormula(token)
        except ValueError as exc:
            raise WTError(f"Unbekannter S/Q-Formelsatz {token!r}") from exc

    def set_sq_formula(self, formula: SQFormula | str) -> None:
        """Formelsatz setzen. TYPE3 verlangt die Rechenoption '/G6'."""
        token = formula.value if isinstance(formula, SQFormula) else str(formula).strip().upper()
        try:
            gewaehlt = SQFormula(canonical_enum_token(token, SQFORMULA_TOKENS))
        except ValueError as exc:
            raise WTError(
                f"S/Q-Formelsatz {formula!r} unzulaessig; erlaubt: TYPE1, TYPE2, TYPE3"
            ) from exc
        if gewaehlt is SQFormula.TYPE3 and self._advanced is False:
            raise WTError(
                "S/Q-Formelsatz TYPE3 verlangt die Rechenoption /G6 (Handbuch 6-80); "
                "dieses Geraet meldet sie nicht."
            )
        self._write(
            f"{_NODE_SQFORMULA} {gewaehlt.value}",
            _NODE_SQFORMULA,
            lambda actual: enum_match(gewaehlt.value, actual, SQFORMULA_TOKENS),
            "S/Q-Formelsatz setzen",
        )

    def sync_mode(self) -> SyncMode:
        """Rolle bei synchronisierter Mehrgeraetemessung."""
        token = canonical_enum_token(self._query(_NODE_SYNC), SYNC_TOKENS)
        for mode in SyncMode:
            if mode.value.upper() == token:
                return mode
        raise WTError(f"Unbekannte Synchronisationsrolle {token!r}")

    def set_sync_mode(self, mode: SyncMode | str) -> None:
        """MASTer oder SLAVe. Betrifft nur den Verbund mehrerer Geraete."""
        token = mode.value if isinstance(mode, SyncMode) else str(mode)
        canonical = canonical_enum_token(token, SYNC_TOKENS)
        if canonical not in SYNC_TOKENS:
            raise WTError(f"Synchronisationsrolle {mode!r} unzulaessig; erlaubt: MASTer, SLAVe")
        self._write(
            f"{_NODE_SYNC} {token}",
            _NODE_SYNC,
            lambda actual: enum_match(token, actual, SYNC_TOKENS),
            "Synchronisationsrolle setzen",
        )

    # =======================================================================
    # Momentaufnahme
    # =======================================================================

    def capture(self) -> ComputationSettings:
        """Vollstaendige Momentaufnahme der abgedeckten Stellgroessen."""
        return ComputationSettings(
            averaging=self.averaging(),
            frequency_items=tuple(
                self.frequency_item(i) for i in range(1, FREQUENCY_ITEMS + 1)
            ),
            efficiency=tuple(
                self.efficiency(i) for i in range(1, EFFICIENCY_EQUATIONS + 1)
            ),
            sq_formula=self.sq_formula(),
            sync_mode=self.sync_mode(),
        )

    def restore(self, settings: ComputationSettings) -> None:
        """Eine Momentaufnahme zurueckschreiben."""
        self.set_averaging(
            settings.averaging.enabled, settings.averaging.type, settings.averaging.count
        )
        for index, quelle in enumerate(settings.frequency_items, start=1):
            self.set_frequency_item(index, quelle)
        for index, equation in enumerate(settings.efficiency, start=1):
            self.set_efficiency(index, equation.numerator, equation.denominator)
        self.set_sq_formula(settings.sq_formula)
        self.set_sync_mode(settings.sync_mode)

    def log_summary(self) -> None:
        """Momentaufnahme ins Protokoll schreiben."""
        for line in self.capture().describe():
            _log.info("%s", line)

    # -- gemeinsam ----------------------------------------------------------

    def _require_element(self, element: int, was: str) -> None:
        """Elementnummer gegen die bestueckte Liste halten."""
        if element not in self._elements:
            raise WTError(
                f"{was}: Element {element} ist nicht bestueckt "
                f"(bestueckt: {self._elements})"
            )


# ===========================================================================
# Oberschwingungen ':HARMonics'
# ===========================================================================
#
# Die Gruppe verlangt die Option '/G5' oder '/G6'; die Fassade prueft sie.
# Bei PLL-Quelle SAMPle kann das Geraet ausserhalb des Wide-Band-Modus
# EXTernal zurueckmelden. set_pll_source() akzeptiert diese dokumentierte
# Abweichung. Ein fehlendes PLL-Signal erscheint zusaetzlich als Condition-Bit 7.


class FrequencyBand(Enum):
    """Messbandbreite der Oberschwingungsanalyse (':HARMonics:FBANd')."""

    NORMAL = "NORMal"
    WIDE = "WIDE"


class ThdFormula(Enum):
    """Bezugsgroesse der Klirrfaktorrechnung (':HARMonics:THD').

    TOTAL        bezogen auf den Gesamteffektivwert aller gemessenen Ordnungen
    FUNDAMENTAL  bezogen auf die Grundschwingung
    """

    TOTAL = "TOTal"
    FUNDAMENTAL = "FUNDamental"


class IecGrouping(Enum):
    """Gruppierung der IEC-Oberschwingungsmessung (Handbuch 6-57)."""

    OFF = "OFF"
    TYPE1 = "TYPE1"
    TYPE2 = "TYPE2"


FBAND_TOKENS: frozenset[str] = frozenset(b.value.upper() for b in FrequencyBand)
THD_TOKENS: frozenset[str] = frozenset(f.value.upper() for f in ThdFormula)
GROUPING_TOKENS: frozenset[str] = frozenset(g.value for g in IecGrouping)

#: PLL-Quellen ohne Elementbezug (Handbuch 6-58).
PLL_FIXED_SOURCES: frozenset[str] = frozenset({"EXTERNAL", "SAMPLE"})

#: Grenzen von ':HARMonics:ORDer' (Handbuch 6-57).
ORDER_MIN_CHOICES: tuple[int, ...] = (0, 1)
ORDER_MAX_LIMIT: int = 100

_NODE_FBAND: str = ":HARMonics:FBANd"
_NODE_ORDER: str = ":HARMonics:ORDer"
_NODE_PLLSOURCE: str = ":HARMonics:PLLSource"
_NODE_PLLWARNING: str = ":HARMonics:PLLWarning:STATe"
_NODE_THD: str = ":HARMonics:THD"
_NODE_IEC_OBJECT: str = ":HARMonics:IEC:OBJect"
_NODE_IEC_UGROUPING: str = ":HARMonics:IEC:UGRouping"
_NODE_IEC_IGROUPING: str = ":HARMonics:IEC:IGRouping"

#: Gruppe fuer die Schreibsperre der Oberschwingungsanalyse.
GROUP_HARMONICS: str = "HARMONICS"


@dataclass(frozen=True)
class HarmonicsSettings:
    """Momentaufnahme der Oberschwingungsgruppe."""

    band: FrequencyBand
    order_min: int
    order_max: int
    pll_source: str
    pll_warning: bool
    thd: ThdFormula
    iec_object: str
    iec_voltage_grouping: IecGrouping
    iec_current_grouping: IecGrouping

    def to_dict(self) -> dict:
        """Serialisierbare Darstellung fuer das Sitzungs-Backup."""
        return {
            "band": self.band.value,
            "order_min": self.order_min,
            "order_max": self.order_max,
            "pll_source": self.pll_source,
            "pll_warning": self.pll_warning,
            "thd": self.thd.value,
            "iec_object": self.iec_object,
            "iec_voltage_grouping": self.iec_voltage_grouping.value,
            "iec_current_grouping": self.iec_current_grouping.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HarmonicsSettings":
        """Gegenstueck zu to_dict()."""
        return cls(
            band=FrequencyBand(data["band"]),
            order_min=int(data["order_min"]),
            order_max=int(data["order_max"]),
            pll_source=str(data["pll_source"]),
            pll_warning=bool(data["pll_warning"]),
            thd=ThdFormula(data["thd"]),
            iec_object=str(data["iec_object"]),
            iec_voltage_grouping=IecGrouping(data["iec_voltage_grouping"]),
            iec_current_grouping=IecGrouping(data["iec_current_grouping"]),
        )

    def describe(self) -> list[str]:
        """Als Zeilenliste fuer Protokoll und Konsole."""
        return [
            f"Oberschwingungen: Ordnung {self.order_min}..{self.order_max}, "
            f"Bandbreite {self.band.value}",
            f"  PLL-Quelle:  {self.pll_source}"
            + ("    (Warnung ein)" if self.pll_warning else "    (Warnung aus)"),
            f"  THD-Bezug:   {self.thd.value}",
            f"  IEC:         Objekt {self.iec_object}, "
            f"U-Gruppierung {self.iec_voltage_grouping.value}, "
            f"I-Gruppierung {self.iec_current_grouping.value}",
        ]


def parse_order(response: str) -> tuple[int, int]:
    """Antwort auf ':HARMonics:ORDer?' als (min, max) lesen. '1,100' -> (1, 100)."""
    text = strip_response_header(response)
    parts = [teil.strip() for teil in text.split(",")]
    if len(parts) != 2:
        raise WTError(f"Kein Ordnungspaar in der Antwort {response!r}")
    try:
        return int(float(parts[0])), int(float(parts[1]))
    except ValueError as exc:
        raise WTError(f"Ordnungspaar {text!r} enthaelt keine Zahlen") from exc


class HarmonicsConfig:
    """Lesen und (gesichertes) Einstellen der Oberschwingungsanalyse.

    Dieselbe doppelte Sperre wie bei den anderen Gruppen dieses Moduls; eine
    zusaetzlich geschuetzte Gruppe gibt es nicht, weil kein Kommando hier
    Messwerte verwirft.

    OPTIONSPFLICHT: Die Gruppe antwortet nur mit '/G5' oder '/G6'. Die Fassade
    prueft das beim Zugriff auf 'wt.harmonics' und wirft sonst eine WTError,
    die die fehlende Option benennt - statt eines Timeouts, der wie ein
    Verbindungsabbruch aussieht. Wer die Klasse von Hand baut, hat diese
    Pruefung nicht; das ist gewollt, denn dieses Modul kennt den Steckbrief
    nicht.

    Zum Auslesen gehoert wie bei der Integration ein Messprofil:
    'wt3000_measure.build_harmonics_profile()' liefert die Summengroessen
    (UTHD, ITHD, PTHD) und die Einzelordnungen ueber den <Order>-Parameter der
    Item-Tabelle, den 'ItemSpec' laengst vorsieht.
    """

    def __init__(
        self,
        session: WTSession,
        allow_changes: bool = False,
        elements: tuple[int, ...] = DEFAULT_ELEMENTS,
        sigma_units: tuple[str, ...] = ("SIGMA", "SIGMB"),
        verify: bool = True,
        check_errors: bool = True,
    ) -> None:
        self._session = session
        self._allow_changes = allow_changes
        self._elements = tuple(elements)
        self._sigma_units = tuple(unit.strip().upper() for unit in sigma_units)
        self._verify = verify
        self._check_errors = check_errors

    # -- Sperre und Basisoperationen ---------------------------------------

    @property
    def allow_changes(self) -> bool:
        """True, wenn dieses Objekt schreiben darf."""
        return self._allow_changes

    @property
    def elements(self) -> tuple[int, ...]:
        """Die bestueckten Elemente, gegen die Parameter geprueft werden."""
        return self._elements

    def _require_writable(self) -> None:
        if not self._allow_changes:
            raise ConfigLocked(
                "Schreibzugriff auf die Oberschwingungsanalyse abgelehnt: "
                "HarmonicsConfig wurde mit allow_changes=False erzeugt."
            )

    def _query(self, node: str) -> str:
        return strip_response_header(self._session.query(f"{node}?"))

    def _write(
        self,
        command: str,
        query_node: str,
        matches: Callable[[str], bool],
        label: str,
    ) -> None:
        """Senden, zuruecklesen, Fehlerqueue pruefen - wie in den Nachbarklassen."""
        self._require_writable()
        _log.info("SET %s", command)
        self._session.write(command)

        if self._verify:
            actual = self._query(query_node)
            if not matches(actual):
                raise WTError(f"{label}: Geraet meldet {actual!r} nach '{command}'")
            _log.info("  verifiziert: %s = %s", query_node, actual)

        if self._check_errors:
            self._session.assert_no_error(label)

    # =======================================================================
    # Bandbreite, Ordnungen, THD
    # =======================================================================

    def band(self) -> FrequencyBand:
        """Messbandbreite (':HARMonics:FBANd')."""
        token = canonical_enum_token(self._query(_NODE_FBAND), FBAND_TOKENS)
        for kind in FrequencyBand:
            if kind.value.upper() == token:
                return kind
        raise WTError(f"Unbekannte Messbandbreite {token!r}; erwartet: NORMal, WIDE")

    def set_band(self, band: FrequencyBand | str) -> None:
        """Messbandbreite setzen. NORMal oder WIDE."""
        token = band.value if isinstance(band, FrequencyBand) else str(band)
        if canonical_enum_token(token, FBAND_TOKENS) not in FBAND_TOKENS:
            raise WTError(f"Messbandbreite {band!r} unzulaessig; erlaubt: NORMal, WIDE")
        self._write(
            f"{_NODE_FBAND} {token}",
            _NODE_FBAND,
            lambda actual: enum_match(token, actual, FBAND_TOKENS),
            "Messbandbreite setzen",
        )

    def order_range(self) -> tuple[int, int]:
        """Gemessener Ordnungsbereich als (min, max)."""
        return parse_order(self._query(_NODE_ORDER))

    def set_order_range(self, minimum: int, maximum: int) -> None:
        """Ordnungsbereich setzen (Handbuch 6-57).

        Zulaessig sind laut Handbuch nur 0 oder 1 als Minimum - 0 nimmt den
        Gleichanteil mit, 1 beginnt bei der Grundschwingung - und 1 bis 100
        als Maximum. Beides wird hier geprueft, bevor gesendet wird: eine
        Ordnung 150 waere sonst ein Eintrag in der Fehlerqueue und der
        Ordnungsbereich stuende danach unbestimmt da.
        """
        if minimum not in ORDER_MIN_CHOICES:
            raise WTError(
                f"Minimale Ordnung {minimum} unzulaessig; erlaubt ist "
                f"{' oder '.join(str(w) for w in ORDER_MIN_CHOICES)} "
                "(0 nimmt den Gleichanteil mit)"
            )
        if not 1 <= maximum <= ORDER_MAX_LIMIT:
            raise WTError(
                f"Maximale Ordnung {maximum} liegt ausserhalb 1..{ORDER_MAX_LIMIT}"
            )
        # Eine Pruefung auf "Maximum unter Minimum" steht hier bewusst NICHT:
        # das Minimum ist 0 oder 1, das Maximum mindestens 1 - ein leerer
        # Bereich ist mit diesen beiden Grenzen nicht bildbar. Der Zweig waere
        # unerreichbar und taeuschte eine Pruefung vor, die es nicht gibt.
        ziel = (minimum, maximum)
        self._write(
            f"{_NODE_ORDER} {minimum},{maximum}",
            _NODE_ORDER,
            lambda actual: parse_order(actual) == ziel,
            "Ordnungsbereich setzen",
        )

    def thd_formula(self) -> ThdFormula:
        """Bezugsgroesse der Klirrfaktorrechnung."""
        token = canonical_enum_token(self._query(_NODE_THD), THD_TOKENS)
        for kind in ThdFormula:
            if kind.value.upper() == token:
                return kind
        raise WTError(f"Unbekannte THD-Formel {token!r}; erwartet: TOTal, FUNDamental")

    def set_thd_formula(self, formula: ThdFormula | str) -> None:
        """THD-Bezug setzen: Gesamteffektivwert (TOTal) oder Grundschwingung."""
        token = formula.value if isinstance(formula, ThdFormula) else str(formula)
        if canonical_enum_token(token, THD_TOKENS) not in THD_TOKENS:
            raise WTError(
                f"THD-Formel {formula!r} unzulaessig; erlaubt: TOTal, FUNDamental"
            )
        self._write(
            f"{_NODE_THD} {token}",
            _NODE_THD,
            lambda actual: enum_match(token, actual, THD_TOKENS),
            "THD-Formel setzen",
        )

    # =======================================================================
    # PLL-Quelle
    # =======================================================================

    def pll_source(self) -> str:
        """Eingestellte PLL-Quelle, z.B. 'U1', 'I3', 'EXTERNAL'.

        Die Oberschwingungsanalyse synchronisiert sich auf diese Quelle. Fehlt
        dort das Signal, meldet das Geraet Bit 7 des Condition-Registers
        (PLLE) - 'wt.log_condition()' schreibt das ins Protokoll.
        """
        return strip_response_header(self._query(_NODE_PLLSOURCE)).upper()

    def set_pll_source(self, source: str) -> None:
        """PLL-Quelle setzen: 'U<x>', 'I<x>', 'EXTernal' oder 'SAMPle'.

        DIE AUSNAHME BEIM ZURUECKLESEN: Fuer 'SAMPle' sagt das Handbuch
        (6-58) ausdruecklich, dass das Geraet ausserhalb der Breitbandmessung
        stattdessen 'EXTernal' verwendet UND auf eine Abfrage auch
        'EXTernal' zurueckmeldet. Die Rueckleseprobe wuerde das sonst als
        Abweichung melden und einen vollkommen richtigen Aufruf unbenutzbar
        machen. Beide Antworten gelten hier deshalb als Erfolg - und ein
        Protokolleintrag sagt, dass das Geraet die Quelle umgedeutet hat,
        damit es niemandem still passiert.
        """
        token = self._canonical_pll_source(source)
        erlaubt = {token, "EXTERNAL"} if token == "SAMPLE" else {token}

        def _matches(actual: str) -> bool:
            gemeldet = strip_response_header(actual).strip().upper()
            if token == "SAMPLE" and gemeldet == "EXTERNAL":
                _log.info(
                    "PLL-Quelle SAMPle wird vom Geraet als EXTernal gefuehrt - "
                    "laut Handbuch 6-58 der Normalfall ausserhalb der "
                    "Breitbandmessung, kein Fehler"
                )
            return gemeldet in erlaubt

        self._write(
            f"{_NODE_PLLSOURCE} {source}",
            _NODE_PLLSOURCE,
            _matches,
            "PLL-Quelle setzen",
        )

    def _canonical_pll_source(self, source: str) -> str:
        token = str(source).strip().upper()
        canonical = canonical_enum_token(token, frozenset(PLL_FIXED_SOURCES))
        if canonical in PLL_FIXED_SOURCES:
            return canonical
        if len(token) >= 2 and token[0] in {"U", "I"} and token[1:].isdigit():
            self._require_element(int(token[1:]), f"PLL-Quelle {source!r}")
            return token
        raise WTError(
            f"PLL-Quelle {source!r} unzulaessig; erlaubt: U<x>, I<x>, EXTernal, SAMPle"
        )

    def pll_warning(self) -> bool:
        """Warnt das Geraet, wenn an der PLL-Quelle kein Signal anliegt?"""
        return parse_boolean(self._query(_NODE_PLLWARNING), _NODE_PLLWARNING)

    def set_pll_warning(self, enabled: bool) -> None:
        """PLL-Warnmeldung schalten. Laut Handbuch nur im Breitbandmodus wirksam."""
        self._write(
            f"{_NODE_PLLWARNING} {'ON' if enabled else 'OFF'}",
            _NODE_PLLWARNING,
            lambda actual: parse_boolean(actual, _NODE_PLLWARNING) is enabled,
            "PLL-Warnung schalten",
        )

    # =======================================================================
    # IEC-Teil
    # =======================================================================

    def iec_object(self) -> str:
        """Messobjekt der IEC-Messung, z.B. 'ELEMENT1' oder 'SIGMA'."""
        return strip_response_header(self._query(_NODE_IEC_OBJECT)).upper()

    def set_iec_object(self, target: str | int) -> None:
        """Messobjekt setzen: Elementnummer, 'ELEMent<x>', 'SIGMA' oder 'SIGMB'.

        Eine Zahl wird als Element gelesen: 'set_iec_object(2)' entspricht
        'ELEMent2'. Geprueft wird gegen die bestueckte Elementliste und - bei
        SIGMA/SIGMB - gegen die Wiring-Units, die die Fassade hineinreicht.
        """
        token = self._canonical_iec_object(target)
        self._write(
            f"{_NODE_IEC_OBJECT} {token}",
            _NODE_IEC_OBJECT,
            lambda actual: strip_response_header(actual).strip().upper() == token,
            "IEC-Messobjekt setzen",
        )

    def _canonical_iec_object(self, target: str | int) -> str:
        if isinstance(target, int):
            self._require_element(target, f"IEC-Messobjekt {target!r}")
            return f"ELEMENT{target}"
        token = str(target).strip().upper()
        if token in {"SIGMA", "SIGMB"}:
            if token not in self._sigma_units:
                raise WTError(
                    f"Wiring-Unit {token} gibt es bei dieser Verdrahtung nicht "
                    f"(vorhanden: {', '.join(self._sigma_units) or 'keine'})"
                )
            return token
        if token.startswith("ELEMENT") and token[len("ELEMENT"):].isdigit():
            self._require_element(
                int(token[len("ELEMENT"):]), f"IEC-Messobjekt {target!r}"
            )
            return token
        if token.isdigit():
            self._require_element(int(token), f"IEC-Messobjekt {target!r}")
            return f"ELEMENT{int(token)}"
        raise WTError(
            f"IEC-Messobjekt {target!r} unzulaessig; erlaubt: ELEMent<x>, SIGMA, SIGMB"
        )

    def iec_grouping(self, quantity: str = "U") -> IecGrouping:
        """Gruppierung der IEC-Messung fuer Spannung ('U') oder Strom ('I')."""
        node = self._grouping_node(quantity)
        token = canonical_enum_token(self._query(node), GROUPING_TOKENS)
        try:
            return IecGrouping(token)
        except ValueError as exc:
            raise WTError(f"Unbekannte IEC-Gruppierung {token!r}") from exc

    def set_iec_grouping(self, quantity: str, grouping: IecGrouping | str) -> None:
        """Gruppierung setzen: OFF, TYPE1 oder TYPE2 (Handbuch 6-57)."""
        node = self._grouping_node(quantity)
        token = grouping.value if isinstance(grouping, IecGrouping) else str(grouping)
        canonical = canonical_enum_token(token, GROUPING_TOKENS)
        if canonical not in GROUPING_TOKENS:
            raise WTError(
                f"IEC-Gruppierung {grouping!r} unzulaessig; erlaubt: OFF, TYPE1, TYPE2"
            )
        self._write(
            f"{node} {canonical}",
            node,
            lambda actual: enum_match(canonical, actual, GROUPING_TOKENS),
            "IEC-Gruppierung setzen",
        )

    @staticmethod
    def _grouping_node(quantity: str) -> str:
        token = str(quantity).strip().upper()
        if token in {"U", "VOLTAGE", "SPANNUNG"}:
            return _NODE_IEC_UGROUPING
        if token in {"I", "CURRENT", "STROM"}:
            return _NODE_IEC_IGROUPING
        raise WTError(
            f"Groesse {quantity!r} unbekannt; erlaubt ist 'U' (Spannung) oder "
            "'I' (Strom)"
        )

    # =======================================================================
    # Momentaufnahme
    # =======================================================================

    def capture(self) -> HarmonicsSettings:
        """Vollstaendige Momentaufnahme der Gruppe."""
        order_min, order_max = self.order_range()
        return HarmonicsSettings(
            band=self.band(),
            order_min=order_min,
            order_max=order_max,
            pll_source=self.pll_source(),
            pll_warning=self.pll_warning(),
            thd=self.thd_formula(),
            iec_object=self.iec_object(),
            iec_voltage_grouping=self.iec_grouping("U"),
            iec_current_grouping=self.iec_grouping("I"),
        )

    def restore(self, settings: HarmonicsSettings) -> None:
        """Eine Momentaufnahme zurueckschreiben."""
        self.set_band(settings.band)
        self.set_order_range(settings.order_min, settings.order_max)
        self.set_pll_source(settings.pll_source)
        self.set_pll_warning(settings.pll_warning)
        self.set_thd_formula(settings.thd)
        self.set_iec_object(settings.iec_object)
        self.set_iec_grouping("U", settings.iec_voltage_grouping)
        self.set_iec_grouping("I", settings.iec_current_grouping)

    def log_summary(self) -> None:
        """Momentaufnahme ins Protokoll schreiben."""
        for line in self.capture().describe():
            _log.info("%s", line)

    def _require_element(self, element: int, was: str) -> None:
        """Elementnummer gegen die bestueckte Liste halten."""
        if element not in self._elements:
            raise WTError(
                f"{was}: Element {element} ist nicht bestueckt "
                f"(bestueckt: {self._elements})"
            )
