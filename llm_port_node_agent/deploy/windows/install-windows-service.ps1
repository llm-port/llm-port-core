# install-windows-service.ps1
# Registers the llm-port node agent as a Windows service using NSSM.
# Requires: NSSM (https://nssm.cc) on PATH and Administrator privileges.
# Usage: .\install-windows-service.ps1 [-PythonExe <path>] [-EnvFile <path>]

[CmdletBinding()]
param(
    [string]$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source,
    [string]$EnvFile   = ""
)

$ServiceName = "llm-port-node-agent"
$ErrorActionPreference = "Stop"

# ── Pre-checks ──────────────────────────────────────────────────────────
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator."
    exit 1
}

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Error "NSSM not found on PATH. Install it from https://nssm.cc"
    exit 1
}

if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    Write-Error "Python executable not found. Specify -PythonExe <path>."
    exit 1
}

# ── Resolve the entry-point module ──────────────────────────────────────
$ModuleArgs = "-m llm_port_node_agent"

# ── Install service ─────────────────────────────────────────────────────
Write-Host "Installing service '$ServiceName' ..."
nssm install $ServiceName $PythonExe $ModuleArgs

nssm set $ServiceName DisplayName "LLM Port Node Agent"
nssm set $ServiceName Description  "Manages LLM workloads on this node"
nssm set $ServiceName Start        SERVICE_AUTO_START
nssm set $ServiceName AppStdout    "$env:ProgramData\llm-port-node-agent\service.log"
nssm set $ServiceName AppStderr    "$env:ProgramData\llm-port-node-agent\service.log"
nssm set $ServiceName AppRotateFiles 1
nssm set $ServiceName AppRotateBytes 10485760   # 10 MB

# ── Environment variables ───────────────────────────────────────────────
if ($EnvFile -and (Test-Path $EnvFile)) {
    Write-Host "Loading environment from $EnvFile ..."
    $envPairs = @()
    foreach ($line in Get-Content $EnvFile) {
        $line = $line.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $envPairs += $line
        }
    }
    if ($envPairs.Count -gt 0) {
        $envBlock = $envPairs -join "`n"
        nssm set $ServiceName AppEnvironmentExtra $envBlock
    }
}

# ── Data directory ──────────────────────────────────────────────────────
$DataDir = "$env:ProgramData\llm-port-node-agent"
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
}

# Restrict to SYSTEM and current user
icacls $DataDir /inheritance:r | Out-Null
icacls $DataDir /grant:r "SYSTEM:(OI)(CI)(F)" | Out-Null
icacls $DataDir /grant:r "$env:USERNAME:(OI)(CI)(F)" | Out-Null

Write-Host ""
Write-Host "Service installed. Configure environment variables before starting:"
Write-Host "  nssm set $ServiceName AppEnvironmentExtra 'LLM_PORT_NODE_AGENT_BACKEND_URL=https://...\nLLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN=...'"
Write-Host ""
Write-Host "Then start with:  nssm start $ServiceName"
Write-Host "Status:           nssm status $ServiceName"
Write-Host "Remove:           nssm remove $ServiceName confirm"
