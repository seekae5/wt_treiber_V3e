# =============================================================================
# Datei: tests/test_transport_fehlerpfade.py
# TmctlTransport uebersetzt alle Konstruktorfehler in WTError.
#
# 'resolve_dll_path()' und die anschliessenden Ladeoperationen muessen WTError
# mit konkreten Abhilfen liefern:
#
#     os.add_dll_directory(str(dll.parent))   # OSError
#     self._tm = ct.WinDLL(str(dll))          # OSError; sonst AttributeError
#
# Alle sieben ausfuehrbaren Skripte fangen ausschliesslich WTError - das ist
# die richtige Wahl, sie ist die dokumentierte Treibergrenze. Sie trug hier
# aber nicht: statt der Zeile "Abbruch: TMCTL-DLL nicht gefunden ..." bekam der
# Anwender einen Traceback ohne jeden Hinweis auf die Aufloesungskette,
# 'raise SystemExit(main())' wurde nicht erreicht, der Rueckgabewert 1 kam aus
# dem Traceback statt aus dem Skript. Seit Schritt 3 steht die Protokolldatei
# bereits - dieser Schritt fuellt sie im haeufigsten Installationsfehler
# ueberhaupt.
#
# WARUM DIESES MODUL DEN ECHTEN KONSTRUKTOR BENUTZT: tests/conftest.py legt
# 'TmctlTransport.__init__' still, damit aus der Suite heraus keine Verbindung
# entstehen kann. Die Fehlerwege liegen aber IM Konstruktor. Dieses Modul ist
# die eine Ausnahme - und es kommt nie bis 'TmcInitialize': 'ct.WinDLL' wird
# vorher ersetzt, der Lauf endet im Ladeteil. Eine Verbindung entsteht auch
# hier nicht.
# =============================================================================

from __future__ import annotations

import ctypes
import sys

import pytest
from conftest import ECHTER_TMCTL_KONSTRUKTOR

from wt3000_scpi.wt3000_transport import (
    ProtocolError,
    TmctlTransport,
    WTConfig,
    WTError,
)


@pytest.fixture
def echter_konstruktor(monkeypatch):
    """Die Sperre aus conftest.py fuer dieses Modul aufheben."""
    monkeypatch.setattr(TmctlTransport, "__init__", ECHTER_TMCTL_KONSTRUKTOR)


def baue(dll_path: str = "tmctl64.dll") -> TmctlTransport:
    """Konstruktor mit einem blossen Dateinamen betreten.

    Ein Name ohne Trenner wird von resolve_dll_path() durchgereicht (Windows
    sucht dann selbst). Damit braucht dieser Test keine Datei auf der Platte
    und landet direkt in dem Teil, um den es geht.
    """
    return TmctlTransport(WTConfig(ip="10.0.0.5", dll_path=dll_path))


# ---------------------------------------------------------------------------
# ct.WinDLL
# ---------------------------------------------------------------------------


def test_fehlende_oder_falsche_dll_wird_zu_wterror(echter_konstruktor, monkeypatch):
    """Der praktisch haeufigste TMCTL-Installationsfehler.

    'ct.WinDLL' wirft OSError, wenn die DLL fehlt, wenn eine abhaengige DLL
    fehlt, oder wenn die Bitness nicht passt - 64-Bit-Python braucht
    tmctl64.dll. Das ist der Fall, den ein Anwender beim ersten Aufbau trifft.
    """
    def _wirft(_pfad):
        raise OSError("[WinError 193] %1 ist keine zulaessige Win32-Anwendung")

    monkeypatch.setattr(ctypes, "WinDLL", _wirft, raising=False)

    with pytest.raises(WTError) as fehler:
        baue()

    text = str(fehler.value)
    assert "tmctl64.dll" in text, "Die Meldung nennt die beanstandete DLL nicht"
    assert "Bitness" in text, "Die Meldung nennt die haeufigste Ursache nicht"


def test_ursache_bleibt_erhalten(echter_konstruktor, monkeypatch):
    """Die urspruengliche OSError haengt als __cause__ daran.

    Ohne 'raise ... from exc' waere die Windows-Fehlernummer verloren - und
    genau die unterscheidet 'Datei fehlt' von 'falsche Bitness'.
    """
    ursprung = OSError("[WinError 126] Das angegebene Modul wurde nicht gefunden")

    def _wirft(_pfad):
        raise ursprung

    monkeypatch.setattr(ctypes, "WinDLL", _wirft, raising=False)

    with pytest.raises(WTError) as fehler:
        baue()

    assert fehler.value.__cause__ is ursprung
    assert "WinError 126" in str(fehler.value)


