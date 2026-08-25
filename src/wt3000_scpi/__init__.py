# =============================================================================
# Datei: src/wt3000_scpi/__init__.py
# NEU (Punkt 4, src-Layout): Paketwurzel des WT3000-Treibers.
#
# Hintergrund (ANALYSE.md 2.1 / Fundstellen Abschnitt 5): Unter 'Build/' lag
# eine vollstaendige Zweitkopie von 9 der 14 Module, die sich von der Wurzel nur
# durch relative Importe unterschied und die neuere Haelfte des Projekts
# (wt3000_common, wt3000_rangeio, wt3000_ranging, stage5b) gar nicht enthielt.
# Statt zwei Staende zu pflegen, ist jetzt die Wurzel selbst das Paket. Der
# Klon unter 'Build/' ist damit ersatzlos zu loeschen - er kann aus diesem
# Projektbestand heraus nicht geloescht werden, weil er hier nicht vorliegt.
#
# SCHICHTUNG (Importrichtung ausnahmslos nach unten, azyklisch):
#   UEBERARBEITET (ROADMAP M1-2): Layer 0 ist ein eigenes Modul geworden.
#   Layer 0    wt3000_transport Protocol 'Transport', TmctlTransport,
#                               FakeTransport, WTConfig - importiert nichts
#                               aus dem Paket
#   Layer 1    wt3000_core      Sitzung (WTSession), Fehlerklassen - kennt kein
#                               einziges WT3000-Kommando
#   Layer 1    wt3000_common    geraeteunabhaengige Querschnittsregeln
#   Layer 2    wt3000_numeric   ':NUMeric'-Knoten und Messwertbloecke
#              wt3000_rangeio   ':INPut'-Bereichsknoten
#              wt3000_input     uebrige ':INPut'-Stellgroessen
#   Layer 3    wt3000_itemspec  Ablauf um die Item-Tabelle
#              wt3000_ranging   Ablauf um die Messbereiche
#   UEBERARBEITET (ROADMAP M4-2): die CSV ist aus wt3000_measure ausgezogen;
#   geblieben sind dort Messschleife, Datensatz 'Sample' und der Vertrag
#   'SampleSink'. Die Formate stehen als Fachmodul DANEBEN, nicht neben der
#   Fassade: wt3000_sinks kennt kein SCPI-Kommando und keine Sitzung.
#              wt3000_measure   Messschleife, Sample, SampleSink
#              wt3000_sinks     Ausgabeformate: CsvSink, JsonlSink,
#                               CallbackSink, MultiSink
#   UEBERARBEITET (ROADMAP M1-1): Layer 4 hat einen zweiten Bewohner.
#   Layer 4    wt3000_device    Fassade WT3000 - der Einstiegspunkt
#              stage2..stage5b  ausfuehrbare Stufenskripte
#
# Aufruf der Stufenskripte nach der Umstellung:
#     python -m wt3000_scpi.stage2_read_numeric
#     python -m wt3000_scpi.stage5b_range_probe
#
# UEBERARBEITET (ROADMAP M1-1): dieses Modul importierte bis hierher BEWUSST
# nichts aus dem Paket, damit ein Rechner ohne Geraet und ohne 'tmctl.dll' es
# trotzdem importieren kann. Diese Eigenschaft bleibt erhalten - sie haengt
# aber nicht am Verzicht auf Importe, sondern daran, dass KEIN Fachmodul beim
# Import etwas voraussetzt (TmctlTransport laedt die DLL erst bei
# Instanziierung; tests/test_package_layout.py haelt genau das fest). Deshalb
# koennen die Namen, die ein Anwender braucht, jetzt hier stehen:
#
#     from wt3000_scpi import WT3000, WTConfig, Quantity, WTError
#
#     with WT3000.connect(ip="192.168.10.20") as wt:
#         wt.device.log_summary()
#
# Alles Uebrige bleibt in den Fachmodulen und wird von dort importiert:
#     from wt3000_scpi.wt3000_ranging import RangePlan, RangeSpec
# =============================================================================

from __future__ import annotations

