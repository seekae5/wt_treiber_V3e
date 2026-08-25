# =============================================================================
# Datei: tools/hardware/probe_voltage_range.py
#
# GERAETESKRIPT. Baut eine echte Verbindung auf und SCHREIBT einen Messbereich.
#
# Liegt bewusst nicht unter tests/: die Testsuite laeuft ohne Geraet und ohne
# tmctl.dll, und tests/conftest.py setzt das aktiv durch. Aufruf:
#
#     python tools/hardware/probe_voltage_range.py
#
# (verlangt ein installiertes Paket - 'pip install -e .' - oder PYTHONPATH=src)
#
# Sendet ueber RangeAccess.set_range() einen Wert an ein unkritisches Element
# und liest ihn zurueck. Die Probe umfasst genau einen Wert und ein Element.
#
# Sicherheitsmassnahmen:
#   - Element 4 (Direkteingang, unkritisch fuer diese Probe)
#   - Ausgangswert wird vor dem Schreiben gelesen und im 'finally' wieder
#     gesetzt, auch bei Timeout oder Strg+C
#   - Fehlerqueue wird geprueft, NACHDEM zurueckgestellt wurde
#   - Scheitert die Rueckstellung selbst, nennt die Fehlermeldung den Sollwert,
#     der am Geraet von Hand einzustellen ist
#
# main() vergleicht Sende- und Ruecklesewert und liefert bei Abweichung 1.
# =============================================================================

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from wt3000_scpi.wt3000_common import output_dir, setup_logging
from wt3000_scpi.wt3000_core import (
    TmctlTransport,
    WTConfig,
    WTError,
    WTSession,
    config_file_in_use,
)
from wt3000_scpi.wt3000_rangeio import (
    Quantity,
    RangeAccess,
    RangeValue,
    ranges_match,
)

# ---------------------------------------------------------------------------
# Laufparameter
# ---------------------------------------------------------------------------

ELEMENT: int = 4
TEST_VALUE: float = 1000.0

#: Fernsteuerung waehrend der Probe. Bewusst NICHT aus 'config.use_remote':
#
# Die feste Einstellung trennt die Syntaxprobe von der Frage, ob REMOTE
# erforderlich ist. True schafft fuer die Syntax den sichersten Schreibzustand;
# den Gegenversuch ohne REMOTE uebernimmt stage5b_range_probe.py.
USE_REMOTE: bool = True

# Ablage relativ zur Projektwurzel.
OUTPUT_DIR: Path = output_dir("konfiguration")


def main() -> int:
    """Einen Wert per rangeio setzen und zuruecklesen. Rueckgabe: 0 = ok."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = OUTPUT_DIR / f"wt3000_probe_voltage_range_{timestamp}.txt"

    setup_logging(log_file)
    log = logging.getLogger("wt3000.probe_voltage_range")
    log.info("Protokolldatei: %s", log_file)

    exit_code = 0

    try:
        # Herkunft vor dem Lesen protokollieren; so bleibt auch fehlerhaftes
        # JSON einem konkreten Pfad zuordenbar.
        log.info("Konfigurationsdatei: %s", config_file_in_use() or "<keine, Voreinstellungen>")
        config = WTConfig.from_environment()
        log.info("Verbindung: %s", config.describe())

        with TmctlTransport(config) as transport:
            session = WTSession(transport, config, read_only=False)
            access = RangeAccess(session, allow_changes=True)

            if USE_REMOTE:
                session.enable_remote()
            log.info(
                "Fernsteuerung: %s (Modulkonstante, nicht aus der Konfiguration)",
                "ON" if USE_REMOTE else "OFF",
            )

            try:
                original = access.get_range(Quantity.VOLTAGE, ELEMENT)
                log.info(
                    "Ausgangswert Element %d: %s", ELEMENT, original.describe(Quantity.VOLTAGE)
                )

                # Der Testwert darf auch bei Timeout oder Strg+C nicht am
                # eingemessenen Geraet stehen bleiben.
                try:
                    command = access.set_range(Quantity.VOLTAGE, ELEMENT, TEST_VALUE)
                    log.info("Gesendet: %s", command)

                    readback = access.get_range(Quantity.VOLTAGE, ELEMENT)
                    log.info("Zurueckgelesen: %s", readback.describe(Quantity.VOLTAGE))

                    # ranges_match() und nicht values_match(): es vergleicht die
                    # Eingangsart mit. 10 A direkt und 10 V am Sensoreingang sind
                    # nicht derselbe Zustand, auch bei gleichem Zahlenwert.
                    erwartet = RangeValue(TEST_VALUE, sensor=False)
                    if ranges_match(erwartet, readback):
                        log.info(
                            "BELEG M0-1: Wert uebernommen - '%s' ist gueltige Syntax",
                            command,
                        )
                    else:
                        log.error(
                            "BELEG M0-1: gesendet %s, zurueckgelesen %s - NICHT uebernommen",
                            erwartet.describe(Quantity.VOLTAGE),
                            readback.describe(Quantity.VOLTAGE),
                        )
                        exit_code = 1

                finally:
                    # Auch bei Strg+C zwischen Schreiben und Ruecklesen. Ein
                    # Fehlschlag HIER wird protokolliert und nicht geworfen: sonst
                    # verdraengte er die urspruengliche Ausnahme. Dieselbe Haltung
                    # wie in Stufe 3 und 4 bei restore_item_table(). Die Meldung
                    # nennt den Wert, den jemand am Geraet von Hand zuruecksetzen
                    # muss - das ist die wichtigste Zeile, wenn es schiefgeht.
                    try:
                        zurueck = access.set_range(
                            Quantity.VOLTAGE, ELEMENT, original.value, sensor=original.sensor
                        )
                        log.info("Ausgangswert wiederhergestellt: %s", zurueck)
                    except WTError as error:
                        log.error(
                            "RUECKSTELLUNG FEHLGESCHLAGEN: %s - Element %d steht "
                            "moeglicherweise noch auf dem Testwert. Sollwert: %s",
                            error,
                            ELEMENT,
                            original.describe(Quantity.VOLTAGE),
                        )
                        exit_code = 1

                # Die Fehlerqueue deckt den GANZEN Vorgang ab, Rueckstellung
                # eingeschlossen - deshalb steht sie hinter dem finally und nicht
                # darin. Bricht der Nutzteil ab, wird sie nicht mehr erreicht: dann
                # traegt die Ausnahme selbst die Aussage, und ein zusaetzlicher
                # DeviceError wuerde sie nur verdecken.
                session.assert_no_error("Schreibprobe rangeio")

            finally:
                session.disable_remote()

    except WTError as error:
        log.error("Abbruch: %s", error)
        return 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
