"""Tests de castkit.blender: localización del ejecutable y ejecución de scripts."""

from pathlib import Path

import pytest

from castkit.blender import (
    BlenderNotFoundError,
    find_blender,
    localizar_blender,
    mensaje_blender_no_encontrado,
    run_blender_script,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
LOAD_MESH_SCRIPT = (
    Path(__file__).parent.parent / "src" / "castkit" / "blender_scripts" / "load_mesh.py"
)


def _preparar_windows_falso(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *carpetas: str, con_exe: bool = True
) -> Path:
    """Simula un Windows sin `BLENDER_BIN` ni `blender` en el PATH.

    Crea, bajo `tmp_path/Blender Foundation`, una carpeta por cada nombre en
    `carpetas` (p. ej. "Blender 5.2"), cada una con un `blender.exe` vacío
    (salvo que `con_exe` sea `False`, para simular una carpeta sin
    ejecutable). Fija `ProgramFiles` a `tmp_path` y devuelve esa misma ruta.
    """
    monkeypatch.delenv("BLENDER_BIN", raising=False)
    monkeypatch.setattr("castkit.blender.shutil.which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))

    carpeta_foundation = tmp_path / "Blender Foundation"
    for nombre in carpetas:
        carpeta = carpeta_foundation / nombre
        carpeta.mkdir(parents=True)
        if con_exe:
            (carpeta / "blender.exe").write_text("")
    return tmp_path


def test_find_blender_uses_blender_bin_when_valid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_blender = tmp_path / "blender"
    fake_blender.write_text("")
    monkeypatch.setenv("BLENDER_BIN", str(fake_blender))

    assert find_blender() == fake_blender


def test_find_blender_raises_when_blender_bin_does_not_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "no-existe" / "blender"
    monkeypatch.setenv("BLENDER_BIN", str(missing))

    with pytest.raises(BlenderNotFoundError):
        find_blender()


def test_find_blender_falls_back_to_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BLENDER_BIN", raising=False)
    monkeypatch.setattr("castkit.blender.shutil.which", lambda name: "/usr/bin/blender")

    assert find_blender() == Path("/usr/bin/blender")


def test_find_blender_raises_blender_not_found_error_with_spanish_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BLENDER_BIN", raising=False)
    monkeypatch.setattr("castkit.blender.shutil.which", lambda name: None)

    with pytest.raises(BlenderNotFoundError) as exc_info:
        find_blender()

    message = str(exc_info.value)
    assert "BLENDER_BIN" in message
    assert "Blender" in message


# ---------------------------------------------------------------------------
# `localizar_blender()`: carpeta de instalación de Blender en Windows (#77).
# ---------------------------------------------------------------------------


def test_localizar_blender_windows_encuentra_en_program_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = _preparar_windows_falso(monkeypatch, tmp_path, "Blender 5.2")

    ruta, origen = localizar_blender(plataforma="win32")

    assert ruta == base / "Blender Foundation" / "Blender 5.2" / "blender.exe"
    assert origen == "carpeta de instalación de Blender"


def test_localizar_blender_windows_prefiere_la_version_fijada(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = _preparar_windows_falso(
        monkeypatch, tmp_path, "Blender 4.5", "Blender 5.2", "Blender 5.10"
    )
    monkeypatch.setattr("castkit.blender.version_blender_fijada", lambda: "5.2.2")

    ruta, origen = localizar_blender(plataforma="win32")

    assert ruta == base / "Blender Foundation" / "Blender 5.2" / "blender.exe"
    assert origen == "carpeta de instalación de Blender"


def test_localizar_blender_windows_sin_coincidencia_elige_la_version_mas_alta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = _preparar_windows_falso(
        monkeypatch, tmp_path, "Blender 4.5", "Blender 5.9", "Blender 5.10"
    )
    monkeypatch.setattr("castkit.blender.version_blender_fijada", lambda: "6.0.0")

    ruta, origen = localizar_blender(plataforma="win32")

    assert ruta == base / "Blender Foundation" / "Blender 5.10" / "blender.exe"
    assert origen == "carpeta de instalación de Blender"


def test_localizar_blender_windows_ignora_carpeta_sin_ejecutable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = _preparar_windows_falso(monkeypatch, tmp_path, "Blender 9.9", con_exe=False)
    # "Blender 5.2" sí trae blender.exe: es la única carpeta elegible, aunque
    # "Blender 9.9" tenga un número de versión más alto.
    (base / "Blender Foundation" / "Blender 5.2").mkdir(parents=True)
    (base / "Blender Foundation" / "Blender 5.2" / "blender.exe").write_text("")

    ruta, origen = localizar_blender(plataforma="win32")

    assert ruta == base / "Blender Foundation" / "Blender 5.2" / "blender.exe"
    assert origen == "carpeta de instalación de Blender"


def test_localizar_blender_blender_bin_tiene_prioridad_sobre_program_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _preparar_windows_falso(monkeypatch, tmp_path, "Blender 5.2")
    blender_bin = tmp_path / "mi-blender" / "blender.exe"
    blender_bin.parent.mkdir()
    blender_bin.write_text("")
    monkeypatch.setenv("BLENDER_BIN", str(blender_bin))

    ruta, origen = localizar_blender(plataforma="win32")

    assert ruta == blender_bin
    assert origen == "variable BLENDER_BIN"


def test_localizar_blender_path_tiene_prioridad_sobre_program_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _preparar_windows_falso(monkeypatch, tmp_path, "Blender 5.2")
    monkeypatch.setattr("castkit.blender.shutil.which", lambda name: "/usr/bin/blender")

    ruta, origen = localizar_blender(plataforma="win32")

    assert ruta == Path("/usr/bin/blender")
    assert origen == "PATH"


def test_localizar_blender_fuera_de_windows_no_busca_en_program_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _preparar_windows_falso(monkeypatch, tmp_path, "Blender 5.2")

    with pytest.raises(BlenderNotFoundError):
        localizar_blender(plataforma="linux")


def test_localizar_blender_bin_inexistente_falla_aunque_haya_program_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _preparar_windows_falso(monkeypatch, tmp_path, "Blender 5.2")
    monkeypatch.setenv("BLENDER_BIN", str(tmp_path / "no-existe" / "blender.exe"))

    with pytest.raises(BlenderNotFoundError):
        localizar_blender(plataforma="win32")


# ---------------------------------------------------------------------------
# `mensaje_blender_no_encontrado()`: texto según la plataforma (#77).
# ---------------------------------------------------------------------------


def test_mensaje_blender_no_encontrado_windows_explica_setx_y_terminal_nueva() -> None:
    mensaje = mensaje_blender_no_encontrado("win32")

    assert "setx BLENDER_BIN" in mensaje
    assert "terminal nueva" in mensaje
    from castkit.config import version_blender_fijada

    assert version_blender_fijada() in mensaje
    assert ".blender-version" not in mensaje


def test_mensaje_blender_no_encontrado_resto_explica_export() -> None:
    mensaje = mensaje_blender_no_encontrado("linux")

    assert "export BLENDER_BIN" in mensaje
    from castkit.config import version_blender_fijada

    assert version_blender_fijada() in mensaje
    assert ".blender-version" not in mensaje


@pytest.mark.blender
def test_run_blender_script_loads_cube_fixture() -> None:
    result = run_blender_script(LOAD_MESH_SCRIPT, str(FIXTURES_DIR / "cube.stl"))

    assert result.returncode == 0
    assert "castkit: malla cargada" in result.stdout
