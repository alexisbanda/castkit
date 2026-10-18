"""Configuración compartida de pytest para Castkit."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from castkit.blender import BlenderNotFoundError, find_blender


def pytest_runtest_setup(item: pytest.Item) -> None:
    if "blender" in item.keywords:
        try:
            find_blender()
        except BlenderNotFoundError as exc:
            pytest.skip(f"Blender no disponible: {exc}")


@pytest.fixture
def blender_falso(monkeypatch: pytest.MonkeyPatch):
    """Sustituye `find_blender`/`run_blender_script` de `castkit.control1`.

    Simula un Blender headless disponible que escribe 4 PNG reales (600x600)
    en las rutas pedidas, para poder probar en macOS/Windows (sin Blender
    instalado en la CI) la lógica de aprobación/rechazo del punto de control 1
    sin depender de un ejecutable real.
    """
    import castkit.control1 as mod_c1

    llamadas: list[tuple[Path, tuple[str, ...]]] = []

    monkeypatch.setattr(mod_c1, "find_blender", lambda: Path("/usr/bin/blender"))

    def _fake_run_blender(script: Path, *args: str) -> None:
        llamadas.append((script, args))
        # args: ruta_limpia, frente, perfil, arriba, 3-4
        filas = np.arange(600, dtype=np.float64)[:, None]
        columnas = np.arange(600, dtype=np.float64)[None, :]
        for idx, ruta_str in enumerate(args[1:5]):
            patron = np.sin((filas + idx * 70.0) / 35.0) + np.cos((columnas + idx * 40.0) / 45.0)
            canal = ((patron - patron.min()) / (patron.max() - patron.min()) * 255.0).astype(
                np.uint8
            )
            arr = np.stack(
                [
                    canal,
                    np.roll(canal, 15 + idx * 10, axis=0),
                    np.roll(canal, 25 + idx * 5, axis=1),
                ],
                axis=-1,
            )
            Image.fromarray(arr, mode="RGB").save(ruta_str, format="PNG")

    monkeypatch.setattr(mod_c1, "run_blender_script", _fake_run_blender)
    return llamadas
