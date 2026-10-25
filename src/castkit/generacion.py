"""Generación de la malla en bruto a partir de los insumos del brief.

El artesano o responsable de producción puede entregar en `<diseño>/brief/`
cualquier combinación de insumos opcionales:
- Imágenes de referencia o generadas con IA (`.png`, `.jpg`, `.jpeg`, `.webp`).
- Documentos o archivos de diseño (`.pdf`, `.ai`).
- Archivos de texto o prompts (`.txt`, `.md`) o un `--prompt` por CLI.
- Un archivo `.stl` opcional si ya cuenta con un modelo 3D.

Si el brief ya contiene un `.stl` (y no se fuerza `--proveedor`), se valida y
se copia directamente a `input/<diseño>_v<actual>_bruto.stl`. En caso
contrario, se delega la creación del `.stl` en un **proveedor enchufable**
(:class:`ProveedorGeneracion3D`), se valida la malla resultante con
:func:`castkit.ingesta.validar_malla` y se registra en la etapa `"generacion"`.
"""

from __future__ import annotations

import json
import math
import mimetypes
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import trimesh

from castkit import profundidad, relieve
from castkit.config import (
    cargar_config_generacion,
    cargar_config_profundidad,
    cargar_config_relieve,
)
from castkit.ingesta import IngestaError, sha256_archivo, validar_malla
from castkit.versiones import (
    VersionError,
    cargar_diseno,
    datos_version,
    nombre_archivo,
    registrar_parametros,
)

EXT_IMAGENES = (".png", ".jpg", ".jpeg", ".webp")
EXT_DOCUMENTOS = (".pdf", ".ai")
EXT_TEXTOS = (".txt", ".md")
EXT_MALLAS = (".stl",)
FORMATOS_SOPORTADOS = (*EXT_IMAGENES, *EXT_DOCUMENTOS, *EXT_TEXTOS, *EXT_MALLAS)


class GeneracionError(VersionError):
    """Se lanza cuando no se puede generar la malla en bruto desde el brief.

    El mensaje siempre está en español. Hereda de :class:`VersionError` (y por
    lo tanto de :class:`castkit.diseno.AltaError`).
    """


@dataclass
class InsumosBrief:
    """Insumos recolectados de la carpeta `brief/` de un diseño."""

    imagenes: list[Path] = field(default_factory=list)
    documentos: list[Path] = field(default_factory=list)
    archivos_texto: list[Path] = field(default_factory=list)
    mallas_stl: list[Path] = field(default_factory=list)
    prompt: str = ""

    def tiene_insumos_para_proveedor(self) -> bool:
        """Indica si hay al menos una imagen, documento o texto/prompt."""
        return bool(self.imagenes or self.documentos or self.prompt.strip())

    def rutas_para_proveedor(self) -> list[Path]:
        """Rutas de archivos visuales/documentales que se pasan al proveedor."""
        return [*self.imagenes, *self.documentos]

    def nombres_archivos_usados(self) -> list[str]:
        """Nombres de todos los archivos del brief utilizados."""
        archivos = [*self.imagenes, *self.documentos, *self.archivos_texto]
        return [ruta.name for ruta in archivos]


class ProveedorGeneracion3D(Protocol):
    """Interfaz común que debe implementar cualquier proveedor de generación 3D."""

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        """Genera un archivo STL en ``destino_stl`` a partir de ``insumos``.

        Debe lanzar :class:`GeneracionError` si la generación no puede
        completarse.
        """


def _descargar_y_convertir_a_stl(url_modelo: str, destino_stl: Path) -> None:
    """Descarga un modelo 3D (`.glb`, `.gltf`, `.obj`, `.stl`) desde ``url_modelo``.

    Lo exporta como STL.
    """
    sufijo = Path(url_modelo.split("?")[0]).suffix.lower() or ".glb"
    descriptor, tmp_str = tempfile.mkstemp(prefix=".modelo-remoto-", suffix=sufijo)
    os.close(descriptor)
    tmp_path = Path(tmp_str)
    try:
        req = urllib.request.Request(url_modelo, headers={"User-Agent": "castkit/0.1"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            tmp_path.write_bytes(resp.read())
        escena_o_malla = trimesh.load(tmp_path, force="mesh")
        if sufijo in (".glb", ".gltf"):
            # `trimesh.load` no reconvierte la convención Y-arriba de glTF/GLB:
            # se comprobó cargando una caja de 10x40x10 (altura en Y) y la
            # altura seguía en Y tras cargarla. Se gira +90° alrededor de X
            # para que el +Y original pase a +Z, como espera el resto del
            # pipeline de castkit (Z-arriba).
            escena_o_malla.apply_transform(
                trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0])
            )
        escena_o_malla.export(destino_stl, file_type="stl")
    except Exception as exc:
        raise GeneracionError(
            f"No se pudo descargar o convertir a STL el modelo generado ({url_modelo}): {exc}"
        ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)


