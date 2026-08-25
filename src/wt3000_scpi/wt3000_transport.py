# Unterste, paketunabhaengige Transportschicht. Transport beschreibt den
# Vertrag fuer WTSession; TmctlTransport bindet die Windows-DLL an und
# FakeTransport bildet Leitung, Blockdaten und Fehlerqueue fuer Tests nach.

from __future__ import annotations

import ctypes as ct
import json
import logging
import os
import struct
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, fields, replace
from pathlib import Path
from types import TracebackType
from typing import Protocol, runtime_checkable

# TMCTL-Konstante fuer Ethernet-Transport (aus tmctl.h)
TM_CTL_ETHER: int = 4

# Maximale Laenge einer Programmnachricht inkl. Terminator (Handbuch Kap. 5).
MAX_PROGRAM_MESSAGE_BYTES: int = 1024


# ---------------------------------------------------------------------------
# Verbindungsparameter
# ---------------------------------------------------------------------------


#: Name der Konfigurationsdatei, die from_environment() sucht.
CONFIG_FILE_NAME: str = "wt3000.json"

#: Umgebungsvariable -> Feld von WTConfig. Der Name der Variablen ist bewusst
#: das Feld in Grossschrift mit Praefix; damit ist die Zuordnung ohne Tabelle
#: erratbar.
ENV_PREFIX: str = "WT3000_"


@dataclass(frozen=True)
class WTConfig:
    """Verbindungs- und Laufzeitparameter.

    Die Voreinstellungen sind bewusst NEUTRAL: keine IP, keine Zugangsdaten,
    kein rechnerspezifischer DLL-Pfad. Wo diese Werte herkommen, entscheidet
    from_environment() - siehe dort. Ein 'WTConfig()' ohne Argumente ist
    deshalb NICHT verbindungsfaehig; es ist der Ausgangspunkt, auf den die
    Auflaesungskette ihre Werte legt.

    Verbindungsdaten gehoeren in Parameter, Umgebung oder Konfigurationsdatei,
    nicht als rechner- oder laborspezifische Werte in den Quelltext.
    """

    #: Pfad ODER blosser Dateiname. Ein blosser Name wird von Windows selbst
    #: gesucht (PATH, Anwendungsverzeichnis) - siehe resolve_dll_path().
    dll_path: str = "tmctl64.dll"
    #: Ohne IP ist keine Verbindung moeglich; TmctlTransport bricht dann mit
    #: einer Meldung ab, die auf die Auflaesungskette verweist.
    ip: str = ""
    #: Leer, wenn das Geraet ohne Anmeldung erreichbar ist.
    user: str = ""
    password: str = ""
    # ZU VERIFIZIEREN: Einheit von TmcSetTimeout (ms angenommen).
    timeout_ms: int = 5000
    drain_timeout_ms: int = 500
    read_buffer_size: int = 64 * 1024
    # REMOTE kann fuer Set-Kommandos erforderlich sein und sperrt zugleich das
    # Bedienfeld. Lange Integrationslaeufe sollten diese Wirkung dokumentieren.
    use_remote: bool = True

    # -- Auflaesungskette ---------------------------------------------------

    @classmethod
    def from_environment(
        cls, config_file: "str | Path | None" = None, **overrides: object
    ) -> "WTConfig":
        """Verbindungsparameter aus der Umgebung zusammensetzen.

        RANGFOLGE, von stark nach schwach:

          1. ausdruecklicher Parameter   from_environment(ip="10.0.0.5")
          2. Umgebungsvariable           WT3000_IP=10.0.0.5
          3. Konfigurationsdatei         wt3000.json  {"ip": "10.0.0.5"}
          4. Voreinstellung der Klasse   (neutral, siehe oben)

        Die Datei wird in dieser Reihenfolge gesucht: der Pfad aus
        'config_file', dann WT3000_CONFIG, dann 'wt3000.json' im
        Arbeitsverzeichnis und in JEDEM Elternverzeichnis darueber, zuletzt
        '~/wt3000.json'. Die erste vorhandene gewinnt; fehlt sie ueberall,
        ist das kein Fehler.

        Umgebungsvariablen heissen wie das Feld in Grossschrift mit Praefix:
        WT3000_IP, WT3000_DLL_PATH, WT3000_USER, WT3000_PASSWORD,
        WT3000_TIMEOUT_MS, WT3000_USE_REMOTE, ...

        Bewusst eine Klassenmethode und kein Verhalten von __init__: WTConfig
        bleibt eine reine Datenklasse, und der blosse Import dieses Moduls
        liest weder Umgebung noch Dateisystem.
        """
        werte: dict[str, object] = dict(_config_file_values(config_file))
        werte.update(_environment_values())
        werte.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**werte)  # type: ignore[arg-type]

    def with_values(self, **overrides: object) -> "WTConfig":
        """Kopie mit geaenderten Feldern. 'None' laesst das Feld unveraendert."""
        gesetzt = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **gesetzt)  # type: ignore[arg-type]

    def describe(self) -> str:
        """Kurzform fuer Protokolle - OHNE Passwort.

        Die Angabe umfasst auch Timeout und REMOTE-Zustand, damit archivierte
        Laeufe nachvollziehbar bleiben. Zugangsdaten gehoeren nicht ins Log.
        """
        anmeldung = f", Benutzer {self.user}" if self.user else ", ohne Anmeldung"
        return (
            f"{self.ip or '<keine IP>'}{anmeldung}, DLL {self.dll_path}, "
            f"Timeout {self.timeout_ms} ms, "
            f"REMOTE {'ein' if self.use_remote else 'aus'}"
        )