def test_nicht_windows_wird_zu_wterror(echter_konstruktor, monkeypatch):
    """Reproduktion 7.3 der Analyse.

    'ctypes' hat ausserhalb von Windows kein 'WinDLL'. Vorher verliess ein
    nackter AttributeError den Konstruktor:
        AttributeError: module 'ctypes' has no attribute 'WinDLL'
    Das ist fuer jemanden, der die Suite auf Linux oder macOS laufen laesst,
    keine brauchbare Auskunft.
    """
    monkeypatch.delattr(ctypes, "WinDLL", raising=False)

    with pytest.raises(WTError) as fehler:
        baue()

    text = str(fehler.value)
    assert "Windows" in text
    assert "FakeTransport" in text, "Die Meldung nennt den geraetefreien Weg nicht"


# ---------------------------------------------------------------------------
# os.add_dll_directory
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not hasattr(__import__("os"), "add_dll_directory"),
    reason="os.add_dll_directory gibt es nur unter Windows",
)
def test_verschwundenes_dll_verzeichnis_wird_zu_wterror(echter_konstruktor, monkeypatch, tmp_path):
    """Ein Pfad, dessen Verzeichnis es nicht mehr gibt.

    Kommt vor, wenn 'wt3000.json' einen Pfad auf ein Netzlaufwerk oder einen
    Wechseldatentraeger nennt.
    """
    import os

    datei = tmp_path / "tmctl64.dll"
    datei.write_bytes(b"kein echtes DLL-Abbild")

    def _wirft(_verzeichnis):
        raise OSError("[WinError 87] Falscher Parameter")

    monkeypatch.setattr(os, "add_dll_directory", _wirft)

    with pytest.raises(WTError):
        baue(str(datei))


# ---------------------------------------------------------------------------
# encode("ascii")
# ---------------------------------------------------------------------------


def test_nicht_ascii_im_kommando_wird_zu_protocolerror():
    """Ein Nicht-ASCII-Zeichen in einem Kommando.

    Kommt ueber einen Parameter aus einer Konfigurationsdatei herein.
    'command.encode("ascii")' warf einen nackten UnicodeEncodeError.
    """
    transport = TmctlTransport.__new__(TmctlTransport)

    with pytest.raises(ProtocolError) as fehler:
        TmctlTransport.write(transport, ":INPut:VOLTage:RANGe:ELEMent1 1000µ")

    assert "\\xb5" in repr(str(fehler.value)) or "µ" in str(fehler.value)


def test_nicht_ascii_in_den_zugangsdaten_wird_zu_wterror(echter_konstruktor, monkeypatch):
    """Ein Passwort mit Umlaut in 'wt3000.json'.

    NICHT in der Analyse genannt, beim Nachsehen zu A-04 gefunden:
    '_initialize()' baut den Adressstring aus ip, user und password und
    codiert ihn nach ASCII. Ein Umlaut im Passwort - nicht abwegig - brach den
    Verbindungsaufbau mit UnicodeEncodeError ab, also ebenfalls nicht als
    WTError.

    Die Meldung nennt das FELD, nicht den Wert: ein Protokoll wird archiviert
    und ist der falsche Ort fuer Zugangsdaten.
    """
    monkeypatch.setattr(ctypes, "WinDLL", lambda _p: _AttrappenDll(), raising=False)

    with pytest.raises(WTError) as fehler:
        TmctlTransport(WTConfig(ip="10.0.0.5", user="pruefer", password="geändert"))

    text = str(fehler.value)
    assert "password" in text
    assert "geaendert" not in text and "geändert" not in text, (
        "Die Meldung gibt den Wert preis"
    )


class _AttrappenDll:
    """Steht fuer die geladene DLL - beantwortet jede Prototyp-Zuweisung.

    Der Konstruktor kommt damit durch '_declare_prototypes()' hindurch bis in
    '_initialize()', wo der Adressstring gebaut wird. Weiter als bis zum
    encode() laeuft nichts: TmcInitialize wird nie erreicht.
    """

    def __getattr__(self, name):
        if name.startswith("Tmc"):
            return _AttrappenFunktion()
        raise AttributeError(name)


class _AttrappenFunktion:
    argtypes = None
    restype = None

    def __call__(self, *args, **kwargs):  # pragma: no cover - wird nie erreicht
        raise AssertionError("Es haette keine TMCTL-Funktion aufgerufen werden duerfen")


def test_platform_hinweis_nennt_die_laufende_plattform(echter_konstruktor, monkeypatch):
    """Die Meldung soll sagen, WO sie entstanden ist."""
    monkeypatch.delattr(ctypes, "WinDLL", raising=False)

    with pytest.raises(WTError) as fehler:
        baue()

    assert sys.platform in str(fehler.value)
