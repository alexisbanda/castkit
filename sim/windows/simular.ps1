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

# Credencial vía git (no en la URL) para que no aparezca en los logs subidos.
git config --global url."https://x-access-token:$($env:GH_TOKEN)@github.com/".insteadOf "https://github.com/"
$Repo = "git+https://github.com/$($env:GITHUB_REPOSITORY).git@main"

Etapa 'entorno' {
    [Environment]::OSVersion.VersionString
    "Cultura: $((Get-Culture).Name)  UI: $((Get-UICulture).Name)"
    "Code page consola: $(chcp)"
    "[Console]::OutputEncoding: $([Console]::OutputEncoding.WebName)"
    "PSVersion: $($PSVersionTable.PSVersion)"
}

# 1. Instalar uv como lo haría el operador (instalador oficial de PowerShell).
Etapa 'instalar-uv' {
    powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
}
$UvBin = Join-Path $HOME '.local\bin'

# 2. ¿Qué pasa si se pega el comando de la guía (comillas simples) en cmd.exe?
Etapa 'guia-en-cmd-comillas-simples' {
    cmd /c "`"$UvBin\uv.exe`" tool install 'castkit @ $Repo'"
}

# 3. Instalación real, en PowerShell, con el extra profundidad (como en la guía).
Etapa 'uv-tool-install-profundidad' {
    & "$UvBin\uv.exe" tool install --force "castkit[profundidad] @ $Repo"
}

# 4. ¿Una terminal nueva encuentra `castkit`? (PATH leído del registro, como al abrir otra ventana)
Etapa 'castkit-en-terminal-nueva' {
    $p = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    "PATH de usuario: $([Environment]::GetEnvironmentVariable('Path', 'User'))"
    pwsh -NoProfile -Command "`$env:Path = '$p'; (Get-Command castkit -ErrorAction Stop).Source; castkit --help"
}
$env:Path = "$UvBin;$env:Path"

# 5. Blender instalado con el .msi pero sin BLENDER_BIN (lo normal en Windows).
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

# 6. Diseño plano (relieve).
Etapa 'alta-plano-relieve' {
    castkit alta logo-pena --brief $BriefConComillas --tipo-molde abierto --largo-mm 60 --grosor-mm 5 --cavidades 2 --proveedor relieve @Comun
}
Etapa 'flujo-plano-relieve' { castkit flujo logo-pena --aprobar-control1 }

# 7. Media figura (profundidad, torch en CPU).
Etapa 'alta-media-profundidad' {
    castkit alta media-pena --brief $BriefConComillas --tipo-molde abierto --largo-mm 80 --grosor-mm 15 --cavidades 1 --proveedor profundidad @Comun
}
Etapa 'flujo-media-profundidad' { castkit flujo media-pena --aprobar-control1 }

# 8. Figura completa (imagen-3d, local).
Etapa 'alta-completa-imagen3d' {
    castkit alta figura-pena --brief $BriefConComillas --tipo-molde completo --alto-mm 70 --cavidades 1 --proveedor imagen-3d @Comun
}
Etapa 'flujo-completa-imagen3d' { castkit flujo figura-pena --aprobar-control1 }

# 9. Sin argumentos y sin terminal: ¿qué ve quien lanza `castkit alta` desde un script/acceso directo?
Etapa 'alta-sin-argumentos-sin-tty' { '' | castkit alta }

Etapa 'arbol-de-disenos' {
    Get-ChildItem -Recurse $env:CASTKIT_DISENOS | Select-Object FullName, Length | Format-Table -AutoSize | Out-String -Width 300
}
Copy-Item -Recurse $env:CASTKIT_DISENOS (Join-Path $Out 'disenos')

# 10. Tests marcados `blender` desde el clon, que la CI de Windows siempre salta.
Etapa 'pytest-blender' {
    Set-Location $env:GITHUB_WORKSPACE
    & "$UvBin\uv.exe" sync --locked
    & "$UvBin\uv.exe" run pytest -m blender -rs
}

Get-Content $Resumen | Add-Content -Encoding utf8 $env:GITHUB_STEP_SUMMARY
exit 0
