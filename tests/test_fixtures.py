"""Tests sobre las mallas de referencia en tests/fixtures/."""

from pathlib import Path

import pytest
import trimesh
from trimesh.grouping import group_rows

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _perimetros_huecos(mesh: trimesh.Trimesh) -> list[float]:
    import networkx as nx
    import numpy as np

    grupos = group_rows(mesh.edges_sorted, require_count=1)
    if len(grupos) == 0:
        return []
    borde = mesh.edges[grupos]
    ciclos = nx.cycle_basis(nx.from_edgelist(borde))
    perimetros = []
    for ciclo in ciclos:
        puntos = mesh.vertices[ciclo]
        perimetros.append(float(np.linalg.norm(np.roll(puntos, -1, axis=0) - puntos, axis=1).sum()))
    return perimetros


def test_cube_fixture_is_watertight_and_has_expected_volume() -> None:
    mesh = trimesh.load(FIXTURES_DIR / "cube.stl")

    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(1000.0, rel=1e-3)


def test_agujeros_fixture_tiene_dos_huecos_chicos() -> None:
    """`agujeros.stl`: dos zonas abiertas pequeñas (menores a 10 mm de perímetro)."""
    mesh = trimesh.load(FIXTURES_DIR / "agujeros.stl")

    assert not mesh.is_watertight
    perimetros = _perimetros_huecos(mesh)
    assert len(perimetros) == 2
    assert all(p < 10.0 for p in perimetros)
    assert trimesh.repair.fill_holes(mesh)
    assert mesh.is_watertight


def test_rota_fixture_tiene_un_hueco_grande() -> None:
    """`rota.stl`: una zona abierta grande (mayor a 10 mm de perímetro)."""
    mesh = trimesh.load(FIXTURES_DIR / "rota.stl")

    assert not mesh.is_watertight
    perimetros = _perimetros_huecos(mesh)
    assert len(perimetros) == 1
    assert perimetros[0] > 10.0


def test_agujeros_grandes_fixture_tiene_huecos_de_mas_de_4_aristas() -> None:
    """`agujeros_grandes.stl`: un hueco de 6 aristas y uno de 9, ambos chicos
    (menores a 10 mm de perímetro) pero con más de 4 aristas, así que
    `trimesh.repair.fill_holes` no los cierra con `use_fan=False` (el valor
    por defecto, que solo cierra huecos de 3 o 4 aristas) pero sí con
    `use_fan=True`.
    """
    mesh = trimesh.load(FIXTURES_DIR / "agujeros_grandes.stl")

    assert not mesh.is_watertight

    import networkx as nx

    grupos = group_rows(mesh.edges_sorted, require_count=1)
    borde = mesh.edges[grupos]
    ciclos = nx.cycle_basis(nx.from_edgelist(borde))
    tamanos_aristas = sorted(len(ciclo) for ciclo in ciclos)
    assert tamanos_aristas == [6, 9]

    perimetros = _perimetros_huecos(mesh)
    assert len(perimetros) == 2
    assert all(p < 10.0 for p in perimetros)

    sin_fan = mesh.copy()
    trimesh.repair.fill_holes(sin_fan)
    assert not sin_fan.is_watertight

    con_fan = mesh.copy()
    assert trimesh.repair.fill_holes(con_fan, use_fan=True)
    assert con_fan.is_watertight


def test_tumbada_fixture_es_una_caja_asimetrica_cerrada() -> None:
    """`tumbada.stl`: caja cerrada con sus tres dimensiones distintas."""
    mesh = trimesh.load(FIXTURES_DIR / "tumbada.stl")

    assert mesh.is_watertight
    extents = sorted(mesh.extents)
    assert extents == pytest.approx([10.0, 20.0, 30.0], rel=1e-3)
