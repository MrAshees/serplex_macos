param(
    [string]$Version = "",
    [string]$OutputDir = "",
    [string[]]$TauriArgs = @(),
    [switch]$SkipNpmInstall,
    [switch]$SkipBackendExecutable
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$resourcesRoot = Join-Path $root "src-tauri/resources/app"
$versionPath = Join-Path $root "app-version.json"
$tauriConfigPath = Join-Path $root "src-tauri/tauri.conf.json"
$cargoPath = Join-Path $root "src-tauri/Cargo.toml"
$packagePath = Join-Path $root "package.json"
$cacheRoot = Join-Path $root ".dist-cache"
$pythonVersion = "3.12.10"
$pythonZip = Join-Path $cacheRoot "python-$pythonVersion-embed-amd64.zip"
$pythonUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-embed-amd64.zip"

if (!$Version) {
    $versionInfo = Get-Content -Raw -LiteralPath $versionPath | ConvertFrom-Json
    $Version = [string]$versionInfo.version
}
if (!$OutputDir) {
    $OutputDir = Join-Path $root "dist/SerplexTauri"
}

function Test-IsWindows {
    return ($IsWindows -or $env:OS -eq "Windows_NT")
}

function Get-NpmCommand {
    if (Test-IsWindows) { return "npm.cmd" }
    return "npm"
}

function Get-PythonCommand {
    if (Test-IsWindows) { return "python" }
    return "python3"
}

function Get-HostPlatform {
    $os = if (Test-IsWindows) {
        "windows"
    } elseif ($IsMacOS) {
        "macos"
    } elseif ($IsLinux) {
        "linux"
    } else {
        throw "Unsupported build OS."
    }

    $archValue = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
    $arch = switch ($archValue) {
        "x64" { "x64" }
        "arm64" { "arm64" }
        "arm" { "arm64" }
        default { throw "Unsupported build architecture: $archValue" }
    }

    return [ordered]@{
        os = $os
        arch = $arch
        platform = "$os-$arch"
    }
}

function Set-JsonVersion {
    param([string]$Path, [string]$Version)
    $json = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    $json.version = $Version
    Write-Utf8NoBom -Path $Path -Value ($json | ConvertTo-Json -Depth 20)
}

function Copy-CleanDirectory {
    param([string]$Source, [string]$Destination)
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse
    Get-ChildItem -LiteralPath $Destination -Directory -Recurse -Force |
        Where-Object { $_.Name -eq "__pycache__" } |
        Remove-Item -Recurse -Force
}

function Invoke-NativeChecked {
    param([string]$FilePath, [string[]]$Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Value)
    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Value, $encoding)
}

function New-TarGz {
    param([string]$Archive, [string]$Cwd, [string]$Entry)
    if (Test-Path -LiteralPath $Archive) {
        Remove-Item -LiteralPath $Archive -Force
    }
    Invoke-NativeChecked -FilePath "tar" -Arguments @("-czf", $Archive, "-C", $Cwd, $Entry)
}

