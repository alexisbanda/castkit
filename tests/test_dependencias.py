"""Comprueba el stack de dependencias tras retirar CadQuery y matplotlib (#37).

`numpy`, `scipy` y `Pillow` se usan directamente en el código (antes llegaban
solo de forma transitiva vía `cadquery` → `cadquery-ocp` → `vtk` →
`matplotlib`) y deben quedar declaradas en `pyproject.toml`; `cadquery` y
`matplotlib` ya no se usan y no deben declararse.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]


def test_numpy_scipy_pillow_son_importables() -> None:
    import numpy  # noqa: F401
    import PIL.Image  # noqa: F401
    import scipy.ndimage  # noqa: F401


def test_pyproject_declara_numpy_scipy_pillow_y_no_cadquery_ni_matplotlib() -> None:
    with (_RAIZ / "pyproject.toml").open("rb") as flujo:
        datos = tomllib.load(flujo)

    dependencias = datos["project"]["dependencies"]
    nombres = {dep.split(">=")[0].split("==")[0].strip().lower() for dep in dependencias}

    for requerida in ("numpy", "scipy", "pillow"):
        assert requerida in nombres, f'"{requerida}" debe estar declarada en pyproject.toml'

    for retirada in ("cadquery", "matplotlib"):
        assert retirada not in nombres, f'"{retirada}" ya no debe declararse en pyproject.toml'
