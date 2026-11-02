# Instala castkit en Windows sin necesitar Git ni una cuenta de GitHub (#80).
# Tambien instala Blender si falta (#85).
# Uso normal (PowerShell 5.1, la que trae un Windows limpio):
#   irm https://github.com/alexisbanda/castkit/releases/latest/download/install.ps1 | iex
# Para saltar la instalacion de Blender:
#   & ([scriptblock]::Create((irm https://github.com/alexisbanda/castkit/releases/latest/download/install.ps1))) -SinBlender
# Para pruebas, -Rueda acepta una ruta local o una URL a una rueda (.whl) de castkit, y
# -Sha256BlenderDePrueba sustituye la huella sha256 esperada del instalador de Blender.
param([string]$Rueda, [switch]$SinBlender, [string]$Sha256BlenderDePrueba)

$ErrorActionPreference = 'Continue'

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    # Si este .NET no conoce Tls12 por nombre, se sigue con el valor por defecto.
}

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # No es grave si no se puede cambiar (por ejemplo, con la salida redirigida a un archivo).
}

function Tr([string]$Texto) {
    $Texto = $Texto.Replace('{a}', [string][char]0x00E1)
    $Texto = $Texto.Replace('{e}', [string][char]0x00E9)
    $Texto = $Texto.Replace('{i}', [string][char]0x00ED)
    $Texto = $Texto.Replace('{o}', [string][char]0x00F3)
    $Texto = $Texto.Replace('{u}', [string][char]0x00FA)
    $Texto = $Texto.Replace('{n}', [string][char]0x00F1)
    $Texto = $Texto.Replace('{A}', [string][char]0x00C1)
    $Texto = $Texto.Replace('{E}', [string][char]0x00C9)
    $Texto = $Texto.Replace('{I}', [string][char]0x00CD)
    $Texto = $Texto.Replace('{O}', [string][char]0x00D3)
    $Texto = $Texto.Replace('{U}', [string][char]0x00DA)
    $Texto = $Texto.Replace('{N}', [string][char]0x00D1)
    return $Texto
}

function Fail {
    param([string]$Mensaje)
    Write-Host ''
    Write-Host $Mensaje -ForegroundColor Red
    if ($PSCommandPath) {
        exit 1
    } else {
        throw (Tr 'La instalaci{o}n de castkit no termin{o}.')
    }
}

if (-not $SinBlender) {
    Write-Host (Tr 'Si falta Blender en la versi{o}n que usa castkit, este instalador tambi{e}n lo descargar{a} (unos 365 MB) y lo instalar{a}. Windows va a pedir permiso de administrador en una ventana aparte: qu{e}date pendiente de esa ventana y acepta para que la instalaci{o}n siga.') -ForegroundColor Yellow
}

# 1. uv: lo instala si falta, y deja su carpeta en el PATH de esta sesion (sin pedir otra ventana).
$UvBinDir = Join-Path $HOME '.local\bin'

function Buscar-Uv {
    $comando = Get-Command uv -ErrorAction SilentlyContinue
    if ($comando) { return $comando.Source }
    $candidato = Join-Path $UvBinDir 'uv.exe'
    if (Test-Path $candidato) { return $candidato }
    return $null
}

$UvExe = Buscar-Uv
if (-not $UvExe) {
    Write-Host (Tr 'uv no est{a} instalado. Instal{a}ndolo...')
    powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    if ($LASTEXITCODE -ne 0) {
        Fail (Tr 'No se pudo instalar uv. Revisa la conexi{o}n a internet y vuelve a pegar la l{i}nea.')
    }
    $env:Path = "$UvBinDir;$env:Path"
    $UvExe = Buscar-Uv
    if (-not $UvExe) {
        Fail (Tr 'uv se instal{o} pero no se encuentra en esta ventana. Vuelve a pegar la l{i}nea de instalaci{o}n.')
    }
} else {
    $env:Path = "$UvBinDir;$env:Path"
}

