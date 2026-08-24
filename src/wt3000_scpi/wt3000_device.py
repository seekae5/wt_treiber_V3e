# =============================================================================
# Datei: wt3000_device.py
# NEU (ROADMAP M1-1): Layer 4 - die Fassade.
#
# Hintergrund. Bis hierher musste jeder Anwender Transport, Sitzung,
# InputConfig, RangeAccess und die Wiring-Units von Hand zusammenstecken - so,
# wie es die fuenf Stufenskripte jeweils erneut vormachen. Besonders die
# Verdrahtung der Wiring-Units war eine Stolperfalle: wer
#     RangeAccess(session, allow_changes=True)
# ohne 'sigma_members' anlegt, bekommt bei jedem SIGMA-Scope einen Fehler, und
# zwar erst mitten im Ablauf. Die Zuordnung steht am Geraet zur Verfuegung -
# sie zu erfragen war nur nirgends vorgesehen.
#
# Diese Datei ist der einzige Einstiegspunkt, den ein Anwender braucht:
#
#     from wt3000_scpi import WT3000, Quantity
#
#     with WT3000.connect(ip="192.168.10.20") as wt:
#         wt.device.log_summary()
#         print(wt.input.get_wiring())
#         print(wt.ranges.dump(Quantity.VOLTAGE))
#
# Fuenf Zeilen, danach ist sauber getrennt. Schreibend geht es nur, wenn beide
# Schloesser bewusst geoeffnet werden - die Voreinstellung ist read_only:
#
#     with WT3000.connect(read_only=False, allow_changes=True) as wt:
#         ...
#
# SCHICHTUNG. Layer 4 darf aus allen tieferen Schichten importieren und wird
# von keiner importiert. Die Stufenskripte bleiben unveraendert bestehen; sie
# sind ab jetzt Beispiele fuer den Weg ohne Fassade, nicht mehr der einzige.
#
# BEWUSST NICHT hier erledigt (jeweils eigener Meilenstein):
#   M1-3  TEILWEISE erledigt: die verbauten Geraeteoptionen werden seit dem
#         21.08.2026 beim Verbinden erhoben ('*OPT?') und sind ueber
#         DeviceInfo.supports()/require_option() abfragbar - Voraussetzung
#         fuer jede optionsgebundene Kommandogruppe. Offen bleiben die
#         Bereichstabellen nach Modultyp, InputConfig._elements_of('ALL')
#         (Befund B-12) und die Modellpruefung beim Verbinden.
#   M1-4  ensure_protocol_state() - der Sollzustand wird hier geprueft
#         (check_protocol_state), aber nicht hergestellt.
#   M1-5  drain_after_failure() wird weiterhin nirgends aufgerufen (B-04).
# =============================================================================

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

from .wt3000_common import (
    DEFAULT_ELEMENTS,
    condition_warnings,
    parse_condition,
    strip_response_header,
)
from .wt3000_core import TmctlTransport, Transport, WTConfig, WTError, WTSession
# NEU (M3-2/M2-1): die Geraetegruppen jenseits von ':INPut' und ':NUMeric'.
from .wt3000_deviceconfig import ComputationConfig, IntegrationConfig
from .wt3000_input import InputConfig, WiringUnit
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
from .wt3000_measure import (
    LoopStatistics,
    NumericHold,
    SampleSink,
    build_integration_profile,
    build_standard_profile,
    run_measurement_loop,
    write_metadata,
)
from .wt3000_sinks import CsvSink
from .wt3000_numeric import ItemTable, NumericItem, NumericValue, read_numeric_values
from .wt3000_rangeio import RangeAccess, sigma_members_from_units
from .wt3000_ranging import RangeBackup, RangePlan, RangeReport, applied_ranges

__all__ = [
    "OPTION_REQUIREMENTS",
    "DeviceInfo",
    "ItemAccess",
    "MeasureControl",
    "WT3000",
    "parse_options",
]

_log = logging.getLogger("wt3000.device")


# ---------------------------------------------------------------------------
# Geraeteoptionen
# NEU (ROADMAP M1-3 "Optionen und Firmware erfassen (pruefen)")
# ---------------------------------------------------------------------------
#
# Zehn der 22 SCPI-Kommandogruppen des WT3000 haengen an einer verbauten
# Hardwareoption (docs/ANALYSE_FEHLENDE_FUNKTIONEN.md, Abschnitt 0.1). Fehlt
# sie, ist das Kommando nicht etwa wirkungslos: das Geraet legt einen Eintrag
# in die Fehlerqueue und ANTWORTET NICHT - der Query laeuft in den Timeout.
# Ohne diese Tabelle faellt das erst dort auf, und zwar mit einer Meldung, die
# nach Verbindungsabbruch aussieht statt nach fehlender Option. Genau deshalb
# steht Rang 0 der Analyse vor den Raengen 3, 5, 8 und 10: erst wissen, was
# das Geraet kann, dann daran bauen.
#
# '*OPT?' liefert die Bestueckung als kommagetrennte Liste (Handbuch 6-115),
# am eingemessenen Geraet 'G6,B5,DT,C7,C5,CC'; ist keine Option verbaut,
# antwortet das Geraet mit '0'. Die Abfrage ist ein Common Command und selbst
# an keine Option gebunden - sie funktioniert also immer.
#
# ':MOTor' FEHLT IN DIESER TABELLE, UND ZWAR MIT ABSICHT. Das Handbuch nennt
# zu '*OPT?' zwar eine "motor evaluation function (MTR)", am realen Geraet
# (Protokoll vom 21.08.2026, tools/hardware/probe_capabilities.py) trug dieses
# Indiz aber nicht: '*OPT?' meldete KEIN MTR, ':MOTor:PM?' antwortete
# trotzdem. Zuverlaessig war dort der Modellcode aus '*IDN?' ('760304-40-MV').
# Stuende ':MOTor' mit ('MTR',) in der Tabelle, wuerde der Treiber eine
# vorhandene Gruppe abweisen - schlimmer als gar keine Pruefung. Die Gruppe
# wird deshalb in 'supports()' gesondert behandelt: Modellcode ODER MTR.


#: Kommandogruppe -> Optionscodes, von denen MINDESTENS EINER verbaut sein muss.
#
# Die Schluessel stehen in der Langform mit der ueblichen SCPI-Auszeichnung
# (Grossbuchstaben = Kurzform des Handbuchs). Verglichen wird unabhaengig von
# Gross- und Kleinschreibung, ABER nicht gegen die Kurzform: ':harmonics'
# trifft, ':HARM' nicht. Ein Unterknoten erbt die Anforderung seines Eintrags,
# ':HARMonics:ORDer' also die von ':HARMonics'.
OPTION_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    ":HARMonics": ("G5", "G6"),
    ":ACQuisition": ("G6",),
    ":CURSor:FFT": ("G6",),
    ":DISPlay:FFT": ("G6",),
    ":CBCycle": ("CC",),
    ":FLICker": ("FL",),
    ":AOUTput": ("DA",),
    ":HCOPy": ("B5", "C7"),
    ":MEASure:DMeasure": ("DT",),
    ":MEASure:COMPensation:V3A3": ("DT",),
}

