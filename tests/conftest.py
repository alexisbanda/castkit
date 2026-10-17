"""Configuración compartida de pytest para Castkit."""

import pytest

from castkit.blender import BlenderNotFoundError, find_blender


def pytest_runtest_setup(item: pytest.Item) -> None:
    if "blender" in item.keywords:
        try:
            find_blender()
        except BlenderNotFoundError as exc:
            pytest.skip(f"Blender no disponible: {exc}")
