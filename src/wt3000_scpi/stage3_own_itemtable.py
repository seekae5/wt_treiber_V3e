# =============================================================================
# Datei: stage3_own_itemtable.py
# Layer 4 - Stufe 3: eigene Item-Tabelle setzen, Messwerte lesen, Namen
#                    zuordnen und den Ausgangszustand wiederherstellen.
#
# Aendert AUSSCHLIESSLICH die Item-Tabelle der NUMeric-Gruppe.
# Ranges, Wiring, Filter, Skalierung und Update-Rate bleiben unangetastet.
# =============================================================================

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# Paketmodule werden mit 'python -m wt3000_scpi.stage3_own_itemtable' gestartet.
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
from .wt3000_itemspec import (
    ItemSpec,
    apply_item_table,
    build_item_table,
    probe_extra_items,
    probe_item_write_capability,
    restore_item_table,
    save_backup_bundle,
    verify_item_table,
)
from .wt3000_numeric import ItemTable, NumericItem, ValueStatus, read_numeric_values


# Passend zur vorgefundenen Verdrahtung V3A3,P1W2:
#   Elemente 1-3 = Drehstromseite (Wiring-Unit SigmaA)
#   Element 4    = separater DC-Kanal (Wiring-Unit SigmaB)
# 33 Items - bewusst unter den 34 des Backups, damit der Restore vollstaendig
# ist. Wird die Liste laenger, sichert probe_extra_items() den Rest automatisch.

_THREE_PHASE_FUNCTIONS = ("U", "I", "P", "S", "Q", "LAMBDA", "PHI", "FU")
_SUM_FUNCTIONS = ("U", "I", "P", "S", "Q", "LAMBDA")
_DC_FUNCTIONS = ("U", "I", "P")  # Element 4 ist DC: S/Q/LAMBDA/PHI/FU waeren NO_DATA

TARGET_ITEMS: tuple[ItemSpec, ...] = (
    *(ItemSpec(f, str(e)) for e in (1, 2, 3) for f in _THREE_PHASE_FUNCTIONS),
    *(ItemSpec(f, "SIGMA") for f in _SUM_FUNCTIONS),
    *(ItemSpec(f, "4") for f in _DC_FUNCTIONS),
)

# ---------------------------------------------------------------------------
# Laufzeitparameter
# ---------------------------------------------------------------------------

READ_CYCLES: int = 3
POLL_INTERVAL_S: float = 1.0  # entspricht :RATE? = 1.00E+00

# Auf True setzen, um beim Restore alle Items zu schreiben statt nur die
# abweichenden. Normalerweise nicht noetig.
FORCE_FULL_RESTORE: bool = False

# Zielverzeichnis relativ zur Projektwurzel. Die Konstante ist in Tests
# ersetzbar; ihre Ermittlung liest beim Import nur vorhandene Pfadmarker.
OUTPUT_DIR: Path = output_dir()


def check_preconditions(session: WTSession) -> None:
    """Voraussetzungen pruefen, ohne etwas zu veraendern."""
    log = logging.getLogger("wt3000.stage3")

    header = session.query(":COMMunicate:HEADer?")
    if header.strip() != "0":
        raise WTError(f":COMMunicate:HEADer ist {header!r}, erwartet '0'")

    fmt = session.query(":NUMeric:FORMat?")
    if not fmt.upper().startswith("FLO"):
        raise WTError(f":NUMeric:FORMat ist {fmt!r}, erwartet 'FLO'")

    wiring = session.query(":INPut:WIRing?")
    log.info("Verdrahtung: %s (bestimmt, ob SIGMA/SIGMB gueltig sind)", wiring)

    # Gemeinsamer Parser liefert WTError und deckt alle bekannten Bits ab.
    for meldung in condition_warnings(parse_condition(session.query(":STATus:CONDition?"))):
        log.warning("%s", meldung)


def read_and_log(session: WTSession, table: ItemTable, cycle: int) -> Counter:
    """Einen Lesedurchlauf ausgeben und die Statusverteilung zurueckgeben."""
    log = logging.getLogger("wt3000.stage3")
    values = read_numeric_values(session, expected_count=len(table.items))
    mapped = table.map_values(values)

    log.info("-" * 78)
    log.info("Lesedurchlauf %d", cycle)
    log.info("%-4s %-14s %-12s %-10s %s", "Idx", "Name", "Item", "Status", "Wert")

    statistics: Counter = Counter()
    for item, (name, value) in zip(table.items, mapped.items()):
        statistics[value.status] += 1
        log.info(
            "%-4d %-14s %-12s %-10s %s",
            item.index,
            name,
            item.argument,
            value.status.value,
            value,
        )
    return statistics


