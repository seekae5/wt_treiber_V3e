# Gemeinsamer Sicherungspunkt fuer die Sitzung. Die Fachmodule erfassen,
# serialisieren und restaurieren ihre Daten selbst; SessionBackup bestimmt nur
# Reihenfolge und Endkontrolle.
#
# Messbereiche werden ueber InputSnapshot restauriert. RangeBackup erfasst
# denselben Zustand unabhaengig und dient nur zur Kontrolle. Messwerte und
# Integrationszaehler sind Ergebnisse, kein wiederherstellbarer Geraetezustand.

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .wt3000_common import strip_response_header
from .wt3000_core import WTError, WTSession
from .wt3000_deviceconfig import (
    ComputationConfig,
    ComputationSettings,
    HarmonicsConfig,
    HarmonicsSettings,
    IntegrationConfig,
    IntegrationSettings,
)
from .wt3000_input import InputConfig, InputSnapshot, restore_input_snapshot
from .wt3000_itemspec import probe_extra_items, restore_item_table, verify_item_table
from .wt3000_numeric import ItemTable, NumericItem
from .wt3000_rangeio import RangeAccess
from .wt3000_ranging import RangeBackup

__all__ = ["BACKUP_VERSION", "SessionBackup"]

_log = logging.getLogger("wt3000.backup")

#: Formatversion der Backup-Datei.
#
# Sie steht in der Datei und wird beim Laden geprueft. Eine aeltere Datei
# stillschweigend zu akzeptieren, waere die schlechteste aller Moeglichkeiten:
# fehlende Felder faenden sich erst beim Wiederherstellen - also genau dann,
# wenn das Geraet schon halb verstellt ist.
BACKUP_VERSION: int = 1

#: Wie weit ueber NUMber hinaus die Item-Tabelle mitgesichert wird.
#
# ':NUMeric:NORMal?' gibt nur Items bis NUMber aus. Wer spaeter eine LAENGERE
# Tabelle setzt, ueberschreibt Items, die nie gesichert wurden - genau dafuer
# gibt es 'probe_extra_items()'. 64 ist die uebliche Obergrenze der hier
# gebauten Profile (das Oberschwingungsprofil kommt dem am naechsten).
DEFAULT_TAIL_LIMIT: int = 64


