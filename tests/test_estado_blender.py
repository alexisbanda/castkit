"""Tests de `castkit comprobar --estado-blender` (ticket #85).

Pensada para que `install.ps1` decida si descarga e instala Blender, no para
personas: imprime solo dos líneas ASCII (`estado=...` y `version=...`) y
siempre termina con código 0. Cubre los tres estados (`ok`, `falta`, `otra`),
`BLENDER_BIN` roto, y el caso de Windows en el que no hay que volver a
descargar el instalador porque la carpeta de instalación ya tiene la versión
fijada aunque el `blender` del PATH (o de `BLENDER_BIN`) sea otro.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import castkit.comprobar as comprobar_mod
from castkit.blender import BlenderNotFoundError
from castkit.cli import main
from castkit.config import version_blender_fijada


def _version_fijada() -> str:
    return version_blender_fijada()


# ---------------------------------------------------------------------------
# `_estado_blender()`: los tres estados y el caso Windows.
# ---------------------------------------------------------------------------


def test_estado_blender_ok_cuando_la_version_coincide(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        comprobar_mod,
        "localizar_blender",
        lambda plataforma=None: (Path("/usr/bin/blender"), "PATH"),
    )
    monkeypatch.setattr(
        comprobar_mod,
        "_version_instalada_blender",
        lambda ejecutable: f"Blender {_version_fijada()} (hash abc1234)",
    )

    estado, version = comprobar_mod._estado_blender()

    assert estado == "ok"
    assert version == _version_fijada()


def test_estado_blender_falta_cuando_no_se_localiza(monkeypatch: pytest.MonkeyPatch) -> None:
    def _sin_blender(plataforma=None):
        raise BlenderNotFoundError("no encontrado")

    monkeypatch.setattr(comprobar_mod, "localizar_blender", _sin_blender)

    estado, version = comprobar_mod._estado_blender()

    assert estado == "falta"
    assert version == _version_fijada()


def test_estado_blender_falta_con_blender_bin_roto(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sin mockear localizar_blender: BLENDER_BIN apuntando a una ruta
    # inexistente hace que la propia función real lance BlenderNotFoundError.
    monkeypatch.setenv("BLENDER_BIN", "/ruta/que/no/existe/blender")

    estado, version = comprobar_mod._estado_blender()

    assert estado == "falta"
    assert version == _version_fijada()


def test_estado_blender_otra_cuando_la_version_es_distinta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        comprobar_mod,
        "localizar_blender",
        lambda plataforma=None: (Path("/usr/bin/blender"), "PATH"),
    )
    monkeypatch.setattr(
        comprobar_mod,
        "_version_instalada_blender",
        lambda ejecutable: "Blender 4.9.9 (hash abc1234)",
    )

    estado, version = comprobar_mod._estado_blender()

    assert estado == "otra"
    assert version == _version_fijada()


def test_estado_blender_otra_cuando_no_se_puede_leer_la_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        comprobar_mod,
        "localizar_blender",
        lambda plataforma=None: (Path("/usr/bin/blender"), "PATH"),
    )
    monkeypatch.setattr(comprobar_mod, "_version_instalada_blender", lambda ejecutable: None)

    estado, version = comprobar_mod._estado_blender()

    assert estado == "otra"
    assert version == _version_fijada()


def test_estado_blender_windows_ok_por_carpeta_de_instalacion_sin_redescargar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BLENDER_BIN/PATH apunta a otra versión, pero la carpeta del `.msi` ya tiene la
    fijada: debe dar `ok` para que `install.ps1` no vuelva a descargar 365 MB."""

    ruta_otra = Path(r"C:\ruta\vieja\blender.exe")
    ruta_instalacion = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")

    monkeypatch.setattr(
        comprobar_mod,
        "localizar_blender",
        lambda plataforma=None: (ruta_otra, "variable BLENDER_BIN"),
    )

    def _version(ejecutable: Path) -> str:
        if ejecutable == ruta_instalacion:
            return f"Blender {_version_fijada()} (hash abc1234)"
        return "Blender 4.0.0 (hash def5678)"

    monkeypatch.setattr(comprobar_mod, "_version_instalada_blender", _version)
    monkeypatch.setattr(
        comprobar_mod,
        "_buscar_en_carpeta_instalacion_windows",
        lambda: ruta_instalacion,
    )

    estado, version = comprobar_mod._estado_blender(plataforma="win32")

    assert estado == "ok"
    assert version == _version_fijada()


