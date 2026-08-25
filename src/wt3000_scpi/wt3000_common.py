# =============================================================================
# Datei: wt3000_common.py
# Layer 1 (Querschnitt) - Bausteine, die mehrere Fachmodule gemeinsam brauchen.
#
# Haengt NUR von wt3000_core.py ab. Enthaelt bewusst keine Geraetezugriffe,
# damit dieses Modul ohne Verbindung getestet werden kann.
#
# Hintergrund: Die Normalisierung von Element-/Scope-Angaben lag bisher als
# private Funktion in wt3000_itemspec.py. Dort wurde ein metrologisch fataler
# Bug gefunden (beidseitiges Praefixmatching setzte SIGMA und SIGMB gleich).
# Damit dieselbe Falle nicht in der INPut-Gruppe ein zweites Mal unabhaengig
# entsteht, liegt die Regel ab jetzt genau EINMAL - hier.
# =============================================================================

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Final

from .wt3000_core import WTError

# ---------------------------------------------------------------------------
# Scope-Token
# ---------------------------------------------------------------------------

SIGMA: Final[str] = "SIGMA"
SIGMB: Final[str] = "SIGMB"
ALL: Final[str] = "ALL"

# Elementnummern des vorliegenden 4-Element-Geraets.
DEFAULT_ELEMENTS: Final[tuple[int, ...]] = (1, 2, 3, 4)

# Schreibweisen, die das Geraet fuer SigmaA zurueckliefern kann.
# KEIN Praefixmatching - 'SIGMB'.startswith('SIGM') waere wahr und wuerde
# die beiden Wiring-Units stillschweigend vertauschen.
_SIGMA_TOKENS: Final[frozenset[str]] = frozenset({"SIGMA", "SIGM"})
_SIGMB_TOKENS: Final[frozenset[str]] = frozenset({"SIGMB"})


def canonical_scope(scope: str | int) -> str:
    """Scope-Angabe auf ein eindeutiges Token normalisieren.

    Zulaessig sind Elementnummern (1..4, auch als 'ELEMent2' oder '2'),
    die Wiring-Units 'SIGMA'/'SIGMB' sowie 'ALL'.

    Rueckgabe: '1'..'4' | 'SIGMA' | 'SIGMB' | 'ALL'
    """
    if isinstance(scope, int):
        token = str(scope)
    else:
        token = scope.strip().upper()

    # 'ELEMENT3' / 'ELEM3' / 'E3' -> '3'
    for prefix in ("ELEMENT", "ELEM", "ELE", "E"):
        if token.startswith(prefix) and token[len(prefix) :].isdigit():
            token = token[len(prefix) :]
            break

    if token.isdigit():
        return token
    if token in _SIGMB_TOKENS:
        return SIGMB
    if token in _SIGMA_TOKENS:
        return SIGMA
    if token == ALL:
        return ALL

    raise WTError(f"Unbekannter Scope: {scope!r}")


def canonical_element(element: str | None) -> str:
    """Elementangabe der NUMeric-Gruppe normalisieren.

    Eigene Funktion, weil hier eine andere Konvention gilt als bei
    canonical_scope(): ein fehlendes <Element> bedeutet laut Handbuch
    'Element 1', und Funktionsnamen wie 'SIGMA' duerfen nicht als Zahl
    interpretiert werden.

    wt3000_itemspec._canonical_element() kann hierher delegieren.
    """
    if element is None:
        return "1"
    token = element.strip().upper()
    if token in _SIGMB_TOKENS:
        return SIGMB
    if token in _SIGMA_TOKENS:
        return SIGMA
    return token


def is_element_scope(scope: str | int) -> bool:
    """True, wenn der Scope genau ein Element bezeichnet."""
    return canonical_scope(scope).isdigit()


def element_number(scope: str | int) -> int:
    """Elementnummer eines Element-Scopes. Fehler bei SIGMA/SIGMB/ALL."""
    token = canonical_scope(scope)
    if not token.isdigit():
        raise WTError(f"Scope {token!r} bezeichnet kein einzelnes Element")
    return int(token)