# ---------------------------------------------------------------------------
# Die Auflaesungskette im Einzelnen
# ---------------------------------------------------------------------------

#: Umwandlung Text -> Feldwert. Alles, was nicht hier steht, bleibt Text.
_FELD_TYPEN: dict[str, "Callable[[str], object]"] = {
    "timeout_ms": int,
    "drain_timeout_ms": int,
    "read_buffer_size": int,
    "use_remote": lambda text: text.strip().lower() in {"1", "true", "yes", "on", "ja"},
}


def _felder() -> tuple[str, ...]:
    """Feldnamen von WTConfig - eine Quelle fuer Umgebung und Datei."""
    return tuple(f.name for f in fields(WTConfig))


def _wandeln(feld: str, text: str) -> object:
    """Textwert in den Feldtyp wandeln, mit verstaendlicher Meldung."""
    wandler = _FELD_TYPEN.get(feld)
    if wandler is None:
        return text
    try:
        return wandler(text)
    except ValueError as exc:
        raise WTError(
            f"Wert fuer '{feld}' ist nicht auswertbar: {text!r}"
        ) from exc


def _environment_values() -> dict[str, object]:
    """Gesetzte WT3000_*-Variablen einsammeln. Leere Werte zaehlen nicht."""
    werte: dict[str, object] = {}
    for feld in _felder():
        rohwert = os.environ.get(f"{ENV_PREFIX}{feld.upper()}")
        if rohwert is not None and rohwert.strip() != "":
            werte[feld] = _wandeln(feld, rohwert)
    return werte


def config_search_paths(config_file: "str | Path | None" = None) -> list[Path]:
    """Alle Orte, an denen nach der Konfigurationsdatei gesucht wird - in Reihenfolge.

    Oeffentlich, weil eine Fehlermeldung sie aufzaehlen koennen muss: "keine
    IP gesetzt" ist ohne die Liste der durchsuchten Orte kaum zu beheben.

    Vom Arbeitsverzeichnis wird bis zur Dateisystemwurzel gesucht. Die
    naechstgelegene Datei gewinnt; ein Unterverzeichnis darf daher eine eigene
    Konfiguration mitbringen.
    """
    kandidaten: list[Path] = []
    if config_file is not None:
        kandidaten.append(Path(config_file))
    aus_umgebung = os.environ.get(f"{ENV_PREFIX}CONFIG")
    if aus_umgebung:
        kandidaten.append(Path(aus_umgebung))
    # Arbeitsverzeichnis und alle Elternverzeichnisse, von innen nach aussen.
    startpunkt = Path.cwd()
    for verzeichnis in (startpunkt, *startpunkt.parents):
        kandidaten.append(verzeichnis / CONFIG_FILE_NAME)
    kandidaten.append(Path.home() / CONFIG_FILE_NAME)

    # Dubletten entfernen, Reihenfolge behalten: liegt das Arbeitsverzeichnis
    # unterhalb des Home-Verzeichnisses, steht dessen 'wt3000.json' sonst
    # zweimal in der Liste - einmal als Elternverzeichnis, einmal als letzter
    # Kandidat. Fuer die Suche ist das folgenlos, in der Fehlermeldung sieht es
    # nach einem Fehler aus.
    gesehen: set[Path] = set()
    eindeutig: list[Path] = []
    for pfad in kandidaten:
        if pfad not in gesehen:
            gesehen.add(pfad)
            eindeutig.append(pfad)
    return eindeutig


