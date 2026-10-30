"""Configuración compartida de pytest para Castkit."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from castkit.blender import BlenderNotFoundError, find_blender

_RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _config_fabricacion_de_prueba(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fija `CASTKIT_FABRICACION_TOML` al toml del repo para toda la suite (#64).

    Sin esto, `ruta_config_fabricacion()` iría a la carpeta de configuración
    del usuario (y, si no existiera ahí, la crearía) cada vez que un test
    carga una sección de `config/fabricacion.toml`, lo que escribiría fuera
    del repositorio y fuera del control de los tests. Se aplica a cada test
    automáticamente (vía `monkeypatch`, que deshace el cambio al terminar);
    un test que necesite otro valor (o ninguno) puede pisarlo con su propio
    `monkeypatch.setenv`/`delenv` en el cuerpo del test, sin que esta fixture
    se lo impida.
    """
    monkeypatch.setenv("CASTKIT_FABRICACION_TOML", str(_RAIZ / "config" / "fabricacion.toml"))


@pytest.fixture(autouse=True)
def _carpeta_disenos_aislada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita que la suite toque la carpeta Documentos real del equipo (#83).

    Quita ``CASTKIT_DISENOS`` del entorno heredado (un test que la necesite
    la vuelve a fijar en su propio cuerpo, con `monkeypatch.setenv`) y hace
    que :func:`castkit.diseno.carpeta_documentos` devuelva una carpeta
    dentro de `tmp_path`, para que `carpeta_disenos()` sin la variable
    definida nunca cree ni escriba en los Documentos reales del equipo que
    corre los tests.
    """
    import castkit.diseno as mod_diseno

    monkeypatch.delenv("CASTKIT_DISENOS", raising=False)
    documentos = tmp_path / "Documents"
    monkeypatch.setattr(mod_diseno, "carpeta_documentos", lambda plataforma=None: documentos)


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