def main() -> int:
    """Stufe 3 ausfuehren. Rueckgabewert 0 = erfolgreich."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = OUTPUT_DIR / f"wt3000_stage3_{timestamp}.txt"
    backup_file = OUTPUT_DIR / f"wt3000_itemtable_backup_{timestamp}.json"
    setup_logging(log_file)
    log = logging.getLogger("wt3000.stage3")
    log.info("Protokolldatei: %s", log_file)
    log.info("Ausgabeverzeichnis: %s", OUTPUT_DIR)
    log.info("Stufe 3 - eigene Item-Tabelle (%d Items)", len(TARGET_ITEMS))

    backup: ItemTable | None = None
    tail: list[NumericItem] = []
    exit_code = 0

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

            try:
                try:
                    check_preconditions(session)

                    # 1) Ist-Zustand sichern - vor jedem Schreibzugriff.
                    backup = ItemTable.read_from_device(session)
                    target = build_item_table(TARGET_ITEMS)

                    # Items jenseits von NUMber sichern, falls die Zieltabelle
                    # weiter reicht als das, was :NUMeric:NORMal? ausgibt.
                    tail = probe_extra_items(
                        session,
                        first_index=len(backup.items) + 1,
                        last_index=len(target.items),
                    )
                    save_backup_bundle(backup_file, backup, tail)

                    # 2) Fail-Fast: nur ein einziges Item schreiben und pruefen.
                    probe_item_write_capability(session, target, backup)

                    # 3) Vollstaendige Zieltabelle schreiben inkl. NUMber.
                    apply_item_table(session, target)

                    # 4) Zuruecklesen und Feld fuer Feld vergleichen.
                    problems = verify_item_table(session, target)
                    if problems:
                        for problem in problems:
                            log.error("Verifikation: %s", problem)
                        raise WTError(
                            f"{len(problems)} Abweichung(en) beim Verifizieren der Tabelle"
                        )

                    # 5) Messwerte lesen. Eine Aktualisierungsperiode abwarten,
                    #    damit der erste Block sicher zur neuen Tabelle passt.
                    time.sleep(POLL_INTERVAL_S)
                    for cycle in range(1, READ_CYCLES + 1):
                        statistics = read_and_log(session, target, cycle)
                        log.info(
                            "Statusverteilung: OK=%d, NO_DATA=%d, OVERRANGE=%d",
                            statistics[ValueStatus.OK],
                            statistics[ValueStatus.NO_DATA],
                            statistics[ValueStatus.OVERRANGE],
                        )
                        if cycle < READ_CYCLES:
                            time.sleep(POLL_INTERVAL_S)

                    session.assert_no_error("Messwertabfrage")

                except WTError as exc:
                    log.error("Abbruch: %s", exc)
                    exit_code = 1

                finally:
                    # 6) Wiederherstellung in derselben Sitzung - die Verbindung
                    #    steht noch, das ist zuverlaessiger als ein zweiter Aufbau.
                    if backup is not None:
                        try:
                            written = restore_item_table(
                                session, backup, tail, force=FORCE_FULL_RESTORE
                            )
                            log.info("Wiederherstellung abgeschlossen (%d Kommandos)", written)
                            remaining = verify_item_table(session, backup)
                            if remaining:
                                for problem in remaining:
                                    log.error("Restore-Kontrolle: %s", problem)
                            else:
                                log.info(
                                    "Restore-Kontrolle: Ausgangszustand exakt wiederhergestellt"
                                )
                        except WTError as exc:
                            log.error(
                                "Wiederherstellung fehlgeschlagen: %s - Backup liegt unter %s",
                                exc,
                                backup_file,
                            )
                            exit_code = 1
            finally:
                # Eigenes finally fuer den gesamten Nutzteil: REMOTE muss auch
                # bei unerwarteten Ausnahmen vor dem Schliessen geloest werden.
                session.disable_remote()

    except WTError as exc:
        # Umfasst Verbindungs- und Konfigurationsfehler.
        log.error("Abbruch: %s", exc)
        if backup is not None:
            log.error("Backup liegt unter: %s", backup_file)
        return 1

    log.info("=" * 78)
    log.info("Stufe 3 beendet. Backup: %s", backup_file)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
