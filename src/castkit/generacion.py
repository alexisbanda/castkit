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

import base64
import json
import mimetypes
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import trimesh

from castkit.config import cargar_config_generacion
from castkit.ingesta import IngestaError, sha256_archivo, validar_malla
from castkit.versiones import (
    VersionError,
    cargar_diseno,
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
    """Descarga un modelo 3D (`.glb`, `.obj`, `.stl`) desde ``url_modelo`` y lo exporta como STL."""
    sufijo = Path(url_modelo.split("?")[0]).suffix.lower() or ".glb"
    descriptor, tmp_str = tempfile.mkstemp(prefix=".modelo-remoto-", suffix=sufijo)
    os.close(descriptor)
    tmp_path = Path(tmp_str)
    try:
        req = urllib.request.Request(url_modelo, headers={"User-Agent": "castkit/0.1"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            tmp_path.write_bytes(resp.read())
        escena_o_malla = trimesh.load(tmp_path, force="mesh")
        escena_o_malla.export(destino_stl, file_type="stl")
    except Exception as exc:
        raise GeneracionError(
            f"No se pudo descargar o convertir a STL el modelo generado ({url_modelo}): {exc}"
        ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)


class ProveedorFal:
    """Proveedor enchufable para Fal.ai (p. ej. `fal-ai/trellis` o `fal-ai/hunyuan3d/v2/mini`).

    Lee la clave de la variable de entorno ``FAL_KEY``.
    """

    def __init__(self, endpoint: str = "fal-ai/trellis") -> None:
        self.endpoint = endpoint.strip() or "fal-ai/trellis"

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        api_key = os.environ.get("FAL_KEY", "").strip()
        if not api_key:
            raise GeneracionError(
                'El proveedor "fal" requiere la variable de entorno FAL_KEY '
                '(por ejemplo: export FAL_KEY="tu-api-key").'
            )
        if not insumos.imagenes:
            raise GeneracionError(
                'El proveedor "fal" requiere al menos una imagen (.png, .jpg, .webp) en brief/.'
            )

        img = insumos.imagenes[0]
        mime = mimetypes.guess_type(img.name)[0] or "image/png"
        b64 = base64.b64encode(img.read_bytes()).decode("ascii")
        payload: dict[str, Any] = {"image_url": f"data:{mime};base64,{b64}"}
        if insumos.prompt:
            payload["prompt"] = insumos.prompt

        url = f"https://fal.run/{self.endpoint}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Key {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                datos = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            cuerpo = exc.read().decode("utf-8", errors="replace")
            raise GeneracionError(f'Error HTTP {exc.code} del proveedor "fal": {cuerpo}') from exc
        except Exception as exc:
            raise GeneracionError(f'Error al conectar con el proveedor "fal": {exc}') from exc

        url_modelo = (
            (datos.get("model_mesh") or {}).get("url")
            or (datos.get("mesh") or {}).get("url")
            or datos.get("model_url")
        )
        if not url_modelo:
            raise GeneracionError(
                f'La respuesta de "fal" no incluyó ninguna URL de malla 3D: {datos}'
            )

        _descargar_y_convertir_a_stl(str(url_modelo), destino_stl)


class ProveedorTripo3D:
    """Proveedor enchufable para Tripo3D API v2 (soporta Imagen → 3D y Texto → 3D).

    Lee la clave de la variable de entorno ``TRIPO_API_KEY``.
    """

    def generar(self, insumos: InsumosBrief, destino_stl: Path) -> None:
        api_key = os.environ.get("TRIPO_API_KEY", "").strip()
        if not api_key:
            raise GeneracionError(
                'El proveedor "tripo3d" requiere la variable de entorno TRIPO_API_KEY '
                '(por ejemplo: export TRIPO_API_KEY="tsk_...").'
            )

        headers_json = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

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

            req_up = urllib.request.Request(
                "https://api.tripo3d.ai/v2/openapi/upload",
                data=cuerpo_multipart,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req_up, timeout=120) as resp:
                    up_data = json.loads(resp.read().decode("utf-8"))
                image_token = up_data["data"]["image_token"]
            except Exception as exc:
                raise GeneracionError(f'No se pudo subir la imagen a "tripo3d": {exc}') from exc

            ext_img = img.suffix.lstrip(".").lower() or "png"
            task_payload: dict[str, Any] = {
                "type": "image_to_model",
                "file": {"type": ext_img, "file_token": image_token},
            }
        else:
            task_payload = {
                "type": "text_to_model",
                "prompt": insumos.prompt,
            }

        req_task = urllib.request.Request(
            "https://api.tripo3d.ai/v2/openapi/task",
            data=json.dumps(task_payload).encode("utf-8"),
            headers=headers_json,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req_task, timeout=60) as resp:
                task_data = json.loads(resp.read().decode("utf-8"))
            task_id = task_data["data"]["task_id"]
        except Exception as exc:
            raise GeneracionError(f'No se pudo crear la tarea en "tripo3d": {exc}') from exc

        for _ in range(90):
            req_poll = urllib.request.Request(
                f"https://api.tripo3d.ai/v2/openapi/task/{task_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            with urllib.request.urlopen(req_poll, timeout=60) as resp:
                estado_data = json.loads(resp.read().decode("utf-8"))["data"]
            status = estado_data.get("status")
            if status == "success":
                salida = estado_data.get("output") or {}
                url_modelo = salida.get("model") or salida.get("pbr_model")
                if not url_modelo:
                    raise GeneracionError(
                        f'La tarea de "tripo3d" terminó sin URL de modelo: {estado_data}'
                    )
                _descargar_y_convertir_a_stl(str(url_modelo), destino_stl)
                return
            if status in ("failed", "cancelled", "banned", "expired"):
                raise GeneracionError(f'La tarea de "tripo3d" falló con estado "{status}".')
            time.sleep(2.0)

        raise GeneracionError('Tiempo de espera agotado esperando al proveedor "tripo3d".')


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

        partes = shlex.split(self.comando)
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
    y luego en los proveedores integrados (`"comando"`, `"imagen-3d"`, `"fal"`, `"tripo3d"`).
    Lanza :class:`GeneracionError` si el nombre no corresponde a ningún proveedor.
    """
    if nombre in _PROVEEDORES_REGISTRADOS:
        return _PROVEEDORES_REGISTRADOS[nombre]

    if nombre == "comando":
        comando = (config_generacion or {}).get("comando", "")
        return ProveedorComando(str(comando))
    if nombre == "imagen-3d":
        return ProveedorImagen3D()
    if nombre == "fal":
        return ProveedorFal()
    if nombre == "tripo3d":
        return ProveedorTripo3D()

    disponibles = sorted(
        {"comando", "imagen-3d", "fal", "tripo3d", *_PROVEEDORES_REGISTRADOS.keys()}
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
    4. En caso contrario, ejecuta el proveedor enchufable configurado, valida
       el STL generado y lo guarda en `input/`, registrando los parámetros en
       la etapa `"generacion"`.
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

    # Vía directa si el brief ya incluye un STL opcional y no se forzó proveedor.
    if insumos.mallas_stl and proveedor is None:
        stl_brief = insumos.mallas_stl[0]
        metricas = _copiar_y_validar_stl_atomico(stl_brief, destino)
        nombre_proveedor = "stl-directo"
        archivos_usados = [stl_brief.name]
    else:
        nombre_proveedor = proveedor if proveedor is not None else str(config_gen["proveedor"])
        instancia_proveedor = obtener_proveedor(nombre_proveedor, config_gen)

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

    parametros: dict[str, Any] = {
        "proveedor": nombre_proveedor,
        "archivos_brief": archivos_usados,
        "prompt": insumos.prompt,
        "sha256": metricas["sha256"],
        "caras": metricas["caras"],
        "vertices": metricas["vertices"],
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
