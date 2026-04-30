[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",

    [int]$Port = 8765,

    [switch]$OpenBrowser,

    [switch]$NoEmbedding,

    [int]$EmbeddingPort = 8081,

    [string]$ModelPath,

    [int]$BatchSize = 4,

    [switch]$EnableClaudeChat,

    [ValidateSet("once", "continue", "both")]
    [string]$ClaudeMode = "both",

    [ValidateSet("ask", "normal", "dangerous")]
    [string]$ClaudePermissionMode = "ask",

    [switch]$Background
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$SrcPath = Join-Path $PluginRoot "src"
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

function Test-WebConsole([string]$ServiceHost, [int]$ServicePort) {
    try {
        $health = Invoke-RestMethod -Uri "http://$ServiceHost`:$ServicePort/api/health" -Method Get -TimeoutSec 2
        return [bool]$health.ok
    } catch {
        return $false
    }
}

function Test-PortFree([int]$ServicePort) {
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("127.0.0.1"), $ServicePort)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) {
            $listener.Stop()
        }
    }
}

function Resolve-WebPort([int]$PreferredPort) {
    if (Test-PortFree $PreferredPort) {
        return $PreferredPort
    }

    Write-Warning "Port $PreferredPort is already in use. Looking for another local port..."
    for ($candidate = $PreferredPort + 1; $candidate -lt ($PreferredPort + 50); $candidate++) {
        if (Test-PortFree $candidate) {
            Write-Host "Using http://127.0.0.1:$candidate instead."
            return $candidate
        }
    }
    throw "Could not find a free local port near $PreferredPort."
}

function Enable-EmbeddingEnv([int]$ServicePort) {
    $env:NOVEL_READER_EMBED_BASE_URL = "http://127.0.0.1:$ServicePort/v1"
    $env:NOVEL_READER_EMBED_API_KEY = "local"
    $env:NOVEL_READER_EMBED_MODEL = $ModelName
}

function Resolve-ClaudePermission([string]$PermissionMode) {
    if ($PermissionMode -eq "normal") {
        return "normal"
    }
    if ($PermissionMode -eq "dangerous") {
        Write-Warning "Claude Web bridge will use --dangerously-skip-permissions."
        Write-Warning "Messages and attached novel documents can be sent through Claude Code, and dangerous mode may bypass normal permission prompts."
        $confirm = Read-Host "Type DANGEROUS to confirm"
        if ($confirm -eq "DANGEROUS") {
            return "dangerous"
        }
        Write-Host "Dangerous mode was not confirmed. Falling back to normal Claude permission mode."
        return "normal"
    }

    Write-Host ""
    Write-Host "Choose Claude Web bridge permission mode:"
    Write-Host "  1. Normal Claude"
    Write-Host "  2. Claude --dangerously-skip-permissions"
    $choice = Read-Host "Enter 1 or 2"
    if ($choice -eq "2") {
        Write-Warning "Dangerous mode can bypass normal permission prompts."
        $confirm = Read-Host "Type DANGEROUS to confirm"
        if ($confirm -eq "DANGEROUS") {
            return "dangerous"
        }
        Write-Host "Dangerous mode was not confirmed. Falling back to normal Claude permission mode."
    }
    return "normal"
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
        return $true
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Warning "Python was not found. The web console will start without embedding."
        return $false
    }

    $server = Join-Path $PluginRoot "qwen_embed_server.py"
    if (-not (Test-Path -LiteralPath $server)) {
        Write-Warning "Missing qwen_embed_server.py. The web console will start without embedding."
        return $false
    }

    if (-not (Test-Path -LiteralPath $LocalDir)) {
        New-Item -ItemType Directory -Path $LocalDir | Out-Null
    }
    $stdout = Join-Path $LocalDir "qwen-embedding-web.out.log"
    $stderr = Join-Path $LocalDir "qwen-embedding-web.err.log"

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

