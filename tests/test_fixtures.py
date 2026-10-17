"""Tests sobre las mallas de referencia en tests/fixtures/."""

from pathlib import Path

import pytest
import trimesh

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_cube_fixture_is_watertight_and_has_expected_volume() -> None:
    mesh = trimesh.load(FIXTURES_DIR / "cube.stl")

    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(1000.0, rel=1e-3)
