# Simulación de un operador del taller usando castkit en Windows.
# Cada etapa se ejecuta aunque falle la anterior; el resultado queda en
# $env:SIM_OUT (logs por etapa + resumen.md).

$ErrorActionPreference = 'Continue'
$Out = $env:SIM_OUT
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Resumen = Join-Path $Out 'resumen.md'
"| # | Etapa | Código | Segundos |`n|---|---|---|---|" | Set-Content -Encoding utf8 $Resumen
$script:n = 0

function Etapa([string]$nombre, [scriptblock]$bloque) {
    $script:n++
    $log = Join-Path $Out ('{0:D2}-{1}.log' -f $script:n, ($nombre -replace '[^\w-]', '_'))
    Write-Host "::group::[$script:n] $nombre"
    $t = [Diagnostics.Stopwatch]::StartNew()
    $global:LASTEXITCODE = 0
    try {
        & $bloque *>&1 | Tee-Object -FilePath $log
        $codigo = $global:LASTEXITCODE
    } catch {
        $_ | Out-String | Tee-Object -FilePath $log -Append
        $codigo = 'excepción'
    }
    $t.Stop()
    Write-Host "::endgroup::"
    "| $script:n | $nombre | $codigo | $([int]$t.Elapsed.TotalSeconds) |" | Add-Content -Encoding utf8 $Resumen
}

# install.ps1 y la rueda (.whl) de castkit a probar: los deja el job "construir"
# (ubuntu, sin Windows) en el artifact descargado antes de este paso, ver
# sim-windows.yml (#80: Windows limpio, sin Git ni cuenta de GitHub).
$InstallPs1 = $env:SIM_INSTALL
$RuedaWhl = (Get-ChildItem $env:SIM_RUEDA -Filter '*.whl' | Select-Object -First 1).FullName
"install.ps1: $InstallPs1`nrueda: $RuedaWhl" | Set-Content -Encoding utf8 (Join-Path $Out '00-paquete.log')

Etapa 'entorno' {
    [Environment]::OSVersion.VersionString
    "Cultura: $((Get-Culture).Name)  UI: $((Get-UICulture).Name)"
    "Code page consola: $(chcp)"
    "[Console]::OutputEncoding: $([Console]::OutputEncoding.WebName)"
    "PSVersion: $($PSVersionTable.PSVersion)"
}

$UvBin = Join-Path $HOME '.local\bin'

function Estado-Blender {
    "Blender en Program Files: $([bool](Get-ChildItem 'C:\Program Files\Blender Foundation' -Recurse -Filter blender.exe -ErrorAction SilentlyContinue))"
    "msi en TEMP: $(Test-Path (Join-Path $env:TEMP 'castkit-blender-*.msi'))"
}

# 2. El install.ps1 de esta rama bajo iex, como la línea real, con la rueda ya publicada
# (no conoce --estado-blender): debe avisar, saltar Blender y terminar.
Etapa 'iex-rueda-sin-estado-blender' {
    Estado-Blender
    powershell -NoProfile -ExecutionPolicy ByPass -c "Get-Content -Raw '$InstallPs1' | iex"
    $codigo = $LASTEXITCODE
    Estado-Blender
    $global:LASTEXITCODE = $codigo
}

# 3. -SinBlender, en la forma documentada: no descarga nada.
Etapa 'sin-blender' {
    powershell -NoProfile -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((Get-Content -Raw '$InstallPs1'))) -Rueda '$RuedaWhl' -SinBlender"
    $codigo = $LASTEXITCODE
    Estado-Blender
    $global:LASTEXITCODE = $codigo
}

# 4. Huella incorrecta: no instala, borra el msi, castkit queda instalado.
Etapa 'sha256-incorrecto' {
    powershell -NoProfile -ExecutionPolicy ByPass -File $InstallPs1 -Rueda $RuedaWhl -Sha256BlenderDePrueba 0000000000000000000000000000000000000000000000000000000000000000
    $codigo = $LASTEXITCODE
    Estado-Blender
    $global:LASTEXITCODE = $codigo
}

# 5. Sin Blender: lo descarga con el sha256 oficial, lo instala y comprobar lo encuentra.
Etapa 'instala-blender' {
    Estado-Blender
    powershell -NoProfile -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((Get-Content -Raw '$InstallPs1'))) -Rueda '$RuedaWhl'"
    $codigo = $LASTEXITCODE
    Estado-Blender
    $global:LASTEXITCODE = $codigo
}

# 5b. Repetir: no vuelve a descargar.
Etapa 'repetir-no-descarga' {
    powershell -NoProfile -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((Get-Content -Raw '$InstallPs1'))) -Rueda '$RuedaWhl'"
}

# 4. -Rueda con una ruta que no existe: debe fallar con un mensaje en español.
Etapa 'rueda-inexistente' {
    powershell -NoProfile -ExecutionPolicy ByPass -File $InstallPs1 -Rueda 'C:\no-existe-esta-rueda-de-prueba.whl'
}


$env:Path = "$UvBin;$env:Path"

# 6. ¿Una terminal nueva encuentra `castkit`? (PATH leído del registro, como al abrir otra ventana)
Etapa 'castkit-en-terminal-nueva' {
    $p = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    "PATH de usuario: $([Environment]::GetEnvironmentVariable('Path', 'User'))"
    pwsh -NoProfile -Command "`$env:Path = '$p'; (Get-Command castkit -ErrorAction Stop).Source; castkit --help"
}