def test_estado_blender_windows_otra_si_la_carpeta_de_instalacion_tampoco_coincide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ruta_otra = Path(r"C:\ruta\vieja\blender.exe")

    monkeypatch.setattr(
        comprobar_mod,
        "localizar_blender",
        lambda plataforma=None: (ruta_otra, "PATH"),
    )
    monkeypatch.setattr(
        comprobar_mod, "_version_instalada_blender", lambda ejecutable: "Blender 4.0.0 (hash)"
    )
    monkeypatch.setattr(comprobar_mod, "_buscar_en_carpeta_instalacion_windows", lambda: None)

    estado, version = comprobar_mod._estado_blender(plataforma="win32")

    assert estado == "otra"
    assert version == _version_fijada()


def test_estado_blender_no_busca_en_carpeta_de_instalacion_fuera_de_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llamadas: list[None] = []

    monkeypatch.setattr(
        comprobar_mod,
        "localizar_blender",
        lambda plataforma=None: (Path("/usr/bin/blender"), "PATH"),
    )
    monkeypatch.setattr(
        comprobar_mod, "_version_instalada_blender", lambda ejecutable: "Blender 4.0.0 (hash)"
    )

    def _no_deberia_llamarse():
        llamadas.append(None)
        return None

    monkeypatch.setattr(
        comprobar_mod, "_buscar_en_carpeta_instalacion_windows", _no_deberia_llamarse
    )

    estado, _version = comprobar_mod._estado_blender(plataforma="linux")

    assert estado == "otra"
    assert llamadas == []


# ---------------------------------------------------------------------------
# `castkit comprobar --estado-blender`: salida de exactamente dos líneas
# ASCII y código de salida 0.
# ---------------------------------------------------------------------------


def test_cli_estado_blender_imprime_dos_lineas_ascii_y_sale_con_cero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        comprobar_mod,
        "localizar_blender",
        lambda plataforma=None: (Path("/usr/bin/blender"), "PATH"),
    )
    monkeypatch.setattr(
        comprobar_mod,
        "_version_instalada_blender",
        lambda ejecutable: f"Blender {_version_fijada()} (hash abc1234)",
    )

    codigo = main(["comprobar", "--estado-blender"])
    salida = capsys.readouterr().out

    assert codigo == 0
    assert salida == f"estado=ok\nversion={_version_fijada()}\n"
    assert all(ord(c) < 128 for c in salida)


def test_cli_estado_blender_falta_sale_con_cero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _sin_blender(plataforma=None):
        raise BlenderNotFoundError("no encontrado")

    monkeypatch.setattr(comprobar_mod, "localizar_blender", _sin_blender)

    codigo = main(["comprobar", "--estado-blender"])
    salida = capsys.readouterr().out

    assert codigo == 0
    assert salida == f"estado=falta\nversion={_version_fijada()}\n"


def test_cli_comprobar_sin_la_opcion_no_cambia(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`castkit comprobar` sin `--estado-blender` sigue con la comprobación completa."""
    monkeypatch.setattr(
        comprobar_mod,
        "localizar_blender",
        lambda plataforma=None: (Path("/usr/bin/blender"), "PATH"),
    )
    monkeypatch.setattr(
        comprobar_mod,
        "_version_instalada_blender",
        lambda ejecutable: f"Blender {_version_fijada()} (hash abc1234)",
    )

    codigo = main(["comprobar"])
    salida = capsys.readouterr().out

    assert codigo == 0
    assert "estado=" not in salida
    assert "La configuración es válida." in salida