def _config_file_path(config_file: "str | Path | None") -> "Path | None":
    """Erste vorhandene Konfigurationsdatei der Suchreihenfolge."""
    for pfad in config_search_paths(config_file):
        if pfad.is_file():
            return pfad
    # Ein ausdruecklich benannter Pfad, den es nicht gibt, ist ein Fehler -
    # sonst laeuft der Aufrufer still mit den Voreinstellungen weiter.
    if config_file is not None and not Path(config_file).is_file():
        raise WTError(f"Konfigurationsdatei nicht gefunden: {config_file}")
    return None


def config_file_in_use(config_file: "str | Path | None" = None) -> "Path | None":
    """Die Konfigurationsdatei, die 'from_environment()' tatsaechlich liest.

    Gegenstueck zu 'config_search_paths()': Rueckgabe ist der gewinnende Pfad
    oder None. Die Datei wird dabei nicht gelesen, damit der Herkunftshinweis
    auch bei fehlerhaftem JSON verfuegbar bleibt.
    """
    return _config_file_path(config_file)


def _config_file_values(config_file: "str | Path | None") -> dict[str, object]:
    """Werte aus der Konfigurationsdatei lesen. Fehlt sie, ist das in Ordnung."""
    pfad = _config_file_path(config_file)
    if pfad is None:
        return {}
    try:
        inhalt = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WTError(f"Konfigurationsdatei {pfad} ist nicht lesbar: {exc}") from exc
    if not isinstance(inhalt, dict):
        raise WTError(f"Konfigurationsdatei {pfad} enthaelt kein JSON-Objekt")

    bekannt = set(_felder())
    # JSON kennt keine Kommentare. Schluessel mit fuehrendem '_' gelten
    # deshalb als solche und werden stillschweigend uebergangen - so laesst
    # sich 'wt3000.example.json' mit Erklaertext ausliefern.
    unbekannt = sorted(k for k in set(inhalt) - bekannt if not k.startswith("_"))
    if unbekannt:
        logging.getLogger("wt3000.transport").warning(
            "Konfigurationsdatei %s: unbekannte Schluessel uebergangen: %s",
            pfad,
            ", ".join(unbekannt),
        )
    werte = {
        feld: _wandeln(feld, wert) if isinstance(wert, str) else wert
        for feld, wert in inhalt.items()
        if feld in bekannt
    }

    # Pfade aus der Datei gelten relativ zu ihr. Ein blosser DLL-Dateiname
    # bleibt unangetastet, damit Windows ihn ueber PATH suchen kann.
    roher_pfad = werte.get("dll_path")
    if isinstance(roher_pfad, str) and roher_pfad:
        kandidat = Path(roher_pfad)
        if len(kandidat.parts) > 1 and not kandidat.is_absolute():
            werte["dll_path"] = str((pfad.parent / kandidat).resolve())

    return werte


def resolve_dll_path(dll_path: str) -> "str | Path":
    """Angabe aus WTConfig.dll_path in etwas Ladbares uebersetzen.

    Zwei Faelle, bewusst unterschieden:

      Pfadangabe (enthaelt einen Trenner)  muss existieren. Sonst laedt ctypes
                                           irgendetwas oder nichts, und die
                                           Meldung waere unbrauchbar. Wird
                                           relativ angegeben (z.B. zum
                                           Projektverzeichnis), hier auf einen
                                           absoluten Pfad aufgeloest - sonst
                                           bricht os.add_dll_directory() beim
                                           Aufrufer mit WinError 87 ab, das
                                           verlangt zwingend einen absoluten
                                           Verzeichnispfad.
      blosser Dateiname                    wird durchgereicht. Windows sucht
                                           dann selbst in PATH und im
                                           Anwendungsverzeichnis - der uebliche
                                           Weg fuer eine installierte TMCTL.
    """
    kandidat = Path(dll_path)
    if len(kandidat.parts) <= 1:
        return dll_path
    if not kandidat.is_file():
        raise WTError(
            f"TMCTL-DLL nicht gefunden: {kandidat}. Pfad setzen ueber "
            f"{ENV_PREFIX}DLL_PATH, ueber '{CONFIG_FILE_NAME}' oder als "
            "Parameter. Ein blosser Dateiname ('tmctl64.dll') laesst Windows "
            "selbst suchen."
        )
    return kandidat.resolve()


