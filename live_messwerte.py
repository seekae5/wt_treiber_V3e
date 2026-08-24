#!/usr/bin/env python
# =============================================================================
# Datei: live_messwerte.py
# Kleines Anwenderskript zum Erproben des gebauten Pakets 'wt3000_scpi':
# verbindet sich mit dem WT3000, liest die AKTUELL im Geraet eingestellte
# Item-Tabelle und gibt die Messwerte im Takt in der Konsole aus.
#
# Die Sitzung ist bewusst NUR LESEND (read_only=True): das Skript schreibt
# keine Item-Tabelle, keine Bereiche und kein HOLD. Was am Geraet eingestellt
# ist, bleibt eingestellt - deshalb ist es zum Ausprobieren ungefaehrlich.
# Folge davon: ohne HOLD ist der Zeitstempel unschaerfer als bei einer
# Messreihe mit stage4/record_csv. Fuer eine Sichtpruefung reicht das.
#
# Aufruf (IP aus wt3000.json / WT3000_IP, sonst --ip):
#     python live_messwerte.py
#     python live_messwerte.py --ip 192.168.10.20 --intervall 0.5 --anzahl 20
#     python live_messwerte.py --csv messwerte/live.csv
# Abbruch mit Strg+C.
# =============================================================================

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from wt3000_scpi import (
    WT3000,
    CallbackSink,
    CsvSink,
    MultiSink,
    Sample,
    ValueStatus,
    WTError,
)


class KonsolenAnzeige:
    """Rueckruf fuer 'CallbackSink': ein Datensatz -> eine Zeile.

    Laeuft im Takt der Messschleife, macht also absichtlich nichts weiter als
    formatieren und ausgeben - alles Aufwendigere verzoegert den naechsten
    Zyklus (siehe Doku von CallbackSink).
    """

    def __init__(self, spalten: list[str], kopf_alle: int = 20) -> None:
        self._spalten = spalten
        self._breiten = [max(10, len(name) + 1) for name in spalten]
        self._kopf_alle = kopf_alle
        self._zeilen = 0

    def _kopf(self) -> None:
        kopf = "  ".join(f"{n:>{b}}" for n, b in zip(self._spalten, self._breiten))
        print(f"\n{'Nr':>5}  {'t/s':>8}  {kopf}", flush=True)

    def __call__(self, sample: Sample) -> None:
        if self._zeilen % self._kopf_alle == 0:
            self._kopf()
        self._zeilen += 1

        felder = []
        for wert, breite in zip(sample.values, self._breiten):
            if wert.status is ValueStatus.OK:
                felder.append(f"{wert.value:>{breite}.5g}")
            else:
                felder.append(f"{'<' + wert.status.value + '>':>{breite}}")

        zeile = f"{sample.number:>5}  {sample.elapsed_s:>8.2f}  " + "  ".join(felder)
        # Auffaelligkeiten des ganzen Zyklus (DUPLICATE/MISSING) hinten dran.
        if sample.mark.value != "OK":
            zeile += f"   [{sample.mark.value}]"
        print(zeile, flush=True)


def argumente() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Messwerte des WT3000 live in der Konsole ausgeben (nur lesend)."
    )
    p.add_argument("--ip", default=None, help="Geraete-IP; sonst aus WT3000_IP / wt3000.json")
    p.add_argument(
        "--intervall", type=float, default=1.0, help="Abtastintervall in s (Vorgabe 1.0)"
    )
    p.add_argument("--anzahl", type=int, default=None, help="Nach n Datensaetzen beenden")
    p.add_argument("--dauer", type=float, default=None, help="Nach n Sekunden beenden")
    p.add_argument("--csv", type=Path, default=None, help="Zusaetzlich in diese CSV schreiben")
    p.add_argument("--debug", action="store_true", help="Protokoll der Bibliothek mit ausgeben")
    return p.parse_args()


def main() -> int:
    args = argumente()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    try:
        with WT3000.connect(ip=args.ip, read_only=True) as wt:
            for zeile in wt.device.describe():
                print(zeile)

            # Die Tabelle des Geraets, nicht eine eigene: nur lesend heisst,
            # dass gemessen wird, was dort ohnehin eingestellt ist.
            tabelle = wt.items.read()
            spalten = [item.key for item in tabelle.items]
            print(f"Items:       {len(spalten)}  ({', '.join(spalten)})")
            print(f"Takt:        {args.intervall:.3f} s   Abbruch mit Strg+C")

            anzeige = CallbackSink(KonsolenAnzeige(spalten))
            senke = MultiSink(CsvSink(args.csv), anzeige) if args.csv else anzeige

            stats = wt.measure.record(
                senke,
                tabelle,
                interval_s=args.intervall,
                max_samples=args.anzahl,
                max_duration_s=args.dauer,
                use_hold=False,  # Set-Kommando - in einer Nur-Lesen-Sitzung nicht erlaubt
                record_condition=True,
            )

        print(f"\nFertig: {stats.samples} Datensaetze, {stats.overruns} Overruns")
        if args.csv:
            print(f"CSV:    {args.csv}")
    except WTError as exc:
        print(f"Fehler: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