function Prepare-Resources {
    New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null

    Write-Host "Preparing Tauri resources..."
    if (Test-Path -LiteralPath $resourcesRoot) {
        Remove-Item -LiteralPath $resourcesRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $resourcesRoot | Out-Null

    Copy-CleanDirectory -Source (Join-Path $root "codex_local") -Destination (Join-Path $resourcesRoot "codex_local")
    Copy-Item -LiteralPath (Join-Path $root "serplex.desktop.json") -Destination $resourcesRoot -Force
    Copy-Item -LiteralPath $versionPath -Destination $resourcesRoot -Force

    $runtimeTarget = Join-Path $resourcesRoot "codex_local/.runtime"
    if (Test-Path -LiteralPath $runtimeTarget) {
        Remove-Item -LiteralPath $runtimeTarget -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $runtimeTarget | Out-Null
    $runtimeSource = Join-Path $root "codex_local/.runtime/secrets.json"
    if (Test-Path -LiteralPath $runtimeSource) {
        $sourceSecrets = Get-Content -Raw -LiteralPath $runtimeSource | ConvertFrom-Json
        $safeSecrets = [ordered]@{}
        foreach ($name in @("serper_api_key", "serper_search_url", "web_search_api_key", "web_search_url", "vision_model", "update_manifest_url")) {
            if ($sourceSecrets.PSObject.Properties.Name -contains $name) {
                $safeSecrets[$name] = $sourceSecrets.$name
            }
        }
        if ($safeSecrets.Count -gt 0) {
            $safeSecrets | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runtimeTarget "secrets.json") -Encoding UTF8
        }
    }

    if ((Test-IsWindows) -and $SkipBackendExecutable) {
        if (!(Test-Path -LiteralPath $pythonZip)) {
            Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonZip
        }
        Expand-Archive -LiteralPath $pythonZip -DestinationPath (Join-Path $resourcesRoot "python") -Force
    }
}

function Ensure-PyInstaller {
    $python = Get-PythonCommand
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $python -m PyInstaller --version > $null 2> $null
    $pyInstallerExit = $LASTEXITCODE
    $ErrorActionPreference = $previousPreference
    if ($pyInstallerExit -eq 0) {
        return
    }
    Write-Host "Installing PyInstaller for backend bundling..."
    Invoke-NativeChecked -FilePath $python -Arguments @("-m", "pip", "install", "--user", "pyinstaller")
}

function Build-BackendExecutable {
    if ($SkipBackendExecutable) {
        return
    }

    Ensure-PyInstaller

    $python = Get-PythonCommand
    $backendDir = Join-Path $resourcesRoot "backend"
    $workPath = Join-Path $cacheRoot "pyinstaller-work"
    $specPath = Join-Path $cacheRoot "pyinstaller-spec"
    if (Test-Path -LiteralPath $backendDir) {
        Remove-Item -LiteralPath $backendDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $backendDir, $workPath, $specPath | Out-Null

    $sep = if (Test-IsWindows) { ";" } else { ":" }
    $addData = @(
        "$(Join-Path $root "codex_local/web")${sep}web",
        "$(Join-Path $root "codex_local/update_public_key.json")${sep}."
    )
    $safeSecrets = Join-Path $resourcesRoot "codex_local/.runtime/secrets.json"
    if (Test-Path -LiteralPath $safeSecrets) {
        $addData += "$safeSecrets${sep}.runtime"
    }

    $args = @(
        "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onefile",
        "--name", "serplex-backend",
        "--distpath", $backendDir,
        "--workpath", $workPath,
        "--specpath", $specPath
    )
    foreach ($entry in $addData) {
        $args += @("--add-data", $entry)
    }
    $args += (Join-Path $root "codex_local/codex_lite_server.py")

    Invoke-NativeChecked -FilePath $python -Arguments $args
}

function Sync-Version {
    Set-JsonVersion -Path $packagePath -Version $Version
    Set-JsonVersion -Path $tauriConfigPath -Version $Version

    $cargoText = Get-Content -Raw -LiteralPath $cargoPath
    $cargoText = $cargoText -replace '(?m)^version = ".*"', "version = `"$Version`""
    Write-Utf8NoBom -Path $cargoPath -Value $cargoText
}

function Copy-BundleOutputs {
    $bundleRoot = Join-Path $root "src-tauri/target/release/bundle"
    if (!(Test-Path -LiteralPath $bundleRoot)) {
        return
    }
    Get-ChildItem -LiteralPath $bundleRoot -Recurse -File |
        Where-Object { $_.Extension -in ".exe", ".msi", ".dmg", ".deb", ".rpm", ".AppImage", ".gz" } |
        ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $OutputDir $_.Name) -Force }
}

function Publish-WindowsArtifact {
    $bundleRoot = Join-Path $root "src-tauri/target/release/bundle"
    $setup = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File -Filter "*.exe" |
        Where-Object { $_.Name -match "setup|install|Serplex" } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (!$setup) {
        throw "Tauri Windows installer was not created under $bundleRoot"
    }
    $versioned = Join-Path $OutputDir "SerplexInstall_$Version.exe"
    $latest = Join-Path $OutputDir "SerplexInstall.exe"
    Copy-Item -LiteralPath $setup.FullName -Destination $versioned -Force
    Copy-Item -LiteralPath $setup.FullName -Destination $latest -Force
    return $versioned
}

function Publish-LinuxArtifact {
    param([string]$Arch)
    $bundleRoot = Join-Path $root "src-tauri/target/release/bundle"
    $appImage = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File -Filter "*.AppImage" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (!$appImage) {
        throw "Tauri Linux AppImage was not created under $bundleRoot"
    }

    $packageRoot = Join-Path $cacheRoot "tauri-package-linux-$Arch"
    $appDir = Join-Path $packageRoot "Serplex"
    if (Test-Path -LiteralPath $packageRoot) {
        Remove-Item -LiteralPath $packageRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $appDir | Out-Null
    Copy-Item -LiteralPath $appImage.FullName -Destination (Join-Path $appDir "Serplex.AppImage") -Force

    Write-Utf8NoBom -Path (Join-Path $appDir "serplex.sh") -Value @'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$DIR/Serplex.AppImage" >/dev/null 2>&1 || true
exec "$DIR/Serplex.AppImage" "$@"
'@
    Write-Utf8NoBom -Path (Join-Path $appDir "README.txt") -Value "Serplex $Version Linux $Arch. Run ./serplex.sh or Serplex.AppImage."
    Write-Utf8NoBom -Path (Join-Path $appDir "platform.json") -Value (@{
        platform = "linux-$Arch"
        version = $Version
        kind = "tauri-appimage"
    } | ConvertTo-Json -Depth 4)

    $archive = Join-Path $OutputDir "Serplex_linux_$($Arch)_$Version.tar.gz"
    New-TarGz -Archive $archive -Cwd $packageRoot -Entry "Serplex"
    Copy-Item -LiteralPath $archive -Destination (Join-Path $OutputDir "Serplex_linux_$Arch.tar.gz") -Force
    return $archive
}

function Publish-MacosArtifact {
    param([string]$Arch)
    $bundleRoot = Join-Path $root "src-tauri/target/release/bundle"
    $appBundle = Get-ChildItem -LiteralPath $bundleRoot -Recurse -Directory -Filter "Serplex.app" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (!$appBundle) {
        throw "Tauri macOS .app bundle was not created under $bundleRoot"
    }

    $packageRoot = Join-Path $cacheRoot "tauri-package-macos-$Arch"
    if (Test-Path -LiteralPath $packageRoot) {
        Remove-Item -LiteralPath $packageRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null
    Copy-Item -LiteralPath $appBundle.FullName -Destination (Join-Path $packageRoot "Serplex.app") -Recurse -Force
    Write-Utf8NoBom -Path (Join-Path $packageRoot "README.txt") -Value "Serplex $Version macOS $Arch. Move Serplex.app to Applications and run it."

    $archive = Join-Path $OutputDir "Serplex_macos_$($Arch)_$Version.tar.gz"
    New-TarGz -Archive $archive -Cwd $packageRoot -Entry "Serplex.app"
    Copy-Item -LiteralPath $archive -Destination (Join-Path $OutputDir "Serplex_macos_$Arch.tar.gz") -Force
    return $archive
}

$platformInfo = Get-HostPlatform
New-Item -ItemType Directory -Force -Path $cacheRoot, $OutputDir | Out-Null

Prepare-Resources
Build-BackendExecutable
Sync-Version

if (!$SkipNpmInstall -and !(Test-Path -LiteralPath (Join-Path $root "node_modules"))) {
    Invoke-NativeChecked -FilePath (Get-NpmCommand) -Arguments @("install")
}

if (Test-IsWindows) {
    $env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
} else {
    $env:Path = "$HOME/.cargo/bin:$env:Path"
}
$args = @("run", "tauri", "--", "build") + $TauriArgs
Invoke-NativeChecked -FilePath (Get-NpmCommand) -Arguments $args

if (Test-Path -LiteralPath $OutputDir) {
    Remove-Item -LiteralPath $OutputDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
Copy-BundleOutputs

$artifact = switch ($platformInfo.os) {
    "windows" { Publish-WindowsArtifact }
    "linux" { Publish-LinuxArtifact -Arch $platformInfo.arch }
    "macos" { Publish-MacosArtifact -Arch $platformInfo.arch }
}

Write-Host ""
Write-Host "Serplex Tauri build is ready:"
Write-Host "  Platform: $($platformInfo.platform)"
Write-Host "  Update artifact: $artifact"
Get-ChildItem -LiteralPath $OutputDir -File | ForEach-Object { Write-Host "  $($_.FullName)" }
