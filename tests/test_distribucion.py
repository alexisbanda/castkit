"""Tests de distribución de castkit sin Git ni claves SSH (ticket #80).

Cubre: `castkit.__version__` sincronizado con `pyproject.toml`; que `install.ps1` use la
API de releases, instale `[profundidad]` con `--force`, no use comillas simples en
`uv tool install`, sea ASCII puro y solo use `exit` dentro de su único helper de fallo;
que `.github/workflows/release.yml` dispare con una etiqueta `v*`, compruebe que coincide
con la versión de `pyproject.toml` y adjunte la rueda e `install.ps1` a la release; y que
`README.md` documente la instalación desde la release en Linux/macOS con el índice de
PyTorch para CPU, con la misma versión que `pyproject.toml`.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import castkit

_RAIZ = Path(__file__).resolve().parents[1]
_PYPROJECT = _RAIZ / "pyproject.toml"
_UV_LOCK = _RAIZ / "uv.lock"
_INSTALL_PS1 = (_RAIZ / "install.ps1").read_text(encoding="utf-8")
_RELEASE_YML = (_RAIZ / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
_CI_YML = (_RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
_README = (_RAIZ / "README.md").read_text(encoding="utf-8")


def _version_pyproject() -> str:
    with _PYPROJECT.open("rb") as flujo:
        datos = tomllib.load(flujo)
    return datos["project"]["version"]


# ---------------------------------------------------------------------------
# castkit.__version__ sincronizado con pyproject.toml
# ---------------------------------------------------------------------------


def test_version_de_castkit_coincide_con_pyproject() -> None:
    assert castkit.__version__ == _version_pyproject()


def test_uv_lock_tiene_la_misma_version_de_castkit_que_pyproject() -> None:
    texto = _UV_LOCK.read_text(encoding="utf-8")
    coincidencia = re.search(r'name = "castkit"\nversion = "([0-9]+\.[0-9]+\.[0-9]+)"', texto)
    assert coincidencia, "No se encontró la versión de castkit en uv.lock"
    assert coincidencia.group(1) == _version_pyproject()


# ---------------------------------------------------------------------------
# install.ps1
# ---------------------------------------------------------------------------


def test_install_ps1_usa_la_api_de_releases_sin_autenticar() -> None:
    assert "api.github.com/repos/alexisbanda/castkit/releases/latest" in _INSTALL_PS1
    assert "Authorization" not in _INSTALL_PS1
    assert "GH_TOKEN" not in _INSTALL_PS1


def test_install_ps1_tiene_respaldo_sin_api_por_releases_latest() -> None:
    assert "github.com/alexisbanda/castkit/releases/latest" in _INSTALL_PS1
    assert "ResponseUri" in _INSTALL_PS1


def test_install_ps1_ya_no_dice_espera_unos_minutos() -> None:
    assert "unos minutos" not in _INSTALL_PS1


def test_install_ps1_instala_el_extra_profundidad_con_force() -> None:
    assert "castkit[profundidad] @" in _INSTALL_PS1
    assert "--force" in _INSTALL_PS1
    assert 'tool install --force "castkit[profundidad] @' in _INSTALL_PS1


def test_install_ps1_sin_comillas_simples_en_uv_tool_install() -> None:
    assert "tool install '" not in _INSTALL_PS1
    assert "tool install --force 'castkit" not in _INSTALL_PS1


def test_install_ps1_es_ascii_puro() -> None:
    no_ascii = [c for c in _INSTALL_PS1 if ord(c) > 127]
    assert not no_ascii, f"Caracteres no ASCII en install.ps1: {set(no_ascii)!r}"


def _bloque_funcion_fail(texto: str) -> tuple[str, str]:
    """Separa el cuerpo de `function Fail { ... }` del resto del archivo."""

    lineas = texto.splitlines()
    inicio = next(i for i, linea in enumerate(lineas) if linea.strip().startswith("function Fail"))
    profundidad = 0
    fin = inicio
    for i in range(inicio, len(lineas)):
        profundidad += lineas[i].count("{") - lineas[i].count("}")
        if i > inicio and profundidad <= 0:
            fin = i
            break
    bloque = "\n".join(lineas[inicio : fin + 1])
    resto = "\n".join(lineas[:inicio] + lineas[fin + 1 :])
    return bloque, resto


def test_install_ps1_exit_aparece_solo_en_el_helper_de_fallo() -> None:
    bloque_fail, resto = _bloque_funcion_fail(_INSTALL_PS1)
    assert re.search(r"\bexit\b", bloque_fail, re.IGNORECASE), (
        "No se encontró `exit` dentro de `function Fail`."
    )
    assert not re.search(r"\bexit\b", resto, re.IGNORECASE), (
        "`exit` aparece fuera de `function Fail` (bajo `iex` cerraría la ventana del operador)."
    )


def test_install_ps1_tiene_parametro_rueda_opcional() -> None:
    assert re.search(r"param\(\s*\[string\]\$Rueda", _INSTALL_PS1)


def test_install_ps1_fija_tls12_antes_de_descargar() -> None:
    assert "SecurityProtocol" in _INSTALL_PS1
    assert "Tls12" in _INSTALL_PS1


def test_install_ps1_comprueba_que_castkit_exista_antes_de_ejecutarlo() -> None:
    assert "Get-Command castkit" in _INSTALL_PS1


# ---------------------------------------------------------------------------
# install.ps1: instalación de Blender (#85).
# ---------------------------------------------------------------------------


def test_install_ps1_tiene_parametro_sin_blender() -> None:
    assert re.search(r"\[switch\]\$SinBlender", _INSTALL_PS1)


def test_install_ps1_usa_estado_blender() -> None:
    assert "castkit comprobar --estado-blender" in _INSTALL_PS1


def test_install_ps1_no_contiene_la_version_fijada_de_blender() -> None:
    raiz = Path(__file__).resolve().parents[1]
    version_fijada = (raiz / ".blender-version").read_text(encoding="utf-8").strip()
    assert version_fijada not in _INSTALL_PS1


def test_install_ps1_verifica_el_sha256_del_instalador_de_blender() -> None:
    assert "sha256" in _INSTALL_PS1.lower()
    assert "Get-FileHash" in _INSTALL_PS1
    assert "Sha256BlenderDePrueba" in _INSTALL_PS1


def test_install_ps1_instala_blender_en_modo_pasivo() -> None:
    assert "/passive" in _INSTALL_PS1


def test_install_ps1_bloque_de_blender_no_llama_a_fail() -> None:
    inicio = _INSTALL_PS1.index("function Instalar-Blender-Si-Falta")
    fin = _INSTALL_PS1.index("Instalar-Blender-Si-Falta\n\n", inicio) + len(
        "Instalar-Blender-Si-Falta\n"
    )
    bloque = _INSTALL_PS1[inicio:fin]
    assert not re.search(r"\bFail\b", bloque), (
        "El bloque de instalación de Blender no debe usar `Fail`: un fallo ahí debe avisar y "
        "seguir hasta `castkit comprobar`, no cortar el script."
    )


# ---------------------------------------------------------------------------
# .github/workflows/release.yml
# ---------------------------------------------------------------------------


def test_release_yml_dispara_con_etiqueta_v() -> None:
    assert re.search(r'tags:\s*\n\s*-\s*"v\*"', _RELEASE_YML)


def test_release_yml_comprueba_la_version_de_pyproject() -> None:
    assert "pyproject.toml" in _RELEASE_YML
    assert "GITHUB_REF_NAME" in _RELEASE_YML
    assert re.search(r'if\s*\[\s*"\$TAG"\s*!=\s*"v\$VERSION"\s*\]', _RELEASE_YML)


def test_release_yml_reutiliza_ci_yml() -> None:
    assert "uses: ./.github/workflows/ci.yml" in _RELEASE_YML


def test_ci_yml_admite_workflow_call() -> None:
    assert "workflow_call:" in _CI_YML


def test_release_yml_adjunta_la_rueda_y_el_instalador() -> None:
    bloque_publicar = _RELEASE_YML.split("publicar:")[1]
    assert "gh release create" in bloque_publicar
    assert "dist/*.whl" in bloque_publicar
    assert "install.ps1" in bloque_publicar


def test_release_yml_contents_write_solo_en_publicar() -> None:
    jobs = _RELEASE_YML.split("jobs:")[1]
    bloques = re.split(r"\n  (?=[a-z][a-z-]*:\n)", jobs)
    for bloque in bloques:
        nombre = bloque.split(":")[0].strip()
        if "contents: write" in bloque:
            assert nombre == "publicar", (
                f"`contents: write` aparece en el job «{nombre}», no solo en «publicar»."
            )


def test_release_yml_dry_run_no_omite_jobs_posteriores_a_uno_saltado() -> None:
    # El job `tests` depende de `comprobar-etiqueta`, que se salta en un ensayo en seco;
    # su condición debe seguir dejándolo correr cuando ese job queda "skipped".
    assert "needs.comprobar-etiqueta.result == 'skipped'" in _RELEASE_YML


# ---------------------------------------------------------------------------
# README.md
# ---------------------------------------------------------------------------


def test_readme_instalacion_linux_macos_usa_el_indice_pytorch_cpu() -> None:
    assert "--index pytorch-cpu=https://download.pytorch.org/whl/cpu" in _README
    assert (
        'uv tool install "castkit[profundidad] @ https://github.com/alexisbanda/castkit/' in _README
    )


def test_readme_version_de_la_url_de_ejemplo_coincide_con_pyproject() -> None:
    coincidencias = re.findall(r"castkit-([0-9]+\.[0-9]+\.[0-9]+)-py3-none-any\.whl", _README)
    assert coincidencias, "No se encontró ninguna URL de rueda de castkit en README.md"
    version = _version_pyproject()
    for encontrada in coincidencias:
        assert encontrada == version, (
            f"La URL de ejemplo en README.md usa la versión {encontrada}, "
            f"pero pyproject.toml está en {version}."
        )


def test_readme_documenta_como_publicar_una_version() -> None:
    assert "## Publicar una versión" in _README
    assert "git tag v" in _README
    assert "git push origin v" in _README


def test_readme_publicar_una_version_menciona_uv_lock() -> None:
    bloque_publicar = _README.split("## Publicar una versión")[1].split("## ")[0]
    assert "uv lock" in bloque_publicar