# ---------------------------------------------------------------------------
# Fehlerklassen des Transports
# ---------------------------------------------------------------------------


class WTError(Exception):
    """Basisklasse fuer alle Fehler dieses Treibers."""


class TmctlError(WTError):
    """Eine TMCTL-Funktion hat einen Fehlercode != 0 zurueckgegeben."""

    def __init__(self, function: str, code: int, detail: str = "") -> None:
        self.function = function
        self.code = code
        suffix = f" ({detail})" if detail else ""
        super().__init__(f"{function} fehlgeschlagen, TMCTL-Fehlercode 0x{code:08X}{suffix}")


class ProtocolError(WTError):
    """Verstoss gegen die Protokollregeln aus Kapitel 5 des Handbuchs."""


# ---------------------------------------------------------------------------
# Transportvertrag
# ---------------------------------------------------------------------------


@runtime_checkable
class Transport(Protocol):
    """Was 'WTSession' von einem Transport voraussetzt - und sonst nichts.

    Bewusst klein gehalten: fuenf Methoden, keine Kenntnis eines einzigen
    WT3000-Kommandos. Wer diese fuenf Methoden anbietet, kann eine WTSession
    tragen - ohne von einer Basisklasse zu erben (strukturelle Typisierung).

    Regeln, auf die sich WTSession verlaesst:
      * write()  haengt KEINEN Terminator an - das erledigt die Gegenstelle
      * read()   liefert genau einen Lesevorgang; dass die Antwort damit
                 vollstaendig ist, ist NICHT zugesichert. Der Zusammenbau von
                 Blockdaten passiert in WTSession._assemble_block()
      * query()  ist write() gefolgt von read()
      * jeder Fehler auf der Leitung kommt als TmctlError heraus, damit die
        Aufrufer oben nur eine Fehlerklasse abfangen muessen
    """

    def write(self, command: str) -> None: ...

    def read(self) -> bytes: ...

    def query(self, command: str) -> bytes: ...

    def set_timeout(self, timeout_ms: int) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class ReconnectableTransport(Protocol):
    """Transport, der eine abgerissene Verbindung neu aufbauen kann.

    Bewusst NICHT Teil von 'Transport': ein Transport bleibt mit fuenf
    Methoden vollstaendig, und ein selbstgeschriebener soll nicht ungueltig
    werden, nur weil die Messschleife eine Wiederverbindung anbietet. Die
    Fehlerstrategie fragt ueber isinstance() nach dieser Faehigkeit und
    verzichtet auf die Wiederverbindung, wenn sie fehlt.

    Zusage: nach erfolgreichem 'reconnect()' ist der Transport benutzbar wie
    nach dem Verbindungsaufbau. Geraetezustand wird dabei NICHT
    wiederhergestellt - REMOTE, HOLD und Protokollknoten koennen weg sein.
    """

    def reconnect(self) -> None: ...


# ---------------------------------------------------------------------------
# TMCTL-Transport
# ---------------------------------------------------------------------------