_TRIPO_V3_BASE = "https://openapi.tripo3d.ai"
_TRIPO_ESTADOS_FALLO = ("failed", "cancelled", "banned", "expired")
_TRIPO_INTERVALO_SONDEO_S = 3.0


class ProveedorTripo3D:
    """Proveedor enchufable para Tripo3D API v3 (soporta Imagen → 3D y Texto → 3D).

    Lee la clave de la variable de entorno ``TRIPO_API_KEY``. La API v2 se
    apaga el 2026-10-31; este proveedor usa la base ``https://openapi.tripo3d.ai``.
    Tras crear la tarea, expone ``self.parametros_registro`` con
    ``tripo_modelo`` y ``tripo_task_id`` para que queden registrados en la
    etapa ``"generacion"`` del diseño.
    """

    def __init__(self, modelo: str = "v2.5-20250123", espera_max_s: float = 600.0) -> None:
        self.modelo = modelo
        self.espera_max_s = float(espera_max_s)
        self.parametros_registro: dict[str, Any] = {}

    def _peticion(
        self,
        metodo: str,
        ruta: str,
        api_key: str,
        *,
        data: bytes | None = None,
        content_type: str | None = None,
        timeout: float = 60.0,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Llama a la API v3 de tripo3d y desenvuelve `{"code": 0, "data": {...}}`.

        Si ``task_id`` se indica, se incluye en cualquier mensaje de error
        (la tarea ya existe en tripo3d aunque la petición haya fallado).
        """
        sufijo_tarea = f' (tarea "{task_id}" de "tripo3d")' if task_id else ""
        headers = {"Authorization": f"Bearer {api_key}"}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(
            f"{_TRIPO_V3_BASE}{ruta}", data=data, headers=headers, method=metodo
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                cuerpo = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detalle = exc.read().decode("utf-8", errors="replace")
            raise GeneracionError(
                f'Error HTTP {exc.code} de "tripo3d" en {ruta}{sufijo_tarea}: {detalle}'
            ) from exc
        except Exception as exc:
            raise GeneracionError(
                f'No se pudo conectar con "tripo3d" en {ruta}{sufijo_tarea}: {exc}'
            ) from exc

        codigo = cuerpo.get("code")
        if codigo != 0:
            mensaje = cuerpo.get("message") or cuerpo
            raise GeneracionError(
                f'La API de "tripo3d" devolvió un error en {ruta}{sufijo_tarea} '
                f"(code={codigo}): {mensaje}"
            )
        return cuerpo.get("data") or {}

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        api_key = os.environ.get("TRIPO_API_KEY", "").strip()
        if not api_key:
            raise GeneracionError(
                'El proveedor "tripo3d" requiere la variable de entorno TRIPO_API_KEY '
                '(por ejemplo: export TRIPO_API_KEY="tsk_...").'
            )

        self.parametros_registro = {}

        if insumos.imagenes:
            img = insumos.imagenes[0]
            boundary = "----CastkitTripoBoundary"
            mime = mimetypes.guess_type(img.name)[0] or "image/png"
            cuerpo_multipart = (
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="file"; filename="{img.name}"\r\n'
                    f"Content-Type: {mime}\r\n\r\n"
                ).encode()
                + img.read_bytes()
                + f"\r\n--{boundary}--\r\n".encode()
            )
            datos_subida = self._peticion(
                "POST",
                "/v3/files",
                api_key,
                data=cuerpo_multipart,
                content_type=f"multipart/form-data; boundary={boundary}",
                timeout=120.0,
            )
            file_token = datos_subida.get("file_token")
            if not file_token:
                raise GeneracionError(
                    f'La subida a "tripo3d" no devolvió file_token: {datos_subida}'
                )
            payload_tarea: dict[str, Any] = {
                "input": file_token,
                "model": self.modelo,
                "texture": False,
                "pbr": False,
            }
            ruta_tarea = "/v3/generation/image-to-model"
        else:
            payload_tarea = {
                "prompt": insumos.prompt,
                "model": self.modelo,
                "texture": False,
                "pbr": False,
            }
            ruta_tarea = "/v3/generation/text-to-model"

        datos_tarea = self._peticion(
            "POST",
            ruta_tarea,
            api_key,
            data=json.dumps(payload_tarea).encode("utf-8"),
            content_type="application/json",
            timeout=60.0,
        )
        task_id = datos_tarea.get("task_id")
        if not task_id:
            raise GeneracionError(
                f'La creación de la tarea en "tripo3d" no devolvió task_id: {datos_tarea}'
            )

        self.parametros_registro = {"tripo_modelo": self.modelo, "tripo_task_id": task_id}

        inicio = time.monotonic()
        while True:
            estado_data = self._peticion(
                "GET", f"/v3/tasks/{task_id}", api_key, timeout=60.0, task_id=task_id
            )
            status = estado_data.get("status")
            if status == "success":
                salida = estado_data.get("output") or {}
                url_modelo = (
                    salida.get("model_url")
                    or salida.get("model")
                    or salida.get("pbr_model_url")
                    or salida.get("pbr_model")
                )
                if not url_modelo:
                    raise GeneracionError(
                        f'La tarea "{task_id}" de "tripo3d" terminó sin URL de modelo: '
                        f"{estado_data}"
                    )
                _descargar_y_convertir_a_stl(str(url_modelo), destino_stl)
                return
            if status in _TRIPO_ESTADOS_FALLO:
                raise GeneracionError(
                    f'La tarea "{task_id}" de "tripo3d" falló con estado "{status}".'
                )
            if time.monotonic() - inicio >= self.espera_max_s:
                raise GeneracionError(
                    f"Tiempo de espera agotado ({self.espera_max_s:.0f} s) esperando a la "
                    f'tarea "{task_id}" de "tripo3d".'
                )
            time.sleep(_TRIPO_INTERVALO_SONDEO_S)


class ProveedorImagen3D:
    """Proveedor local que reconstruye un volumen 3D cerrado a partir de una imagen 2D.

    Segmenta la silueta de la figura respecto al fondo, calcula la transformada
    de distancia euclidiana y el mapa de luminancia para dar relieve volumétrico
    suave a ambos lados, y exporta un STL cerrado (`watertight`) listo para
    `castkit limpieza`.
    """

    def __init__(self, resolucion: int = 160) -> None:
        self.resolucion = max(32, int(resolucion))

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        if not insumos.imagenes:
            raise GeneracionError(
                'El proveedor "imagen-3d" requiere al menos una imagen (.png, .jpg, .webp) '
                "en la carpeta brief/."
            )

        import numpy as np
        from PIL import Image
        from scipy import ndimage

        ruta_img = insumos.imagenes[0]
        try:
            im = Image.open(ruta_img).convert("RGBA")
        except Exception as exc:
            raise GeneracionError(
                f'No se pudo abrir la imagen de referencia "{ruta_img.name}": {exc}'
            ) from exc

        arr = np.asarray(im, dtype=np.float32)
        rgb = arr[..., :3]
        alpha = arr[..., 3]

        bordes = np.vstack([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
        bg = np.median(bordes, axis=0)
        dist_bg = np.linalg.norm(rgb - bg, axis=-1)
        fg_raw = (dist_bg > 22.0) & (alpha > 128)
        if not fg_raw.any():
            fg_raw = alpha > 128
        if not fg_raw.any():
            fg_raw = np.ones(rgb.shape[:2], dtype=bool)

        etiquetas, n_comp = ndimage.label(fg_raw)
        if n_comp > 1:
            tamanos = ndimage.sum(fg_raw, etiquetas, range(1, n_comp + 1))
            mayor = int(np.argmax(tamanos)) + 1
            mask_full = etiquetas == mayor
        else:
            mask_full = fg_raw

        ys, xs = np.where(mask_full)
        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())

        h_crop, w_crop = y1 - y0 + 1, x1 - x0 + 1
        escala = self.resolucion / max(h_crop, w_crop)
        nh = max(16, int(round(h_crop * escala)))
        nw = max(16, int(round(w_crop * escala)))

        mask_img = Image.fromarray((mask_full[y0 : y1 + 1, x0 : x1 + 1] * 255).astype(np.uint8))
        mask_small = np.asarray(mask_img.resize((nw, nh), Image.Resampling.BILINEAR)) > 127
        lbls, n_lbl = ndimage.label(mask_small)
        if n_lbl > 1:
            sz = ndimage.sum(mask_small, lbls, range(1, n_lbl + 1))
            mask_small = lbls == (int(np.argmax(sz)) + 1)

        rgb_crop = Image.fromarray(rgb[y0 : y1 + 1, x0 : x1 + 1].astype(np.uint8)).resize(
            (nw, nh), Image.Resampling.BILINEAR
        )
        rgb_small = np.asarray(rgb_crop, dtype=np.float32) / 255.0
        luma = 0.299 * rgb_small[..., 0] + 0.587 * rgb_small[..., 1] + 0.114 * rgb_small[..., 2]

        mask = np.pad(mask_small, 1, mode="constant", constant_values=False)
        luma = np.pad(luma, 1, mode="edge")
        alto_g, ancho_g = mask.shape

        mask_filled = ndimage.binary_fill_holes(mask)
        dist_hull = ndimage.distance_transform_edt(mask_filled)
        dist_local = ndimage.distance_transform_edt(mask)
        dist_hull_norm = dist_hull / max(float(dist_hull.max()), 1.0)
        dist_local_norm = dist_local / max(float(dist_local.max()), 1.0)

        cell_thickness = (
            0.55 * np.power(dist_hull_norm, 0.55)
            + 0.30 * np.power(dist_local_norm, 0.5)
            + 0.15 * ndimage.gaussian_filter(luma, sigma=1.0)
        )
        cell_thickness = ndimage.gaussian_filter(cell_thickness, sigma=1.2)

        v_thick = np.zeros((alto_g + 1, ancho_g + 1), dtype=np.float64)
        v_count = np.zeros((alto_g + 1, ancho_g + 1), dtype=np.float64)
        for dr in (0, 1):
            for dc in (0, 1):
                v_thick[dr : alto_g + dr, dc : ancho_g + dc] += np.where(mask, cell_thickness, 0.0)
                v_count[dr : alto_g + dr, dc : ancho_g + dc] += mask.astype(np.float64)

        active_v = v_count > 0
        v_thick[active_v] = v_thick[active_v] / v_count[active_v]
        max_dim = float(max(alto_g, ancho_g))
        half_depth = 1.2 + v_thick * (0.20 * max_dim)

        front_idx = -np.ones((alto_g + 1, ancho_g + 1), dtype=np.int64)
        back_idx = -np.ones((alto_g + 1, ancho_g + 1), dtype=np.int64)
        verts: list[list[float]] = []
        for r in range(alto_g + 1):
            for c in range(ancho_g + 1):
                if active_v[r, c]:
                    x = float(c)
                    z = float(alto_g - r)
                    d = float(half_depth[r, c])
                    front_idx[r, c] = len(verts)
                    verts.append([x, -d, z])
                    back_idx[r, c] = len(verts)
                    verts.append([x, +d, z])

        faces: list[list[int]] = []
        for r in range(alto_g):
            for c in range(ancho_g):
                if not mask[r, c]:
                    continue
                f00 = int(front_idx[r, c])
                f10 = int(front_idx[r + 1, c])
                f11 = int(front_idx[r + 1, c + 1])
                f01 = int(front_idx[r, c + 1])
                faces.append([f00, f10, f11])
                faces.append([f00, f11, f01])

                b00 = int(back_idx[r, c])
                b10 = int(back_idx[r + 1, c])
                b11 = int(back_idx[r + 1, c + 1])
                b01 = int(back_idx[r, c + 1])
                faces.append([b00, b11, b10])
                faces.append([b00, b01, b11])

                if not mask[r - 1, c]:
                    faces.append([f00, f01, b01])
                    faces.append([f00, b01, b00])
                if not mask[r + 1, c]:
                    faces.append([f11, f10, b10])
                    faces.append([f11, b10, b11])
                if not mask[r, c - 1]:
                    faces.append([f10, f00, b00])
                    faces.append([f10, b00, b10])
                if not mask[r, c + 1]:
                    faces.append([f01, f11, b11])
                    faces.append([f01, b11, b01])

        malla = trimesh.Trimesh(vertices=np.asarray(verts), faces=np.asarray(faces), process=True)
        trimesh.smoothing.filter_taubin(malla, iterations=5)
        trimesh.repair.fix_normals(malla)
        malla.export(destino_stl, file_type="stl")


class ProveedorRelieve:
    """Proveedor local: bajorrelieve por capas de color para diseños planos (#43).

    Usa la primera imagen del brief y un archivo ``relieve.toml`` junto a
    ella (lo propone y lo escribe si no existe; ver :mod:`castkit.relieve`).
    Es, por construcción, un molde abierto (#55): la versión actual del
    diseño necesita ``tipo_molde == "abierto"`` con ``largo_mm`` y
    ``grosor_mm``. :func:`generar_malla` llama a :meth:`usar_version` con
    esos datos antes de invocar :meth:`generar`.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._config = relieve.ConfigRelieve(
            colores_max=int(cfg.get("colores_max", 8)),
            base_mm=float(cfg.get("base_mm", 2.0)),
            paso_mm=float(cfg.get("paso_mm", 0.4)),
            escalon_mm=float(cfg.get("escalon_mm", 0.05)),
            area_min_mm2=float(cfg.get("area_min_mm2", 0.5)),
            placa_mm=float(cfg.get("placa_mm", 0.0)),
            placa_margen_mm=float(cfg.get("placa_margen_mm", 1.0)),
        )
        self._info_version: dict[str, Any] = {}
        self.parametros_registro: dict[str, Any] = {}

    def usar_version(self, info_version: dict[str, Any]) -> None:
        """Recibe los datos de :func:`castkit.versiones.datos_version` de la versión actual."""
        self._info_version = dict(info_version)

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        if not insumos.imagenes:
            raise GeneracionError(
                'El proveedor "relieve" requiere al menos una imagen (.png, .jpg, .webp) en la '
                "carpeta brief/."
            )

        tipo_molde = self._info_version.get("tipo_molde", "completo")
        largo_mm = self._info_version.get("largo_mm")
        grosor_mm = self._info_version.get("grosor_mm")
        if tipo_molde != "abierto" or largo_mm is None or grosor_mm is None:
            raise GeneracionError(
                'El proveedor "relieve" genera un bajorrelieve de espalda plana, que es un '
                "molde abierto (#55): la versión actual necesita --tipo-molde abierto, "
                "--largo-mm y --grosor-mm. Por ejemplo: "
                '"castkit version <diseño> --motivo <...> --tipo-molde abierto '
                '--largo-mm <mm> --grosor-mm <mm>" (o --tipo-molde abierto/--largo-mm/'
                f'--grosor-mm en "castkit alta"). Tipo de molde actual: "{tipo_molde}", '
                f"largo_mm={largo_mm}, grosor_mm={grosor_mm}."
            )

        ruta_imagen = insumos.imagenes[0]
        ruta_toml = ruta_imagen.parent / relieve.NOMBRE_TOML

        try:
            if not ruta_toml.is_file():
                datos_relieve = relieve.proponer_relieve(ruta_imagen, self._config)
                relieve.escribir_relieve_toml(ruta_toml, datos_relieve)
                print(
                    f'"relieve": se propusieron alturas a partir de "{ruta_imagen.name}" y se '
                    f'escribió "{ruta_toml}". Edita "altura_mm" de cada color y vuelve a '
                    "generar con --reemplazar para ajustarlas."
                )
            else:
                datos_relieve = relieve.leer_relieve_toml(ruta_toml)

            resultado = relieve.generar_malla_relieve(
                ruta_imagen,
                datos_relieve,
                largo_mm=float(largo_mm),
                grosor_mm=float(grosor_mm),
                destino_stl=destino_stl,
            )
        except relieve.RelieveError as exc:
            raise GeneracionError(str(exc)) from exc

        colores_sin_construir = resultado["parametros_registro"].get("colores_sin_construir") or []
        if colores_sin_construir:
            descripcion = ", ".join(
                f"{c['hex']} (altura_mm={c['altura_mm']})" for c in colores_sin_construir
            )
            print(
                f'Aviso: el proveedor "relieve" no construyó estos colores de "{ruta_toml}" '
                f"(sus manchas quedaron por debajo de area_min_mm2, o se perdieron al contraer "
                f"escalon_mm): {descripcion}. Revisa esos valores en relieve.toml si esperabas "
                "verlos en el relieve."
            )

        self.parametros_registro = {
            **resultado["parametros_registro"],
            "colores_max": self._config.colores_max,
            "relieve_toml_sha256": sha256_archivo(ruta_toml),
        }


class ProveedorProfundidad:
    """Proveedor local: media figura por profundidad para molde abierto (#56).

    Usa la primera imagen del brief y un archivo ``profundidad.toml`` junto a
    ella (lo escribe con los valores de ``[profundidad]`` si no existe; ver
    :mod:`castkit.profundidad`). Es, por construcción, un molde abierto (#55):
    la versión actual del diseño necesita ``tipo_molde == "abierto"`` con
    ``largo_mm`` y ``grosor_mm``. :func:`generar_malla` llama a
    :meth:`usar_version` con esos datos antes de invocar :meth:`generar`.

    ``estimar_profundidad`` se puede inyectar (para los tests, sin PyTorch);
    por defecto es :func:`castkit.profundidad.estimar_profundidad`, que
    importa ``torch``/``transformers`` de forma perezosa y lanza
    :class:`castkit.profundidad.ExtraNoInstaladoError` si el extra opcional
    ``profundidad`` no está instalado.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        estimar_profundidad: Callable[[Path, str, str], Any] | None = None,
    ) -> None:
        cfg = config or {}
        self._modelo = str(cfg.get("modelo", profundidad.MODELO_PERMITIDO))
        self._revision = str(cfg.get("revision", ""))
        try:
            profundidad._validar_modelo(self._modelo)
        except profundidad.ProfundidadError as exc:
            raise GeneracionError(str(exc)) from exc

        self._config_alturas: dict[str, Any] = {
            clave: (float(cfg[clave]) if clave in cfg else None)
            for clave in profundidad.CLAVES_PROFUNDIDAD
        }
        faltantes = [clave for clave, valor in self._config_alturas.items() if valor is None]
        if faltantes:
            raise GeneracionError(
                f'A la configuración del proveedor "profundidad" le faltan estas claves: '
                f"{', '.join(faltantes)}."
            )

        self._estimar_profundidad = estimar_profundidad or profundidad.estimar_profundidad
        self._info_version: dict[str, Any] = {}
        self.parametros_registro: dict[str, Any] = {}

    def usar_version(self, info_version: dict[str, Any]) -> None:
        """Recibe los datos de :func:`castkit.versiones.datos_version` de la versión actual."""
        self._info_version = dict(info_version)

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        if not insumos.imagenes:
            raise GeneracionError(
                'El proveedor "profundidad" requiere al menos una imagen (.png, .jpg, .webp) en '
                "la carpeta brief/ (la vista frontal de la figura)."
            )

        tipo_molde = self._info_version.get("tipo_molde", "completo")
        largo_mm = self._info_version.get("largo_mm")
        grosor_mm = self._info_version.get("grosor_mm")
        if tipo_molde != "abierto" or largo_mm is None or grosor_mm is None:
            raise GeneracionError(
                'El proveedor "profundidad" construye una media figura de espalda plana, que es '
                "un molde abierto (#55): la versión actual necesita --tipo-molde abierto, "
                "--largo-mm y --grosor-mm. Por ejemplo: "
                '"castkit version <diseño> --motivo <...> --tipo-molde abierto '
                '--largo-mm <mm> --grosor-mm <mm>" (o --tipo-molde abierto/--largo-mm/'
                f'--grosor-mm en "castkit alta"). Tipo de molde actual: "{tipo_molde}", '
                f"largo_mm={largo_mm}, grosor_mm={grosor_mm}."
            )

        ruta_imagen = insumos.imagenes[0]
        ruta_toml = ruta_imagen.parent / profundidad.NOMBRE_TOML

        try:
            if not ruta_toml.is_file():
                profundidad.escribir_profundidad_toml(ruta_toml, self._config_alturas)
                print(
                    f'"profundidad": se escribió "{ruta_toml}" con los valores de [profundidad] '
                    f"de config/fabricacion.toml. Edítalo y vuelve a generar con --reemplazar "
                    "para ajustarlo."
                )
                datos_alturas = self._config_alturas
            else:
                datos_alturas = profundidad.leer_profundidad_toml(ruta_toml)

            inicio = time.monotonic()
            mapa = self._estimar_profundidad(ruta_imagen, self._modelo, self._revision)
            segundos_calculo = time.monotonic() - inicio

            resultado = profundidad.generar_malla_profundidad(
                ruta_imagen,
                mapa,
                datos_alturas,
                largo_mm=float(largo_mm),
                grosor_mm=float(grosor_mm),
                destino_stl=destino_stl,
            )
        except profundidad.ProfundidadError as exc:
            raise GeneracionError(str(exc)) from exc

        self.parametros_registro = {
            **resultado["parametros_registro"],
            "modelo": self._modelo,
            "revision": self._revision,
            "imagen_sha256": sha256_archivo(ruta_imagen),
            "profundidad_toml_sha256": sha256_archivo(ruta_toml),
            "segundos_calculo": round(segundos_calculo, 3),
        }


def _partir_comando(comando: str, posix: bool = os.name != "nt") -> list[str]:
    """Separa `comando` en argumentos respetando las rutas de Windows.

    En modo POSIX, `shlex` trata `\\` como escape y rompe rutas como
    `C:\\Users\\...`; en Windows se usa el modo no POSIX y se quitan las
    comillas que envuelven cada argumento.
    """
    if posix:
        return shlex.split(comando)
    partes = shlex.split(comando, posix=False)
    return [
        parte[1:-1] if len(parte) >= 2 and parte[0] == parte[-1] and parte[0] in "\"'" else parte
        for parte in partes
    ]


class ProveedorComando:
    """Proveedor enchufable que ejecuta un comando o script externo configurable.

    Pasa al subproceso:
    - Variable `CASTKIT_SALIDA_STL` (y último argumento posicional): ruta donde
      debe escribir el archivo `.stl`.
    - Variable `CASTKIT_INSUMOS`: rutas de imágenes y documentos separadas por
      `:` (o `;` en Windows).
    - Variable `CASTKIT_PROMPT`: texto de contexto combinado del brief.

    Si `comando` está vacío pero el brief incluye imágenes, delega en el
    reconstructor volumétrico local (:class:`ProveedorImagen3D`).
    """

    def __init__(self, comando: str) -> None:
        self.comando = comando.strip()

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        if not self.comando:
            if insumos.imagenes:
                ProveedorImagen3D().generar(insumos, destino_stl)
                return
            raise GeneracionError(
                'El proveedor "comando" no tiene ningún comando configurado en la sección '
                "[generacion] de config/fabricacion.toml."
            )

        partes = _partir_comando(self.comando)
        rutas_insumos = [str(ruta) for ruta in insumos.rutas_para_proveedor()]
        entorno = os.environ.copy()
        entorno["CASTKIT_SALIDA_STL"] = str(destino_stl)
        entorno["CASTKIT_INSUMOS"] = os.pathsep.join(rutas_insumos)
        entorno["CASTKIT_PROMPT"] = insumos.prompt

        try:
            resultado = subprocess.run(
                [*partes, str(destino_stl)],
                env=entorno,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except Exception as exc:
            raise GeneracionError(
                f'No se pudo ejecutar el proveedor "comando" ({self.comando}): {exc}'
            ) from exc

        if resultado.returncode != 0:
            detalle = (resultado.stderr or resultado.stdout or "").strip()
            sufijo = f": {detalle}" if detalle else "."
            raise GeneracionError(
                f'El proveedor "comando" terminó con error (código {resultado.returncode}){sufijo}'
            )


_PROVEEDORES_REGISTRADOS: dict[str, ProveedorGeneracion3D] = {}


def registrar_proveedor(nombre: str, proveedor: ProveedorGeneracion3D) -> None:
    """Registra un proveedor enchufable por nombre (útil para extensiones y tests)."""
    _PROVEEDORES_REGISTRADOS[nombre] = proveedor


def desregistrar_proveedor(nombre: str) -> None:
    """Elimina un proveedor registrado en memoria, si existía."""
    _PROVEEDORES_REGISTRADOS.pop(nombre, None)


def obtener_proveedor(
    nombre: str, config_generacion: dict[str, Any] | None = None
) -> ProveedorGeneracion3D:
    """Devuelve la instancia del proveedor ``nombre``.

    Busca primero en los proveedores registrados con :func:`registrar_proveedor`
    y luego en los proveedores integrados (`"comando"`, `"imagen-3d"`, `"tripo3d"`).
    Lanza :class:`GeneracionError` si el nombre no corresponde a ningún proveedor.
    """
    if nombre in _PROVEEDORES_REGISTRADOS:
        return _PROVEEDORES_REGISTRADOS[nombre]

    if nombre == "comando":
        comando = (config_generacion or {}).get("comando", "")
        return ProveedorComando(str(comando))
    if nombre == "imagen-3d":
        return ProveedorImagen3D()
    if nombre == "relieve":
        return ProveedorRelieve(cargar_config_relieve())
    if nombre == "profundidad":
        return ProveedorProfundidad(cargar_config_profundidad())
    if nombre == "tripo3d":
        cfg = config_generacion or {}
        return ProveedorTripo3D(
            modelo=str(cfg.get("tripo_modelo", "v2.5-20250123")),
            espera_max_s=float(cfg.get("tripo_espera_max_s", 600.0)),
        )

    disponibles = sorted(
        {
            "comando",
            "imagen-3d",
            "relieve",
            "profundidad",
            "tripo3d",
            *_PROVEEDORES_REGISTRADOS.keys(),
        }
    )
    raise GeneracionError(
        f'El proveedor de generación 3D "{nombre}" no está registrado. '
        f"Proveedores disponibles: {', '.join(disponibles)}."
    )


def recolectar_insumos_brief(carpeta_brief: Path, prompt_extra: str | None = None) -> InsumosBrief:
    """Inspecciona ``carpeta_brief`` y clasifica los archivos soportados."""
    carpeta_brief = Path(carpeta_brief)
    insumos = InsumosBrief()

    textos_encontrados: list[str] = []

    if carpeta_brief.is_dir():
        for ruta in sorted(carpeta_brief.iterdir(), key=lambda p: p.name.lower()):
            if not ruta.is_file() or ruta.name.startswith("."):
                continue
            ext = ruta.suffix.lower()
            if ext in EXT_IMAGENES and ruta.stat().st_size > 0:
                insumos.imagenes.append(ruta)
            elif ext in EXT_DOCUMENTOS and ruta.stat().st_size > 0:
                insumos.documentos.append(ruta)
            elif ext in EXT_TEXTOS and ruta.stat().st_size > 0:
                insumos.archivos_texto.append(ruta)
                contenido = ruta.read_text(encoding="utf-8", errors="replace").strip()
                if contenido:
                    textos_encontrados.append(contenido)
            elif ext in EXT_MALLAS:
                insumos.mallas_stl.append(ruta)

    if prompt_extra and prompt_extra.strip():
        textos_encontrados.append(prompt_extra.strip())

    insumos.prompt = "\n\n".join(textos_encontrados)
    return insumos


def _copiar_y_validar_stl_atomico(origen_stl: Path, destino_stl: Path) -> dict[str, Any]:
    """Valida ``origen_stl`` como STL legible y lo copia de forma atómica a ``destino_stl``."""
    try:
        info = validar_malla(origen_stl)
    except IngestaError as exc:
        raise GeneracionError(str(exc)) from exc

    huella = sha256_archivo(origen_stl)

    descriptor, tmp_str = tempfile.mkstemp(
        prefix=f".{destino_stl.name}.tmp-", dir=destino_stl.parent, suffix=".stl"
    )
    os.close(descriptor)
    tmp_path = Path(tmp_str)
    try:
        shutil.copyfile(origen_stl, tmp_path)
        os.replace(tmp_path, destino_stl)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return {
        "sha256": huella,
        "caras": info["caras"],
        "vertices": info["vertices"],
    }


def generar_malla(
    carpeta: Path,
    *,
    proveedor: str | None = None,
    prompt: str | None = None,
    reemplazar: bool = False,
) -> dict[str, Any]:
    """Obtiene o genera la malla en bruto de la versión actual desde el brief.

    1. Comprueba si la versión actual ya tiene `input/<diseño>_v<actual>_bruto.stl`
       y, si `reemplazar` es `False`, lanza :class:`GeneracionError` antes de
       ejecutar ningún proveedor ni tocar el disco.
    2. Recolecta los insumos de `<carpeta>/brief/` más `prompt` (si se indicó).
    3. Si hay un `.stl` en `brief/` y no se forzó `proveedor`, valida ese STL y
       lo guarda directamente en `input/`.
    4. En caso contrario, ejecuta el proveedor enchufable: si se pasó
       `proveedor` explícito se usa ese; si no, el guardado en la versión
       actual (lo dejó `castkit alta` o `castkit version`, ticket #65); si
       tampoco hay uno guardado, el de `config/fabricacion.toml`. Valida el
       STL generado, lo guarda en `input/` y registra los parámetros en la
       etapa `"generacion"`.
    """
    carpeta = Path(carpeta)
    datos = cargar_diseno(carpeta)
    nombre_diseno = datos["nombre"]
    version_actual = datos["version_actual"]

    nombre_destino = nombre_archivo(nombre_diseno, version_actual, "bruto", "stl")
    destino = carpeta / "input" / nombre_destino

    habia_malla_previa = destino.exists()
    if habia_malla_previa and not reemplazar:
        raise GeneracionError(
            f"La versión v{version_actual} de «{nombre_diseno}» ya tiene una malla en "
            f'"{destino}"; usa --reemplazar para sustituirla.'
        )

    config_gen = cargar_config_generacion()
    insumos = recolectar_insumos_brief(carpeta / "brief", prompt_extra=prompt)

    registro_proveedor: dict[str, Any] = {}

    # Vía directa si el brief ya incluye un STL opcional y no se forzó proveedor.
    if insumos.mallas_stl and proveedor is None:
        stl_brief = insumos.mallas_stl[0]
        metricas = _copiar_y_validar_stl_atomico(stl_brief, destino)
        nombre_proveedor = "stl-directo"
        archivos_usados = [stl_brief.name]
    else:
        info_version = datos_version(datos)
        # Si no se pasó --proveedor explícito, se usa el proveedor guardado en
        # la versión actual (lo dejó `castkit alta` o `castkit version`); solo
        # si tampoco hay uno guardado (diseños antiguos, o que no lo guardaron)
        # se cae al de `config/fabricacion.toml`, igual que antes del #65.
        if proveedor is not None:
            nombre_proveedor = proveedor
        elif info_version.get("proveedor"):
            nombre_proveedor = str(info_version["proveedor"])
        else:
            nombre_proveedor = str(config_gen["proveedor"])
        instancia_proveedor = obtener_proveedor(nombre_proveedor, config_gen)

        metodo_usar_version = getattr(instancia_proveedor, "usar_version", None)
        if callable(metodo_usar_version):
            metodo_usar_version(info_version)

        if not insumos.tiene_insumos_para_proveedor():
            formatos_txt = ", ".join(FORMATOS_SOPORTADOS)
            raise GeneracionError(
                f'La carpeta "brief/" de «{nombre_diseno}» no contiene insumos utilizables '
                f"para generar la malla 3D (formatos aceptados: {formatos_txt}, o bien "
                "pasa --prompt con la descripción)."
            )

        descriptor, tmp_gen_str = tempfile.mkstemp(
            prefix=f".{nombre_destino}.gen-", dir=destino.parent, suffix=".stl"
        )
        os.close(descriptor)
        tmp_gen_path = Path(tmp_gen_str)
        tmp_gen_path.unlink(missing_ok=True)
        try:
            instancia_proveedor.generar(insumos, tmp_gen_path)
            if not tmp_gen_path.is_file():
                raise GeneracionError(
                    f'El proveedor "{nombre_proveedor}" no generó ningún archivo STL de salida.'
                )
            metricas = _copiar_y_validar_stl_atomico(tmp_gen_path, destino)
        finally:
            tmp_gen_path.unlink(missing_ok=True)

        archivos_usados = insumos.nombres_archivos_usados()
        registro_proveedor = getattr(instancia_proveedor, "parametros_registro", None) or {}

    parametros: dict[str, Any] = {
        "proveedor": nombre_proveedor,
        "archivos_brief": archivos_usados,
        "prompt": insumos.prompt,
        "sha256": metricas["sha256"],
        "caras": metricas["caras"],
        "vertices": metricas["vertices"],
        **registro_proveedor,
    }
    try:
        registrar_parametros(carpeta, "generacion", parametros)
    except Exception:
        if not habia_malla_previa:
            destino.unlink(missing_ok=True)
        raise

    return {
        "destino": destino,
        "proveedor": nombre_proveedor,
        "archivos_brief": archivos_usados,
        "caras": metricas["caras"],
        "vertices": metricas["vertices"],
        "sha256": metricas["sha256"],
    }