if ($HostName -ne "127.0.0.1" -and $HostName -ne "localhost") {
    Write-Warning "The web console may expose local novel content if you bind it outside 127.0.0.1."
}

if (-not $NoEmbedding) {
    if (Test-EmbeddingService $EmbeddingPort) {
        Enable-EmbeddingEnv $EmbeddingPort
        Write-Host "Embedding service detected at http://127.0.0.1:$EmbeddingPort/v1"
    } else {
        $config = Read-LocalConfig
        $resolvedModelPath = Resolve-QwenModelPath $config
        if ($resolvedModelPath -and (Start-QwenEmbeddingService $resolvedModelPath $EmbeddingPort $BatchSize)) {
            Enable-EmbeddingEnv $EmbeddingPort
            Write-Host "Embedding service is ready at http://127.0.0.1:$EmbeddingPort/v1"
        } else {
            Write-Host "Embedding service is not available. The web console will still work with keyword search."
            Write-Host "Use -ModelPath to provide your local Qwen model path, or -NoEmbedding to skip this check."
        }
    }
} else {
    Remove-Item Env:NOVEL_READER_EMBED_BASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:NOVEL_READER_EMBED_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:NOVEL_READER_EMBED_MODEL -ErrorAction SilentlyContinue
    Write-Host "Embedding disabled by -NoEmbedding."
}

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$SrcPath;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $SrcPath
}

if ($EnableClaudeChat) {
    $env:NOVEL_READER_CLAUDE_ENABLED = "1"
    $env:NOVEL_READER_CLAUDE_MODE = $ClaudeMode
    $env:NOVEL_READER_CLAUDE_PERMISSION = Resolve-ClaudePermission $ClaudePermissionMode
    Write-Host "Claude Web bridge enabled. Mode: $ClaudeMode, permission: $env:NOVEL_READER_CLAUDE_PERMISSION"
} else {
    Remove-Item Env:NOVEL_READER_CLAUDE_ENABLED -ErrorAction SilentlyContinue
    Remove-Item Env:NOVEL_READER_CLAUDE_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:NOVEL_READER_CLAUDE_PERMISSION -ErrorAction SilentlyContinue
}

$ResolvedWebPort = Resolve-WebPort $Port
$WebUrl = "http://$HostName`:$ResolvedWebPort"

if ($Background) {
    if (-not (Test-Path -LiteralPath $LocalDir)) {
        New-Item -ItemType Directory -Path $LocalDir | Out-Null
    }
    $stdout = Join-Path $LocalDir "web-console-$ResolvedWebPort.out.log"
    $stderr = Join-Path $LocalDir "web-console-$ResolvedWebPort.err.log"
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python was not found."
    }

    Write-Host "Starting Novel Reader Web Console in background at $WebUrl"
    $process = Start-Process -FilePath $python.Source `
        -ArgumentList @("-m", "novel_reader.web_app", "--host", $HostName, "--port", [string]$ResolvedWebPort) `
        -WorkingDirectory $PluginRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-WebConsole $HostName $ResolvedWebPort) {
            if ($OpenBrowser) {
                Start-Process $WebUrl
            }
            Write-Host "Novel Reader Web Console is ready: $WebUrl"
            Write-Host "Process id: $($process.Id)"
            return
        }
        if ($process.HasExited) {
            Write-Warning "Web console exited early. Check logs:"
            Write-Warning "  $stdout"
            Write-Warning "  $stderr"
            return
        }
    }

    Write-Warning "Web console did not become ready in time. Check logs:"
    Write-Warning "  $stdout"
    Write-Warning "  $stderr"
    return
}

if ($OpenBrowser) {
    Start-Process $WebUrl
}

Write-Host "Starting Novel Reader Web Console at $WebUrl"
Write-Host "Press Ctrl+C to stop."

Set-Location $PluginRoot
python -m novel_reader.web_app --host $HostName --port $ResolvedWebPort