def scope_suffix(scope: str | int) -> str:
    """SCPI-Pfadendung fuer einen Scope.

    '2'     -> ':ELEMent2'
    'SIGMA' -> ':SIGMA'
    'ALL'   -> ':ALL'
    """
    token = canonical_scope(scope)
    if token.isdigit():
        return f":ELEMent{token}"
    return f":{token}"


# ---------------------------------------------------------------------------
# Antworten auswerten
# ---------------------------------------------------------------------------


def strip_response_header(response: str) -> str:
    """Fuehrenden Kommandokopf entfernen, falls doch einer mitkommt.

    Der Treiber setzt ':COMMunicate:HEADer 0' voraus, dann antwortet das
    Geraet nur mit dem Wert. Diese Funktion ist die Absicherung fuer den
    Fall, dass jemand HEADer eingeschaltet hat: aus
    ':INPUT:VOLTAGE:RANGE:ELEMENT1 1.000E+03' wird '1.000E+03'.
    """
    text = response.strip()
    if text.startswith(":") and " " in text:
        return text.split(" ", 1)[1].strip()
    return text


def parse_nr3(response: str, context: str = "") -> float:
    """Zahlenantwort im NR3-Format in einen float wandeln."""
    text = strip_response_header(response)
    try:
        return float(text)
    except ValueError as exc:
        suffix = f" ({context})" if context else ""
        raise WTError(f"Keine Zahl in der Antwort {response!r}{suffix}") from exc


def parse_nr1(response: str, context: str = "") -> int:
    """Ganzzahlantwort (Registerinhalt, Zaehler) in einen int wandeln.

    Gegenstueck zu parse_nr3() fuer Bitmasken und Zaehler. Unerwartete
    Antworten werden mit Kontext als WTError statt als roher ValueError
    gemeldet.
    """
    text = strip_response_header(response)
    try:
        return int(text)
    except ValueError as exc:
        suffix = f" ({context})" if context else ""
        raise WTError(f"Keine Ganzzahl in der Antwort {response!r}{suffix}") from exc


def parse_condition(response: str) -> int:
    """Antwort auf ':STATus:CONDition?' als Bitmaske lesen."""
    return parse_nr1(response, ":STATus:CONDition")


#: Zentral gepflegte Bits, die eine Messreihe unbrauchbar machen koennen.
_CONDITION_BITS: tuple[tuple[int, str], ...] = (
    (1 << 4, "Condition Bit 4 (FOV): Frequenzmessung im Fehler"),
    (1 << 7, "Condition Bit 7 (PLLE): kein Signal an der PLL-Quelle"),
    (0x0F00, "Condition: Overrange an mindestens einem Element"),
    (1 << 15, "Condition Bit 15 (POV): Peak Over an mindestens einem Eingang"),
)


def condition_warnings(bits: int) -> list[str]:
    """Auffaelligkeiten des Condition-Registers als Meldungstexte.

    Gibt die Texte ZURUECK, statt sie zu protokollieren: dieses Modul kennt
    keine Sitzung und soll auch keinen Logger der Aufrufstelle bekommen. Wer
    sie ausgibt, entscheidet der Aufrufer mit seinem eigenen Logger - und die
    Messschleife in wt3000_measure ruft die Funktion bewusst NICHT auf, weil
    eine Warnung je Zyklus ueber Stunden nichts nuetzt.
    """
    return [text for maske, text in _CONDITION_BITS if bits & maske]


# ---------------------------------------------------------------------------
# Geraeteunabhaengige Aufzaehlungswerte
# ---------------------------------------------------------------------------


def canonical_enum_token(text: str, allowed: frozenset[str]) -> str:
    """Kurzform der Geraeteantwort auf die Langform der Aufzaehlung abbilden.

    'EXT' -> 'EXTERNAL', 'NORM' -> 'NORMAL', 'RES' -> 'RESET', 'I3' -> 'I3'.

    Ist die Kurzform mehrdeutig (mehrere Kandidaten) oder unbekannt, bleibt der
    Text unveraendert. Der Vergleich schlaegt dann an - lieber eine gemeldete
    Abweichung als eine stillschweigend falsche Zuordnung.
    """
    token = strip_response_header(text).upper()
    if token in allowed:
        return token
    candidates = [value for value in allowed if value.startswith(token)]
    if len(candidates) == 1:
        return candidates[0]
    return token


