[CmdletBinding()]
param(
    [ValidateSet("claude", "opencode", "none")]
    [string]$Client = "claude",

    [string]$ModelPath,

    [int]$Port = 8081,

    [switch]$NoEmbedding,

    [int]$BatchSize = 4,

    [ValidateSet("ask", "normal", "dangerous")]
    [string]$ClaudePermissionMode = "ask"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$LocalDir = Join-Path $PluginRoot ".novel-reader-local"
$ConfigPath = Join-Path $LocalDir "config.json"
$ModelName = "qwen3-embedding-0.6b"

function Read-LocalConfig {
    $config = @{}
    if (Test-Path -LiteralPath $ConfigPath) {
        try {
            $json = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
            foreach ($prop in $json.PSObject.Properties) {
                $config[$prop.Name] = $prop.Value
            }
        } catch {
            Write-Warning "Could not read local config: $ConfigPath"
        }
    }
    return $config
}

function Save-LocalConfig([hashtable]$Config) {
    if (-not (Test-Path -LiteralPath $LocalDir)) {
        New-Item -ItemType Directory -Path $LocalDir | Out-Null
    }
    $Config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
}

function Test-EmbeddingService([int]$ServicePort) {
    $baseUrl = "http://127.0.0.1:$ServicePort"
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/health" -Method Get -TimeoutSec 2
        return [bool]$health.ok
    } catch {
        try {
            $body = '{"model":"qwen3-embedding-0.6b","input":["health"]}'
            $result = Invoke-RestMethod -Uri "$baseUrl/v1/embeddings" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 5
            return [bool]($result.data)
        } catch {
            return $false
        }
    }
}

function Resolve-QwenModelPath([hashtable]$Config) {
    $candidates = @()
    if ($ModelPath) { $candidates += $ModelPath }
    if ($env:QWEN_EMBED_MODEL_PATH) { $candidates += $env:QWEN_EMBED_MODEL_PATH }
    if ($Config.ContainsKey("modelPath")) { $candidates += [string]$Config["modelPath"] }
    $candidates += (Join-Path $HOME ".cache\modelscope\hub\models\Qwen\Qwen3-Embedding-0.6B")

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Start-QwenEmbeddingService([string]$ResolvedModelPath, [int]$ServicePort, [int]$ServiceBatchSize) {
    if (Test-EmbeddingService $ServicePort) {
        Write-Host "Embedding service already available at http://127.0.0.1:$ServicePort/v1"
        return $true
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Warning "Python was not found. Start without embedding or install Python dependencies first."
        return $false
    }

    $server = Join-Path $PluginRoot "qwen_embed_server.py"
    if (-not (Test-Path -LiteralPath $server)) {
        Write-Warning "Missing qwen_embed_server.py. Start without embedding."
        return $false
    }

    if (-not (Test-Path -LiteralPath $LocalDir)) {
        New-Item -ItemType Directory -Path $LocalDir | Out-Null
    }
    $stdout = Join-Path $LocalDir "qwen-embedding.out.log"
    $stderr = Join-Path $LocalDir "qwen-embedding.err.log"

    $env:QWEN_EMBED_MODEL_PATH = $ResolvedModelPath
    $env:QWEN_EMBED_BATCH = [string]$ServiceBatchSize
    $env:QWEN_EMBED_HOST = "127.0.0.1"
    $env:QWEN_EMBED_PORT = [string]$ServicePort
    $env:QWEN_EMBED_MODEL_NAME = $ModelName

    Write-Host "Starting Qwen embedding service on 127.0.0.1:$ServicePort ..."
    $process = Start-Process -FilePath $python.Source `
        -ArgumentList @($server) `
        -WorkingDirectory $PluginRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if (Test-EmbeddingService $ServicePort) {
            Write-Host "Embedding service is ready."
            return $true
        }
        if ($process.HasExited) {
            Write-Warning "Embedding service exited early. Check logs:"
            Write-Warning "  $stdout"
            Write-Warning "  $stderr"
            return $false
        }
    }

    Write-Warning "Embedding service did not become ready in time. Check logs:"
    Write-Warning "  $stdout"
    Write-Warning "  $stderr"
    return $false
}