#: Dieselbe Tabelle in Grossschrift - der Vergleichsschluessel, einmal gebaut.
_OPTION_REQUIREMENTS_UPPER: dict[str, tuple[str, ...]] = {
    node.upper(): codes for node, codes in OPTION_REQUIREMENTS.items()
}


def normalize_option_code(code: str) -> str:
    """Einen Optionscode vergleichbar machen: '/G6', ' g6 ' und 'G6' sind eins.

    Das Handbuch schreibt die Optionen in der Bestellbezeichnung mit
    Schraegstrich ('/G6'), '*OPT?' antwortet ohne ihn ('G6'). Beide Formen
    sollen hier zum selben Ergebnis fuehren, damit ein Aufrufer die Schreibung
    nicht raten muss.
    """
    return code.strip().upper().lstrip("/")


def parse_options(response: str) -> frozenset[str]:
    """Antwort auf '*OPT?' in eine Menge von Optionscodes zerlegen.

    Die '0' des Geraets bedeutet "keine Option verbaut" und ist deshalb KEIN
    Code, sondern liefert die leere Menge.
    """
    text = strip_response_header(response)
    return frozenset(
        code for code in (normalize_option_code(t) for t in text.split(",")) if code and code != "0"
    )


def required_options(group: str) -> tuple[str, ...] | None:
    """Welche Optionen kommen fuer diese Kommandogruppe in Frage?

    Rueckgabe: Tupel der Codes, von denen einer genuegt - oder None, wenn die
    Gruppe an keine Option gebunden ist (':INTEGrate', ':MEASure', ':STORe',
    ':WAVeform' und die uebrigen Basisgruppen).
    """
    key = group.strip().upper()
    treffer = [
        (node, codes)
        for node, codes in _OPTION_REQUIREMENTS_UPPER.items()
        if key == node or key.startswith(node + ":")
    ]
    if not treffer:
        return None
    # Der laengste Treffer gewinnt: ':MEASure:DMeasure' ist genauer als ein
    # (hier nicht vorhandener, spaeter denkbarer) Eintrag ':MEASure'.
    return max(treffer, key=lambda paar: len(paar[0]))[1]