# 7. Blender instalado con el .msi pero sin BLENDER_BIN (lo normal en Windows).
Etapa 'comprobar-sin-BLENDER_BIN' {
    Remove-Item Env:BLENDER_BIN -ErrorAction SilentlyContinue
    castkit comprobar
}

$Blender = Get-ChildItem 'C:\Program Files\Blender Foundation' -Recurse -Filter blender.exe -ErrorAction SilentlyContinue | Select-Object -First 1
$env:BLENDER_BIN = $Blender.FullName
# Carpeta de diseños con tilde, eñe y espacios, como la de un usuario real.
$env:CASTKIT_DISENOS = Join-Path $HOME 'Documents\Diseños del taller'
New-Item -ItemType Directory -Force -Path $env:CASTKIT_DISENOS | Out-Null

Etapa 'comprobar-con-BLENDER_BIN' {
    "BLENDER_BIN=$env:BLENDER_BIN"
    "CASTKIT_DISENOS=$env:CASTKIT_DISENOS"
    castkit comprobar
}

# Brief en una carpeta con espacios y eñe; se pasa con comillas, como al
# usar «Copiar como ruta» en el Explorador.
$Brief = Join-Path $HOME 'Documents\Pedidos clientes\Brief señora Peña'
New-Item -ItemType Directory -Force -Path $Brief | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'benchy_referencia.png') $Brief -ErrorAction Stop
$BriefConComillas = "`"$Brief`""

$Comun = @('--modo', 'cubeta', '--material', 'silicona', '--cantidad', '10',
    '--fecha', '2026-12-15', '--licencia', 'propio', '--origen', 'dibujo del cliente')

# 7b. Sin CASTKIT_DISENOS (#83): los diseños van a Documentos\castkit, se lance desde donde se lance.
$DisenosGuardada = $env:CASTKIT_DISENOS
Remove-Item Env:CASTKIT_DISENOS -ErrorAction SilentlyContinue
$DocsDefecto = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'castkit'
Push-Location $HOME
Etapa 'defecto-alta-desde-HOME' {
    castkit alta logo-defecto --brief $BriefConComillas --tipo-molde abierto --largo-mm 60 --grosor-mm 5 --cavidades 1 --proveedor relieve @Comun
}
Push-Location $env:TEMP
Etapa 'defecto-flujo-desde-TEMP' { castkit flujo logo-defecto --aprobar-control1 }
Pop-Location
Etapa 'defecto-comprobar' {
    "Documentos según Windows: $([Environment]::GetFolderPath('MyDocuments'))"
    castkit comprobar
}
Etapa 'defecto-diseno-en-Documentos-castkit' {
    $d = Join-Path $DocsDefecto 'logo-defecto'
    if (-not (Test-Path (Join-Path $d 'diseno.json'))) { throw "No está en $d" }
    Get-ChildItem -Recurse $d | Select-Object FullName, Length | Format-Table -AutoSize | Out-String -Width 300
}
Pop-Location
$env:CASTKIT_DISENOS = $DisenosGuardada

# 8. Diseño plano (relieve).
Etapa 'alta-plano-relieve' {
    castkit alta logo-pena --brief $BriefConComillas --tipo-molde abierto --largo-mm 60 --grosor-mm 5 --cavidades 2 --proveedor relieve @Comun
}
Etapa 'flujo-plano-relieve' { castkit flujo logo-pena --aprobar-control1 }

# 9. Media figura (profundidad, torch en CPU).
Etapa 'alta-media-profundidad' {
    castkit alta media-pena --brief $BriefConComillas --tipo-molde abierto --largo-mm 80 --grosor-mm 15 --cavidades 1 --proveedor profundidad @Comun
}
Etapa 'flujo-media-profundidad' { castkit flujo media-pena --aprobar-control1 }

# 10. Figura completa (imagen-3d, local).
Etapa 'alta-completa-imagen3d' {
    castkit alta figura-pena --brief $BriefConComillas --tipo-molde completo --alto-mm 70 --cavidades 1 --proveedor imagen-3d @Comun
}
Etapa 'flujo-completa-imagen3d' { castkit flujo figura-pena --aprobar-control1 }

# 11. Sin argumentos y sin terminal: ¿qué ve quien lanza `castkit alta` desde un script/acceso directo?
Etapa 'alta-sin-argumentos-sin-tty' { '' | castkit alta }

Etapa 'arbol-de-disenos' {
    Get-ChildItem -Recurse $env:CASTKIT_DISENOS | Select-Object FullName, Length | Format-Table -AutoSize | Out-String -Width 300
}
Copy-Item -Recurse $env:CASTKIT_DISENOS (Join-Path $Out 'disenos')

# 12. Tests marcados `blender` desde el clon, que la CI de Windows siempre salta.
Etapa 'pytest-blender' {
    Set-Location $env:GITHUB_WORKSPACE
    & "$UvBin\uv.exe" sync --locked
    & "$UvBin\uv.exe" run pytest -m blender -rs
}

Get-Content $Resumen | Add-Content -Encoding utf8 $env:GITHUB_STEP_SUMMARY
exit 0
