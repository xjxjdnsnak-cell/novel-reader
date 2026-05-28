[CmdletBinding()]
param(
    [ValidateSet("claude", "opencode", "none")]
    [string]$Client = "claude",

    [string]$ModelPath,

    [int]$Port = 8081,

    [switch]$NoEmbedding,

    [int]$BatchSize = 4,

    [double]$CudaMemoryFraction = 0.85,

    [int]$MaxSeqLength = 1024,

    [ValidateSet("auto", "sentence-transformers", "llama-cpp")]
    [string]$Backend = "auto",

    [ValidateSet("auto", "cpu", "cuda")]
    [string]$Device = "auto",

    [int]$GpuLayers = 99,

    [int]$ContextSize = 2048,

    [int]$LlamaBatchSize = 512,

    [ValidateSet("ask", "normal", "dangerous")]
    [string]$ClaudePermissionMode = "ask"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$LocalDir = Join-Path $PluginRoot ".novel-reader-local"
$ConfigPath = Join-Path $LocalDir "config.json"
$ModelName = "qwen3-embedding-0.6b"
$EffectiveEmbeddingPort = $Port

if (-not $env:NOVEL_READER_HOME) {
    $env:NOVEL_READER_HOME = Join-Path $PluginRoot ".novel-reader"
}

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
        if ($health.model) {
            $script:ModelName = [string]$health.model
        }
        if ($health.model_path) {
            $script:DetectedEmbeddingModelPath = [string]$health.model_path
        }
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

function Stop-EmbeddingServiceOnPort([int]$ServicePort) {
    try {
        $health = $null
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ServicePort/health" -Method Get -TimeoutSec 2
        } catch {
            $health = $null
        }
        $looksLikeEmbedding = $false
        if ($health) {
            $looksLikeEmbedding = [bool]($health.PSObject.Properties.Name -contains "model" -or
                $health.PSObject.Properties.Name -contains "model_path" -or
                $health.PSObject.Properties.Name -contains "model_loaded")
        }
        if (-not $looksLikeEmbedding) {
            Write-Warning "Port $ServicePort is occupied, but it does not look like this Qwen/Novel Reader embedding service. Not stopping it automatically. Use another port or close that program manually."
            return $false
        }
        $connections = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $ServicePort -State Listen -ErrorAction Stop
        foreach ($connection in $connections) {
            Write-Host "Stopping existing embedding service on port $ServicePort (pid $($connection.OwningProcess)) ..."
            Stop-Process -Id $connection.OwningProcess -Force -ErrorAction Stop
        }
        Start-Sleep -Seconds 2
        return $true
    } catch {
        Write-Warning "Could not stop the existing process on port $ServicePort. Close it manually, then run the launcher again."
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

function Resolve-EmbeddingPort([int]$PreferredPort) {
    if (Test-PortFree $PreferredPort) {
        return $PreferredPort
    }
    for ($candidate = $PreferredPort + 1; $candidate -lt ($PreferredPort + 50); $candidate++) {
        if (Test-PortFree $candidate) {
            Write-Host "Using embedding port $candidate instead."
            return $candidate
        }
    }
    throw "Could not find a free local embedding port near $PreferredPort."
}

function Resolve-QwenModelPath([hashtable]$Config) {
    $candidates = @()
    if ($ModelPath) { $candidates += $ModelPath }
    if ($env:QWEN_EMBED_MODEL_PATH) { $candidates += $env:QWEN_EMBED_MODEL_PATH }
    if ($Config.ContainsKey("modelPath")) { $candidates += [string]$Config["modelPath"] }
    $candidates += (Join-Path $HOME ".cache\modelscope\hub\models\Qwen\Qwen3-Embedding-0.6B")
    $candidates += (Join-Path $HOME ".cache\modelscope\hub\models\Qwen\Qwen3-Embedding-4B-GGUF\Qwen3-Embedding-4B-Q4_K_M.gguf")
    $candidates += (Join-Path $HOME ".cache\modelscope\hub\models\Qwen\Qwen3-Embedding-4B-GGUF")
    $candidates += (Join-Path $HOME ".cache\modelscope\hub\models\Qwen\Qwen3-Embedding-4B")

    foreach ($candidate in $candidates) {
        if (-not $candidate -or -not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        $resolved = (Resolve-Path -LiteralPath $candidate).Path
        if ($Backend -eq "sentence-transformers" -and $resolved.ToLowerInvariant().EndsWith(".gguf")) {
            continue
        }
        if ((Get-Item -LiteralPath $resolved).PSIsContainer) {
            if ($Backend -eq "sentence-transformers") {
                return $resolved
            }
            $preferred = Get-ChildItem -LiteralPath $resolved -Filter "*Q4_K_M*.gguf" -File -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $preferred) {
                $preferred = Get-ChildItem -LiteralPath $resolved -Filter "*.gguf" -File -ErrorAction SilentlyContinue | Sort-Object Length | Select-Object -First 1
            }
            if ($preferred) {
                return $preferred.FullName
            }
            continue
        }
        return $resolved
    }
    return $null
}

function Resolve-QwenModelName([string]$ResolvedModelPath) {
    $leaf = Split-Path -Leaf $ResolvedModelPath
    if ($leaf -match "4B") {
        return "qwen3-embedding-4b"
    }
    if ($leaf -match "0\.6B") {
        return "qwen3-embedding-0.6b"
    }
    if ($env:QWEN_EMBED_MODEL_NAME) {
        return $env:QWEN_EMBED_MODEL_NAME
    }
    return $ModelName
}

function Resolve-QwenBackend([string]$ResolvedModelPath) {
    if ($Backend -ne "auto") {
        return $Backend
    }
    if ($ResolvedModelPath.ToLowerInvariant().EndsWith(".gguf")) {
        return "llama-cpp"
    }
    return "sentence-transformers"
}

function Start-DetachedPythonProcess([string[]]$Arguments, [string]$WorkingDirectory, [string]$StdoutPath, [string]$StderrPath) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python was not found on PATH."
    }

    $quote = {
        param([string]$Value)
        '"' + ($Value -replace '"', '\"') + '"'
    }
    $pythonCommand = @(& $quote $python.Source)
    foreach ($arg in $Arguments) {
        $pythonCommand += (& $quote $arg)
    }
    $commandLine = ($pythonCommand -join " ") + " 1> " + (& $quote $StdoutPath) + " 2> " + (& $quote $StderrPath)

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $env:ComSpec
    if (-not $psi.FileName) {
        $psi.FileName = "cmd.exe"
    }
    $psi.Arguments = '/d /s /c "' + $commandLine + '"'
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $processEnv = $psi.Environment
    if ($null -eq $processEnv) {
        $processEnv = $psi.EnvironmentVariables
    }
    $pathValue = $env:Path
    [void]$processEnv.Remove("PATH")
    [void]$processEnv.Remove("Path")
    if ($pathValue) {
        $processEnv["Path"] = $pathValue
    }
    foreach ($name in @(
        "QWEN_EMBED_MODEL_PATH",
        "QWEN_EMBED_BATCH",
        "QWEN_EMBED_HOST",
        "QWEN_EMBED_PORT",
        "QWEN_EMBED_MODEL_NAME",
        "QWEN_EMBED_CUDA_MEMORY_FRACTION",
        "QWEN_EMBED_MAX_SEQ_LENGTH",
        "QWEN_EMBED_DEVICE",
        "QWEN_EMBED_BACKEND",
        "QWEN_EMBED_N_GPU_LAYERS",
        "QWEN_EMBED_N_CTX",
        "QWEN_EMBED_N_BATCH"
    )) {
        $value = [Environment]::GetEnvironmentVariable($name, "Process")
        if ($value) {
            $processEnv[$name] = $value
        } else {
            [void]$processEnv.Remove($name)
        }
    }

    return [System.Diagnostics.Process]::Start($psi)
}