class TmctlTransport:
    """Transportschicht ueber die Yokogawa-TMCTL-DLL.

    Erfuellt das 'Transport'-Protocol. Kennt keinerlei WT3000-Kommandos, nur
    write/read/query/set_timeout/close.

    Windows-gebunden: 'ctypes.WinDLL' existiert auf anderen Betriebssystemen
    nicht. Der Import dieses Moduls ist davon nicht betroffen - erst die
    Instanziierung laedt die DLL. Genau deshalb kann die Testsuite dieses Modul
    importieren und trotzdem geraetefrei laufen.
    """

    def __init__(self, config: WTConfig) -> None:
        self._log = logging.getLogger("wt3000.transport")
        self._config = config
        self._device_id = ct.c_int(0)
        self._open = False

        if not config.ip:
            # Die Suchorte gehoeren in die Meldung, weil eine vorhandene Datei
            # sonst leicht wie eine ignorierte Datei wirkt.
            gesucht = "\n".join(f"    {p}" for p in config_search_paths())
            raise WTError(
                "Keine IP-Adresse gesetzt. WTConfig() allein ist nicht "
                f"verbindungsfaehig - {ENV_PREFIX}IP setzen, '{CONFIG_FILE_NAME}' "
                "anlegen oder WTConfig.from_environment(ip=...) benutzen.\n"
                f"  Arbeitsverzeichnis: {Path.cwd()}\n"
                f"  Gesucht wurde nach '{CONFIG_FILE_NAME}' in:\n{gesucht}"
            )

        dll = resolve_dll_path(config.dll_path)

        # Plattform- und Ladefehler in die gemeinsame Treiberfehlerklasse
        # uebersetzen und mit konkreten Abhilfen ergaenzen.
        try:
            # Abhaengige DLLs liegen ueblicherweise im selben Verzeichnis. Bei
            # einem blossen Dateinamen gibt es kein Verzeichnis, das man
            # ergaenzen koennte - dann sucht Windows selbst.
            if isinstance(dll, Path) and hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(dll.parent))

            self._tm = ct.WinDLL(str(dll))
        except AttributeError as exc:
            # Ausserhalb von Windows gibt es 'ctypes.WinDLL' nicht.
            raise WTError(
                f"ctypes.WinDLL steht auf dieser Plattform nicht zur Verfuegung "
                f"({sys.platform}). TmctlTransport ist Windows-gebunden; fuer "
                "geraetefreie Laeufe gibt es FakeTransport."
            ) from exc
        except OSError as exc:
            raise WTError(
                f"TMCTL-DLL {dll} konnte nicht geladen werden: {exc}\n"
                "  Haeufigste Ursachen: falsche Bitness (64-Bit-Python braucht "
                "tmctl64.dll, 32-Bit tmctl.dll), eine fehlende abhaengige DLL "
                "im selben Verzeichnis, oder das Verzeichnis existiert nicht "
                f"mehr. Pfad setzen ueber {ENV_PREFIX}DLL_PATH oder ueber "
                f"'{CONFIG_FILE_NAME}'."
            ) from exc
        self._declare_prototypes()
        self._initialize()

    # -- Setup --------------------------------------------------------------

    def _declare_prototypes(self) -> None:
        """Signaturen der genutzten TMCTL-Funktionen festlegen."""
        tm = self._tm
        tm.TmcInitialize.argtypes = [ct.c_int, ct.c_char_p, ct.POINTER(ct.c_int)]
        tm.TmcInitialize.restype = ct.c_int
        tm.TmcSend.argtypes = [ct.c_int, ct.c_char_p]
        tm.TmcSend.restype = ct.c_int
        tm.TmcReceive.argtypes = [ct.c_int, ct.c_char_p, ct.c_int, ct.POINTER(ct.c_int)]
        tm.TmcReceive.restype = ct.c_int
        tm.TmcSetTimeout.argtypes = [ct.c_int, ct.c_int]
        tm.TmcSetTimeout.restype = ct.c_int
        tm.TmcFinish.argtypes = [ct.c_int]
        tm.TmcFinish.restype = ct.c_int

    def _initialize(self) -> None:
        """Verbindung aufbauen. Adressstring hat das Format 'ip,user,password'."""
        cfg = self._config
        # TMCTL erwartet ASCII. Die Fehlermeldung nennt aus Datenschutzgruenden
        # nur das betroffene Feld, nicht dessen Wert.
        for feld, wert in (("ip", cfg.ip), ("user", cfg.user), ("password", cfg.password)):
            try:
                wert.encode("ascii")
            except UnicodeEncodeError as exc:
                raise WTError(
                    f"Feld '{feld}' der Verbindungsparameter enthaelt ein Zeichen "
                    f"ausserhalb von ASCII (Position {exc.start}). TMCTL nimmt den "
                    "Adressstring nur als ASCII entgegen."
                ) from exc
        address = f"{cfg.ip},{cfg.user},{cfg.password}".encode("ascii")
        self._check(
            self._tm.TmcInitialize(TM_CTL_ETHER, address, ct.byref(self._device_id)),
            "TmcInitialize",
            f"Adresse={cfg.ip}",
        )
        self._open = True
        self._log.info("Verbindung aufgebaut, Device-ID %d", self._device_id.value)
        self.set_timeout(cfg.timeout_ms)

    @staticmethod
    def _check(rc: int, function: str, detail: str = "") -> None:
        """Jeden TMCTL-Rueckgabewert pruefen, Fehlercode hexadezimal melden."""
        if rc != 0:
            raise TmctlError(function, rc, detail)

    # -- Basisoperationen ---------------------------------------------------

    def set_timeout(self, timeout_ms: int) -> None:
        """Kommunikationstimeout setzen (Einheit ZU VERIFIZIEREN)."""
        self._check(self._tm.TmcSetTimeout(self._device_id, timeout_ms), "TmcSetTimeout")

    def write(self, command: str) -> None:
        """Programmnachricht senden.

        Es wird bewusst KEIN Terminator angehaengt: TMCTL setzt ihn selbst
        (verifiziert mit '*IDN?').
        ZU VERIFIZIEREN: Verhalten bei mit ';' verketteten Kommandos.
        """
        # Kodierungsfehler als ProtocolError an die gemeinsame API uebersetzen.
        try:
            payload = command.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ProtocolError(
                f"Kommando enthaelt ein Zeichen ausserhalb von ASCII "
                f"({command[exc.start:exc.end]!r} an Position {exc.start}): {command!r}"
            ) from exc
        if len(payload) + 1 > MAX_PROGRAM_MESSAGE_BYTES:
            raise ProtocolError(
                f"Programmnachricht zu lang ({len(payload)} Bytes), "
                f"Limit inkl. Terminator {MAX_PROGRAM_MESSAGE_BYTES} Bytes"
            )
        self._log.debug("TX: %r", command)
        self._check(self._tm.TmcSend(self._device_id, payload), "TmcSend", command)

    def read(self) -> bytes:
        """Einen Lesevorgang ausfuehren und die Rohbytes zurueckgeben."""
        size = self._config.read_buffer_size
        buffer = ct.create_string_buffer(size)
        received = ct.c_int(0)
        self._check(
            self._tm.TmcReceive(self._device_id, buffer, size, ct.byref(received)),
            "TmcReceive",
        )
        data = buffer.raw[: received.value]
        if received.value >= size:
            self._log.warning(
                "Lesepuffer (%d Bytes) vollstaendig gefuellt - Antwort evtl. unvollstaendig", size
            )
        self._log.debug("RX: %d Bytes", len(data))
        return data

    def query(self, command: str) -> bytes:
        """Query senden und einen Lesevorgang ausfuehren."""
        self.write(command)
        return self.read()

    def close(self) -> None:
        """Verbindung schliessen. Mehrfachaufruf ist unschaedlich."""
        if not self._open:
            return
        self._open = False
        rc = self._tm.TmcFinish(self._device_id)
        if rc != 0:
            self._log.warning("TmcFinish meldete Fehlercode 0x%08X", rc)
        else:
            self._log.info("Verbindung geschlossen")

    def reconnect(self) -> None:
        """Verbindung schliessen und neu aufbauen (ReconnectableTransport).

        Das alte Handle wird auch dann verworfen, wenn 'TmcFinish' meckert:
        nach einem Abriss ist es ohnehin wertlos, und ein Fehler beim
        Schliessen darf den Neuaufbau nicht verhindern. Die DLL ist bereits
        geladen und die Prototypen sind erklaert - neu aufgebaut wird nur die
        Sitzung zum Geraet.

        Der Geraetezustand wird NICHT wiederhergestellt. Wer nach einem
        Neuaufbau weitermisst, muss Item-Tabelle und Protokollzustand
        nachpruefen; 'verify_after_reconnect()' in wt3000_measure tut das.
        """
        self.close()
        self._initialize()
        self._log.warning("Verbindung neu aufgebaut, Device-ID %d", self._device_id.value)

    # -- Context Manager ----------------------------------------------------

    def __enter__(self) -> "TmctlTransport":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

