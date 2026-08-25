# =============================================================================
# Datei: wt3000_core.py
# Layer 1 - Protokollschicht: Query-Regeln, Blockdaten, Fehlerqueue,
#           Nur-Lesen-Sperre, Fernsteuerung.
#
# Layer 0 liegt in 'wt3000_transport.py': Transport-Protocol, TmctlTransport,
# WTConfig und die Transport-Fehlerklassen. Diese Namen werden hier
# unveraendert weiter-exportiert - Importe der Form
#     from .wt3000_core import WTConfig, TmctlTransport, WTError
# funktionieren also wortgleich, und weil es dieselben Klassenobjekte sind,
# faengt 'except WTError' weiterhin alles.
# =============================================================================

from __future__ import annotations

import logging
# Das Lock schuetzt den Draht, der Sitzungsbesitz den Messtakt.
import threading
from collections.abc import Iterator
from contextlib import contextmanager

# Importrichtung nach unten: Layer 1 zieht sich Layer 0 herein. 'Transport' ist
# der Vertrag, den WTSession voraussetzt; die uebrigen Namen werden nur
# durchgereicht (siehe __all__).
from .wt3000_transport import (
    MAX_PROGRAM_MESSAGE_BYTES,
    TM_CTL_ETHER,
    ProtocolError,
    ReconnectableTransport,
    TmctlError,
    TmctlTransport,
    Transport,
    WTConfig,
    WTError,
    config_file_in_use,
)

# Haelt fest, dass die durchgereichten Namen zur Schnittstelle dieses Moduls
# gehoeren - und haelt Linter davon ab, die scheinbar ungenutzten Importe oben
# zu entfernen.
__all__ = [
    # weitergereicht aus wt3000_transport (Layer 0)
    "MAX_PROGRAM_MESSAGE_BYTES",
    "TM_CTL_ETHER",
    "ProtocolError",
    "ReconnectableTransport",
    "TmctlError",
    "TmctlTransport",
    "Transport",
    "WTConfig",
    "WTError",
    # Herkunft der aufgeloesten Konfiguration fuer Protokollkoepfe.
    "config_file_in_use",
    # hier beheimatet (Layer 1)
    "MAX_BLOCK_READS",
    "ConcurrentAccessError",
    "DeviceError",
    "ReadOnlyViolation",
    "WTSession",
]


# ---------------------------------------------------------------------------
# Konstanten und Exceptions der Sitzungsschicht
#
# WTError, TmctlError und ProtocolError entstehen im Transport und wohnen
# deshalb in wt3000_transport. Die beiden Klassen hier entstehen erst in dieser
# Schicht.
# ---------------------------------------------------------------------------

# Grenze der Nachlese-Schleife in _assemble_block(). Zweimal gebraucht: als
# Schleifengrenze und - mit der Puffergroesse multipliziert - als groesste
# Nutzlast, die sich ueberhaupt zusammensetzen laesst.
MAX_BLOCK_READS: int = 64


class DeviceError(WTError):
    """Das Geraet hat einen Eintrag in die Fehlerqueue gelegt."""


class ReadOnlyViolation(WTError):
    """In einer Nur-Lesen-Session wurde ein schreibendes Kommando versucht."""


class ConcurrentAccessError(WTError):
    """Fremdzugriff auf eine Sitzung, die gerade einem anderen Thread gehoert.

    write() und query() sind ein
    Schreib-Lese-Paar auf EINER Verbindung. Laufen zwei davon nebenlaeufig,
    bekommt der eine Aufrufer die Antwort des anderen - und zwar
    stillschweigend, weil beide Antworten fuer sich plausibel aussehen. Eine
    Messreihe, die so entstanden ist, laesst sich hinterher nicht mehr von
    einer richtigen unterscheiden.

    Deshalb ist die Meldung hier laut und nicht bloss ein Log-Eintrag: eine
    laufende Messung besitzt ihre Sitzung, und wer sie von aussen anspricht,
    soll das in dem Moment erfahren, in dem er es tut.
    """


# ---------------------------------------------------------------------------
# Layer 1 - Session / Plumbing
# ---------------------------------------------------------------------------