# ---------------------------------------------------------------------------
# Geraetesteckbrief
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceInfo:
    """Was beim Verbinden einmalig ueber das Geraet erhoben wird.

    Bewusst klein gehalten (ROADMAP M1-1): hier steht genau das, was die
    Fassade braucht, um die Fachobjekte zu verdrahten.

    UEBERARBEITET (M1-3, Teil "Optionen und Firmware erfassen"): die verbauten
    Geraeteoptionen gehoeren inzwischen dazu. Sie stehen hier und nicht an
    einer zweiten Stelle, weil sie dieselbe Eigenschaft haben wie Verdrahtung
    und Modultypen - einmal beim Verbinden erhoben, danach unveraenderlich.
    Wer wissen will, ob eine Kommandogruppe an diesem Geraet ueberhaupt
    ansprechbar ist, fragt 'supports()' oder laesst 'require_option()' den
    Fehler mit Begruendung werfen:

        if wt.device.supports(":HARMonics"):
            ...

    Offen aus M1-3 bleiben die Bereichstabellen nach Modultyp und die
    Modellpruefung beim Verbinden.
    """

    #: Rohantwort auf '*IDN?'. 'unbekannt', wenn die Abfrage fehlgeschlagen ist.
    identity: str
    #: Hersteller, Modell, Seriennummer, Firmware - aus identity zerlegt.
    manufacturer: str
    model: str
    serial: str
    firmware: str
    #: Verdrahtungsmuster in Elementreihenfolge, z.B. ('V3A3', 'P1W2').
    wiring: tuple[str, ...]
    #: Wiring-Units mit Elementzuordnung.
    wiring_units: tuple[WiringUnit, ...]
    #: Elementnummer -> Modultyp (30, 2 oder 0 = nicht bestueckt).
    modules: dict[int, int] = field(default_factory=dict)
    #: Bestueckte Elemente, aufsteigend.
    elements: tuple[int, ...] = DEFAULT_ELEMENTS
    #: Scope-Abbildung fuer RangeAccess, z.B. {'SIGMA': (1,2,3), 'SIGMB': (4,)}.
    sigma_members: dict[str, tuple[int, ...]] = field(default_factory=dict)
    #: True, wenn die Elementliste angenommen werden musste statt gelesen.
    elements_assumed: bool = False
    #: Rohantwort auf '*OPT?'. 'unbekannt', wenn die Abfrage fehlgeschlagen ist.
    options_raw: str = "unbekannt"
    #: Verbaute Optionscodes ohne Schraegstrich, z.B. {'G6', 'DT', 'CC'}.
    options: frozenset[str] = frozenset()
    #: True, wenn '*OPT?' beantwortet wurde.
    #
    # Der Unterschied ist wichtig: eine leere 'options' heisst bei
    # options_known=True "keine Option verbaut" (Geraeteantwort '0'), bei
    # options_known=False dagegen "nicht bekannt". Nur im ersten Fall darf der
    # Treiber eine Gruppe vorab abweisen - im zweiten wuerde er raten.
    options_known: bool = False

    # -- Erzeugen -----------------------------------------------------------

    @classmethod
    def read(cls, session: WTSession) -> "DeviceInfo":
        """Steckbrief vom Geraet lesen. Reine Queries, veraendert nichts.

        Die Fehlerbehandlung ist mit Absicht zweigeteilt:

        '*IDN?' und '*OPT?' sind rein informativ - schlaegt eines fehl, wird
        das protokolliert und weitergearbeitet. Verdrahtung und Modultypen
        dagegen tragen die Verdrahtung der Fachobjekte; ohne sie muesste die
        Fassade die Elementzuordnung raten, und geraten wird in diesem Treiber
        nichts. Ein Fehler dort kommt deshalb als WTError heraus.

        NEU (M1-3): nach jedem der beiden informativen Queries steht im
        Fehlerfall 'drain_after_failure()'. Der Grund ist nicht Kosmetik -
        eine verspaetete Antwort wuerde sonst den NAECHSTEN Query beantworten,
        und der naechste ist hier entweder '*OPT?' (der dann eine
        Geraetekennung als Optionsliste laese) oder ':INPut:WIRing?' (das die
        Verdrahtung traegt). Der ganze Steckbrief waere um eine Position
        verschoben, ohne dass irgendwo ein Fehler auftraete.
        """
        identity = "unbekannt"
        try:
            identity = session.query("*IDN?")
        except WTError as error:
            _log.warning("*IDN? fehlgeschlagen: %s - Steckbrief bleibt unvollstaendig", error)
            session.drain_after_failure()

        parts = [p.strip() for p in identity.split(",")]
        while len(parts) < 4:
            parts.append("")

        # NEU (M1-3): die verbaute Bestueckung. Zur Reihenfolge sagt das
        # Handbuch (6-115): "The *OPT? query must be the last query of the
        # program message." Gemeint ist die einzelne Programmnachricht, und
        # WTSession sendet ohnehin genau einen Query je Nachricht - die Regel
        # ist hier also schon durch die Bauart eingehalten.
        options_raw = "unbekannt"
        options: frozenset[str] = frozenset()
        options_known = False
        try:
            options_raw = session.query("*OPT?")
            options = parse_options(options_raw)
            options_known = True
        except WTError as error:
            _log.warning(
                "*OPT? fehlgeschlagen: %s - die verbauten Optionen bleiben "
                "unbekannt; optionsgebundene Gruppen werden deshalb nicht "
                "vorab abgewiesen, sondern laufen im Zweifel ins Geraet",
                error,
            )
            session.drain_after_failure()

        # Rein lesende Sicht: dieses Objekt benutzt die vorhandenen Parser aus
        # wt3000_input, statt ':INPut:MODUle?' ein viertes Mal selbst zu
        # zerlegen (vgl. Befund B-03).
        reader = InputConfig(session, allow_changes=False)

        wiring = reader.get_wiring()
        units = tuple(reader.get_wiring_units())
        modules = reader.get_modules()

        populated = tuple(sorted(e for e, kind in modules.items() if kind != 0))
        assumed = False
        if not populated:
            _log.warning(
                "Kein bestuecktes Element gemeldet (:INPut:MODUle? -> %s) - "
                "es wird mit %s weitergearbeitet",
                modules,
                DEFAULT_ELEMENTS,
            )
            populated = DEFAULT_ELEMENTS
            assumed = True

        return cls(
            identity=identity,
            manufacturer=parts[0],
            model=parts[1],
            serial=parts[2],
            firmware=parts[3],
            wiring=wiring,
            wiring_units=units,
            modules=modules,
            elements=populated,
            sigma_members=sigma_members_from_units(units),
            elements_assumed=assumed,
            options_raw=options_raw,
            options=options,
            options_known=options_known,
        )

    # -- Auswerten ----------------------------------------------------------

    def describe(self) -> list[str]:
        """Steckbrief als Zeilenliste - fuer Protokoll und Konsole."""
        lines = [
            f"Geraet:      {self.model or '?'} ({self.manufacturer or '?'})",
            f"Seriennr.:   {self.serial or '?'}    Firmware: {self.firmware or '?'}",
            f"Optionen:    {self.options_summary()}",
            f"Verdrahtung: {', '.join(self.wiring) or '?'}",
            f"Elemente:    {self.elements}"
            + ("  (angenommen, nicht gelesen)" if self.elements_assumed else ""),
        ]
        # Was an DIESEM Geraet nicht ansprechbar ist, gehoert in den
        # Steckbrief und nicht erst in den Timeout des ersten Kommandos.
        gesperrt = self.unavailable_groups()
        if gesperrt:
            lines.append(
                "  Nicht ansprechbar (Option fehlt): "
                + ", ".join(f"{gruppe} ({'/'.join(codes)})" for gruppe, codes in gesperrt)
            )
        for element in sorted(self.modules):
            kind = self.modules[element]
            label = {30: "30-A-Element", 2: "2-A-Element", 0: "nicht bestueckt"}.get(
                kind, f"Typ {kind}"
            )
            lines.append(f"  Element {element}: {label}")
        for unit in self.wiring_units:
            lines.append(
                f"  Unit {unit.name or '-'}: {unit.pattern} auf Elementen {unit.elements}"
            )
        return lines

    def log_summary(self) -> None:
        """Steckbrief ins Protokoll schreiben."""
        for line in self.describe():
            _log.info("%s", line)

    def has_element(self, element: int) -> bool:
        """True, wenn dieses Element bestueckt ist."""
        return element in self.elements

    # -- Optionen (M1-3) ----------------------------------------------------

    def has_option(self, code: str) -> bool:
        """True, wenn dieser Optionscode als verbaut gemeldet wurde.

        '/G6' und 'G6' sind gleichwertig. Ist '*OPT?' fehlgeschlagen, ist die
        Antwort immer False - fuer die Frage "darf ich diese Gruppe
        ansprechen?" ist deshalb 'supports()' zustaendig und nicht diese
        Methode: nur 'supports()' unterscheidet "fehlt" von "unbekannt".
        """
        return normalize_option_code(code) in self.options

    @property
    def is_motor_model(self) -> bool:
        """True, wenn der Modellcode die Motorvariante '-MV' traegt.

        Die Motorauswertung ist keine Nachruestoption, sondern eine
        Modellvariante ('760304-40-MV'). Am eingemessenen Geraet war dieser
        Code der zuverlaessige Indikator, '*OPT?' dagegen nicht - die
        Begruendung steht am Kopf von OPTION_REQUIREMENTS.
        """
        return "MV" in {teil.strip().upper() for teil in self.model.split("-")}

    def supports(self, group: str) -> bool:
        """Ist diese SCPI-Kommandogruppe an diesem Geraet ansprechbar?

        Drei Faelle, und der dritte ist der, auf den es ankommt:

        * Die Gruppe braucht keine Option (':INTEGrate', ':MEASure',
          ':STORe', ':WAVeform', ...) -> True, ohne Ruecksicht auf '*OPT?'.
        * Die Gruppe braucht eine, und '*OPT?' wurde beantwortet -> True,
          wenn mindestens einer der in Frage kommenden Codes verbaut ist.
        * Die Gruppe braucht eine, aber '*OPT?' ist fehlgeschlagen -> True.
          Unbekannt ist NICHT dasselbe wie "fehlt": lieber laeuft das
          Kommando ins Geraet und scheitert dort mit dessen eigener Meldung,
          als dass der Treiber eine vorhandene Gruppe aufgrund einer
          fehlenden Antwort sperrt. Die Warnung dazu steht im Protokoll von
          'read()'.
        """
        key = group.strip().upper()
        if key == ":MOTOR" or key.startswith(":MOTOR:"):
            # Sonderfall, siehe Kopf von OPTION_REQUIREMENTS.
            return self.is_motor_model or self.has_option("MTR")
        required = required_options(key)
        if required is None:
            return True
        if not self.options_known:
            return True
        return any(code in self.options for code in required)

    def require_option(self, group: str) -> None:
        """Vor dem ersten Kommando einer optionsgebundenen Gruppe aufrufen.

        Wirft WTError, wenn die Option nachweislich fehlt - mit Modellcode
        und Rohantwort im Text, damit die Meldung ohne Rueckfrage einzuordnen
        ist. Ohne diesen Aufruf antwortet das Geraet auf ein Kommando einer
        nicht verbauten Gruppe gar nicht; der Query laeuft in den Timeout und
        die Meldung sieht nach Verbindungsabbruch aus.
        """
        if self.supports(group):
            return
        required = required_options(group)
        codes = " oder ".join(required) if required else "MTR bzw. Modellvariante -MV"
        raise WTError(
            f"Kommandogruppe {group} ist an diesem Geraet nicht ansprechbar: "
            f"Option {codes} fehlt. Modell {self.model or '?'}, "
            f"*OPT? -> {self.options_raw}"
        )

    def unavailable_groups(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Alle Gruppen, deren Option nachweislich fehlt - fuer den Steckbrief.

        Solange '*OPT?' unbeantwortet blieb, ist die Rueckgabe leer: nichts
        ist nachweislich gesperrt, wenn nichts bekannt ist.
        """
        if not self.options_known:
            return ()
        gesperrt = [
            (gruppe, codes)
            for gruppe, codes in OPTION_REQUIREMENTS.items()
            if not self.supports(gruppe)
        ]
        if not self.supports(":MOTor"):
            gesperrt.append((":MOTor", ("Modellvariante -MV",)))
        return tuple(gesperrt)

    def options_summary(self) -> str:
        """Optionen als eine Zeile - unterscheidet 'keine' von 'unbekannt'."""
        if not self.options_known:
            return "unbekannt (*OPT? nicht beantwortet)"
        if not self.options:
            return "keine verbaut (*OPT? -> 0)"
        return ", ".join(sorted(self.options))


# ---------------------------------------------------------------------------
# Item-Tabelle als Objekt
# ---------------------------------------------------------------------------


class ItemAccess:
    """Bindet die Ablauffunktionen aus wt3000_itemspec an eine Sitzung.

    Die Funktionen dort sind bewusst frei und ohne Zustand geblieben - sie
    nehmen alle eine 'session' als ersten Parameter. Diese Klasse ist die
    Stelle, an der die Sitzung genau einmal eingesetzt wird, damit der
    Aufrufer sie nicht durch jeden Aufruf durchreichen muss.
    """

    def __init__(self, session: WTSession, allow_changes: bool = False) -> None:
        self._session = session
        self._allow_changes = allow_changes

    @property
    def allow_changes(self) -> bool:
        """True, wenn dieses Objekt schreiben darf."""
        return self._allow_changes

    def _require_writable(self) -> None:
        if not self._allow_changes:
            raise WTError(
                "Schreibzugriff auf die Item-Tabelle abgelehnt: WT3000 wurde ohne "
                "allow_changes=True geoeffnet."
            )

    # -- Lesen --------------------------------------------------------------

    def read(self) -> ItemTable:
        """Aktuelle Item-Tabelle vom Geraet lesen."""
        return ItemTable.read_from_device(self._session)

    @staticmethod
    def integration_profile() -> tuple[ItemSpec, ...]:
        """Messprofil fuer eine Wh-/Ah-Messung (TIME, WH, AH, ... je Element).

        Das Gegenstueck zu 'wt.integration': jene Klasse steuert den Lauf,
        dieses Profil macht sein Ergebnis lesbar.
        """
        return build_integration_profile()

    @staticmethod
    def standard_profile() -> tuple[ItemSpec, ...]:
        """Das Messprofil dieses Aufbaus (aus wt3000_measure)."""
        return build_standard_profile()

    @staticmethod
    def build(specs: Sequence[ItemSpec]) -> ItemTable:
        """Aus einer Spec-Liste die Zieltabelle erzeugen."""
        return build_item_table(list(specs))

    def verify(self, target: ItemTable) -> list[str]:
        """Ist-Tabelle mit der Anforderung vergleichen. Leer = in Ordnung."""
        return verify_item_table(self._session, target)

    def capture_tail(self, backup: ItemTable, target: ItemTable) -> list[NumericItem]:
        """Items jenseits von NUMber sichern, die die Zieltabelle ueberschreibt."""
        return probe_extra_items(
            self._session,
            first_index=len(backup.items) + 1,
            last_index=len(target.items),
        )

    # -- Schreiben ----------------------------------------------------------

    def apply(self, target: ItemTable, backup: ItemTable | None = None) -> None:
        """Zieltabelle schreiben und verifizieren.

        Vor dem Schreiben der ganzen Tabelle geht genau EIN Item als Probe
        hinaus. Faellt die durch, ist ein einziges Item veraendert statt
        aller - das ist der Grund fuer den Umweg.
        """
        self._require_writable()
        probe_item_write_capability(self._session, target, backup or self.read())
        apply_item_table(self._session, target)
        problems = self.verify(target)
        if problems:
            for problem in problems:
                _log.error("Verifikation: %s", problem)
            raise WTError(f"{len(problems)} Abweichung(en) beim Verifizieren der Item-Tabelle")

    def restore(
        self, backup: ItemTable, tail: Sequence[NumericItem] = (), force: bool = False
    ) -> int:
        """Gesicherten Zustand wiederherstellen. Rueckgabe: Anzahl Kommandos."""
        self._require_writable()
        return restore_item_table(self._session, backup, list(tail), force=force)

    @contextmanager
    def applied(
        self,
        specs: Sequence[ItemSpec] | ItemTable,
        backup_file: Path | None = None,
        force_restore: bool = False,
    ) -> Iterator[ItemTable]:
        """Tabelle setzen, Block ausfuehren, Ausgangszustand garantiert zurueck.

        Das Gegenstueck zu 'applied_ranges()' fuer die Item-Tabelle: derselbe
        try/finally-Ablauf, den Stufe 3 und Stufe 4 heute jeweils von Hand
        nachbauen - sichern, Tail sichern, Schreibprobe, anwenden,
        verifizieren, Nutzblock, wiederherstellen.

        Die Wiederherstellung laeuft im finally und damit auch bei Strg+C.

        UEBERARBEITET (P-2, siehe PLAN_BEFUNDE_2026-08-19.md): Was 'garantiert'
        hier bedeutet, ist jetzt auch im Fehlerfall wahr. Wer diesen Block ohne
        Ausnahme verlaesst, darf sich darauf verlassen, dass die Item-Tabelle
        wieder im Ausgangszustand steht. Misslingt die Wiederherstellung, kommt
        eine WTError heraus - siehe die Begruendung im finally.
        """
        self._require_writable()

        target = specs if isinstance(specs, ItemTable) else self.build(specs)
        backup = self.read()
        tail = self.capture_tail(backup, target)
        if backup_file is not None:
            save_backup_bundle(backup_file, backup, tail)

        try:
            self.apply(target, backup)
            yield target
        finally:
            # UEBERARBEITET (P-2, siehe PLAN_BEFUNDE_2026-08-19.md): Der Fehler
            # wurde hier bisher nur protokolliert und dann verschluckt. Ein
            # Aufrufer konnte den Kontextmanager also normal verlassen, obwohl
            # die Item-Tabelle nicht wiederhergestellt war - genau das, was der
            # Docstring ausschliesst. 'applied_ranges()' in wt3000_ranging.py
            # macht es an derselben Stelle seit jeher richtig und loest erneut
            # aus; die beiden Ablaeufe verhalten sich jetzt gleich.
            #
            # Zur Fehlerverkettung: eine im finally ausgeloeste Ausnahme traegt
            # eine bereits unterwegs befindliche automatisch als '__context__'
            # mit. Schlaegt also erst der Nutzblock fehl und dann die
            # Wiederherstellung, zeigt der Traceback beide - ohne Zutun und
            # ohne Abhaengigkeit von Python 3.11.
            try:
                self.restore(backup, tail, force=force_restore)

                # Gegenprobe. 'applied_ranges()' protokolliert das Ergebnis
                # nur, weil es einen RangeReport herausgibt, in dem der
                # Aufrufer danach nachsehen kann. Hier gibt es kein solches
                # Objekt - dieser Kontextmanager liefert die ItemTable. Eine
                # bloss protokollierte Abweichung waere deshalb wieder
                # unbemerkbar, also dieselbe Falle eine Ebene tiefer. Sie wird
                # gemeldet.
                problems = self.verify(backup)
                if problems:
                    for problem in problems:
                        _log.error("Restore-Kontrolle: %s", problem)
                    raise WTError(
                        f"{len(problems)} Abweichung(en) nach der Wiederherstellung "
                        "der Item-Tabelle"
                    )
                _log.info("Restore-Kontrolle: Ausgangszustand exakt wiederhergestellt")

            except WTError as error:
                location = backup_file if backup_file is not None else "nicht gesichert"
                _log.error(
                    "Wiederherstellung der Item-Tabelle fehlgeschlagen: %s - Backup: %s",
                    error,
                    location,
                )
                raise


# ---------------------------------------------------------------------------
# Messung
# ---------------------------------------------------------------------------


class MeasureControl:
    """Messwerte lesen und aufzeichnen.

    ZUM UMFANG: die Messschleife ist weiterhin blockierend und bricht nur ueber
    Strg+C oder ein gesetztes Limit ab. Sie hier anzubinden macht sie
    erreichbar, nicht steuerbar - das ist M3-1 (Aufzeichnung als Objekt mit
    start()/stop()) und ausdruecklich nicht Teil von M1-1.

    ENTSCHIEDEN (21.08.2026), frueher hier als offene Zustaendigkeitsfrage
    notiert: Die Geraetesteuerung (':INTEGrate:STARt / :STOP / :RESet') sitzt
    NICHT hier, sondern in 'wt3000_deviceconfig.IntegrationConfig' - dem
    Modul, das ROADMAP Abschnitt 3 unter M2-1 ohnehin vorsieht. Der damals
    hier notierte Vorschlag ("die Knotenebene gehoert nach unten in die
    Konfigurationsschicht") ist genau so umgesetzt worden, und die befuerchtete
    vierte Parserkopie ist nicht entstanden: das neue Modul benutzt
    ausschliesslich die Regeln aus wt3000_common.

    Was hier bleibt, ist die Leseseite: die aufgelaufenen Werte kommen ueber
    die Item-Tabelle wie alle Messwerte. Das passende Profil steht in
    'wt3000_measure.build_integration_profile()' und ist ueber
    'ItemAccess.integration_profile()' erreichbar - also neben
    'standard_profile()', wo ein Messprofil hingehoert.

    OFFEN (ROADMAP M0-3) - blockiert die Erprobung von M3-2, nicht dessen
    Umsetzung: jedes Kommando dort ist ein Set-Kommando (auch '*CLS'), verlangt
    also read_only=False. Ob das Geraet Set-Kommandos ueber Ethernet ohne
    ':COMMunicate:REMote ON' annimmt, ist unbeantwortet - siehe
    WTConfig.use_remote. Gegen FakeTransport laesst sich M3-2 schreiben und
    pruefen; belegen laesst es sich erst am Geraet.
    """

    def __init__(self, session: WTSession, items: ItemAccess, read_only: bool = True) -> None:
        self._session = session
        self._items = items
        self._read_only = read_only

    # -- Einzelwerte --------------------------------------------------------

    def read_values(self, table: ItemTable | None = None) -> list[NumericValue]:
        """Einen Datensatz als Werteliste lesen (Reihenfolge = Item-Reihenfolge)."""
        expected = len(table.items) if table is not None else None
        return read_numeric_values(self._session, expected_count=expected)

    def read_mapped(self, table: ItemTable | None = None) -> dict[str, NumericValue]:
        """Einen Datensatz auf sprechende Namen abgebildet lesen."""
        used = table if table is not None else self._items.read()
        return used.map_values(self.read_values(used))

    def hold(self, enabled: bool = True) -> NumericHold:
        """Context Manager fuer ':NUMeric:HOLD'.

        In einer Nur-Lesen-Sitzung wird HOLD abgeschaltet statt einen Fehler
        auszuloesen: HOLD ist ein Set-Kommando, und read_only heisst, dass
        nichts gesendet wird. Die Werte sind dann ungefroren - der Zeitstempel
        wird unschaerfer, die Messung bleibt gueltig.
        """
        if enabled and self._read_only:
            _log.warning("Nur-Lesen-Sitzung: HOLD wird nicht benutzt (Set-Kommando)")
            enabled = False
        return NumericHold(self._session, enabled=enabled)

    # -- Aufzeichnung -------------------------------------------------------

    def record(
        self,
        sink: SampleSink,
        table: ItemTable,
        interval_s: float = 1.0,
        max_samples: int | None = None,
        max_duration_s: float | None = None,
        use_hold: bool = True,
        record_condition: bool = True,
        log_every: int = 0,
        metadata_path: Path | None = None,
        parameters: dict | None = None,
    ) -> LoopStatistics:
        """Messschleife in eine beliebige Senke schreiben.

        UEBERARBEITET (ROADMAP M4-2): nahm bis hierher einen 'csv_path' und
        legte die CSV selbst an - damit war die Fassade auf ein Ausgabeformat
        festgelegt, obwohl das Zielbild ausdruecklich 'CSV, mit Platz fuer
        weitere Formate' verlangt. Jetzt nimmt sie die Senke entgegen:

            wt.measure.record(CsvSink(pfad), tabelle)
            wt.measure.record(JsonlSink(pfad), tabelle)
            wt.measure.record(MultiSink(CsvSink(a), CallbackSink(anzeigen)), tabelle)

        Fuer den haeufigsten Fall gibt es 'record_csv()' - ein Aufruf, der die
        Senke selbst baut.

        Die Senke wird von der Messschleife geoeffnet und geschlossen; hier
        wird sie nur weitergereicht. Blockiert bis zum Erreichen eines Limits
        oder bis Strg+C. Ohne Limit laeuft sie unbegrenzt weiter - das ist
        Absicht, aber beim Einbau in fremden Code selten gewollt.
        """
        if use_hold and self._read_only:
            _log.warning("Nur-Lesen-Sitzung: Messschleife laeuft ohne HOLD")
            use_hold = False

        # Dieselben Angaben gehen an den Sidecar UND an die Senke: ein Format
        # wie JSONL legt sie mit in die Datei, die CSV laesst sie liegen.
        lauf_parameter: dict[str, object] = {
            "sample_interval_s": interval_s,
            "max_samples": max_samples,
            "max_duration_s": max_duration_s,
            "use_hold": use_hold,
            "record_condition": record_condition,
            **(parameters or {}),
        }

        if metadata_path is not None:
            write_metadata(metadata_path, self._session, table, parameters=lauf_parameter)

        return run_measurement_loop(
            session=self._session,
            table=table,
            sink=sink,
            interval_s=interval_s,
            max_samples=max_samples,
            max_duration_s=max_duration_s,
            use_hold=use_hold,
            record_condition=record_condition,
            log_every=log_every,
            metadata=lauf_parameter,
        )

    def record_csv(
        self,
        csv_path: Path,
        table: ItemTable,
        interval_s: float = 1.0,
        max_samples: int | None = None,
        max_duration_s: float | None = None,
        use_hold: bool = True,
        record_condition: bool = True,
        log_every: int = 0,
        delimiter: str = ",",
        metadata_path: Path | None = None,
        parameters: dict | None = None,
    ) -> LoopStatistics:
        """Messschleife in eine CSV schreiben - der haeufigste Fall.

        NEU (ROADMAP M4-2): duenne Weiterleitung an 'record()' mit einer
        fertig gebauten 'CsvSink'. Sie besteht, damit der Normalfall ein
        Aufruf bleibt und nicht zwei werden - wer nur eine CSV will, soll sich
        mit dem Sink-Begriff gar nicht befassen muessen.
        """
        return self.record(
            CsvSink(csv_path, delimiter=delimiter),
            table,
            interval_s=interval_s,
            max_samples=max_samples,
            max_duration_s=max_duration_s,
            use_hold=use_hold,
            record_condition=record_condition,
            log_every=log_every,
            metadata_path=metadata_path,
            parameters={"csv_file": csv_path.name, **(parameters or {})},
        )


# ---------------------------------------------------------------------------
# Die Fassade
# ---------------------------------------------------------------------------


class WT3000:
    """Ein verbundenes WT3000 - der einzige Einstiegspunkt des Treibers.

    Erzeugt wird ausschliesslich ueber die Klassenmethoden:

        WT3000.connect(ip="192.168.10.20")          # rein lesend
        WT3000.from_config(WTConfig(ip="..."), read_only=False, allow_changes=True)
        WT3000.from_transport(FakeTransport({...}))  # geraetefrei, fuer Tests

    ZWEI SCHLOESSER, unveraendert aus den Fachmodulen uebernommen:

        read_only=True      die Sitzung lehnt jedes Nicht-Query-Kommando ab
        allow_changes=False InputConfig/RangeAccess/ItemAccess lehnen jeden
                            Schreibaufruf schon vor dem Senden ab

    Beide stehen in der Voreinstellung zu. Wer messen und nichts veraendern
    will - der Normalfall - braucht keinen der beiden Schalter anzufassen.
    Ausserdem bleiben die Gruppen aus 'DEFAULT_PROTECTED' auch bei
    allow_changes=True gesperrt und muessen einzeln ueber
    'wt.input.unlocked(...)' freigegeben werden.
    """

    def __init__(
        self,
        transport: Transport,
        config: WTConfig | None = None,
        read_only: bool = True,
        allow_changes: bool = False,
        owns_transport: bool = True,
    ) -> None:
        if allow_changes and read_only:
            raise WTError(
                "allow_changes=True zusammen mit read_only=True ist widerspruechlich: "
                "die Sitzung wuerde jedes Set-Kommando ohnehin ablehnen. "
                "Fuer Schreibzugriff read_only=False setzen."
            )

        self._config = config if config is not None else WTConfig()
        self._transport = transport
        self._owns_transport = owns_transport
        self._read_only = read_only
        self._allow_changes = allow_changes
        self._closed = False

        self._session = WTSession(transport, self._config, read_only=read_only)

        # Fernsteuerung nur einschalten, wenn ueberhaupt geschrieben werden
        # darf: ':COMMunicate:REMote ON' ist selbst ein Set-Kommando und
        # scheitert in einer Nur-Lesen-Sitzung an der eigenen Sperre.
        if self._config.use_remote and not read_only:
            self._session.enable_remote()
        elif self._config.use_remote:
            _log.info("Nur-Lesen-Sitzung: ':COMMunicate:REMote ON' wird nicht gesendet")

        # UEBERARBEITET (P-1, siehe PLAN_BEFUNDE_2026-08-19.md): ab hier laeuft
        # der Rest des Konstruktors unter Aufraeumschutz.
        #
        # Vorher stand DeviceInfo.read() ungeschuetzt hinter enable_remote().
        # Scheiterte eine der dortigen Pflichtabfragen - ':INPut:WIRing?' oder
        # ':INPut:MODUle?' -, verliess die Ausnahme den Konstruktor, ohne dass
        # ':COMMunicate:REMote OFF' je gesendet wurde: das Bedienfeld blieb
        # gesperrt zurueck. close() konnte das nicht auffangen, weil bei einem
        # gescheiterten Konstruktor gar kein Objekt entsteht, an dem close()
        # aufrufbar waere.
        #
        # Die Reparatur sitzt bewusst HIER und nicht in from_config(): nur so
        # sind alle drei Wege abgedeckt - from_config(), from_transport() und
        # die direkte Konstruktion. from_transport() raeumte bisher gar nicht
        # auf, weil es den Transport nicht besitzt.
        try:
            # ROADMAP M1-1: die bisher manuelle Verdrahtung
            # sigma_members_from_units(cfg.get_wiring_units()) passiert hier -
            # einmalig, beim Verbinden, fuer alle Fachobjekte gemeinsam.
            self._device = DeviceInfo.read(self._session)
            self._device.log_summary()
        except BaseException:
            # Auch bei Strg+C waehrend des Verbindungsaufbaus: das Bedienfeld
            # gehoert freigegeben.
            self._release_remote_after_failure()
            raise

        self._input: InputConfig | None = None
        self._ranges: RangeAccess | None = None
        self._items: ItemAccess | None = None
        self._measure: MeasureControl | None = None
        self._integration: IntegrationConfig | None = None
        self._computation: ComputationConfig | None = None

    # -- Erzeugen -----------------------------------------------------------

    @classmethod
    def connect(
        cls,
        ip: str | None = None,
        read_only: bool = True,
        allow_changes: bool = False,
        dll_path: str | None = None,
        timeout_ms: int | None = None,
        use_remote: bool | None = None,
    ) -> "WT3000":
        """Ueber die TMCTL-DLL verbinden. Nicht angegebene Werte aus WTConfig.

        Der haeufigste Aufruf ueberhaupt:

            with WT3000.connect() as wt:
                ...
        """
        # UEBERARBEITET (P-7, siehe PLAN_BEFUNDE_2026-08-19.md): Grundlage ist
        # jetzt die Auflaesungskette aus WTConfig.from_environment() -
        # ausdruecklicher Parameter vor Umgebungsvariable vor
        # Konfigurationsdatei vor Voreinstellung. Ein blosses WTConfig() traegt
        # seit P-7 keine IP mehr; connect() ohne Argumente holt sie von dort.
        config = WTConfig.from_environment(
            ip=ip,
            dll_path=dll_path,
            timeout_ms=timeout_ms,
            use_remote=use_remote,
        )
        return cls.from_config(config, read_only=read_only, allow_changes=allow_changes)

    @classmethod
    def from_config(
        cls, config: WTConfig, read_only: bool = True, allow_changes: bool = False
    ) -> "WT3000":
        """Mit einer fertigen WTConfig verbinden. Die Fassade schliesst den Transport."""
        transport = TmctlTransport(config)
        try:
            return cls(
                transport,
                config,
                read_only=read_only,
                allow_changes=allow_changes,
                owns_transport=True,
            )
        except BaseException:
            # Der Transport steht schon, die Sitzung ist aber nicht zustande
            # gekommen (z.B. weil ':INPut:WIRing?' nicht antwortet). Ohne
            # dieses except bliebe die Verbindung offen.
            #
            # UEBERARBEITET (P-1, siehe PLAN_BEFUNDE_2026-08-19.md): Der
            # Kommentar behauptete hier zusaetzlich, dieser Block verhindere
            # auch, dass das Geraet in Fernsteuerung stehen bleibt. Das hat er
            # nie getan - ein 'REMote OFF' kam an dieser Stelle nicht vor, und
            # nach transport.close() waere es ohnehin ins Leere gegangen.
            # Zustaendig ist jetzt der Konstruktor selbst; er schaltet die
            # Fernsteuerung ab, BEVOR die Ausnahme hier ankommt. Dieser Block
            # kuemmert sich nur noch um den Transport, den nur dieser Weg
            # besitzt.
            transport.close()
            raise

    @classmethod
    def from_transport(
        cls,
        transport: Transport,
        config: WTConfig | None = None,
        read_only: bool = True,
        allow_changes: bool = False,
        owns_transport: bool = False,
    ) -> "WT3000":
        """Auf einem bereits bestehenden Transport aufsetzen.

        Damit laeuft die Fassade auch auf 'FakeTransport' (M1-2) und spaeter
        auf einem Socket- oder VISA-Transport. Voreinstellung ist hier
        owns_transport=False: wer den Transport mitbringt, schliesst ihn auch.
        """
        return cls(
            transport,
            config,
            read_only=read_only,
            allow_changes=allow_changes,
            owns_transport=owns_transport,
        )

    # -- Eigenschaften ------------------------------------------------------

    @property
    def session(self) -> WTSession:
        """Die Protokollschicht. Notausgang fuer Kommandos ohne eigene Methode."""
        return self._session

    @property
    def config(self) -> WTConfig:
        """Die benutzten Verbindungsparameter."""
        return self._config

    @property
    def device(self) -> DeviceInfo:
        """Steckbrief, einmalig beim Verbinden erhoben."""
        return self._device

    @property
    def read_only(self) -> bool:
        """True, wenn die Sitzung kein Set-Kommando durchlaesst."""
        return self._read_only

    @property
    def allow_changes(self) -> bool:
        """True, wenn die Fachobjekte schreiben duerfen."""
        return self._allow_changes

    @property
    def input(self) -> InputConfig:
        """Eingangs- und Messkonfiguration (':INPut'), fertig verdrahtet."""
        self._require_open()
        if self._input is None:
            self._input = InputConfig(self._session, allow_changes=self._allow_changes)
        return self._input

    @property
    def ranges(self) -> RangeAccess:
        """Messbereiche und Autorange - mit Elementliste und Wiring-Units.

        Genau die Verdrahtung, die bisher jeder Aufrufer selbst herstellen
        musste und in stage5b schlicht fehlt: ohne 'sigma_members' laeuft dort
        jeder SIGMA-Scope in einen Fehler.
        """
        self._require_open()
        if self._ranges is None:
            self._ranges = RangeAccess(
                self._session,
                allow_changes=self._allow_changes,
                elements=self._device.elements,
                sigma_members=self._device.sigma_members,
            )
        return self._ranges

    @property
    def items(self) -> ItemAccess:
        """Item-Tabelle der NUMeric-Gruppe."""
        self._require_open()
        if self._items is None:
            self._items = ItemAccess(self._session, allow_changes=self._allow_changes)
        return self._items

    @property
    def integration(self) -> IntegrationConfig:
        """Integrationsfunktion (':INTEGrate') - Wh- und Ah-Messung steuern.

        NEU (ROADMAP M3-2, Rang 1 der Analyse). Die Gruppe braucht keine
        Geraeteoption; 'wt.device.supports(\":INTEGrate\")' ist immer wahr und
        wird hier deshalb nicht abgefragt.

        Zur Sperre: 'allow_changes' wird durchgereicht wie bei den anderen
        Fachobjekten, und ':INTEGrate:RESet' bleibt zusaetzlich geschuetzt -
        es verwirft den Zaehlerstand unwiderruflich. Freigabe ausdruecklich
        ueber 'wt.integration.unlocked(GROUP_RESET)'.
        """
        self._require_open()
        if self._integration is None:
            self._integration = IntegrationConfig(
                self._session, allow_changes=self._allow_changes
            )
        return self._integration

    @property
    def computation(self) -> ComputationConfig:
        """Rechenfunktionen (':MEASure') - Averaging, Wirkungsgrad, Frequenzquelle.

        NEU (ROADMAP M2-1 Punkt 2/3, Rang 2 der Analyse). Hier zeigt sich, was
        der Steckbrief aus M1-3 wert ist: die Fassade reicht drei Dinge hinein,
        die das Fachmodul selbst nicht wissen kann -

          * die bestueckte Elementliste, gegen die 'P<x>'/'U<x>' geprueft wird
            (dieselbe, die auch 'wt.ranges' bekommt),
          * ob '/G6' verbaut ist - nur damit ist der S/Q-Formelsatz TYPE3
            waehlbar,
          * ob das Geraet die Motorvariante traegt - nur dann ist 'PM' als
            Glied einer Wirkungsgradgleichung zulaessig.

        Beide Faehigkeiten gehen als bool und nicht als 'vielleicht' hinein:
        'supports()' und 'is_motor_model' haben die Unbekannt-Frage bereits
        entschieden (siehe DeviceInfo.supports - unbekannt gilt dort als
        'nicht ausgeschlossen').
        """
        self._require_open()
        if self._computation is None:
            self._computation = ComputationConfig(
                self._session,
                allow_changes=self._allow_changes,
                elements=self._device.elements,
                advanced_computation=self._device.has_option("G6"),
                motor=self._device.is_motor_model,
            )
        return self._computation

    @property
    def measure(self) -> MeasureControl:
        """Messwerte lesen und aufzeichnen."""
        self._require_open()
        if self._measure is None:
            self._measure = MeasureControl(self._session, self.items, read_only=self._read_only)
        return self._measure

    # -- Ablaeufe -----------------------------------------------------------

    def check_protocol_state(self) -> None:
        """Voraussetzungen der Binaerauswertung pruefen. Veraendert nichts.

        ':COMMunicate:HEADer 0' und ':NUMeric:FORMat FLOat' sind keine
        Feinheiten: mit Headern scheitert das Parsen der Item-Tabelle, im
        ASCii-Format kommt kein Blockheader, den query_block() zerlegen kann.

        Diese Methode ist der designierte Ort fuer Befund B-14 (dieselbe
        Pruefung liegt heute in stage2/3/4 in drei leicht abweichenden
        Fassungen) und die Grundlage fuer M1-4, das den Sollzustand dann nicht
        nur prueft, sondern herstellt und beim Verlassen zuruecknimmt.
        """
        header = self._session.query(":COMMunicate:HEADer?")
        if header.strip() != "0":
            raise WTError(
                f":COMMunicate:HEADer ist {header!r}, erwartet '0'. "
                "Mit Headern schlaegt das Parsen der Item-Tabelle fehl."
            )

        fmt = self._session.query(":NUMeric:FORMat?")
        if not fmt.upper().startswith("FLO"):
            raise WTError(
                f":NUMeric:FORMat ist {fmt!r}, erwartet 'FLO'. "
                "Messwerte werden ausschliesslich als Binaerblock gelesen."
            )

        self.log_condition()

    def log_condition(self) -> int:
        """':STATus:CONDition?' auswerten und Auffaelligkeiten protokollieren."""
        # UEBERARBEITET (Schritt 5b, Befund A-06 / S-02): parse_condition()
        # statt int(), und die Bitauswertung kommt aus wt3000_common statt
        # aus einer vierten hauseigenen Fassung. Bit 15 (POV) fehlte hier.
        bits = parse_condition(self._session.query(":STATus:CONDition?"))
        for meldung in condition_warnings(bits):
            _log.warning("%s", meldung)
        return bits

    def range_backup(self) -> RangeBackup:
        """Ist-Zustand aller Bereiche sichern."""
        return RangeBackup.capture(self.ranges)

    @contextmanager
    def applied_ranges(
        self,
        plan: RangePlan,
        backup_file: Path | None = None,
        allow_snapping: bool = False,
        force_restore: bool = False,
    ) -> Iterator[RangeReport]:
        """Bereiche nach Plan setzen, Block ausfuehren, Ausgangszustand zurueck.

        Duenne Weiterleitung an 'wt3000_ranging.applied_ranges()' mit dem
        bereits verdrahteten RangeAccess - der Ablauf selbst bleibt dort, wo
        er getestet ist.
        """
        with applied_ranges(
            self.ranges,
            plan,
            backup_file=backup_file,
            allow_snapping=allow_snapping,
            force_restore=force_restore,
        ) as report:
            yield report

    # -- Beenden ------------------------------------------------------------

    def _require_open(self) -> None:
        if self._closed:
            raise WTError("Diese WT3000-Sitzung ist bereits geschlossen")

    # UEBERARBEITET (P-1, siehe PLAN_BEFUNDE_2026-08-19.md): Gegenstueck zu
    # close() fuer den Fall, dass der Konstruktor nicht durchlaeuft.
    def _release_remote_after_failure(self) -> None:
        """Fernsteuerung zuruecknehmen, wenn der Verbindungsaufbau scheitert.

        Raeumt ausschliesslich das ab, was der Konstruktor selbst angerichtet
        hat - also ':COMMunicate:REMote ON'. Der Transport wird hier bewusst
        NICHT geschlossen: wer ihn erzeugt hat, schliesst ihn auch. Fuer
        from_config() ist das die Fassade selbst, fuer from_transport() der
        Aufrufer.

        disable_remote() ist fuer diesen Einsatz bereits richtig gebaut: es
        prueft '_remote_active', sendet also nichts, wenn nie eingeschaltet
        wurde, und faengt WTError selbst ab.
        """
        try:
            self._session.disable_remote()
        except Exception as error:  # bewusst breit
            # Ein Fehler beim Aufraeumen darf die eigentliche Ursache niemals
            # verdecken - deshalb nur protokollieren, nicht ausloesen.
            _log.error(
                "REMote OFF nach fehlgeschlagenem Verbindungsaufbau misslungen: %s - "
                "Bedienfeld ggf. am Geraet ueber die LOCAL-Taste freigeben",
                error,
            )

    def close(self) -> None:
        """Sauber trennen. Mehrfachaufruf ist unschaedlich.

        Reihenfolge und Fehlerbehandlung sind hier das Wesentliche: jeder
        Schritt laeuft in seinem eigenen try, damit ein misslungener Schritt
        die folgenden nicht ueberspringt. Ein haengengebliebenes HOLD ist der
        unangenehmste Rest, den eine abgebrochene Sitzung hinterlassen kann -
        das Geraet liefert dann in der naechsten Sitzung eingefrorene Werte,
        waehrend die Anzeige weiterlaeuft.
        """
        if self._closed:
            return
        self._closed = True

        if not self._read_only:
            try:
                self._session.write(":NUMeric:HOLD OFF")
                _log.info("HOLD abgeschaltet")
            except WTError as error:
                _log.error("HOLD OFF fehlgeschlagen: %s - Geraet manuell pruefen", error)

        try:
            self._session.disable_remote()
        except WTError as error:  # pragma: no cover - disable_remote faengt selbst
            _log.error("REMote OFF fehlgeschlagen: %s", error)

        if self._owns_transport:
            try:
                self._transport.close()
            except Exception as error:  # bewusst breit: der Transport ist austauschbar
                _log.error("Transport konnte nicht geschlossen werden: %s", error)

    def __enter__(self) -> "WT3000":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
