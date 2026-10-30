"""Tests de la carpeta de diseños por defecto en Documentos/castkit (#83).

Cubre `castkit.diseno.carpeta_documentos` (y sus ayudantes privados por
plataforma), `carpeta_disenos`/`origen_carpeta_disenos`, la compatibilidad de
`castkit.versiones.ubicar_diseno` con la carpeta actual, y la salida de
`castkit comprobar`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import castkit.diseno as mod_diseno
from castkit.cli import main
from castkit.diseno import (
    AltaError,
    carpeta_disenos,
    carpeta_documentos,
    origen_carpeta_disenos,
)
from castkit.versiones import ubicar_diseno

# ---------------------------------------------------------------------------
# carpeta_documentos: Windows
# ---------------------------------------------------------------------------


def test_documentos_windows_usa_el_known_folder_si_funciona(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    esperado = tmp_path / "Usuarios" / "ana" / "Documents"
    monkeypatch.setattr(mod_diseno, "_documentos_windows", lambda: esperado)

    assert carpeta_documentos(plataforma="win32") == esperado


def test_documentos_windows_usa_userprofile_si_falla_el_known_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod_diseno, "_documentos_windows", lambda: None)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "Usuarios" / "ana"))

    assert carpeta_documentos(plataforma="win32") == tmp_path / "Usuarios" / "ana" / "Documents"


def test_documentos_windows_usa_home_si_tampoco_hay_userprofile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod_diseno, "_documentos_windows", lambda: None)
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home-ana")

    assert carpeta_documentos(plataforma="win32") == tmp_path / "home-ana" / "Documents"


# ---------------------------------------------------------------------------
# carpeta_documentos: macOS
# ---------------------------------------------------------------------------


def test_documentos_macos_es_home_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home-ana")

    assert carpeta_documentos(plataforma="darwin") == tmp_path / "home-ana" / "Documents"


# ---------------------------------------------------------------------------
# carpeta_documentos: Linux (xdg-user-dir)
# ---------------------------------------------------------------------------


def test_documentos_linux_usa_xdg_user_dir_si_devuelve_otra_carpeta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    esperado = tmp_path / "home-ana" / "Documentos"
    monkeypatch.setattr(mod_diseno.shutil, "which", lambda nombre: "/usr/bin/xdg-user-dir")
    monkeypatch.setattr(
        mod_diseno.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=f"{esperado}\n", stderr=""),
    )

    assert carpeta_documentos(plataforma="linux") == esperado


def test_documentos_linux_ignora_xdg_user_dir_si_devuelve_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home-ana"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(mod_diseno.shutil, "which", lambda nombre: "/usr/bin/xdg-user-dir")
    monkeypatch.setattr(
        mod_diseno.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=f"{home}\n", stderr=""),
    )

    assert carpeta_documentos(plataforma="linux") == home / "Documents"


def test_documentos_linux_usa_home_documents_si_falta_xdg_user_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home-ana"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(mod_diseno.shutil, "which", lambda nombre: None)

    assert carpeta_documentos(plataforma="linux") == home / "Documents"


# ---------------------------------------------------------------------------
# carpeta_disenos / origen_carpeta_disenos
# ---------------------------------------------------------------------------


def test_carpeta_disenos_por_defecto_se_crea_dentro_de_documentos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documentos = tmp_path / "Documents"
    monkeypatch.delenv("CASTKIT_DISENOS", raising=False)
    monkeypatch.setattr(mod_diseno, "carpeta_documentos", lambda plataforma=None: documentos)

    carpeta = carpeta_disenos()

    assert carpeta == documentos / "castkit"
    assert carpeta.is_dir()
    assert origen_carpeta_disenos() == "defecto"


def test_carpeta_disenos_con_variable_definida_no_cambia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carpeta_var = tmp_path / "pedidos"
    carpeta_var.mkdir()
    monkeypatch.setenv("CASTKIT_DISENOS", str(carpeta_var))

    assert carpeta_disenos() == carpeta_var
    assert origen_carpeta_disenos() == "variable"


def test_carpeta_disenos_con_variable_inexistente_sigue_fallando(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CASTKIT_DISENOS", str(tmp_path / "no-existe"))

    with pytest.raises(AltaError, match="CASTKIT_DISENOS"):
        carpeta_disenos()


# ---------------------------------------------------------------------------
# ubicar_diseno: compatibilidad con la carpeta actual (#83)
# ---------------------------------------------------------------------------


def _crear_diseno_falso(carpeta_padre: Path, nombre: str) -> Path:
    """Crea lo mínimo que `ubicar_diseno` reconoce como diseño: la carpeta y
    un `diseno.json` cualquiera (su contenido no se valida en esta función).
    """
    carpeta = carpeta_padre / nombre
    carpeta.mkdir(parents=True)
    (carpeta / "diseno.json").write_text("{}", encoding="utf-8")
    return carpeta


def test_ubicar_diseno_usa_la_carpeta_actual_con_aviso_si_no_esta_en_disenos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    carpeta_disenos_dir = tmp_path / "disenos"
    carpeta_disenos_dir.mkdir()
    monkeypatch.setenv("CASTKIT_DISENOS", str(carpeta_disenos_dir))

    cwd = tmp_path / "cwd-vieja"
    diseno_cwd = _crear_diseno_falso(cwd, "toro")
    monkeypatch.chdir(cwd)

    carpeta = ubicar_diseno("toro")

    assert carpeta == diseno_cwd
    aviso = capsys.readouterr().err
    assert 'Aviso: el diseño "toro" está en la carpeta actual' in aviso
    assert str(cwd) in aviso
    assert str(carpeta_disenos_dir) in aviso
    assert "Muévelo allí" in aviso


def test_ubicar_diseno_prefiere_la_carpeta_de_disenos_si_existe_en_ambas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    carpeta_disenos_dir = tmp_path / "disenos"
    carpeta_disenos_dir.mkdir()
    monkeypatch.setenv("CASTKIT_DISENOS", str(carpeta_disenos_dir))

    cwd = tmp_path / "cwd-vieja"
    cwd.mkdir()
    _crear_diseno_falso(cwd, "toro")
    carpeta_buena = _crear_diseno_falso(carpeta_disenos_dir, "toro")
    monkeypatch.chdir(cwd)

    carpeta = ubicar_diseno("toro")

    assert carpeta == carpeta_buena
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# castkit comprobar: origen de la carpeta de diseños
# ---------------------------------------------------------------------------


def test_comprobar_muestra_el_origen_por_defecto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CASTKIT_DISENOS", raising=False)
    monkeypatch.setattr(
        mod_diseno, "carpeta_documentos", lambda plataforma=None: tmp_path / "Documents"
    )

    main(["comprobar"])

    salida = capsys.readouterr().out
    assert "(por defecto; para usar otra, define CASTKIT_DISENOS)" in salida


def test_comprobar_muestra_el_origen_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    carpeta_var = tmp_path / "pedidos"
    carpeta_var.mkdir()
    monkeypatch.setenv("CASTKIT_DISENOS", str(carpeta_var))

    main(["comprobar"])

    salida = capsys.readouterr().out
    assert "(variable CASTKIT_DISENOS)" in salida
