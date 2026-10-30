"""Tests de instalación de castkit como comando (ticket #64).

Cubre: que la configuración por defecto viaje empaquetada y el wheel la
incluya junto a `blender_scripts/`; el orden de resolución de
`ruta_config_fabricacion()`; que los tests nunca escriban en la carpeta de
configuración del usuario; `carpeta_config_usuario()` por sistema operativo;
`castkit comprobar`; y que el resumen de `castkit alta` muestre la carpeta
donde se creará el diseño.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

import castkit.config as config_mod
from castkit.blender import BlenderNotFoundError
from castkit.cli import main
from castkit.config import (
    carpeta_bajo_clon_castkit,
    carpeta_config_usuario,
    ruta_config_fabricacion,
    version_blender_fijada,
)

_RAIZ_REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# El wheel incluye la configuración empaquetada y los scripts de Blender.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv no está en el PATH")
def test_wheel_incluye_datos_empaquetados_y_blender_scripts(tmp_path: Path) -> None:
    resultado = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=_RAIZ_REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert resultado.returncode == 0, resultado.stderr

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as paquete:
        nombres = set(paquete.namelist())

    assert "castkit/datos/fabricacion.toml" in nombres
    assert "castkit/datos/blender-version" in nombres
    assert "castkit/blender_scripts/load_mesh.py" in nombres
    assert "castkit/blender_scripts/remesh_voxel.py" in nombres
    assert "castkit/blender_scripts/render_vistas.py" in nombres


# ---------------------------------------------------------------------------
# Orden de resolución de `ruta_config_fabricacion()`.
# ---------------------------------------------------------------------------


def test_variable_de_entorno_tiene_prioridad(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ruta = tmp_path / "otra.toml"
    ruta.write_text("[limpieza]\n", encoding="utf-8")
    monkeypatch.setenv("CASTKIT_FABRICACION_TOML", str(ruta))
    assert ruta_config_fabricacion() == ruta


def test_sin_variable_en_un_clon_usa_el_toml_del_repositorio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sin CASTKIT_FABRICACION_TOML (la fixture autouse del conftest la fija;
    # aquí se borra para probar la rama de clon de desarrollo tal cual).
    monkeypatch.delenv("CASTKIT_FABRICACION_TOML", raising=False)
    assert ruta_config_fabricacion() == _RAIZ_REPO / "config" / "fabricacion.toml"


def test_version_blender_fijada_en_un_clon_lee_el_archivo_del_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        version_blender_fijada()
        == (_RAIZ_REPO / ".blender-version").read_text(encoding="utf-8").strip()
    )


def test_sin_clon_crea_copia_en_carpeta_de_usuario_y_avisa(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CASTKIT_FABRICACION_TOML", raising=False)
    carpeta_usuario = tmp_path / "config-usuario"
    # Simula "no es un clon de desarrollo" (ver `_raiz_clon_desarrollo`) y fija
    # la carpeta de configuración del usuario a una carpeta de prueba.
    monkeypatch.setattr(config_mod, "_raiz_clon_desarrollo", lambda: None)
    monkeypatch.setattr(
        config_mod, "carpeta_config_usuario", lambda plataforma=None: carpeta_usuario
    )
    contenido_paquete = (_RAIZ_REPO / "config" / "fabricacion.toml").read_bytes()
    monkeypatch.setattr(config_mod, "_contenido_fabricacion_empaquetada", lambda: contenido_paquete)

    ruta = ruta_config_fabricacion()

    assert ruta == carpeta_usuario / "fabricacion.toml"
    assert ruta.is_file()
    assert ruta.read_bytes() == contenido_paquete

    error = capsys.readouterr().err
    assert "Aviso" in error
    assert str(ruta) in error


def test_sin_clon_no_sobrescribe_si_ya_existe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CASTKIT_FABRICACION_TOML", raising=False)
    carpeta_usuario = tmp_path / "config-usuario"
    carpeta_usuario.mkdir(parents=True)
    ruta_existente = carpeta_usuario / "fabricacion.toml"
    ruta_existente.write_text("[limpieza]\n# ya personalizado\n", encoding="utf-8")

    monkeypatch.setattr(config_mod, "_raiz_clon_desarrollo", lambda: None)
    monkeypatch.setattr(
        config_mod, "carpeta_config_usuario", lambda plataforma=None: carpeta_usuario
    )

    ruta = ruta_config_fabricacion()

    assert ruta == ruta_existente
    assert "ya personalizado" in ruta.read_text(encoding="utf-8")
    assert capsys.readouterr().err == ""  # no se avisa: no se creó nada.


def test_con_la_env_var_del_conftest_no_se_escribe_en_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """La fixture autouse del conftest ya fija CASTKIT_FABRICACION_TOML (#64):
    aunque HOME apunte a una carpeta vacía, nada debe escribirse en ella.
    """
    home_falso = tmp_path / "home"
    home_falso.mkdir()
    monkeypatch.setenv("HOME", str(home_falso))

    ruta = ruta_config_fabricacion()

    assert ruta == Path(os.environ["CASTKIT_FABRICACION_TOML"])
    assert list(home_falso.iterdir()) == []


# ---------------------------------------------------------------------------
# `carpeta_config_usuario()` por sistema operativo.
# ---------------------------------------------------------------------------


def test_carpeta_config_usuario_linux_con_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdgcfg"))
    assert carpeta_config_usuario("linux") == tmp_path / "xdgcfg" / "castkit"


def test_carpeta_config_usuario_linux_sin_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert carpeta_config_usuario("linux") == Path.home() / ".config" / "castkit"


def test_carpeta_config_usuario_windows_con_appdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert carpeta_config_usuario("win32") == tmp_path / "Roaming" / "castkit"


def test_carpeta_config_usuario_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        carpeta_config_usuario("darwin")
        == Path.home() / "Library" / "Application Support" / "castkit"
    )


# ---------------------------------------------------------------------------
# `carpeta_bajo_clon_castkit()`: detección de que una carpeta cae dentro del
# propio repositorio de castkit (usado por `castkit comprobar`).
# ---------------------------------------------------------------------------


def test_carpeta_bajo_clon_castkit_detecta_la_raiz_del_repo() -> None:
    assert carpeta_bajo_clon_castkit(_RAIZ_REPO / "tests" / "fixtures") == _RAIZ_REPO


def test_carpeta_bajo_clon_castkit_devuelve_none_fuera_del_repo(tmp_path: Path) -> None:
    assert carpeta_bajo_clon_castkit(tmp_path) is None


# ---------------------------------------------------------------------------
# `castkit comprobar`.
# ---------------------------------------------------------------------------


def _blender_falso(monkeypatch: pytest.MonkeyPatch, *, version: str = "5.2.2") -> None:
    import castkit.comprobar as comprobar_mod

    monkeypatch.setattr(
        comprobar_mod,
        "localizar_blender",
        lambda: (Path("/usr/bin/blender"), "PATH"),
    )

    class _ProcesoFalso:
        stdout = f"Blender {version} (hash abc1234)\n"

    monkeypatch.setattr(comprobar_mod.subprocess, "run", lambda *a, **k: _ProcesoFalso())


def test_comprobar_con_blender_y_config_ok_devuelve_cero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _blender_falso(monkeypatch)

    codigo = main(["comprobar"])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert f"Blender: {Path('/usr/bin/blender')} (PATH)" in salida
    assert "Versión instalada: Blender 5.2.2" in salida
    assert "La configuración es válida." in salida


def test_comprobar_sin_blender_muestra_el_mensaje_del_ticket_y_falla(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import castkit.comprobar as comprobar_mod
    from castkit.blender import mensaje_blender_no_encontrado

    def _sin_blender():
        raise BlenderNotFoundError("no encontrado")

    monkeypatch.setattr(comprobar_mod, "localizar_blender", _sin_blender)

    codigo = main(["comprobar"])

    assert codigo != 0
    salida = capsys.readouterr().out
    assert mensaje_blender_no_encontrado() in salida


def test_comprobar_con_config_invalida_falla(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _blender_falso(monkeypatch)
    toml_invalido = tmp_path / "vacio.toml"
    toml_invalido.write_text("", encoding="utf-8")
    monkeypatch.setenv("CASTKIT_FABRICACION_TOML", str(toml_invalido))

    codigo = main(["comprobar"])

    assert codigo != 0
    salida = capsys.readouterr().out
    assert "no es válida" in salida.lower()


def test_comprobar_informa_version_distinta_como_aviso_sin_fallar(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _blender_falso(monkeypatch, version="4.9.9")

    codigo = main(["comprobar"])

    assert codigo == 0  # una versión distinta es un aviso, no un error.
    salida = capsys.readouterr().out
    assert "Aviso" in salida and "4.9.9" in salida


@pytest.mark.parametrize("instalado", [True, False])
def test_comprobar_informa_extra_profundidad(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], instalado: bool
) -> None:
    import castkit.comprobar as comprobar_mod

    _blender_falso(monkeypatch)
    monkeypatch.setattr(
        comprobar_mod.importlib.util,
        "find_spec",
        lambda nombre: object() if instalado else None,
    )

    main(["comprobar"])

    salida = capsys.readouterr().out
    if instalado:
        assert "Extra «profundidad»: instalado" in salida
    else:
        assert "Extra «profundidad»: no instalado" in salida
        assert "uv tool install" in salida


def test_comprobar_clave_tripo_presente_no_muestra_su_valor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _blender_falso(monkeypatch)
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_secreto_123")

    main(["comprobar"])

    salida = capsys.readouterr().out
    assert "Clave de Tripo (TRIPO_API_KEY): presente." in salida
    assert "tsk_secreto_123" not in salida


def test_comprobar_clave_tripo_ausente(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _blender_falso(monkeypatch)
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)

    main(["comprobar"])

    salida = capsys.readouterr().out
    assert "Clave de Tripo (TRIPO_API_KEY): no definida" in salida


def test_comprobar_avisa_si_la_carpeta_de_disenos_esta_en_el_clon(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import castkit.comprobar as comprobar_mod

    _blender_falso(monkeypatch)
    monkeypatch.setattr(comprobar_mod, "carpeta_disenos", lambda: _RAIZ_REPO)

    main(["comprobar"])

    salida = capsys.readouterr().out
    assert "Aviso" in salida
    assert "dentro del clon de castkit" in salida


def test_comprobar_sin_aviso_cuando_la_carpeta_de_disenos_es_externa(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    import castkit.comprobar as comprobar_mod

    _blender_falso(monkeypatch)
    carpeta_externa = tmp_path / "disenos"
    carpeta_externa.mkdir()
    monkeypatch.setattr(comprobar_mod, "carpeta_disenos", lambda: carpeta_externa)

    main(["comprobar"])

    salida = capsys.readouterr().out
    assert "dentro del clon de castkit" not in salida


# ---------------------------------------------------------------------------
# El resumen de `castkit alta` muestra la carpeta donde se creará el diseño.
# ---------------------------------------------------------------------------


def test_resumen_de_alta_muestra_la_carpeta_del_diseno(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    destino = tmp_path / "disenos"
    destino.mkdir()
    monkeypatch.chdir(destino)
    monkeypatch.delenv("CASTKIT_DISENOS", raising=False)

    brief = tmp_path / "brief-toro"
    brief.mkdir()
    (brief / "toro.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

    class _StdinInteractivo:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", _StdinInteractivo())

    respuestas = iter(["toro", str(brief)] + [""] * 11)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(respuestas))

    codigo = main(["alta"])
    assert codigo == 0

    salida = capsys.readouterr().out
    assert f"Carpeta donde se creará: {(destino / 'toro').resolve()}" in salida