# NEU (M1-1): die Fassade und das, was ein Aufrufer um sie herum braucht -
# Verbindungsparameter, Fehlerklassen, Aufzaehlungen. Bewusst KEIN
# Sammelexport der Ablauffunktionen: wer Bereichsplaene oder die Item-Tabelle
# von Hand baut, importiert aus dem zustaendigen Fachmodul und sieht damit
# schon am Import, in welcher Schicht er arbeitet.
from .wt3000_core import (
    ConcurrentAccessError,
    DeviceError,
    ProtocolError,
    ReadOnlyViolation,
    TmctlError,
    TmctlTransport,
    Transport,
    WTConfig,
    WTError,
    WTSession,
)
# UEBERARBEITET (M1-3): 'OPTION_REQUIREMENTS' steht hier neben der Fassade,
# weil jede kuenftige optionsgebundene Gruppe (Analyse Rang 3, 5, 8, 10)
# dagegen prueft - die Tabelle gehoert damit zur oeffentlichen Oberflaeche
# und nicht in ein Modul, das man erst finden muss.
# NEU (M2-4): der Sitzungs-Sicherungspunkt. Er steht neben der Fassade, weil
# 'wt.backup()' und 'wt.restore_backup()' der uebliche Weg zu ihm sind - wer
# ihn von Hand baut, importiert aus wt3000_backup.
from .wt3000_backup import BACKUP_VERSION, SessionBackup
from .wt3000_device import (
    OPTION_REQUIREMENTS,
    DeviceInfo,
    ItemAccess,
    MeasureControl,
    WT3000,
)
# NEU (M2-1/M3-2): die Integrationssteuerung. Sie steht hier neben der
# Fassade, weil eine Wh-Messung ohne ihre Aufzaehlungen und Gruppennamen nicht
# zu schreiben ist - 'IntegrationMode' waehlt die Betriebsart, GROUP_RESET
# gibt das Zuruecksetzen frei.
from .wt3000_deviceconfig import (
    GROUP_RESET,
    AveragingSettings,
    AveragingType,
    ComputationConfig,
    ComputationSettings,
    EfficiencyEquation,
    FrequencyBand,
    HarmonicsConfig,
    HarmonicsSettings,
    IecGrouping,
    IntegrationConfig,
    IntegrationMode,
    IntegrationSettings,
    IntegrationState,
    IntegrationStateError,
    SQFormula,
    SyncMode,
    ThdFormula,
)
from .wt3000_input import (
    ConfigLocked,
    LineFilter,
    MeasMode,
    SyncSource,
    VerificationError,
    Wiring,
)
# NEU (M4-1): der Datensatz. Er steht hier neben der Fassade und nicht nur im
# Fachmodul, weil ab M4-2 jedes Ausgabeformat gegen ihn gebaut wird - wer einen
# eigenen Sink schreibt, soll ihn aus der Paketwurzel holen koennen wie 'WT3000'.
# NEU (M3-1): die steuerbare Messung. 'Measurement' steht hier neben der
# Fassade, weil 'wt.measure.start()' der uebliche Weg zu ihr ist - der
# Rueckgabetyp gehoert damit zur oeffentlichen Oberflaeche. 'LoopStatistics'
# kommt mit, weil start(), stop() und wait() sie liefern.
from .wt3000_measure import (
    LoopStatistics,
    Measurement,
    Sample,
    SampleMark,
    SampleSink,
    build_harmonics_profile,
    build_integration_profile,
    build_standard_profile,
)
from .wt3000_numeric import NumericValue, ValueStatus
from .wt3000_rangeio import ChangesNotAllowed, Quantity
# NEU (M4-2): die Ausgabeformate. Wer eine Messreihe wegschreibt, waehlt hier -
# und wer ein eigenes Format baut, findet in 'SampleSink' den ganzen Vertrag.
from .wt3000_sinks import CallbackSink, CsvSink, JsonlSink, MultiSink, SinkNotOpen
from .wt3000_transport import FakeTransport

__all__ = [
    "__version__",
    "MODULES",
    # Fassade (M1-1)
    "WT3000",
    "DeviceInfo",
    "ItemAccess",
    "MeasureControl",
    "OPTION_REQUIREMENTS",
    # Verbindung
    "WTConfig",
    "WTSession",
    "Transport",
    "TmctlTransport",
    "FakeTransport",
    # Fehlerklassen
    "WTError",
    "TmctlError",
    "ProtocolError",
    "DeviceError",
    "ReadOnlyViolation",
    "ConcurrentAccessError",
    "ConfigLocked",
    "ChangesNotAllowed",
    "VerificationError",
    # Aufzaehlungen und Werttypen
    "Quantity",
    "Wiring",
    "SyncSource",
    "LineFilter",
    "MeasMode",
    "ValueStatus",
    "NumericValue",
    # Sicherungspunkt (M2-4)
    "SessionBackup",
    "BACKUP_VERSION",
    # Rechenfunktionen (M2-1)
    "ComputationConfig",
    "ComputationSettings",
    "AveragingSettings",
    "AveragingType",
    "EfficiencyEquation",
    "SQFormula",
    "SyncMode",
    # Oberschwingungen (M2-1)
    "HarmonicsConfig",
    "HarmonicsSettings",
    "FrequencyBand",
    "ThdFormula",
    "IecGrouping",
    # Integration (M3-2)
    "IntegrationConfig",
    "IntegrationMode",
    "IntegrationSettings",
    "IntegrationState",
    "IntegrationStateError",
    "GROUP_RESET",
    # Messprofile
    "build_standard_profile",
    "build_integration_profile",
    "build_harmonics_profile",
    # Steuerbare Messung (M3-1)
    "Measurement",
    "LoopStatistics",
    # Datensatz (M4-1)
    "Sample",
    "SampleMark",
    # Ausgabeformate (M4-2)
    "SampleSink",
    "CsvSink",
    "JsonlSink",
    "CallbackSink",
    "MultiSink",
    "SinkNotOpen",
]

__version__ = "0.3.0"

# Die Fachmodule des Pakets, in Schichtreihenfolge. Dient der Dokumentation und
# dem Importtest in tests/test_package_layout.py.
MODULES: tuple[str, ...] = (
    # NEU (M1-2): Layer 0.
    "wt3000_transport",
    "wt3000_core",
    "wt3000_common",
    "wt3000_numeric",
    "wt3000_rangeio",
    "wt3000_input",
    # NEU (M2-1/M3-2): ':INTEGrate' und die weiteren Gruppen aus M2-1.
    "wt3000_deviceconfig",
    "wt3000_itemspec",
    # NEU (M2-4): buendelt die Fachmodule zu einem Sicherungspunkt.
    "wt3000_backup",
    "wt3000_ranging",
    "wt3000_measure",
    # NEU (M4-2): die Ausgabeformate - Fachmodul neben wt3000_measure.
    "wt3000_sinks",
    # NEU (M1-1): die Fassade.
    "wt3000_device",
)
