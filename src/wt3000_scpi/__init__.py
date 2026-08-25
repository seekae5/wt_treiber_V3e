# Oeffentliche Paketoberflaeche des WT3000-Treibers.
#
# Die Module sind azyklisch geschichtet: Transport und Sitzung bilden die
# Basis, darauf liegen SCPI-Fachmodule und Ablaeufe, darueber die Fassade und
# Stufenskripte. Der Import des Pakets benoetigt weder Geraet noch tmctl.dll;
# die DLL wird erst beim Erzeugen eines TmctlTransport geladen.

from __future__ import annotations

# Fassade und die fuer ihre Verwendung benoetigten Grundtypen. Fachliche
# Ablauffunktionen bleiben in ihren Modulen und werden nicht gesammelt exportiert.
from .wt3000_core import (
    ConcurrentAccessError,
    ReconnectableTransport,
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
# Sitzungs-Backup und Optionsanforderungen gehoeren zur Fassade.
from .wt3000_backup import BACKUP_VERSION, SessionBackup
from .wt3000_device import (
    OPTION_REQUIREMENTS,
    DeviceInfo,
    ItemAccess,
    MeasureControl,
    WT3000,
)
# Oeffentliche Konfigurations- und Integrationstypen.
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
# Messdatensatz, Messsteuerung und Profilfunktionen.
from .wt3000_measure import (
    SIDECAR_VERSION,
    ErrorPolicy,
    LoopStatistics,
    Measurement,
    MeasurementAborted,
    RunMetadata,
    SidecarMismatch,
    Sample,
    SampleMark,
    SampleSink,
    build_harmonics_profile,
    build_integration_profile,
    build_standard_profile,
    sidecar_path,
    verify_sidecar,
)
from .wt3000_numeric import NumericValue, ValueStatus
from .wt3000_rangeio import ChangesNotAllowed, Quantity
# Ausgabeformate und ihr gemeinsamer Vertrag SampleSink.
from .wt3000_sinks import (
    AppendMismatch,
    CallbackSink,
    CsvSink,
    JsonlSink,
    MultiSink,
    RotatingSink,
    RotationPolicy,
    SinkNotOpen,
    unique_path,
)
from .wt3000_transport import FakeTransport

__all__ = [
    "__version__",
    "MODULES",
    # Fassade
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
    "ReconnectableTransport",
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
    # Sicherungspunkt
    "SessionBackup",
    "BACKUP_VERSION",
    # Rechenfunktionen
    "ComputationConfig",
    "ComputationSettings",
    "AveragingSettings",
    "AveragingType",
    "EfficiencyEquation",
    "SQFormula",
    "SyncMode",
    # Oberschwingungen
    "HarmonicsConfig",
    "HarmonicsSettings",
    "FrequencyBand",
    "ThdFormula",
    "IecGrouping",
    # Integration
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
    # Steuerbare Messung
    "Measurement",
    "LoopStatistics",
    # Fehlerstrategie bei Kommunikationsabbruechen
    "ErrorPolicy",
    "MeasurementAborted",
    # Verbindliche Metadaten (M4-3)
    "RunMetadata",
    "SidecarMismatch",
    "verify_sidecar",
    "sidecar_path",
    "SIDECAR_VERSION",
    # Datensatz
    "Sample",
    "SampleMark",
    # Ausgabeformate
    "SampleSink",
    "CsvSink",
    "JsonlSink",
    "CallbackSink",
    "MultiSink",
    "SinkNotOpen",
    # Rotation und sicheres Fortsetzen (M4-4)
    "RotatingSink",
    "RotationPolicy",
    "AppendMismatch",
    "unique_path",
]

__version__ = "0.3.0"

# Die Fachmodule des Pakets, in Schichtreihenfolge. Dient der Dokumentation und
# dem Importtest in tests/test_package_layout.py.
MODULES: tuple[str, ...] = (
    "wt3000_transport",
    "wt3000_core",
    "wt3000_common",
    "wt3000_numeric",
    "wt3000_rangeio",
    "wt3000_input",
    "wt3000_deviceconfig",
    "wt3000_itemspec",
    "wt3000_backup",
    "wt3000_ranging",
    "wt3000_measure",
    "wt3000_sinks",
    "wt3000_device",
)
