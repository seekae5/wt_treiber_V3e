# =============================================================================
# Datei: stage5b_range_probe.py
# Layer 4 - Stufe 5b: die offenen Fragen zur Bereichseinstellung klaeren,
#                     BEVOR jemals ein veraendernder Schreibversuch stattfindet.
#
# VOREINSTELLUNG: SCHREIBT NICHTS.
#
#     python -m wt3000_scpi.stage5b_range_probe
#
# Die Sitzung wird mit read_only=True geoeffnet und RangeAccess mit
# allow_changes=False - zwei unabhaengige Sperren, genau wie Stufe 5.
#
# MIT SCHREIBPROBE: nur auf ausdrueckliche Ansage.
#
#     python -m wt3000_scpi.stage5b_range_probe --write-probe
#
# Dann geht zusaetzlich EIN Set-Kommando hinaus, das den aktuellen
# Spannungsbereich des ersten Elements mit seinem EIGENEN Wert ueberschreibt.
# Das ist ein Nulleffekt und beantwortet trotzdem die Frage, ob die
# INPut-Gruppe Set-Kommandos ohne ':COMMunicate:REMote ON' annimmt. Der
# Ausgangszustand wird davor gesichert und danach geprueft.
#
# Der Schalter ist bewusst ein Aufrufparameter und keine Konstante im
# Quelltext: eine Konstante, die man zum Arbeiten auf True stellt, bleibt auf
# True stehen - und dann schreibt ein Skript, dessen Kopf 'schreibt nichts'
# sagt.
#
# Beantwortete Fragen:
#   (1) Rundet das Geraet Bereichswerte?  -> nur teilweise; siehe Hinweis unten
#   (2) Was bedeutet 'Bereich' an den externen Stromsensoren 1-3?
#   (3) Braucht ':INPut' ein ':COMMunicate:REMote ON'?
#   (4) Gibt es an Element 4 (DC) ueberhaupt einen Autorange?
# =============================================================================

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

# Paketmodule werden mit 'python -m wt3000_scpi.stage5b_range_probe' gestartet.
from .wt3000_common import output_dir, setup_logging
from .wt3000_core import (
    TmctlTransport,
    WTConfig,
    WTError,
    WTSession,
    config_file_in_use,
)
from .wt3000_ranging import RangeBackup, probe_range_write_capability
from .wt3000_rangeio import Quantity, RangeAccess

# ---------------------------------------------------------------------------
# Laufparameter
# ---------------------------------------------------------------------------

# Zielverzeichnis fuer Bericht, Backup und Protokoll, relativ zur Projektwurzel.
OUTPUT_DIR: Path = output_dir("konfiguration")

# Der Spannungsbereich dieses Elements wird bei --write-probe mit seinem
# eigenen Wert ueberschrieben. Element 1 ist der erste Eintrag von
# RangeAccess.elements; probe_range_write_capability() waehlt ihn selbst.
PROBE_ELEMENT: int = 1


def report_environment(access: RangeAccess, log: logging.Logger) -> None:
    """Frage 2 und 4: Umfeld der Bereichseinstellung erfassen."""
    log.info("-" * 78)
    log.info("Umfeld")
    log.info("  Wiring:       %s", access.get_wiring())
    log.info("  Module:       %s", access.get_module())
    log.info("  INDependent:  %s", "EIN" if access.get_independent() else "AUS")
    log.info("  Peak Over:    %s", access.get_peak_over())

    # Rohabzuege. Hier steht, in welcher Einheit die Bereiche gefuehrt werden -
    # das entscheidet, ob an den Elementen 1-3 die Sensoreingangsspannung oder
    # ein Amperewert gesetzt werden muss.
    for quantity in Quantity:
        log.info("  %s-Rohabzug: %s", quantity.label, access.dump(quantity))


def report_ranges(access: RangeAccess, log: logging.Logger) -> RangeBackup:
    """Ist-Zustand aller Bereiche erfassen und protokollieren."""
    log.info("-" * 78)
    log.info("Bereichszustand")
    backup = RangeBackup.capture(access)
    backup.log_summary()

    # Frage 4: an einem DC-Kanal koennte Autorange strukturell fehlen. Wenn
    # die Abfrage sauber antwortet, existiert der Knoten - unabhaengig davon,
    # ob er dort sinnvoll ist.
    for state in backup.states:
        if state.current_auto or state.voltage_auto:
            log.info("Element %d: Autorange ist aktiv", state.element)
    return backup