class WTSession:
    """Protokollschicht: Query-Regeln, Blockdaten, Fehlerqueue.

    Nimmt ein 'Transport'-Protocol entgegen, keine konkrete Klasse. Damit
    laeuft dieselbe Sitzung geraetefrei auf 'FakeTransport' - und spaeter auf
    einem Socket- oder VISA-Transport, ohne dass hier eine Zeile faellt.

    Nebenlaeufigkeit wird auf zwei Ebenen geregelt:

      Das RLock ist der Mechanismus. Es liegt um write/query/query_raw/
      query_block und um drain_after_failure(). Es muss query_block() GANZ
      umschliessen, denn _assemble_block() liest ueber self._transport.read()
      nach - sonst liest der zweite Aufrufer mitten in einen fremden Block
      hinein. drain_after_failure() ist aus demselben Grund als Ganzes
      geschuetzt: es verstellt ueber set_timeout() gemeinsamen
      Transportzustand und stellt ihn im 'finally' zurueck.

      Der Besitz ist die Regel. 'claim()' traegt einen Thread als Eigentuemer
      ein; jeder I/O-Aufruf aus einem anderen Thread endet danach in einer
      ConcurrentAccessError statt in einer Warteschlange.

    Warum nicht das Lock allein: es macht einen Fremdzugriff mitten in der
    Messreihe zwar sicher, aber nicht sinnvoll - er verschiebt den naechsten
    Takt und erzeugt einen Overrun, den hinterher niemand erklaeren kann. Ein
    Aufrufer, der waehrend einer laufenden Messung 'wt.input' anfasst, hat
    sich fast immer vertan; das soll er erfahren.

    Warum nicht die Regel allein: sie liesse sich umgehen. Die Fachobjekte
    ('wt.input', 'wt.ranges', ...) werden von der Fassade zwischengespeichert
    - wer eine Referenz VOR dem Start geholt hat, kaeme an jeder Pruefung in
    der Fassade vorbei. Hier unten kommt keiner vorbei: durch diese vier
    Methoden geht jedes einzelne Byte.

    Das Lock bleibt auch ohne Besitzer aktiv. Es kostet bei jedem Aufruf eine
    unbestrittene Lock-Anforderung - gegen ein Messintervall von mindestens
    0,05 s ist das nicht messbar - und schliesst dafuer die stille
    Antwortvertauschung fuer jeden kuenftigen Nebenlaeufigkeitsfall, auch fuer
    solche, die nicht ueber 'Measurement' laufen.
    """

    def __init__(self, transport: Transport, config: WTConfig, read_only: bool = False) -> None:
        self._log = logging.getLogger("wt3000.session")
        self._transport = transport
        self._config = config
        self._read_only = read_only
        self._remote_active = False
        # RLock statt Lock: query_block() ruft query_raw(), beide sperren.
        self._lock = threading.RLock()
        self._owner: int | None = None
        self._owner_reason: str = ""

    # -- Sitzungsbesitz -----------------------------------------------------

    @property
    def owner(self) -> int | None:
        """Thread-Kennung des Eigentuemers, oder None, wenn frei."""
        return self._owner

    def claim(self, thread_ident: int, reason: str) -> None:
        """Die Sitzung einem Thread zuschlagen.

        'reason' geht woertlich in die Meldung eines abgewiesenen
        Fremdzugriffs ein - er soll benennen, WAS die Sitzung gerade tut, denn
        das ist die Information, die dem Abgewiesenen fehlt.

        Wird von 'Measurement.start()' gerufen, bevor der Mess-Thread
        loslaeuft, und nicht vom Thread selbst: sonst gaebe es zwischen
        'start()' und dem ersten Takt ein Fenster, in dem ein Fremdzugriff
        noch durchginge. Ein Vertrag, der erst ein paar Millisekunden spaeter
        gilt, ist keiner.
        """
        with self._lock:
            if self._owner is not None and self._owner != thread_ident:
                raise ConcurrentAccessError(
                    f"Diese Sitzung gehoert bereits Thread {self._owner} "
                    f"({self._owner_reason}). Zwei gleichzeitige Messungen auf einer "
                    "Verbindung sind nicht moeglich - das Geraet hat genau eine."
                )
            self._owner = thread_ident
            self._owner_reason = reason
            self._log.debug("Sitzung an Thread %s vergeben (%s)", thread_ident, reason)

    def release(self) -> None:
        """Besitz aufgeben. Mehrfachaufruf ist unschaedlich."""
        with self._lock:
            if self._owner is None:
                return
            self._log.debug("Sitzung von Thread %s freigegeben", self._owner)
            self._owner = None
            self._owner_reason = ""

    @contextmanager
    def owned_by_current_thread(self, reason: str) -> Iterator["WTSession"]:
        """'claim()' fuer den ausfuehrenden Thread, mit Rueckgabe im 'finally'.

        Der bequeme Weg fuer alles, was NICHT 'Measurement' ist - etwa ein
        Ablauf, der einen zusammenhaengenden Schreibvorgang gegen Fremdzugriff
        abschirmen will.
        """
        self.claim(threading.get_ident(), reason)
        try:
            yield self
        finally:
            self.release()

    def _check_owner(self, command: str) -> None:
        """Fremdzugriff abweisen. Aufrufer haelt bereits das Lock."""
        owner = self._owner
        if owner is None or owner == threading.get_ident():
            return
        raise ConcurrentAccessError(
            f"'{command}' aus Thread {threading.get_ident()} abgewiesen: die Sitzung "
            f"gehoert Thread {owner} ({self._owner_reason}). Ein Zugriff von aussen "
            "wuerde entweder die Antworten vertauschen oder den Messtakt verschieben. "
            "Abhilfe: die Messung vorher mit stop() beenden, die noetigen Werte vor "
            "dem Start lesen - oder statt des Hintergrundlaufs den Generator "
            "'wt.measure.stream()' benutzen, der im eigenen Thread laeuft."
        )

    @contextmanager
    def _exclusive(self, command: str) -> Iterator[None]:
        """Lock nehmen und Besitz pruefen - in dieser Reihenfolge.

        Die Reihenfolge ist der Punkt: erst das Lock, dann die Pruefung. Der
        Mess-Thread haelt das Lock nur waehrend eines Zyklus, nicht dazwischen;
        ein fremder Thread bekommt es also zwischen zwei Takten anstandslos -
        und laeuft dann in die Besitzpruefung, statt bis zum Ende der
        Messreihe zu blockieren.
        """
        with self._lock:
            self._check_owner(command)
            yield

    # -- Fernsteuerung ------------------------------------------------------

    def enable_remote(self) -> None:
        """Fernsteuerung einschalten (REMOTE-LED an, Tasten ausser LOCAL gesperrt)."""
        self.write(":COMMunicate:REMote ON")
        self._remote_active = True
        self._log.info("Fernsteuerung eingeschaltet")

    def disable_remote(self) -> None:
        """Fernsteuerung abschalten. Gibt das Bedienfeld frei."""
        if not self._remote_active:
            return
        try:
            self.write(":COMMunicate:REMote OFF")
            self._log.info("Fernsteuerung abgeschaltet")
        except WTError as exc:
            self._log.warning("REMote OFF fehlgeschlagen: %s", exc)
        finally:
            self._remote_active = False

    # -- Kern ---------------------------------------------------------------

    def write(self, command: str) -> None:
        """Set-Kommando senden (kein Query)."""
        self._validate(command, expect_query=False)
        with self._exclusive(command):
            self._transport.write(command)

    def query(self, command: str) -> str:
        """Genau einen Query absetzen und die Antwort als Text zurueckgeben."""
        self._validate(command, expect_query=True)
        with self._exclusive(command):
            return self.decode(self._transport.query(command))

    def query_raw(self, command: str) -> bytes:
        """Wie query(), liefert aber die unveraenderten Rohbytes."""
        self._validate(command, expect_query=True)
        with self._exclusive(command):
            return self._transport.query(command)

    def query_block(self, command: str) -> bytes:
        """Query absetzen, dessen Antwort ein <Block data> mit '#n'-Header ist.

        Liest so lange nach, bis die im Header angekuendigte Nutzlast
        vollstaendig vorliegt. Damit ist es egal, ob TmcReceive den Block in
        einem Stueck liefert oder an einem 0x0A-Byte innerhalb der Binaerdaten
        abbricht (ZU VERIFIZIEREN, welches Verhalten tatsaechlich vorliegt).

        Das Lock umschliesst beide Haelften: '_assemble_block()' liest ueber
        'self._transport.read()' nach, und ein zweiter Aufrufer, der sich
        dazwischenschiebt, liest mitten in einen fremden Block hinein.
        """
        with self._exclusive(command):
            raw = self.query_raw(command)
            return self._assemble_block(raw)

    def _assemble_block(self, raw: bytes) -> bytes:
        """'#4NNNN<daten>' auswerten und die Nutzlast vollstaendig einsammeln.

        Zusage: JEDER Formfehler einer Blockantwort verlaesst diese Methode als
        ProtocolError, nie als nackter ValueError. Aufrufer behandeln
        pflichtgemaess nur WTError - die Stufenskripte tun genau das.
        """
        if not raw.startswith(b"#"):
            raise ProtocolError(
                f"Kein Blockheader in der Antwort (erste Bytes: {raw[:16]!r}). "
                "Steht :NUMeric:FORMat wirklich auf FLOat?"
            )
        try:
            digit_count = int(raw[1:2])
        except ValueError as exc:
            raise ProtocolError(f"Ungueltiger Blockheader: {raw[:8]!r}") from exc
        if digit_count == 0:
            raise ProtocolError("Block mit unbestimmter Laenge ('#0') wird nicht unterstuetzt")

        header_length = 2 + digit_count

        # Abgeschnittener Kopf: ohne diese Pruefung liefert der Schnitt unten
        # stillschweigend zu wenige oder gar keine Bytes, und int() bricht mit
        # einem ValueError ab, den niemand faengt.
        if len(raw) < header_length:
            raise ProtocolError(
                f"Blockheader abgeschnitten: angekuendigt sind {digit_count} "
                f"Laengenziffern, die Antwort hat aber nur {len(raw)} Bytes "
                f"({raw[:16]!r})"
            )

        # Dieselbe Absicherung wie oben fuer die Ziffernanzahl.
        try:
            payload_length = int(raw[2:header_length])
        except ValueError as exc:
            raise ProtocolError(
                f"Laengenfeld des Blockheaders ist keine Zahl: "
                f"{raw[2:header_length]!r} (Kopf: {raw[:header_length]!r})"
            ) from exc

        # Unplausible Laengen abfangen, BEVOR die Nachlese-Schleife laeuft:
        #
        #   negativ   'payload[:payload_length]' schneidet am Ende statt am
        #             Anfang und die Schleife laeuft gar nicht erst an - heraus
        #             kaeme stillschweigend ein zu kurzer Block, der wie ein
        #             Ergebnis aussieht.
        #   zu gross  die Schleife liest ins Leere und meldet am Ende einen
        #             Abbruch nach n Lesevorgaengen. Das deutet auf eine
        #             langsame Verbindung statt auf den kaputten Kopf.
        max_payload = MAX_BLOCK_READS * self._config.read_buffer_size
        if not 0 <= payload_length <= max_payload:
            raise ProtocolError(
                f"Unplausible Nutzlastlaenge im Blockheader: {payload_length} Bytes "
                f"(Kopf: {raw[:header_length]!r}). Zulaessig sind 0 bis {max_payload} "
                f"Bytes - mehr liesse sich in {MAX_BLOCK_READS} Lesevorgaengen "
                "ohnehin nicht einsammeln."
            )

        payload = raw[header_length:]

        reads = 1
        while len(payload) < payload_length:
            payload += self._transport.read()
            reads += 1
            if reads > MAX_BLOCK_READS:
                raise ProtocolError(
                    f"Blockdaten nach {MAX_BLOCK_READS} Lesevorgaengen immer noch "
                    f"unvollstaendig ({len(payload)} von {payload_length} Bytes)"
                )
        if reads > 1:
            self._log.info("Blockdaten in %d Lesevorgaengen zusammengesetzt", reads)

        return payload[:payload_length]

    def _validate(self, command: str, expect_query: bool) -> None:
        """Protokollregeln pruefen, bevor die Nachricht das Geraet erreicht."""
        stripped = command.strip()
        is_query = stripped.endswith("?")

        if self._read_only and not is_query:
            raise ReadOnlyViolation(
                f"Nur-Lesen-Session: '{command}' ist kein Query und wird nicht gesendet"
            )
        if expect_query and not is_query:
            raise ProtocolError(f"'{command}' ist kein Query, wurde aber als solcher aufgerufen")
        if not expect_query and is_query:
            raise ProtocolError(f"'{command}' ist ein Query, wurde aber als write() aufgerufen")
        # Handbuch Kap. 5: genau ein Query pro Programmnachricht.
        if stripped.count("?") > 1:
            raise ProtocolError(f"Mehr als ein Query in einer Nachricht: '{command}'")

    @staticmethod
    def decode(raw: bytes) -> str:
        """Rohbytes in Text wandeln und Terminator entfernen."""
        return raw.decode("ascii", errors="replace").strip("\r\n\0 ")

    # -- Fehlerqueue --------------------------------------------------------

    # -- Wiederverbindung ---------------------------------------------------

    @property
    def can_reconnect(self) -> bool:
        """Kann der Transport eine abgerissene Verbindung neu aufbauen?"""
        return isinstance(self._transport, ReconnectableTransport)

    def reconnect(self) -> None:
        """Verbindung neu aufbauen und die Fernsteuerung wiederherstellen.

        Nur die VERBINDUNG wird wiederhergestellt, nicht der Geraetezustand:
        Protokollknoten, HOLD und Item-Tabelle koennen nach einem Neuaufbau
        anders stehen - etwa weil das Geraet zwischendurch aus war. Wer
        danach weitermisst, prueft sie nach; 'verify_after_reconnect()' in
        wt3000_measure ist genau dafuer da.

        REMOTE ist der eine Zustand, der hier mitkommt: die Sitzung hat ihn
        selbst eingeschaltet, weiss es als Einzige und wuerde sonst
        stillschweigend ohne Fernsteuerung weiterlaufen.
        """
        with self._exclusive("reconnect()"):
            transport = self._transport
            if not isinstance(transport, ReconnectableTransport):
                raise WTError(
                    f"{type(transport).__name__} kann keine Verbindung neu aufbauen. "
                    "Fuer eine Wiederverbindung muss der Transport 'reconnect()' "
                    "anbieten (siehe ReconnectableTransport)."
                )
            war_remote = self._remote_active
            self._remote_active = False
            transport.reconnect()
            if war_remote:
                self.enable_remote()

    def drain_after_failure(self) -> None:
        """Nach einem fehlgeschlagenen Query eine verspaetete Antwort abraeumen.

        Als Ganzes gesperrt: Die Methode verstellt ueber 'set_timeout()'
        gemeinsamen Transportzustand und nimmt ihn im
        'finally' zurueck - ein Zugriff dazwischen liefe in den kurzen
        Drain-Timeout statt in den konfigurierten und sae einen Timeout, den
        hinterher niemand erklaeren kann.
        """
        with self._exclusive("drain_after_failure()"):
            try:
                self._transport.set_timeout(self._config.drain_timeout_ms)
                leftover = self._transport.read()
                if leftover:
                    self._log.warning("Nachlaufende Antwort verworfen: %r", leftover[:80])
            except TmctlError:
                pass  # Erwarteter Fall: nichts mehr da.
            finally:
                self._transport.set_timeout(self._config.timeout_ms)

    def read_error_queue(self, max_entries: int = 20) -> list[str]:
        """Fehlerqueue leeren. Hinweis: :STATus:ERRor? entfernt den Eintrag."""
        entries: list[str] = []
        for _ in range(max_entries):
            answer = self.query(":STATus:ERRor?")
            entries.append(answer)
            if answer.split(",", 1)[0].strip().lstrip("+") == "0":
                break
        return entries

    def assert_no_error(self, context: str) -> None:
        """Fehlerqueue pruefen und bei Eintraegen eine DeviceError werfen."""
        entries = self.read_error_queue()
        problems = [e for e in entries if e.split(",", 1)[0].strip().lstrip("+") != "0"]
        if problems:
            raise DeviceError(f"Geraetefehler nach '{context}': {problems}")