def enum_match(wanted: str, actual: str, allowed: frozenset[str]) -> bool:
    """Einzige Vergleichsregel fuer Aufzaehlungswerte."""
    return canonical_enum_token(wanted, allowed) == canonical_enum_token(actual, allowed)


def parse_boolean(response: str, context: str = "") -> bool:
    """Boolean-Antwort auswerten. Das Geraet antwortet mit '1' bzw. '0'."""
    text = strip_response_header(response).upper()
    if text in {"1", "ON", "TRUE"}:
        return True
    if text in {"0", "OFF", "FALSE"}:
        return False
    suffix = f" ({context})" if context else ""
    raise WTError(f"Kein Boolean in der Antwort {response!r}{suffix}")


def format_nrf(value: float) -> str:
    """Zahl als <NRf>-Parameter formatieren.

    Ganzzahlige Werte werden ohne Nachkommastellen gesendet ('150' statt
    '150.0'), weil das Handbuch die Bereiche so notiert.

    ZU VERIFIZIEREN: Ob das Geraet fuer Spannungs-/Strombereiche auch die
    Einheitenschreibweise ('150V') erwartet. Die reine NRf-Form ist laut
    Syntaxangabe zulaessig, am Geraet aber noch nicht geprueft.
    """
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def values_match(requested: float, actual: float, tolerance: float = 1e-3) -> bool:
    """Zwei Bereichswerte relativ vergleichen.

    Ein exakter Gleichheitstest ist hier untauglich: das Geraet antwortet in
    NR3 mit begrenzter Mantisse, angefordert wird eine Python-Zahl.
    """
    if requested == actual:
        return True
    reference = max(abs(requested), abs(actual))
    if reference == 0.0:
        return True
    return abs(requested - actual) / reference <= tolerance


# ---------------------------------------------------------------------------
# Protokollierung und Ausgabeorte
# ---------------------------------------------------------------------------


# Dateien, an denen eine Projektwurzel erkennbar ist. 'pyproject.toml' zuerst,
# weil sie das Projekt definiert; '.git' als Rueckfall fuer einen Klon ohne
# Installation; 'wt3000.json' zuletzt, weil sie auch anderswo liegen darf.
_PROJEKT_MARKER: Final[tuple[str, ...]] = ("pyproject.toml", ".git", "wt3000.json")


def find_project_root(start: Path | None = None) -> Path | None:
    """Projektwurzel suchen: vom Startverzeichnis aufwaerts bis zur Dateiwurzel.

    Rueckgabe 'None', wenn kein Marker gefunden wird. Das ist kein Fehler,
    sondern der Normalfall fuer ein installiertes Paket ausserhalb des
    Quellbaums - dort ist 'Path.cwd()' die richtige Antwort, und
    'output_dir()' setzt genau das ein.
    """
    beginn = (start or Path.cwd()).resolve()
    for verzeichnis in (beginn, *beginn.parents):
        if any((verzeichnis / marker).exists() for marker in _PROJEKT_MARKER):
            return verzeichnis
    return None


def output_dir(name: str | None = None, start: Path | None = None) -> Path:
    """Ablageort fuer Protokolle, Sicherungen und Messdateien.

    'output_dir("messungen")' liefert '<Projektwurzel>/messungen', wenn eine
    Projektwurzel gefunden wird, sonst '<Arbeitsverzeichnis>/messungen'. Ohne
    'name' kommt die Wurzel selbst heraus.

    Das Verzeichnis wird NICHT angelegt - das bleibt beim Aufrufer, der auch
    entscheidet, ob er es ueberhaupt braucht.
    """
    wurzel = find_project_root(start) or (start or Path.cwd())
    return wurzel / name if name else wurzel


def setup_logging(log_file: Path) -> None:
    """Logging auf Konsole und in eine Protokolldatei einrichten.

    Setzt die Handler des Root-Loggers neu. Gedacht fuer die Stufenskripte,
    also fuer Programme, die den Prozess allein bewohnen. Wer den Treiber als
    Bibliothek in eine groessere Anwendung einbaut, ruft diese Funktion NICHT
    auf, sondern konfiguriert das Logging der Anwendung - sonst werden deren
    Handler mit entfernt.
    """
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-18s %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)
