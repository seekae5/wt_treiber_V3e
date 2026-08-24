

from __future__ import annotations

import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from wt3000_scpi import WT3000, WTError
from wt3000_scpi.wt3000_common import output_dir, setup_logging
from wt3000_scpi.wt3000_core import config_file_in_use
from wt3000_scpi.wt3000_itemspec import ItemSpec
from wt3000_scpi.wt3000_numeric import NumericValue


FUNKTIONEN: tuple[str, ...] = ("U", "I", "P", "S", "Q", "LAMBDA", "PHI")

# Umfang und Takt der Messreihe. Die Dauer folgt daraus:
#     Dauer = (ANZAHL_MESSUNGEN - 1) / MESSUNGEN_PRO_SEKUNDE
# Wer von der gewuenschten Dauer her denkt, rechnet umgekehrt:
#     20 Messungen ueber 60 s  ->  MESSUNGEN_PRO_SEKUNDE = 19 / 60 = 0.317
# Schneller zu messen als die Aktualisierungsrate des Geraets (':RATE?',
# typisch 1 s) liefert denselben Datensatz mehrfach - main() warnt dann.
ANZAHL_MESSUNGEN: int = 16
MESSUNGEN_PRO_SEKUNDE: float = 4

USE_REMOTE: bool = True

OUTPUT_DIR: Path = output_dir("messwerte")


def messprofil(elemente: tuple[int, ...]) -> tuple[ItemSpec, ...]:

    return tuple(
        ItemSpec(funktion, str(element)) for element in elemente for funktion in FUNKTIONEN
    )


def schreibe_messung(
    writer: Any,
    nummer: int,
    zeitstempel: str,
    sekunden: float,
    elemente: tuple[int, ...],
    werte: dict[tuple[int, str], NumericValue],
) -> None:

    for element in elemente:
        zeile: list[object] = [nummer, zeitstempel, f"{sekunden:.3f}", element]
        for funktion in FUNKTIONEN:
            wert = werte.get((element, funktion))
            if wert is None:
                zeile.append("")
            elif wert.is_usable:
                zeile.append(f"{wert.value:.6g}")
            else:
                zeile.append(f"<{wert.status.value}>")
        writer.writerow(zeile)


def main() -> int:
    """Messreihe ueber alle Elemente aufnehmen und ablegen. Rueckgabe: 0 = ok."""
    if ANZAHL_MESSUNGEN < 1:
        raise ValueError(f"ANZAHL_MESSUNGEN={ANZAHL_MESSUNGEN} - mindestens 1")
    if MESSUNGEN_PRO_SEKUNDE <= 0:
        raise ValueError(f"MESSUNGEN_PRO_SEKUNDE={MESSUNGEN_PRO_SEKUNDE} - muss positiv sein")

    abstand = 1.0 / MESSUNGEN_PRO_SEKUNDE
    dauer = (ANZAHL_MESSUNGEN - 1) * abstand

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = OUTPUT_DIR / f"wt3000_messwerte_{timestamp}.txt"
    csv_file = OUTPUT_DIR / f"wt3000_messwerte_{timestamp}.csv"

    setup_logging(log_file)
    log = logging.getLogger("wt3000.probe_measure_all_elements")
    log.info("Protokolldatei: %s", log_file)
    log.info("Messdatei:      %s", csv_file)
    log.info(
        "Messreihe: %d Messungen, %.4g/s (alle %.3f s), Dauer ca. %.1f s",
        ANZAHL_MESSUNGEN,
        MESSUNGEN_PRO_SEKUNDE,
        abstand,
        dauer,
    )

    geschrieben = 0
    ueberzogen = 0
    unbrauchbar = 0

    try:
        log.info("Konfigurationsdatei: %s", config_file_in_use() or "<keine, Voreinstellungen>")

        # Schreibzugriff nur wegen der Item-Tabelle.
        with WT3000.connect(
            read_only=False, allow_changes=True, use_remote=USE_REMOTE
        ) as wt:
            wt.check_protocol_state()

            elemente = wt.device.elements
            log.info("Bestueckte Elemente: %s", elemente)

            rate = wt.input.get_update_rate()
            log.info("Aktualisierungsrate des Geraets: %.3f s", rate)
            if abstand < rate:
                log.warning(
                    "Abtastintervall %.3f s liegt unter der Aktualisierungsrate "
                    "%.3f s - das Geraet liefert denselben Datensatz mehrfach",
                    abstand,
                    rate,
                )

            with wt.items.applied(messprofil(elemente)) as tabelle:
                with csv_file.open("w", newline="", encoding="utf-8") as datei:
                    writer = csv.writer(datei)
                    writer.writerow(
                        ["messung", "zeitstempel", "sekunden", "element", *FUNKTIONEN]
                    )
                    start = time.monotonic()

                    for nummer in range(1, ANZAHL_MESSUNGEN + 1):
                        # Takt absolut vom Start aus, nicht sleep(abstand) nach
                        # jeder Messung: sonst summiert sich die Dauer jeder
                        # einzelnen Messung zur Drift auf.
                        wartezeit = start + (nummer - 1) * abstand - time.monotonic()
                        if wartezeit > 0:
                            time.sleep(wartezeit)
                        elif nummer > 1:
                            ueberzogen += 1

                        sekunden = time.monotonic() - start
                        zeitstempel = datetime.now().isoformat(timespec="milliseconds")
                        gelesen = wt.measure.read_values(tabelle)

                        werte: dict[tuple[int, str], NumericValue] = {
                            (int(item.element or "1"), item.function): wert
                            for item, wert in zip(tabelle.items, gelesen)
                        }
                        unbrauchbar += sum(1 for w in werte.values() if not w.is_usable)

                        schreibe_messung(writer, nummer, zeitstempel, sekunden, elemente, werte)
                        # Jede Messung einzeln flushen: ein abgebrochener Lauf
                        # behaelt damit alles, was bis dahin gemessen wurde.
                        datei.flush()
                        geschrieben += 1

                        if nummer == 1:
                            for element in elemente:
                                spalten = " ".join(
                                    f"{funktion}={werte[(element, funktion)]}"
                                    for funktion in FUNKTIONEN
                                    if (element, funktion) in werte
                                )
                                log.info("Element %d: %s", element, spalten)
                        log.info(
                            "Messung %d/%d bei t=%.3f s: %d Werte",
                            nummer,
                            ANZAHL_MESSUNGEN,
                            sekunden,
                            len(werte),
                        )

    except KeyboardInterrupt:
        log.warning(
            "Abbruch durch Benutzer nach %d von %d Messungen", geschrieben, ANZAHL_MESSUNGEN
        )
    except WTError as error:
        log.error("Abbruch: %s", error)
        return 1

    log.info("%d Messung(en) in %s geschrieben", geschrieben, csv_file.name)
    if ueberzogen:
        log.warning(
            "%d Messung(en) kamen zu spaet - eine Messung dauert laenger als %.3f s",
            ueberzogen,
            abstand,
        )
    if unbrauchbar:
        log.warning("%d Einzelwert(e) waren nicht verwertbar (siehe CSV)", unbrauchbar)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
