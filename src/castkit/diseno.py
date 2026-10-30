"""Alta de diseños: validación de datos y creación de la carpeta estándar.

Un diseño vive en una carpeta con su nombre normalizado, que contiene una copia
del brief recibido, las carpetas de trabajo (``input/``, ``work/``, ``output/``)
y los metadatos de la primera versión (``diseno.json`` y ``CHANGELOG.md``). El
formato de ``diseno.json`` lo reutilizará el alta de nuevas versiones (ticket #4),
que añadirá entradas a ``versiones`` con los parámetros usados en cada una.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

MATERIALES = ("porcelana-fria", "silicona", "cera")
LICENCIAS = ("propio", "comercial")
TIPOS_MOLDE = ("abierto", "completo")

_PATRON_NOMBRE_VALIDO = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class AltaError(Exception):
    """Se lanza cuando el alta de un diseño no puede completarse.

    El mensaje siempre está en español y explica, cuando aplica, qué opción
    de la línea de comandos falló y qué valores se aceptan.
    """


def normalizar_nombre(texto: str) -> str:
    """Convierte ``texto`` en un nombre de diseño válido (minúsculas y guiones).

    Pasa a minúsculas, quita tildes y diacríticos (incluida la ``ñ``), convierte
    espacios y guiones bajos en guiones, elimina cualquier carácter que no sea
    ``[a-z0-9-]``, colapsa guiones repetidos y quita los guiones al inicio o
    final. Por ejemplo, «Guagua de Pan» se convierte en ``guagua-de-pan`` y
    «Niño Dios» en ``nino-dios``.
    """
    texto = texto.strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r"[\s_]+", "-", texto)
    texto = re.sub(r"[^a-z0-9-]", "", texto)
    texto = re.sub(r"-+", "-", texto)
    return texto.strip("-")


def validar_nombre(nombre: str) -> None:
    """Valida que ``nombre`` use solo minúsculas, números y guiones.

    Lanza :class:`AltaError` si no cumple ``^[a-z0-9]+(-[a-z0-9]+)*$``. El
    mensaje propone el nombre normalizado cuando la propuesta no queda vacía.
    """
    if _PATRON_NOMBRE_VALIDO.match(nombre):
        return

    propuesta = normalizar_nombre(nombre)
    base = (
        f'El nombre "{nombre}" no es válido: usa solo minúsculas, números y guiones, '
        "sin tildes ni espacios."
    )
    if propuesta:
        raise AltaError(f"{base} Propuesta: {propuesta}")
    raise AltaError(base)


def _documentos_windows() -> Path | None:
    """Pide a Windows la carpeta de Documentos vía ``SHGetKnownFolderPath``.

    Usa el *Known Folder* ``FOLDERID_Documents``
    (``{FDD39AD0-238F-46AF-ADB4-6C85480369C7}``), que respeta OneDrive y el
    idioma del sistema (a diferencia de asumir ``%USERPROFILE%\\Documents``
    a ciegas). Devuelve ``None`` ante cualquier fallo, para que quien llama
    use una carpeta de reserva. Vive en su propia función para poder
    sustituirla en los tests (``monkeypatch``), ya que esta API de Windows no
    se puede ejercitar en otras plataformas; el ``import ctypes`` es local
    para no intentar cargarlo fuera de Windows.
    """
    import ctypes

    try:

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        guid = _GUID()
        if (
            ctypes.windll.ole32.CLSIDFromString(  # type: ignore[attr-defined]
                "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}", ctypes.byref(guid)
            )
            != 0
        ):
            return None

        buf = ctypes.c_wchar_p()
        resultado = ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
            ctypes.byref(guid), 0, None, ctypes.byref(buf)
        )
        if resultado != 0 or not buf.value:
            return None
        ruta = Path(buf.value)
        ctypes.windll.ole32.CoTaskMemFree(buf)  # type: ignore[attr-defined]
        return ruta
    except Exception:
        return None


def _documentos_linux() -> Path | None:
    """Pregunta a ``xdg-user-dir DOCUMENTS`` por la carpeta de Documentos.

    Devuelve ``None`` si ``xdg-user-dir`` no está instalado, falla, tarda
    demasiado, o devuelve una ruta vacía o igual a ``$HOME`` (señal de que no
    hay una carpeta de Documentos distinta configurada). Vive en su propia
    función para poder sustituirla en los tests.
    """
    ejecutable = shutil.which("xdg-user-dir")
    if not ejecutable:
        return None
    try:
        resultado = subprocess.run(
            [ejecutable, "DOCUMENTS"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    salida = resultado.stdout.strip()
    if not salida:
        return None
    ruta = Path(salida)
    if ruta.resolve() == Path.home().resolve():
        return None
    return ruta


def carpeta_documentos(plataforma: str | None = None) -> Path:
    """Devuelve la carpeta de Documentos del usuario actual.

    Depende del sistema operativo (ver ``sys.platform``, o el valor forzado
    en ``plataforma``, pensado para los tests):

    - Windows (``win32``): la carpeta conocida de Documentos
      (``SHGetKnownFolderPath(FOLDERID_Documents)``, ver
      :func:`_documentos_windows`), que respeta OneDrive y el idioma; si
      falla, ``%USERPROFILE%\\Documents`` (o ``~/Documents`` si tampoco hay
      ``USERPROFILE``).
    - macOS (``darwin``): ``~/Documents``.
    - Linux (y el resto de *nix): la que indique ``xdg-user-dir DOCUMENTS``
      (ver :func:`_documentos_linux`) si está disponible y es distinta de
      ``$HOME``; si no, ``~/Documents``.
    """
    plataforma = plataforma if plataforma is not None else sys.platform
    if plataforma == "win32":
        ruta = _documentos_windows()
        if ruta is not None:
            return ruta
        base = os.environ.get("USERPROFILE")
        if base:
            return Path(base) / "Documents"
        return Path.home() / "Documents"
    if plataforma == "darwin":
        return Path.home() / "Documents"
    ruta = _documentos_linux()
    if ruta is not None:
        return ruta
    return Path.home() / "Documents"


def carpeta_disenos() -> Path:
    """Devuelve la carpeta donde se crean los diseños, creándola si hace falta.

    Usa la variable de entorno ``CASTKIT_DISENOS`` si está definida (debe
    apuntar a una carpeta existente, como hasta ahora); si no, usa
    ``<Documentos>/castkit`` (ver :func:`carpeta_documentos`), creándola junto
    con sus carpetas padre la primera vez que haga falta, para que el
    operador no tenga que crear ni configurar nada (#83).
    """
    valor = os.environ.get("CASTKIT_DISENOS")
    if not valor:
        carpeta = carpeta_documentos() / "castkit"
        carpeta.mkdir(parents=True, exist_ok=True)
        return carpeta

    ruta = Path(valor)
    if not ruta.is_dir():
        raise AltaError(
            f'La variable de entorno CASTKIT_DISENOS apunta a "{valor}", '
            "que no existe o no es una carpeta."
        )
    return ruta


def origen_carpeta_disenos() -> str:
    """Indica de dónde viene :func:`carpeta_disenos`: ``"variable"`` si la fija
    ``CASTKIT_DISENOS``, o ``"defecto"`` si es ``<Documentos>/castkit``.
    """
    return "variable" if os.environ.get("CASTKIT_DISENOS") else "defecto"


@dataclass
class DatosAlta:
    """Datos obligatorios para dar de alta un diseño.

    La validación ocurre al construir la instancia, antes de tocar el disco:
    si algún dato no es válido, se lanza :class:`AltaError` con un mensaje en
    español que nombra la opción de la línea de comandos correspondiente y,
    cuando aplica, los valores aceptados.
    """

    nombre: str
    brief: Path
    material: str
    alto_mm: float | None
    cavidades: int
    cantidad: int
    fecha: date
    licencia: str
    origen: str
    tipo_molde: str = "completo"
    largo_mm: float | None = None
    grosor_mm: float | None = None
    proveedor: str | None = None
    modo_matriz: str | None = None

    def __post_init__(self) -> None:
        validar_nombre(self.nombre)

        if self.material not in MATERIALES:
            raise AltaError(
                f"--material debe ser uno de los siguientes: {', '.join(MATERIALES)}; "
                f'recibido "{self.material}".'
            )

        if self.licencia not in LICENCIAS:
            raise AltaError(
                f"--licencia debe ser una de las siguientes: {', '.join(LICENCIAS)}; "
                f'recibido "{self.licencia}".'
            )

        if self.tipo_molde not in TIPOS_MOLDE:
            raise AltaError(
                f"--tipo-molde debe ser uno de los siguientes: {', '.join(TIPOS_MOLDE)}; "
                f'recibido "{self.tipo_molde}".'
            )

        if self.tipo_molde == "completo":
            if self.largo_mm is not None or self.grosor_mm is not None:
                raise AltaError(
                    "--largo-mm y --grosor-mm son medidas de molde abierto; en molde completo "
                    "solo se usa --alto-mm."
                )
            if isinstance(self.alto_mm, bool) or not isinstance(self.alto_mm, (int, float)):
                raise AltaError(
                    f'--alto-mm debe ser un número mayor que 0; recibido "{self.alto_mm}".'
                )
            if not math.isfinite(self.alto_mm) or self.alto_mm <= 0:
                raise AltaError(
                    f'--alto-mm debe ser un número mayor que 0; recibido "{self.alto_mm}".'
                )
        else:  # abierto
            if self.alto_mm is not None:
                raise AltaError(
                    "--alto-mm es la medida de molde completo; en molde abierto se usan "
                    "--largo-mm (obligatorio, la medida mayor en planta) y --grosor-mm "
                    "(opcional, lo que sobresale de la placa)."
                )
            if isinstance(self.largo_mm, bool) or not isinstance(self.largo_mm, (int, float)):
                raise AltaError(
                    f'--largo-mm debe ser un número mayor que 0; recibido "{self.largo_mm}".'
                )
            if not math.isfinite(self.largo_mm) or self.largo_mm <= 0:
                raise AltaError(
                    f'--largo-mm debe ser un número mayor que 0; recibido "{self.largo_mm}".'
                )
            if self.grosor_mm is not None:
                if isinstance(self.grosor_mm, bool) or not isinstance(self.grosor_mm, (int, float)):
                    raise AltaError(
                        f'--grosor-mm debe ser un número mayor que 0; recibido "{self.grosor_mm}".'
                    )
                if not math.isfinite(self.grosor_mm) or self.grosor_mm <= 0:
                    raise AltaError(
                        f'--grosor-mm debe ser un número mayor que 0; recibido "{self.grosor_mm}".'
                    )

        if isinstance(self.cavidades, bool) or not isinstance(self.cavidades, int):
            raise AltaError(
                f'--cavidades debe ser un entero mayor o igual a 1; recibido "{self.cavidades}".'
            )
        if self.cavidades < 1:
            raise AltaError(
                f'--cavidades debe ser un entero mayor o igual a 1; recibido "{self.cavidades}".'
            )

        if isinstance(self.cantidad, bool) or not isinstance(self.cantidad, int):
            raise AltaError(
                f'--cantidad debe ser un entero mayor o igual a 1; recibido "{self.cantidad}".'
            )
        if self.cantidad < 1:
            raise AltaError(
                f'--cantidad debe ser un entero mayor o igual a 1; recibido "{self.cantidad}".'
            )

        if not isinstance(self.fecha, date):
            raise AltaError(
                f'--fecha debe ser una fecha AAAA-MM-DD válida; recibido "{self.fecha}".'
            )

        brief_path = Path(self.brief)
        if not brief_path.is_dir():
            raise AltaError(
                f'--brief debe ser una carpeta existente; "{self.brief}" '
                "no existe o no es una carpeta."
            )
        if not any(brief_path.iterdir()):
            raise AltaError(
                f'--brief "{self.brief}" está vacía: debe contener al menos un archivo.'
            )

        if not self.origen or not self.origen.strip():
            raise AltaError(
                "--origen no puede estar vacío: describe de dónde viene el modelo base."
            )


def crear_diseno(datos: DatosAlta, destino: Path | None = None) -> Path:
    """Crea la carpeta del diseño a partir de ``datos`` dentro de ``destino``.

    Si ``destino`` es ``None``, usa :func:`carpeta_disenos`. Si el diseño ya
    existe en ``destino``, lanza :class:`AltaError` sin modificar nada. La
    carpeta se construye primero en una carpeta temporal hermana y, al
    terminar, se renombra al nombre final; si algo falla a mitad, la carpeta
    temporal se borra y la excepción original se vuelve a lanzar.
    """
    if destino is None:
        destino = carpeta_disenos()
    else:
        destino = Path(destino)
        if not destino.is_dir():
            raise AltaError(f'La carpeta destino "{destino}" no existe.')

    destino_final = destino / datos.nombre
    if destino_final.exists():
        raise AltaError(
            f'El diseño "{datos.nombre}" ya existe en {destino}; no se ha modificado nada.'
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix=f".{datos.nombre}.tmp-", dir=destino))
    try:
        shutil.copytree(datos.brief, tmp_dir / "brief")
        (tmp_dir / "input").mkdir()
        (tmp_dir / "work").mkdir()
        (tmp_dir / "output").mkdir()

        ahora = datetime.now(UTC).isoformat()
        contenido_json = {
            "nombre": datos.nombre,
            "version_actual": 1,
            "material": datos.material,
            "medidas": {
                "alto_mm": datos.alto_mm,
                "largo_mm": datos.largo_mm,
                "grosor_mm": datos.grosor_mm,
            },
            "cavidades": datos.cavidades,
            "cantidad": datos.cantidad,
            "fecha_entrega": datos.fecha.isoformat(),
            "modelo_base": {"licencia": datos.licencia, "origen": datos.origen},
            "creado": ahora,
            "versiones": {
                "v1": {
                    "creada": ahora,
                    "tipo_molde": datos.tipo_molde,
                    "alto_mm": datos.alto_mm,
                    "largo_mm": datos.largo_mm,
                    "grosor_mm": datos.grosor_mm,
                    "cavidades": datos.cavidades,
                    "proveedor": datos.proveedor,
                    "modo_matriz": datos.modo_matriz,
                    "parametros": {},
                }
            },
        }
        (tmp_dir / "diseno.json").write_text(
            json.dumps(contenido_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        hoy = date.today().isoformat()
        changelog = f"# Historial de {datos.nombre}\n\n## v1 — {hoy}\n\n- Creación del diseño.\n"
        (tmp_dir / "CHANGELOG.md").write_text(changelog, encoding="utf-8")

        tmp_dir.rename(destino_final)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return destino_final