function Resolve-ClaudeArgs([string]$PermissionMode) {
    if ($PermissionMode -eq "normal") {
        return @()
    }
    if ($PermissionMode -eq "dangerous") {
        Write-Warning "Claude will be started with --dangerously-skip-permissions."
        Write-Warning "This can let Claude perform actions without normal permission prompts. Use only in a trusted workspace."
        $confirm = Read-Host "Type DANGEROUS to confirm"
        if ($confirm -eq "DANGEROUS") {
            return @("--dangerously-skip-permissions")
        }
        Write-Host "Dangerous mode was not confirmed. Starting normal Claude."
        return @()
    }

    Write-Host ""
    Write-Host "Choose Claude permission mode:"
    Write-Host "  1. Normal Claude (recommended)"
    Write-Host "  2. Claude --dangerously-skip-permissions"
    $choice = Read-Host "Enter 1 or 2"
    if ($choice -eq "2") {
        Write-Warning "Dangerous mode can bypass normal permission prompts."
        $confirm = Read-Host "Type DANGEROUS to confirm"
        if ($confirm -eq "DANGEROUS") {
            return @("--dangerously-skip-permissions")
        }
        Write-Host "Dangerous mode was not confirmed. Starting normal Claude."
    }
    return @()
}

function Start-Client([string]$ClientName, [bool]$EmbeddingEnabled, [int]$ServicePort, [string]$PermissionMode) {
    if ($EmbeddingEnabled) {
        $env:NOVEL_READER_EMBED_BASE_URL = "http://127.0.0.1:$ServicePort/v1"
        $env:NOVEL_READER_EMBED_API_KEY = "local"
        $env:NOVEL_READER_EMBED_MODEL = $ModelName
    } else {
        Remove-Item Env:NOVEL_READER_EMBED_BASE_URL -ErrorAction SilentlyContinue
        Remove-Item Env:NOVEL_READER_EMBED_API_KEY -ErrorAction SilentlyContinue
        Remove-Item Env:NOVEL_READER_EMBED_MODEL -ErrorAction SilentlyContinue
    }

    if ($ClientName -eq "none") {
        if ($EmbeddingEnabled) {
            Write-Host "Embedding is ready. No client was started because -Client none was used."
        } else {
            Write-Host "No client was started. Embedding is disabled."
        }
        return
    }

    $command = Get-Command $ClientName -ErrorAction SilentlyContinue
    if (-not $command) {
        Write-Warning "$ClientName was not found on PATH."
        Write-Host "Embedding environment is set in this PowerShell session. You can start your client manually."
        return
    }

    if ($ClientName -eq "claude") {
        $claudeArgs = Resolve-ClaudeArgs $PermissionMode
        if ($claudeArgs.Count -gt 0) {
            Write-Host "Starting claude $($claudeArgs -join ' ') ..."
            & $command.Source @claudeArgs
        } else {
            Write-Host "Starting claude ..."
            & $command.Source
        }
        return
    }

    Write-Host "Starting $ClientName ..."
    & $command.Source
}

$config = Read-LocalConfig
$resolvedPort = $Port
if (-not $PSBoundParameters.ContainsKey("Port") -and $config.ContainsKey("port")) {
    $resolvedPort = [int]$config["port"]
}
$resolvedBatchSize = $BatchSize
if (-not $PSBoundParameters.ContainsKey("BatchSize") -and $config.ContainsKey("batchSize")) {
    $resolvedBatchSize = [int]$config["batchSize"]
}

$embeddingEnabled = $false
if (-not $NoEmbedding) {
    $resolvedModelPath = Resolve-QwenModelPath $config
    if (-not $resolvedModelPath) {
        Write-Host "Qwen embedding model was not found."
        Write-Host "Expected common path: $HOME\.cache\modelscope\hub\models\Qwen\Qwen3-Embedding-0.6B"
        Write-Host "Install suggestion: download Qwen/Qwen3-Embedding-0.6B from ModelScope, then enter its local folder path here."
        $inputPath = Read-Host "Enter Qwen model path, or press Enter to continue without embedding"
        if ($inputPath -and (Test-Path -LiteralPath $inputPath)) {
            $resolvedModelPath = (Resolve-Path -LiteralPath $inputPath).Path
        } elseif ($inputPath) {
            Write-Warning "Path does not exist: $inputPath"
        }
    }

    if ($resolvedModelPath) {
        $config["modelPath"] = $resolvedModelPath
        $config["port"] = $resolvedPort
        $config["batchSize"] = $resolvedBatchSize
        $config["modelName"] = $ModelName
        Save-LocalConfig $config
        $embeddingEnabled = Start-QwenEmbeddingService $resolvedModelPath $resolvedPort $resolvedBatchSize
    } else {
        Write-Host "Continuing without embedding. Keyword search and local FTS still work."
    }
} else {
    Write-Host "Embedding disabled by -NoEmbedding."
}

Start-Client $Client $embeddingEnabled $resolvedPort $ClaudePermissionMode