# ---------------------------------------------------------------------------
# Ersatzgeraet fuer die Testsuite
# ---------------------------------------------------------------------------

# Was in der Antworttabelle stehen darf. Ein Callable bekommt das Kommando in
# Originalschreibweise und liefert die Antwort; eine Liste wird Aufruf fuer
# Aufruf abgearbeitet (der letzte Eintrag bleibt danach stehen). Damit lassen
# sich Messreihen mit wechselnden Werten hinterlegen, ohne einen Zaehler von
# Hand zu fuehren.
FakeReply = bytes | str
FakeEntry = FakeReply | Callable[[str], FakeReply] | list[FakeReply]


def float_block(values: Iterable[float], digits: int = 4) -> bytes:
    """Messwerte in einen '#nNNNN'-Block giessen (IEEE single, MSB first).

    Gegenstueck zu 'wt3000_numeric.parse_float_block()'. Ein 'int' wird als
    rohes 4-Byte-Bitmuster uebernommen - so lassen sich die Sentinel FLOAT_NO_DATA
    (NAN) und FLOAT_OVERRANGE (INF) unveraendert einspeisen, die als IEEE-Zahl
    voellig unauffaellig aussehen.
    """
    payload = b"".join(
        struct.pack(">I", v) if isinstance(v, int) else struct.pack(">f", float(v))
        for v in values
    )
    header = f"#{digits}{len(payload):0{digits}d}".encode("ascii")
    return header + payload


