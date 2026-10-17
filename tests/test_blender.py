"""Tests de castkit.blender: localización del ejecutable y ejecución de scripts."""

from pathlib import Path

import pytest

from castkit.blender import BlenderNotFoundError, find_blender, run_blender_script

FIXTURES_DIR = Path(__file__).parent / "fixtures"
LOAD_MESH_SCRIPT = (
    Path(__file__).parent.parent / "src" / "castkit" / "blender_scripts" / "load_mesh.py"
)


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


@pytest.mark.blender
def test_run_blender_script_loads_cube_fixture() -> None:
    result = run_blender_script(LOAD_MESH_SCRIPT, str(FIXTURES_DIR / "cube.stl"))

    assert result.returncode == 0
    assert "castkit: malla cargada" in result.stdout