# 2. La rueda (.whl) de castkit a instalar: la indicada en -Rueda, o la de la ultima release.
function Convertir-A-Uri([string]$RutaOUrl) {
    if ($RutaOUrl -match '^[a-zA-Z][a-zA-Z0-9+.-]*://') {
        return $RutaOUrl
    }
    $resuelta = Resolve-Path -LiteralPath $RutaOUrl -ErrorAction SilentlyContinue
    if (-not $resuelta) {
        Fail (Tr "No se encontr{o} el archivo de la rueda indicado: $RutaOUrl")
    }
    return ([System.Uri]$resuelta.Path).AbsoluteUri
}

function Obtener-Url-Ultima-Rueda-Por-Redireccion {
    # Sin la API de GitHub (403, 429, red o DNS): se sigue la redireccion de
    # /releases/latest con una peticion HEAD, sin pasar por la API.
    Write-Host (Tr 'Buscando la versi{o}n por otra v{i}a...')
    $urlRueda = $null
    try {
        $peticion = [System.Net.HttpWebRequest]::Create('https://github.com/alexisbanda/castkit/releases/latest')
        $peticion.Method = 'HEAD'
        $peticion.AllowAutoRedirect = $true
        $peticion.UserAgent = 'castkit-install.ps1'
        $respuestaHttp = $peticion.GetResponse()
        try {
            $ruta = $respuestaHttp.ResponseUri.AbsolutePath
        } finally {
            $respuestaHttp.Close()
        }
        if ($ruta -match '/tag/v(\d+\.\d+\.\d+)$') {
            $version = $Matches[1]
            $urlRueda = "https://github.com/alexisbanda/castkit/releases/download/v$version/castkit-$version-py3-none-any.whl"
        }
    } catch {
        $urlRueda = $null
    }
    if (-not $urlRueda) {
        Fail (Tr 'No se pudo consultar la {u}ltima versi{o}n de castkit. Revisa la conexi{o}n a internet. Si la conexi{o}n funciona, espera hasta una hora y vuelve a pegar la l{i}nea.')
    }
    return $urlRueda
}

function Obtener-Url-Ultima-Rueda {
    $url = 'https://api.github.com/repos/alexisbanda/castkit/releases/latest'
    try {
        $respuesta = Invoke-RestMethod -Uri $url -Headers @{ 'User-Agent' = 'castkit-install.ps1' } -ErrorAction Stop
    } catch {
        $codigo = $null
        if ($_.Exception.Response) {
            $codigo = [int]$_.Exception.Response.StatusCode
        }
        if ($codigo -eq 404) {
            Fail (Tr 'Todav{i}a no hay ninguna versi{o}n publicada de castkit. Avisa a quien te mand{o} este enlace.')
            return $null
        }
        return (Obtener-Url-Ultima-Rueda-Por-Redireccion)
    }
    $asset = $respuesta.assets | Where-Object { $_.name -match '^castkit-.*-py3-none-any\.whl$' } | Select-Object -First 1
    if (-not $asset) {
        Fail (Tr 'La {u}ltima versi{o}n publicada de castkit no tiene la rueda (.whl) adjunta. Avisa a quien mantiene castkit.')
    }
    return $asset.browser_download_url
}

if ($Rueda) {
    Write-Host "Usando la rueda indicada: $Rueda"
    $UriRueda = Convertir-A-Uri $Rueda
} else {
    Write-Host (Tr 'Buscando la {u}ltima versi{o}n de castkit...')
    $UriRueda = Obtener-Url-Ultima-Rueda
}

# 3. Instalar (o actualizar) castkit con el extra profundidad.
Write-Host 'Instalando castkit...'
& $UvExe tool install --force "castkit[profundidad] @ $UriRueda"
if ($LASTEXITCODE -ne 0) {
    Fail (Tr 'No se pudo instalar castkit. Revisa que la ruta o el enlace de la rueda sea correcto, que haya conexi{o}n a internet, y vuelve a intentarlo.')
}

# 4. Dejar `castkit` en el PATH de la sesion y del usuario.
& $UvExe tool update-shell | Out-Null
$CastkitBinDir = (& $UvExe tool dir --bin 2>$null)
if ($LASTEXITCODE -eq 0 -and $CastkitBinDir) {
    $CastkitBinDir = $CastkitBinDir.Trim()
    $env:Path = "$CastkitBinDir;$env:Path"
}

