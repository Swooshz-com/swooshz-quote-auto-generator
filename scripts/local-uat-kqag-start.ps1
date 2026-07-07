[CmdletBinding()]
param(
    [string]$PlatformBaseUrl = "http://127.0.0.1:4317",
    [string]$KqagDatabaseUrl = "",
    [string]$UatRoot = "",
    [switch]$SkipMigrations,
    [string]$KqagHost = "127.0.0.1",
    [int]$KqagPort = 8765
)

$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
    $scriptPath = if ($PSCommandPath) { $PSCommandPath } else { $MyInvocation.MyCommand.Path }
    if ([string]::IsNullOrWhiteSpace($scriptPath)) {
        throw "Unable to resolve this script path."
    }

    $scriptDirectory = Split-Path -Parent $scriptPath
    $root = Resolve-Path (Join-Path $scriptDirectory "..")
    if (-not (Test-Path (Join-Path $root "webapp/server.py"))) {
        throw "This helper must run from the KQAG repo checkout; webapp/server.py was not found."
    }
    return $root.Path
}

function Resolve-PythonCommand {
    $candidates = @(
        @{ Command = "py"; Arguments = @('-3') },
        @{ Command = "python"; Arguments = @() },
        @{ Command = "python3"; Arguments = @() }
    )

    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Command -ErrorAction SilentlyContinue
        if (-not $command) {
            continue
        }

        try {
            & $command.Source @($candidate.Arguments + @("-c", "import sys; sys.exit(0)")) *> $null
            if ($LASTEXITCODE -eq 0) {
                return @{
                    FilePath = $command.Source
                    Arguments = $candidate.Arguments
                    Display = if ($candidate.Arguments.Count -gt 0) {
                        "$($candidate.Command) $($candidate.Arguments -join ' ')"
                    } else {
                        $candidate.Command
                    }
                }
            }
        } catch {
            continue
        }
    }

    throw "No Python command is available. Install Python or make one of these commands available on PATH: py -3, python, python3."
}

function New-LocalSessionSecret {
    $bytes = [byte[]]::new(32)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        if ($null -ne $rng) {
            $rng.Dispose()
        }
    }
    return [Convert]::ToBase64String($bytes)
}

function Set-ProcessEnv {
    param(
        [string]$Name,
        [string]$Value
    )
    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
}

$repoRoot = Resolve-RepoRoot
Set-Location $repoRoot

$python = Resolve-PythonCommand

$usingDefaultUatRoot = $false
if ([string]::IsNullOrWhiteSpace($UatRoot)) {
    $UatRoot = Join-Path ([System.IO.Path]::GetTempPath()) "kqag-platform-uat"
    $usingDefaultUatRoot = $true
}

$resolvedUatRoot = [System.IO.Path]::GetFullPath($UatRoot)
$dataRoot = Join-Path $resolvedUatRoot "data"
$outputRoot = Join-Path $resolvedUatRoot "output"
$tmpRoot = Join-Path $resolvedUatRoot "tmp"
$logRoot = Join-Path $resolvedUatRoot "logs"

foreach ($path in @($resolvedUatRoot, $dataRoot, $outputRoot, $tmpRoot, $logRoot)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

if ([string]::IsNullOrWhiteSpace($KqagDatabaseUrl)) {
    $databasePath = [System.IO.Path]::GetFullPath((Join-Path $resolvedUatRoot "kqag-platform-uat.sqlite3"))
    $KqagDatabaseUrl = "sqlite:///" + $databasePath.Replace("\", "/")
}

if ([string]::IsNullOrWhiteSpace($env:SESSION_SECRET)) {
    Set-ProcessEnv -Name "SESSION_SECRET" -Value (New-LocalSessionSecret)
}

Set-ProcessEnv -Name "APP_MODE" -Value "deploy"
Set-ProcessEnv -Name "AUTH_REQUIRED" -Value "true"
Set-ProcessEnv -Name "KQAG_PLATFORM_LAUNCH_MODE" -Value "platform"
Set-ProcessEnv -Name "KQAG_PLATFORM_BASE_URL" -Value $PlatformBaseUrl
Set-ProcessEnv -Name "KQAG_STORAGE_MODE" -Value "database"
Set-ProcessEnv -Name "KQAG_ARTIFACT_STORAGE_MODE" -Value "database"
Set-ProcessEnv -Name "SQAG_DATABASE_URL" -Value $KqagDatabaseUrl
Set-ProcessEnv -Name "QUOTE_DATA_ROOT" -Value $dataRoot
Set-ProcessEnv -Name "QUOTE_OUTPUT_ROOT" -Value $outputRoot
Set-ProcessEnv -Name "QUOTE_TMP_ROOT" -Value $tmpRoot
Set-ProcessEnv -Name "QUOTE_LOG_ROOT" -Value $logRoot

$kqagBaseUrl = "http://${KqagHost}:${KqagPort}"

if ($usingDefaultUatRoot) {
    Write-Host "KQAG local UAT root: $resolvedUatRoot"
} else {
    Write-Host "KQAG local UAT root: custom path configured"
}
Write-Host "Python command: $($python.Display)"
Write-Host "KQAG URL: $kqagBaseUrl/"
Write-Host ""
Write-Host "Set these in the Platform shell before npm run platform:start:"
Write-Host '$env:PLATFORM_KQAG_LAUNCH_MODE="server_handoff"'
# Default line shape: PLATFORM_KQAG_APP_BASE_URL=http://127.0.0.1:<port>
Write-Host "`$env:PLATFORM_KQAG_APP_BASE_URL=`"$kqagBaseUrl`""
Write-Host ""

if (-not $SkipMigrations) {
    Write-Host "Applying KQAG storage migrations to the disposable local database..."
    & $python.FilePath @($python.Arguments + @("scripts/migrate_kqag_storage.py"))
    if ($LASTEXITCODE -ne 0) {
        throw "KQAG storage migrations failed."
    }
}

Write-Host "Starting KQAG for Platform UAT..."
& $python.FilePath @($python.Arguments + @("-m", "webapp.server", "--host", $KqagHost, "--port", [string]$KqagPort))
exit $LASTEXITCODE