function Start-QwenEmbeddingService([string]$ResolvedModelPath, [int]$ServicePort, [int]$ServiceBatchSize) {
    $script:DetectedEmbeddingModelPath = $null
    $serviceHealthy = Test-EmbeddingService $ServicePort
    $desiredPath = (Resolve-Path -LiteralPath $ResolvedModelPath).Path
    if ($script:DetectedEmbeddingModelPath) {
        if ($script:DetectedEmbeddingModelPath -and $script:DetectedEmbeddingModelPath -ne $desiredPath) {
            Write-Host "Embedding service on port $ServicePort is using a different model:"
            Write-Host "  current: $script:DetectedEmbeddingModelPath"
            Write-Host "  desired: $desiredPath"
            if (-not (Stop-EmbeddingServiceOnPort $ServicePort)) {
                $ServicePort = Resolve-EmbeddingPort ($ServicePort + 1)
                $script:EffectiveEmbeddingPort = $ServicePort
                Write-Host "Starting desired model on alternate port $ServicePort."
            }
        } elseif ($serviceHealthy) {
            Write-Host "Embedding service already available at http://127.0.0.1:$ServicePort/v1"
            $script:EffectiveEmbeddingPort = $ServicePort
            return $true
        } else {
            Write-Host "Embedding service on port $ServicePort is unhealthy. Restarting it."
            if (-not (Stop-EmbeddingServiceOnPort $ServicePort)) {
                $ServicePort = Resolve-EmbeddingPort ($ServicePort + 1)
                $script:EffectiveEmbeddingPort = $ServicePort
                Write-Host "Starting desired model on alternate port $ServicePort."
            }
        }
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
    $script:ModelName = Resolve-QwenModelName $ResolvedModelPath
    $env:QWEN_EMBED_MODEL_NAME = $ModelName
    $env:QWEN_EMBED_BACKEND = Resolve-QwenBackend $ResolvedModelPath
    $env:QWEN_EMBED_DEVICE = $Device
    $env:QWEN_EMBED_N_GPU_LAYERS = [string]$GpuLayers
    $env:QWEN_EMBED_N_CTX = [string]$ContextSize
    $env:QWEN_EMBED_N_BATCH = [string]$LlamaBatchSize
    if ($CudaMemoryFraction -gt 0) {
        $env:QWEN_EMBED_CUDA_MEMORY_FRACTION = [string]$CudaMemoryFraction
    } else {
        Remove-Item Env:QWEN_EMBED_CUDA_MEMORY_FRACTION -ErrorAction SilentlyContinue
    }
    if ($MaxSeqLength -gt 0) {
        $env:QWEN_EMBED_MAX_SEQ_LENGTH = [string]$MaxSeqLength
    } else {
        Remove-Item Env:QWEN_EMBED_MAX_SEQ_LENGTH -ErrorAction SilentlyContinue
    }

    Write-Host "Starting Qwen embedding service on 127.0.0.1:$ServicePort ..."
    Write-Host "  selected model path: $ResolvedModelPath"
    Write-Host "  selected backend: $env:QWEN_EMBED_BACKEND"
    Write-Host "  selected device: $env:QWEN_EMBED_DEVICE"
    Write-Host "  selected model name: $ModelName"
    $process = Start-DetachedPythonProcess `
        -Arguments @($server) `
        -WorkingDirectory $PluginRoot `
        -StdoutPath $stdout `
        -StderrPath $stderr

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if (Test-EmbeddingService $ServicePort) {
            Write-Host "Embedding service is ready."
            $script:EffectiveEmbeddingPort = $ServicePort
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
            return @("--permission-mode", "bypassPermissions")
        }
        throw "Dangerous mode was requested but not confirmed. Claude was not started."
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
            return @("--permission-mode", "bypassPermissions")
        }
        throw "Dangerous mode was selected but not confirmed. Claude was not started."
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
            Write-Host "Effective permission mode: bypassPermissions"
            & $command.Source @claudeArgs
        } else {
            Write-Host "Starting claude ..."
            Write-Host "Effective permission mode: normal"
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
$EffectiveEmbeddingPort = $resolvedPort
$resolvedBatchSize = $BatchSize
if (-not $PSBoundParameters.ContainsKey("BatchSize") -and $config.ContainsKey("batchSize")) {
    $resolvedBatchSize = [int]$config["batchSize"]
}

$embeddingEnabled = $false
if (-not $NoEmbedding) {
    $resolvedModelPath = Resolve-QwenModelPath $config
    if (-not $resolvedModelPath) {
        Write-Host "Qwen embedding model was not found."
        Write-Host "Expected common path: $HOME\.cache\modelscope\hub\models\Qwen\Qwen3-Embedding-4B"
        Write-Host "Install suggestion: download Qwen/Qwen3-Embedding-4B from ModelScope, then enter its local folder path here."
        $inputPath = Read-Host "Enter Qwen model path, or press Enter to continue without embedding"
        if ($inputPath -and (Test-Path -LiteralPath $inputPath)) {
            $resolvedModelPath = (Resolve-Path -LiteralPath $inputPath).Path
        } elseif ($inputPath) {
            Write-Warning "Path does not exist: $inputPath"
        }
    }

    if ($resolvedModelPath) {
        if ((Resolve-QwenModelName $resolvedModelPath) -eq "qwen3-embedding-4b" -and -not $PSBoundParameters.ContainsKey("BatchSize")) {
            $resolvedBatchSize = 1
        }
        $config["modelPath"] = $resolvedModelPath
        $config["port"] = $resolvedPort
        $config["batchSize"] = $resolvedBatchSize
        $config["modelName"] = Resolve-QwenModelName $resolvedModelPath
        $config["cudaMemoryFraction"] = $CudaMemoryFraction
        $config["maxSeqLength"] = $MaxSeqLength
        $config["backend"] = Resolve-QwenBackend $resolvedModelPath
        $config["device"] = $Device
        $config["gpuLayers"] = $GpuLayers
        $config["contextSize"] = $ContextSize
        $config["llamaBatchSize"] = $LlamaBatchSize
        Save-LocalConfig $config
        $embeddingEnabled = Start-QwenEmbeddingService $resolvedModelPath $resolvedPort $resolvedBatchSize
    } else {
        Write-Host "Continuing without embedding. Keyword search and local FTS still work."
    }
} else {
    Write-Host "Embedding disabled by -NoEmbedding."
}

Start-Client $Client $embeddingEnabled $EffectiveEmbeddingPort $ClaudePermissionMode