@dataclass
class SessionBackup:
    """Gerätesteckbrief, Konfiguration und Item-Tabelle in einem Datensatz.

    Jedes Feld darf None sein: was nicht erfasst wurde, wird auch nicht
    wiederhergestellt. Das ist kein Schoenheitsfehler, sondern der Normalfall -
    ':HARMonics' gibt es nur mit der passenden Option, und wer nur die
    Eingangskonfiguration sichern will, soll nicht das ganze Geraet abfragen
    muessen.
    """

    #: Steckbrief als einfache Abbildung - Modell, Seriennummer, Firmware,
    #: Optionen, Elemente, Verdrahtung. Bewusst KEIN 'DeviceInfo': das ist
    #: Layer 4, dieses Modul ist Layer 3. Die Fassade fuellt es.
    device: dict[str, Any] = field(default_factory=dict)
    input: InputSnapshot | None = None
    ranges: RangeBackup | None = None
    items: ItemTable | None = None
    item_tail: tuple[NumericItem, ...] = ()
    integration: IntegrationSettings | None = None
    computation: ComputationSettings | None = None
    harmonics: HarmonicsSettings | None = None
    created: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    version: int = BACKUP_VERSION

    # =======================================================================
    # Erfassen
    # =======================================================================

    @classmethod
    def capture(
        cls,
        device: Mapping[str, Any] | None = None,
        input_config: InputConfig | None = None,
        range_access: RangeAccess | None = None,
        session: WTSession | None = None,
        integration: IntegrationConfig | None = None,
        computation: ComputationConfig | None = None,
        harmonics: HarmonicsConfig | None = None,
        tail_limit: int = DEFAULT_TAIL_LIMIT,
    ) -> "SessionBackup":
        """Alles erfassen, was uebergeben wurde. Reine Queries.

        Weggelassene Bausteine bleiben None. Es wird nichts geraten und nichts
        nachgeholt: wer 'range_access' nicht uebergibt, bekommt kein
        Bereichsbackup - und beim Wiederherstellen auch keine Ueberraschung.
        """
        backup = cls(device=dict(device or {}))

        if input_config is not None:
            backup.input = InputSnapshot.capture(input_config)
        if range_access is not None:
            backup.ranges = RangeBackup.capture(range_access)
        if session is not None:
            backup.items = ItemTable.read_from_device(session)
            backup.item_tail = tuple(
                probe_extra_items(session, backup.items.number + 1, tail_limit)
            )
        if integration is not None:
            backup.integration = integration.capture()
        if computation is not None:
            backup.computation = computation.capture()
        if harmonics is not None:
            backup.harmonics = harmonics.capture()

        _log.info("Sitzungs-Backup erfasst: %s", ", ".join(backup.parts()) or "<leer>")
        return backup

    def parts(self) -> list[str]:
        """Namen der tatsaechlich erfassten Bausteine - fuer Protokoll und Diff."""
        vorhanden = []
        if self.input is not None:
            vorhanden.append("Eingangskonfiguration")
        if self.ranges is not None:
            vorhanden.append("Messbereiche")
        if self.items is not None:
            vorhanden.append(f"Item-Tabelle ({len(self.items.items)} + {len(self.item_tail)})")
        if self.integration is not None:
            vorhanden.append("Integration")
        if self.computation is not None:
            vorhanden.append("Rechenfunktionen")
        if self.harmonics is not None:
            vorhanden.append("Oberschwingungen")
        return vorhanden

    def describe(self) -> list[str]:
        """Inhalt als Zeilenliste - fuer Protokoll und Konsole."""
        lines = [
            f"Sitzungs-Backup v{self.version}, erfasst {self.created}",
            f"  Geraet:  {self.device.get('model', '?')} "
            f"(Seriennr. {self.device.get('serial', '?')}, "
            f"Firmware {self.device.get('firmware', '?')})",
            f"  Inhalt:  {', '.join(self.parts()) or '<leer>'}",
        ]
        if self.integration is not None:
            lines.extend(f"  {zeile}" for zeile in self.integration.describe())
        if self.computation is not None:
            lines.extend(f"  {zeile}" for zeile in self.computation.describe())
        if self.harmonics is not None:
            lines.extend(f"  {zeile}" for zeile in self.harmonics.describe())
        return lines

    def log_summary(self) -> None:
        """Inhalt ins Protokoll schreiben."""
        for line in self.describe():
            _log.info("%s", line)

    # =======================================================================
    # Datei
    # =======================================================================

    def to_dict(self) -> dict:
        """Serialisierbare Darstellung - alles ueber die to_dict() der Teile."""
        return {
            "version": self.version,
            "created": self.created,
            "device": self.device,
            "input": None if self.input is None else self.input.to_dict(),
            "ranges": None if self.ranges is None else self.ranges.to_dict(),
            "items": None if self.items is None else self.items.to_dict(),
            "item_tail": [
                {
                    "index": it.index,
                    "function": it.function,
                    "element": it.element,
                    "order": it.order,
                }
                for it in self.item_tail
            ],
            "integration": None if self.integration is None else self.integration.to_dict(),
            "computation": None if self.computation is None else self.computation.to_dict(),
            "harmonics": None if self.harmonics is None else self.harmonics.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionBackup":
        """Gegenstueck zu to_dict(). Prueft die Formatversion."""
        version = int(data.get("version", 0))
        if version != BACKUP_VERSION:
            raise WTError(
                f"Backup hat Formatversion {version}, dieser Treiber schreibt und "
                f"liest {BACKUP_VERSION}. Eine fremde Version wird nicht "
                "stillschweigend uebernommen - fehlende Felder fielen sonst erst "
                "beim Wiederherstellen auf."
            )
        return cls(
            device=dict(data.get("device") or {}),
            input=_optional(InputSnapshot, data.get("input")),
            ranges=_optional(RangeBackup, data.get("ranges")),
            items=_optional(ItemTable, data.get("items")),
            item_tail=tuple(
                NumericItem(
                    index=int(d["index"]),
                    function=d["function"],
                    element=d.get("element"),
                    order=d.get("order"),
                )
                for d in data.get("item_tail") or ()
            ),
            integration=_optional(IntegrationSettings, data.get("integration")),
            computation=_optional(ComputationSettings, data.get("computation")),
            harmonics=_optional(HarmonicsSettings, data.get("harmonics")),
            created=str(data.get("created", "")),
            version=version,
        )

    def save(self, path: Path) -> None:
        """Als JSON ablegen. Lesbar eingerueckt - ein Backup wird gelesen."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        _log.info("Sitzungs-Backup gesichert nach %s (%s)", path, ", ".join(self.parts()))

    @classmethod
    def load(cls, path: Path) -> "SessionBackup":
        """Backup aus JSON laden."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WTError(f"Backup {path} ist nicht lesbar: {exc}") from exc
        if not isinstance(data, dict):
            raise WTError(f"Backup {path} enthaelt kein JSON-Objekt")
        backup = cls.from_dict(data)
        _log.info("Sitzungs-Backup geladen aus %s (%s)", path, ", ".join(backup.parts()))
        return backup

    # =======================================================================
    # Wiederherstellen
    # =======================================================================

    def check_device(self, device: Mapping[str, Any]) -> list[str]:
        """Passt dieses Backup zu diesem Geraet? Rueckgabe: Abweichungen.

        Verglichen werden Modell und Seriennummer - nicht die Firmware und
        nicht die Optionen: ein Firmware-Update aendert den Steckbrief, ohne
        die gesicherten Einstellungen ungueltig zu machen.
        """
        abweichungen = []
        for feld in ("model", "serial"):
            gesichert = self.device.get(feld)
            aktuell = device.get(feld)
            if gesichert and aktuell and gesichert != aktuell:
                abweichungen.append(f"{feld}: Backup {gesichert!r}, Geraet {aktuell!r}")
        return abweichungen

    def restore(
        self,
        input_config: InputConfig | None = None,
        range_access: RangeAccess | None = None,
        session: WTSession | None = None,
        integration: IntegrationConfig | None = None,
        computation: ComputationConfig | None = None,
        harmonics: HarmonicsConfig | None = None,
        device: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> int:
        """Gesicherten Zustand zurueckschreiben. Rueckgabe: Anzahl Schritte.

        REIHENFOLGE, und warum sie so ist:

          1. Eingangskonfiguration - darin zuerst Crest-Faktor und Verdrahtung,
             danach Bereiche, Filter, Skalierung, Sync, Modus, Rate. Die
             Verdrahtung bestimmt, welches Element zu welcher Wiring-Unit
             gehoert; alles Weitere haengt daran. Diese innere Reihenfolge
             stellt 'restore_input_snapshot()' selbst her.
          2. Rechenfunktionen und Oberschwingungen - ihre Parameter verweisen
             auf Elemente ('P1', 'U3') und auf Wiring-Units ('SIGMA'). Vor
             Schritt 1 gesetzt, koennten sie auf eine Verdrahtung zeigen, die
             es gleich nicht mehr gibt.
          3. Integration - haengt an keinem der beiden, steht aber bewusst
             hinter ihnen: sie ist die Gruppe, die eine laufende Messung
             betrifft.
          4. Item-Tabelle zuletzt. Sie sagt, WAS gemessen wird, und verweist
             auf Elemente und Ordnungen, die es vorher geben muss.

        Die Messbereiche werden NICHT gesondert zurueckgeschrieben - sie
        stecken im Input-Snapshot. Die Begruendung steht im Dateikopf.

        Der Aufrufer muss die betroffenen Gruppen vorher freigeben, z.B.:

            with cfg.unlocked(GROUP_RANGE, GROUP_FILTER, GROUP_MODE, GROUP_RATE):
                backup.restore(input_config=cfg, ...)
        """
        if device is not None:
            abweichungen = self.check_device(device)
            if abweichungen and not force:
                raise WTError(
                    "Backup gehoert zu einem anderen Geraet: "
                    + "; ".join(abweichungen)
                    + ". Eine Konfiguration auf ein fremdes Geraet zu schreiben "
                    "kann es verstellen - mit force=True ausdruecklich erzwingbar."
                )
            for zeile in abweichungen:
                _log.warning("Geraeteabweichung uebergangen (force=True): %s", zeile)

        schritte = 0

        if self.input is not None and input_config is not None:
            schritte += restore_input_snapshot(input_config, self.input)
        elif self.input is not None:
            _log.warning("Eingangskonfiguration im Backup, aber kein InputConfig uebergeben")

        if self.computation is not None and computation is not None:
            computation.restore(self.computation)
            schritte += 1
        if self.harmonics is not None and harmonics is not None:
            harmonics.restore(self.harmonics)
            schritte += 1
        if self.integration is not None and integration is not None:
            integration.restore(self.integration)
            schritte += 1

        if self.items is not None and session is not None:
            schritte += restore_item_table(
                session, self.items, list(self.item_tail), force=force
            )

        if self.ranges is not None and range_access is not None:
            # Nicht schreiben - nur gegenlesen. Siehe Dateikopf.
            _log.info(
                "Bereichsteil des Backups wird nicht gesondert geschrieben "
                "(steckt im Input-Snapshot) - er dient der Endkontrolle"
            )

        _log.info("Sitzungs-Backup wiederhergestellt (%d Schritte)", schritte)
        return schritte

    # =======================================================================
    # Endkontrolle
    # =======================================================================

    def verify(
        self,
        input_config: InputConfig | None = None,
        range_access: RangeAccess | None = None,
        session: WTSession | None = None,
        integration: IntegrationConfig | None = None,
        computation: ComputationConfig | None = None,
        harmonics: HarmonicsConfig | None = None,
    ) -> list[str]:
        """Steht das Geraet wieder so da wie im Backup? Rueckgabe: Abweichungen.

        Leere Liste heisst: alles, was gesichert war und geprueft werden
        konnte, stimmt. Geprueft wird nur, wozu ein Objekt uebergeben wurde -
        dieselbe Regel wie bei capture() und restore().

        Der Bereichsteil wird hier zum Zweitbeleg: er ist ueber einen anderen
        Codepfad erfasst worden als der Input-Snapshot, deckt aber denselben
        Zustand ab. Weichen die beiden Urteile voneinander ab, stimmt etwas
        Grundsaetzliches nicht.
        """
        probleme: list[str] = []

        if self.input is not None and input_config is not None:
            jetzt = InputSnapshot.capture(input_config)
            probleme.extend(f"Eingang: {zeile}" for zeile in self.input.diff(jetzt))

        if self.ranges is not None and range_access is not None:
            aktuell = RangeBackup.capture(range_access)
            for gesichert in self.ranges.states:
                try:
                    jetzt = aktuell.state_of(gesichert.element)
                except WTError as fehler:
                    probleme.append(f"Bereiche: {fehler}")
                    continue
                if jetzt != gesichert:
                    probleme.append(
                        f"Bereiche Element {gesichert.element}: Backup {gesichert}, "
                        f"Geraet {jetzt}"
                    )

        if self.items is not None and session is not None:
            probleme.extend(f"Item-Tabelle: {z}" for z in verify_item_table(session, self.items))

        for name, gesichert, objekt in (
            ("Integration", self.integration, integration),
            ("Rechenfunktionen", self.computation, computation),
            ("Oberschwingungen", self.harmonics, harmonics),
        ):
            if gesichert is None or objekt is None:
                continue
            jetzt = objekt.capture()
            if jetzt != gesichert:
                probleme.append(f"{name}: Backup {gesichert}, Geraet {jetzt}")

        if probleme:
            _log.warning("Endkontrolle: %d Abweichung(en)", len(probleme))
        else:
            _log.info("Endkontrolle: Zustand stimmt mit dem Backup ueberein")
        return probleme


def _optional(cls: Any, data: Any) -> Any:
    """'None bleibt None, sonst from_dict()' - fuer jedes Teilstueck dasselbe."""
    return None if data is None else cls.from_dict(data)


def device_fingerprint(
    identity: str = "",
    manufacturer: str = "",
    model: str = "",
    serial: str = "",
    firmware: str = "",
    options: tuple[str, ...] = (),
    elements: tuple[int, ...] = (),
    wiring: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Steckbriefdaten in die Abbildung wandeln, die 'SessionBackup.device' fuehrt.

    Die Fassade fuellt das aus 'DeviceInfo'. Diese Funktion steht hier und
    nicht dort, damit das Feld genau EINE dokumentierte Form hat - ein Backup,
    dessen Steckbriefteil je nach Aufrufer anders aussieht, taugt nicht zur
    Identitaetspruefung.
    """
    return {
        "identity": strip_response_header(identity),
        "manufacturer": manufacturer,
        "model": model,
        "serial": serial,
        "firmware": firmware,
        "options": sorted(options),
        "elements": list(elements),
        "wiring": list(wiring),
    }
