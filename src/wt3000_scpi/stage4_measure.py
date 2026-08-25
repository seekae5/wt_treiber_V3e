# =============================================================================
# Datei: stage4_measure.py
# Layer 4 - Stufe 4: Messschleife mit HOLD-Snapshot, Zeitstempel, CSV und
#                    sauberer Abbruchbehandlung.
#
# Geschrieben werden AUSSCHLIESSLICH: Item-Tabelle, NUMber, :NUMeric:HOLD.
# Ranges, Wiring, Filter, Skalierung, Update-Rate und Frequenzmessquelle
# bleiben unangetastet. Der Ausgangszustand wird am Ende wiederhergestellt.
# =============================================================================

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

# Paketmodule werden mit 'python -m wt3000_scpi.stage4_measure' gestartet.
from .wt3000_common import (
    condition_warnings,
    output_dir,
    parse_condition,
    parse_nr3,
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
    apply_item_table,
    build_item_table,
    probe_extra_items,
    probe_item_write_capability,
    restore_item_table,
    save_backup_bundle,
    verify_item_table,
)
from .wt3000_measure import (
    build_standard_profile,
    run_measurement_loop,
    write_metadata,
)
from .wt3000_sinks import CsvSink
from .wt3000_numeric import ItemTable, NumericItem

# ---------------------------------------------------------------------------
# Laufparameter - hier anpassen
# ---------------------------------------------------------------------------

# Abtastintervall. Sollte mindestens :RATE? entsprechen (aktuell 1.00E+00 s);
# schnelleres Abfragen liefert nur Wiederholungen desselben Datensatzes.
SAMPLE_INTERVAL_S: float = 1.0

# None = laeuft bis Strg+C. Sonst Abbruch beim jeweils ersten erreichten Limit.
MAX_SAMPLES: int | None = 60
MAX_DURATION_S: float | None = None

# HOLD als Zeitstempel-Anker: friert den Datensatz vor dem Lesen ein.
# False spart ein Set-Kommando pro Zyklus, macht den Zeitstempel aber unschaerfer.
USE_HOLD: bool = True

# :STATus:CONDition? je Sample mitschreiben (FOV, PLLE, Overrange, Peak Over).
RECORD_CONDITION: bool = True

# Alle n Samples eine Statuszeile ins Log. 0 = keine.
LOG_EVERY: int = 10

# Fuer deutsches Excel auf ";" setzen. Der Dezimalpunkt bleibt bewusst "." -
# sonst ist die Datei fuer jedes andere Auswertewerkzeug unbrauchbar.
CSV_DELIMITER: str = ","

# Zielverzeichnis fuer CSV, Metadaten, Backup und Protokoll.
OUTPUT_DIR: Path = output_dir("messungen")

# Freitext, landet in der Metadatendatei.
RUN_COMMENT: str = ""


def check_preconditions(session: WTSession) -> None:
    """Voraussetzungen pruefen, ohne etwas zu veraendern."""
    log = logging.getLogger("wt3000.stage4")

    header = session.query(":COMMunicate:HEADer?")
    if header.strip() != "0":
        raise WTError(f":COMMunicate:HEADer ist {header!r}, erwartet '0'")

    fmt = session.query(":NUMeric:FORMat?")
    if not fmt.upper().startswith("FLO"):
        raise WTError(f":NUMeric:FORMat ist {fmt!r}, erwartet 'FLO'")

    rate = parse_nr3(session.query(":RATE?"), ":RATE")
    if SAMPLE_INTERVAL_S < rate:
        log.warning(
            "Abtastintervall %.3f s liegt unter :RATE = %.3f s - "
            "es werden Wiederholungen desselben Datensatzes aufgezeichnet",
            SAMPLE_INTERVAL_S,
            rate,
        )

    # Gemeinsamer Parser liefert WTError und deckt alle bekannten Bits ab.
    for meldung in condition_warnings(parse_condition(session.query(":STATus:CONDition?"))):
        log.warning("%s", meldung)


