# =============================================================================
# Datei: stage2_read_numeric.py
# Layer 4 - Stufe 2: Messwerte gegen die VORHANDENE Item-Tabelle lesen.
#
# Diese Stufe veraendert die Item-Tabelle NICHT. Sie sichert sie, liest
# mehrfach Werte und stellt beim Beenden sicher, dass die Tabelle unveraendert
# ist. Ranges, Wiring und Filter werden nicht angefasst.
# =============================================================================

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# Paketmodule werden mit 'python -m wt3000_scpi.stage2_read_numeric' gestartet.
from .wt3000_common import (
    condition_warnings,
    output_dir,
    parse_condition,
    setup_logging,
)
from .wt3000_core import (
    TmctlTransport,
    WTConfig,
    WTError,
    WTSession,
    config_file_in_use,
)
from .wt3000_numeric import ItemTable, ValueStatus, read_numeric_values

# Anzahl der Lesedurchlaeufe.
READ_CYCLES: int = 3

# Pollingabstand. Sollte mindestens dem :RATE?-Wert entsprechen; schnelleres
# Abfragen liefert nur Wiederholungen. Aktuell gemessen: 1.00E+00 s.
POLL_INTERVAL_S: float = 1.0

# Auf True setzen, um den Schreibpfad der Wiederherstellung bewusst einmal
# durchlaufen zu lassen: schreibt alle Items mit ihren EIGENEN Werten zurueck.
# Damit laesst sich pruefen, ob das Geraet die Kurzform-Funktionsnamen aus der
# Antwort (z.B. 'UTHDG', 'LAMB') auch als Eingabe akzeptiert.
EXERCISE_RESTORE_WRITE: bool = False

# Zielverzeichnis relativ zur Projektwurzel. Die Konstante ist in Tests
# ersetzbar; ihre Ermittlung liest beim Import nur vorhandene Pfadmarker.
OUTPUT_DIR: Path = output_dir()


def check_preconditions(session: WTSession) -> None:
    """Voraussetzungen pruefen, ohne etwas zu veraendern."""
    log = logging.getLogger("wt3000.stage2")

    header = session.query(":COMMunicate:HEADer?")
    if header.strip() != "0":
        raise WTError(
            f":COMMunicate:HEADer ist {header!r}, erwartet '0'. "
            "Mit Headern schlaegt das Parsen der Item-Tabelle fehl."
        )

    fmt = session.query(":NUMeric:FORMat?")
    if not fmt.upper().startswith("FLO"):
        raise WTError(
            f":NUMeric:FORMat ist {fmt!r}, erwartet 'FLO'. "
            "Dieses Skript liest ausschliesslich Binaerbloecke."
        )

    rate = session.query(":RATE?")
    log.info("Datenaktualisierungsintervall: %s s (Polling: %.2f s)", rate, POLL_INTERVAL_S)

    # Gemeinsamer Parser liefert WTError und deckt alle bekannten Bits ab.
    for meldung in condition_warnings(parse_condition(session.query(":STATus:CONDition?"))):
        log.warning("%s", meldung)

    hold = session.query(":NUMeric:HOLD?")
    log.info("NUMeric:HOLD = %s (in Stufe 2 nicht genutzt)", hold)


def log_reading(session: WTSession, table: ItemTable, cycle: int) -> Counter:
    """Einen Lesedurchlauf ausgeben und die Statusverteilung zurueckgeben."""
    log = logging.getLogger("wt3000.stage2")
    values = read_numeric_values(session, expected_count=len(table.items))

    log.info("-" * 78)
    log.info("Lesedurchlauf %d", cycle)
    log.info("%-4s %-14s %-10s %s", "Idx", "Name", "Status", "Wert")

    statistics: Counter = Counter()
    for item, value in zip(table.items, values):
        statistics[value.status] += 1
        log.info("%-4d %-14s %-10s %s", item.index, item.key, value.status.value, value)
    return statistics


def main() -> int:
    """Stufe 2 ausfuehren. Rueckgabewert 0 = erfolgreich."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = OUTPUT_DIR / f"wt3000_stage2_{timestamp}.txt"
    backup_file = OUTPUT_DIR / f"wt3000_itemtable_backup_{timestamp}.json"
    setup_logging(log_file)
    log = logging.getLogger("wt3000.stage2")
    log.info("Protokolldatei: %s", log_file)
    log.info("Ausgabeverzeichnis: %s", OUTPUT_DIR)
    log.info("Stufe 2 - Messwerte gegen die vorhandene Item-Tabelle lesen (FLOat)")

    backup: ItemTable | None = None
    session: WTSession | None = None

    try:
        # Herkunft vor dem Lesen protokollieren; so bleibt auch fehlerhaftes
        # JSON einem konkreten Pfad zuordenbar.
        log.info("Konfigurationsdatei: %s", config_file_in_use() or "<keine, Voreinstellungen>")
        config = WTConfig.from_environment()
        log.info("Verbindung: %s", config.describe())

        with TmctlTransport(config) as transport:
            session = WTSession(transport, config, read_only=False)

            if config.use_remote:
                session.enable_remote()

            # REMOTE im finally loesen, damit das Bedienfeld auch bei einem
            # fruehen Abbruch wieder freigegeben wird.
            try:
                check_preconditions(session)

                # 1) Bestehende Tabelle sichern - als Erstes, vor allem anderen.
                backup = ItemTable.read_from_device(session)
                backup.save(backup_file)
                log.info("Gesicherte Items:")
                for item in backup.items:
                    log.info("  ITEM%-3d %-20s -> %s", item.index, item.argument, item.key)

                # 2) Messwerte lesen.
                for cycle in range(1, READ_CYCLES + 1):
                    statistics = log_reading(session, backup, cycle)
                    log.info(
                        "Statusverteilung: OK=%d, NO_DATA=%d, OVERRANGE=%d",
                        statistics[ValueStatus.OK],
                        statistics[ValueStatus.NO_DATA],
                        statistics[ValueStatus.OVERRANGE],
                    )
                    if cycle < READ_CYCLES:
                        time.sleep(POLL_INTERVAL_S)

                session.assert_no_error("Messwertabfrage")

            finally:
                session.disable_remote()

    except WTError as exc:
        log.error("Abbruch: %s", exc)
        log.error("Backup der Item-Tabelle liegt unter: %s", backup_file)
        return 1

    finally:
        # 3) Wiederherstellung. Die Verbindung ist an dieser Stelle bereits
        #    geschlossen, daher wird eine zweite kurze Sitzung geoeffnet.
        if backup is not None:
            try:
                with TmctlTransport(config) as transport:
                    restore_session = WTSession(transport, config, read_only=False)
                    if config.use_remote:
                        restore_session.enable_remote()
                    try:
                        backup.restore_to_device(restore_session, force=EXERCISE_RESTORE_WRITE)
                    finally:
                        restore_session.disable_remote()
            except WTError as exc:
                logging.getLogger("wt3000.stage2").error(
                    "Wiederherstellung fehlgeschlagen: %s - Backup: %s", exc, backup_file
                )

    log.info("=" * 78)
    log.info("Stufe 2 beendet. Item-Tabelle gesichert unter %s", backup_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