def run_noop_write_probe(
    session: WTSession, backup: RangeBackup, log: logging.Logger
) -> None:
    """Frage 3: Schreibpfad testen, ohne einen Wert zu veraendern."""
    log.info("-" * 78)
    log.warning("Schreibprobe aktiviert - es wird EIN Set-Kommando gesendet")

    writable = RangeAccess(session, allow_changes=True)
    probe_range_write_capability(writable, backup)

    problems = backup.diff(RangeBackup.capture(writable))
    if problems:
        for problem in problems:
            log.error("Nach der Schreibprobe veraendert: %s", problem)
        raise WTError("Die Schreibprobe war kein Nulleffekt - Zustand pruefen")
    log.info("Zustand nach der Schreibprobe unveraendert")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Kommandozeile auswerten. Ohne Schalter bleibt der Lauf rein lesend."""
    parser = argparse.ArgumentParser(
        prog="python -m wt3000_scpi.stage5b_range_probe",
        description=(
            "Messbereiche des WT3000 erfassen. Schreibt in der Voreinstellung "
            "nichts."
        ),
    )
    parser.add_argument(
        "--write-probe",
        action="store_true",
        help=(
            "Zusaetzlich EIN Set-Kommando senden, das den Spannungsbereich von "
            f"Element {PROBE_ELEMENT} mit seinem EIGENEN Wert ueberschreibt "
            "(Nulleffekt). Klaert, ob die INPut-Gruppe Set-Kommandos ohne "
            "':COMMunicate:REMote ON' annimmt - ROADMAP M0-3."
        ),
    )
    return parser.parse_args(argv)


def main(enable_write_probe: bool = False) -> int:
    """Stufe 5b ausfuehren. Rueckgabewert 0 = erfolgreich.

    enable_write_probe=False (Voreinstellung) laesst kein einziges
    Set-Kommando hinaus: die Sitzung wird mit read_only=True geoeffnet, und
    WTSession lehnt dann jedes Nicht-Query-Kommando ab.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    log_file = OUTPUT_DIR / f"wt3000_stage5b_{timestamp}.txt"
    backup_file = OUTPUT_DIR / f"wt3000_ranges_{timestamp}.json"

    setup_logging(log_file)
    log = logging.getLogger("wt3000.stage5b")
    log.info("Protokolldatei: %s", log_file)

    # Der Schalter soll unangenehm sein: wer schreibt, sieht es als Erstes im
    # Protokoll, mit Knoten und Element im Klartext.
    if enable_write_probe:
        log.warning("=" * 78)
        log.warning("SCHREIBPROBE AKTIV (--write-probe)")
        log.warning(
            "  Ein Set-Kommando auf ':INPut:VOLTage:RANGe:ELEMent%d' geht hinaus.",
            PROBE_ELEMENT,
        )
        log.warning("  Geschrieben wird der bereits eingestellte Wert - Nulleffekt.")
        log.warning("  Der Ausgangszustand wird vorher gesichert und danach geprueft.")
        log.warning("=" * 78)
    else:
        log.info("Stufe 5b - Messbereiche erfassen (nur Lesen, keine Schreibprobe)")

    try:
        # Herkunft vor dem Lesen protokollieren; so bleibt auch fehlerhaftes
        # JSON einem konkreten Pfad zuordenbar.
        log.info("Konfigurationsdatei: %s", config_file_in_use() or "<keine, Voreinstellungen>")
        config = WTConfig.from_environment()
        log.info("Verbindung: %s", config.describe())

        with TmctlTransport(config) as transport:
            # Die Sitzung wird nur dann schreibfaehig geoeffnet, wenn die
            # Schreibprobe ausdruecklich verlangt ist.
            session = WTSession(transport, config, read_only=not enable_write_probe)

            access = RangeAccess(session, allow_changes=False)
            report_environment(access, log)
            backup = report_ranges(access, log)
            backup.save(backup_file)

            if enable_write_probe:
                run_noop_write_probe(session, backup, log)

            session.assert_no_error("Bereichserfassung")

    except WTError as error:
        log.error("Abbruch: %s", error)
        return 1

    log.info("=" * 78)
    log.info("Backup: %s", backup_file)
    log.info(
        "Offen bleibt Frage 1 (Rundung ungueltiger Werte). Sie laesst sich nur "
        "mit einer echten Aenderung klaeren und gehoert deshalb in ein eigenes, "
        "bewusst gestartetes Stufenskript."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(enable_write_probe=_parse_args().write_probe))