class FakeTransport:
    """Transport ohne Geraet und ohne tmctl.dll - erfuellt das Transport-Protocol.

    Zweck: 'WTSession', 'query_block()', die Item-Tabelle und die gesamte
    Messschleife pruefbar machen. Die bisherige 'FakeSession' aus
    'tests/conftest.py' setzt eine Ebene hoeher an und laesst genau die Regeln
    ungeprueft, die WTSession selbst durchsetzt.

    Verhalten im Einzelnen:

    responses
        Abbildung Kommando -> Antwort, unabhaengig von Gross-/Kleinschreibung
        und vom abschliessenden '?'. Fehlt ein Eintrag, wird ein KeyError
        geworfen statt still etwas zu erfinden: eine nicht hinterlegte Abfrage
        soll auffallen. Das ist bewusst dasselbe Verhalten wie bei FakeSession.

    chunk_size
        Groesse eines einzelnen Lesevorgangs. Ist sie gesetzt, wird jede
        Antwort in mehrere read()-Haeppchen zerlegt - genau der Fall, fuer den
        'WTSession._assemble_block()' die Nachlese-Schleife besitzt. Ohne
        diesen Schalter waere dieser Zweig nie getestet.

    error_queue
        Antworten auf ':STATus:ERRor?'. Sie werden der Reihe nach ausgegeben;
        ist die Liste leer, kommt der Ruhewert '0,"No error"'. Damit laesst
        sich 'assert_no_error()' in beide Richtungen pruefen.

    fail_commands
        Kommandos, die einen TmctlError ausloesen - der simulierte
        Verbindungsabbruch fuer 'drain_after_failure()'.

    written
        Protokoll aller gesendeten Programmnachrichten in Reihenfolge.
    """

    #: Ruhewert der Fehlerqueue, wenn nichts anliegt.
    NO_ERROR: str = '0,"No error"'

    def __init__(
        self,
        responses: dict[str, FakeEntry] | None = None,
        *,
        chunk_size: int | None = None,
        error_queue: Sequence[str] | None = None,
        fail_commands: Iterable[str] = (),
    ) -> None:
        self._log = logging.getLogger("wt3000.transport.fake")
        self.responses: dict[str, FakeEntry] = {
            self._key(k): v for k, v in (responses or {}).items()
        }
        self.written: list[str] = []
        self.timeouts_ms: list[int] = []
        self.closed = False
        self.reads = 0
        #: Zahl der Neuaufbauten - der Zaehler, an dem ein Test die
        #: Wiederverbindung nachweist.
        self.reconnects = 0
        self.chunk_size = chunk_size
        self.error_queue: list[str] = list(error_queue or ())
        self.fail_commands: set[str] = {self._key(c) for c in fail_commands}
        # Noch nicht abgeholte Haeppchen der zuletzt beantworteten Abfrage.
        self._pending: list[bytes] = []

    # -- Hilfsmittel --------------------------------------------------------

    @staticmethod
    def _key(command: str) -> str:
        """Kommandos vergleichbar machen: ohne '?', ohne Rand, in Grossschrift."""
        return command.strip().rstrip("?").upper()

    def prime(self, data: bytes | str) -> None:
        """Rohbytes so hinterlegen, dass der naechste read() sie liefert.

        Gebraucht fuer den Fall, den 'drain_after_failure()' abraeumen soll:
        eine verspaetete Antwort, die keiner Abfrage mehr zugeordnet ist.
        """
        self._pending.extend(self._split(self._as_bytes(data)))

    @staticmethod
    def _as_bytes(reply: FakeReply) -> bytes:
        if isinstance(reply, bytes):
            return reply
        # Der Terminator gehoert zum Draht, nicht zur Antwort - WTSession.decode()
        # streift ihn wieder ab. Ihn hier mitzuliefern haelt den Test ehrlich.
        return f"{reply}\r\n".encode("ascii")

    def _split(self, data: bytes) -> list[bytes]:
        """Antwort in Lesevorgaenge zerlegen."""
        if not self.chunk_size or self.chunk_size <= 0:
            return [data]
        return [
            data[i : i + self.chunk_size] for i in range(0, len(data), self.chunk_size)
        ] or [b""]

    def _lookup(self, command: str) -> bytes:
        """Antwort zu einem Kommando bestimmen und in Bytes wandeln."""
        key = self._key(command)

        # Die Fehlerqueue wird nicht aus der Tabelle bedient: sie leert sich
        # beim Lesen, genau wie am Geraet (':STATus:ERRor?' entfernt den Eintrag).
        if key == ":STATUS:ERROR" and key not in self.responses:
            entry = self.error_queue.pop(0) if self.error_queue else self.NO_ERROR
            return self._as_bytes(entry)

        if key not in self.responses:
            raise KeyError(
                f"FakeTransport hat keine Antwort fuer {command!r}. "
                "Eintrag in 'responses' ergaenzen oder den Aufruf pruefen."
            )

        entry = self.responses[key]
        if callable(entry):
            entry = entry(command)
        elif isinstance(entry, list):
            if not entry:
                raise KeyError(f"FakeTransport: Antwortliste fuer {command!r} ist leer")
            # Der letzte Eintrag bleibt stehen, damit eine Messschleife
            # beliebig lange weiterlaufen kann.
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        return self._as_bytes(entry)

    # -- Transport-Protocol -------------------------------------------------

    def set_timeout(self, timeout_ms: int) -> None:
        """Timeout nur protokollieren - hier gibt es keine Leitung."""
        self.timeouts_ms.append(timeout_ms)

    def write(self, command: str) -> None:
        """Programmnachricht entgegennehmen und merken."""
        if self.closed:
            raise TmctlError("TmcSend", 0x1, "Transport ist geschlossen")
        payload = command.encode("ascii")
        if len(payload) + 1 > MAX_PROGRAM_MESSAGE_BYTES:
            raise ProtocolError(
                f"Programmnachricht zu lang ({len(payload)} Bytes), "
                f"Limit inkl. Terminator {MAX_PROGRAM_MESSAGE_BYTES} Bytes"
            )
        if self._key(command) in self.fail_commands:
            raise TmctlError("TmcSend", 0xDEAD, command)
        self._log.debug("TX: %r", command)
        self.written.append(command)

    def read(self) -> bytes:
        """Einen Lesevorgang liefern.

        Ist nichts vorbereitet, verhaelt sich der FakeTransport wie ein
        schweigendes Geraet: TmctlError statt einer leeren Antwort. Das ist der
        Fall, den 'drain_after_failure()' erwartet und abfaengt.
        """
        self.reads += 1
        if not self._pending:
            raise TmctlError("TmcReceive", 0x2, "nichts zu lesen (simulierter Timeout)")
        return self._pending.pop(0)

    def query(self, command: str) -> bytes:
        """Query senden und den ersten Lesevorgang liefern."""
        self.write(command)
        if self._key(command) in self.fail_commands:
            raise TmctlError("TmcReceive", 0xDEAD, command)
        self._pending.extend(self._split(self._lookup(command)))
        return self.read()

    def close(self) -> None:
        """Verbindung schliessen. Mehrfachaufruf ist unschaedlich."""
        self.closed = True

    def reconnect(self) -> None:
        """Neuaufbau nachstellen (ReconnectableTransport).

        Verwirft nicht abgeholte Haeppchen - genau das ist der Sinn eines
        Neuaufbaus: was auf der alten Verbindung unterwegs war, ist weg.
        'fail_commands' bleibt unveraendert; ein Test, der eine ERHOLUNG
        nachstellen will, leert die Menge selbst.
        """
        self._pending.clear()
        self.closed = False
        self.reconnects += 1

    # -- Context Manager ----------------------------------------------------

    def __enter__(self) -> "FakeTransport":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