if (-not (Get-Command castkit -ErrorAction SilentlyContinue)) {
    Fail (Tr 'castkit se instal{o} pero no se puede ejecutar en esta ventana. Revisa los mensajes anteriores y vuelve a pegar la l{i}nea de instalaci{o}n.')
}
& castkit --help *> $null
if ($LASTEXITCODE -ne 0) {
    Fail (Tr 'castkit se instal{o} pero no se puede ejecutar en esta ventana. Revisa los mensajes anteriores y vuelve a pegar la l{i}nea de instalaci{o}n.')
}

# 5. Blender: si falta o es otra version, se descarga e instala (salvo -SinBlender).
# Cualquier fallo aqui (no se pudo consultar el estado, descargar, verificar o instalar)
# se avisa en palabras llanas y el script sigue hasta `castkit comprobar`: nunca usa
# `Fail` ni deja que una excepcion corte la instalacion de castkit, que ya quedo lista.
function Obtener-MayorMenor([string]$Version) {
    if ($Version -match '^(\d+\.\d+)') {
        return $Matches[1]
    }
    return $null
}

function Instalar-Blender-Si-Falta {
    if ($SinBlender) {
        Write-Host (Tr 'Se omite la instalaci{o}n de Blender (-SinBlender).')
        return
    }

    try {
        $salidaEstado = & castkit comprobar --estado-blender 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $salidaEstado) {
            Write-Host (Tr 'Aviso: no se pudo comprobar el estado de Blender; se omite este paso. Si falta, instal{a}lo a mano desde blender.org.') -ForegroundColor Yellow
            return
        }

        $estado = $null
        $version = $null
        foreach ($linea in $salidaEstado) {
            if ($linea -match '^estado=(.+)$') { $estado = $Matches[1].Trim() }
            if ($linea -match '^version=(.+)$') { $version = $Matches[1].Trim() }
        }

        if (-not $estado -or -not $version) {
            Write-Host (Tr 'Aviso: no se entendi{o} la respuesta de castkit sobre Blender; se omite este paso. Si falta, instal{a}lo a mano desde blender.org.') -ForegroundColor Yellow
            return
        }

        if ($estado -eq 'ok') {
            Write-Host (Tr "Blender $version ya est{a} instalado; no se descarga de nuevo.") -ForegroundColor Green
            return
        }

        if ($estado -eq 'otra') {
            Write-Host (Tr "Hay otra versi{o}n de Blender instalada; Blender $version se instalar{a} junto a la que ya tienes.") -ForegroundColor Yellow
        } else {
            Write-Host (Tr "No se encontr{o} Blender; se instalar{a} la versi{o}n $version.")
        }

        $mayorMenor = Obtener-MayorMenor $version
        if (-not $mayorMenor) {
            Write-Host (Tr "Aviso: no se entendi{o} el n{u}mero de versi{o}n de Blender (`"$version`"); se omite este paso. Instal{a}lo a mano desde blender.org.") -ForegroundColor Yellow
            return
        }

        $carpetaRelease = "Blender$mayorMenor"
        $nombreMsi = "blender-$version-windows-x64.msi"
        $urlMsi = "https://download.blender.org/release/$carpetaRelease/$nombreMsi"
        $urlSha256 = "https://download.blender.org/release/$carpetaRelease/blender-$version.sha256"
        $msi = Join-Path $env:TEMP "castkit-blender-$version.msi"

        function Obtener-Sha256-Esperado {
            if ($Sha256BlenderDePrueba) {
                return $Sha256BlenderDePrueba.Trim().ToUpper()
            }
            $contenidoSha = (Invoke-WebRequest -Uri $urlSha256 -UseBasicParsing -ErrorAction Stop).Content
            foreach ($lineaSha in ($contenidoSha -split "`n")) {
                if ($lineaSha -match ([regex]::Escape($nombreMsi) + '\s*$')) {
                    return ($lineaSha.Trim() -split '\s+')[0].ToUpper()
                }
            }
            return $null
        }

        $shaEsperado = $null
        try {
            $shaEsperado = Obtener-Sha256-Esperado
        } catch {
            $shaEsperado = $null
        }
        if (-not $shaEsperado) {
            Write-Host (Tr "No se pudo obtener la huella (sha256) oficial de Blender; no se instala. Puedes instalarlo a mano desde $urlMsi. castkit ya qued{o} instalado.") -ForegroundColor Yellow
            return
        }

        $yaValido = $false
        if (Test-Path -LiteralPath $msi) {
            $shaActual = (Get-FileHash -Algorithm SHA256 -LiteralPath $msi).Hash.ToUpper()
            if ($shaActual -eq $shaEsperado) {
                $yaValido = $true
                Write-Host (Tr 'Ya hab{i}a un instalador de Blender descargado y v{a}lido; no se vuelve a descargar.')
            } else {
                Remove-Item -LiteralPath $msi -Force -ErrorAction SilentlyContinue
            }
        }

        if (-not $yaValido) {
            Write-Host (Tr 'Descargando Blender (unos 365 MB; puede tardar varios minutos seg{u}n tu conexi{o}n)...')
            $descargaOk = $false
            if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                & curl.exe -L --fail --retry 2 --progress-bar -o $msi $urlMsi
                $descargaOk = ($LASTEXITCODE -eq 0)
            } else {
                $anteriorProgreso = $ProgressPreference
                $ProgressPreference = 'SilentlyContinue'
                try {
                    Invoke-WebRequest -Uri $urlMsi -OutFile $msi -ErrorAction Stop
                    $descargaOk = $true
                } catch {
                    $descargaOk = $false
                } finally {
                    $ProgressPreference = $anteriorProgreso
                }
            }

            if (-not $descargaOk -or -not (Test-Path -LiteralPath $msi)) {
                Write-Host (Tr "No se pudo descargar Blender. Puedes instalarlo a mano desde $urlMsi. castkit ya qued{o} instalado.") -ForegroundColor Yellow
                return
            }

            $shaActual = (Get-FileHash -Algorithm SHA256 -LiteralPath $msi).Hash.ToUpper()
            if ($shaActual -ne $shaEsperado) {
                Remove-Item -LiteralPath $msi -Force -ErrorAction SilentlyContinue
                Write-Host (Tr "El archivo descargado de Blender no coincide con lo esperado (sha256); no se instala. Puedes instalarlo a mano desde $urlMsi. castkit ya qued{o} instalado.") -ForegroundColor Yellow
                return
            }
        }

        Write-Host (Tr 'Instalando Blender. Windows va a pedir permiso de administrador en una ventana aparte: acepta para que la instalaci{o}n siga.') -ForegroundColor Yellow
        $p = Start-Process msiexec -ArgumentList "/i `"$msi`" /passive /norestart" -Wait -PassThru

        if ($p.ExitCode -eq 0 -or $p.ExitCode -eq 3010) {
            Remove-Item -LiteralPath $msi -Force -ErrorAction SilentlyContinue
            Write-Host (Tr 'Blender qued{o} instalado.') -ForegroundColor Green
        } elseif ($p.ExitCode -eq 1602 -or $p.ExitCode -eq 1223) {
            Write-Host (Tr "No se dio el permiso para instalar Blender. Puedes instalarlo a mano desde $urlMsi, o volver a pegar esta l{i}nea para reintentarlo (el instalador descargado no se vuelve a bajar). castkit ya qued{o} instalado.") -ForegroundColor Yellow
        } else {
            Write-Host (Tr "La instalaci{o}n de Blender termin{o} con un problema (c{o}digo $($p.ExitCode)). Puedes instalarlo a mano desde $urlMsi. castkit ya qued{o} instalado.") -ForegroundColor Yellow
        }
    } catch {
        Write-Host (Tr "No se pudo completar la instalaci{o}n de Blender ($($_.Exception.Message)). Puedes instalarlo a mano desde blender.org. castkit ya qued{o} instalado.") -ForegroundColor Yellow
    }
}

Instalar-Blender-Si-Falta

# 6. Correr `castkit comprobar`.
Write-Host ''
Write-Host (Tr 'castkit qued{o} instalado. Comprobando que todo est{e} en orden...') -ForegroundColor Green
& castkit comprobar
$CodigoComprobar = $LASTEXITCODE

Write-Host ''
if ($CodigoComprobar -eq 0) {
    Write-Host (Tr 'Todo listo: castkit qued{o} instalado y comprobado sin problemas.') -ForegroundColor Green
} else {
    Write-Host (Tr 'castkit qued{o} instalado. Arriba se lista lo que falta para poder usarlo (por ejemplo, Blender).') -ForegroundColor Yellow
}