def main() -> int:
    """Stufe 4 ausfuehren. Rueckgabewert 0 = erfolgreich."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    log_file = OUTPUT_DIR / f"wt3000_stage4_{timestamp}.txt"
    csv_file = OUTPUT_DIR / f"wt3000_measurement_{timestamp}.csv"
    meta_file = OUTPUT_DIR / f"wt3000_measurement_{timestamp}_meta.json"
    backup_file = OUTPUT_DIR / f"wt3000_itemtable_backup_{timestamp}.json"

    setup_logging(log_file)
    log = logging.getLogger("wt3000.stage4")
    log.info("Protokolldatei: %s", log_file)

    specs = build_standard_profile()
    log.info("Stufe 4 - Messschleife (%d Items, Intervall %.3f s)", len(specs), SAMPLE_INTERVAL_S)
    if MAX_SAMPLES is None and MAX_DURATION_S is None:
        log.info("Kein Limit gesetzt - Abbruch mit Strg+C")

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

                    # 1) Ist-Zustand sichern.
                    backup = ItemTable.read_from_device(session)
                    target = build_item_table(specs)
                    tail = probe_extra_items(
                        session,
                        first_index=len(backup.items) + 1,
                        last_index=len(target.items),
                    )
                    save_backup_bundle(backup_file, backup, tail)

                    # 2) Fail-Fast, dann Tabelle schreiben und verifizieren.
                    probe_item_write_capability(session, target, backup)
                    apply_item_table(session, target)
                    problems = verify_item_table(session, target)
                    if problems:
                        for problem in problems:
                            log.error("Verifikation: %s", problem)
                        raise WTError(
                            f"{len(problems)} Abweichung(en) beim Verifizieren der Tabelle"
                        )

                    # 3) Metadaten sichern, bevor die Messung startet.
                    write_metadata(
                        meta_file,
                        session,
                        target,
                        parameters={
                            "sample_interval_s": SAMPLE_INTERVAL_S,
                            "max_samples": MAX_SAMPLES,
                            "max_duration_s": MAX_DURATION_S,
                            "use_hold": USE_HOLD,
                            "record_condition": RECORD_CONDITION,
                            "csv_file": csv_file.name,
                            "comment": RUN_COMMENT,
                        },
                    )

                    # 4) Messschleife. Sie verwaltet Kopf und Lebenszyklus der Senke.
                    log.info("Start der Messung. Abbruch jederzeit mit Strg+C.")
                    stats = run_measurement_loop(
                        session=session,
                        table=target,
                        sink=CsvSink(csv_file, delimiter=CSV_DELIMITER),
                        interval_s=SAMPLE_INTERVAL_S,
                        max_samples=MAX_SAMPLES,
                        max_duration_s=MAX_DURATION_S,
                        use_hold=USE_HOLD,
                        record_condition=RECORD_CONDITION,
                        log_every=LOG_EVERY,
                    )
                    stats.log_summary(SAMPLE_INTERVAL_S)

                    session.assert_no_error("Messschleife")

                except WTError as error:
                    log.error("Abbruch: %s", error)
                    exit_code = 1

                finally:
                    # 5) Ausgangszustand wiederherstellen - in derselben Sitzung.
                    if backup is not None:
                        try:
                            written = restore_item_table(session, backup, tail)
                            log.info("Wiederherstellung abgeschlossen (%d Kommandos)", written)
                            remaining = verify_item_table(session, backup)
                            if remaining:
                                for problem in remaining:
                                    log.error("Restore-Kontrolle: %s", problem)
                                exit_code = 1
                            else:
                                log.info(
                                    "Restore-Kontrolle: Ausgangszustand exakt wiederhergestellt"
                                )
                        except WTError as error:
                            log.error(
                                "Wiederherstellung fehlgeschlagen: %s - Backup: %s",
                                error,
                                backup_file,
                            )
                            exit_code = 1
            finally:
                # Eigenes finally fuer den gesamten Nutzteil: REMOTE muss auch
                # bei unerwarteten Ausnahmen vor dem Schliessen geloest werden.
                session.disable_remote()

    except WTError as error:
        # Umfasst Verbindungs- und Konfigurationsfehler.
        log.error("Abbruch: %s", error)
        if backup is not None:
            log.error("Backup liegt unter: %s", backup_file)
        return 1

    log.info("=" * 78)
    log.info("Messdaten:  %s", csv_file)
    log.info("Metadaten:  %s", meta_file)
    log.info("Backup:     %s", backup_file)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
