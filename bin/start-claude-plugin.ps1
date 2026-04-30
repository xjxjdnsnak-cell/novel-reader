[CmdletBinding()]
param(
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
$ClaudeWorkspace = Join-Path $LocalDir "claude-workspace"
$Launcher = Join-Path $ScriptDir "start-novel-reader.ps1"

if (-not (Test-Path -LiteralPath $ClaudeWorkspace)) {
    New-Item -ItemType Directory -Path $ClaudeWorkspace | Out-Null
}

$ClaudeMd = Join-Path $ClaudeWorkspace "CLAUDE.md"
$ClaudeMdText = @(
    "# Novel Reader Claude Workspace",
    "",
    "This is a dedicated workspace for the Novel Reader plugin.",
    "",
    "Rules:",
    "- Wait for the user's next instruction after startup.",
    "- Do not continue old writing tasks.",
    "- Do not start writing chapters automatically.",
    "- Prefer the novel-reader skill and the local CLI in $PluginRoot.",
    "- If another writing style skill appears relevant, ask the user before using it.",
    "- Default to Chinese output.",
    "",
    "Useful local paths:",
    "- Plugin root: $PluginRoot",
    "- Web launcher: $PluginRoot\bin\start-web.ps1",
    "- CLI wrapper: $PluginRoot\bin\novel-reader.ps1"
) -join [Environment]::NewLine
$ClaudeMdText | Set-Content -LiteralPath $ClaudeMd -Encoding UTF8

Set-Location $ClaudeWorkspace

$launcherParams = @{
    Client = "claude"
    Port = $Port
    BatchSize = $BatchSize
    ClaudePermissionMode = $ClaudePermissionMode
}

if ($ModelPath) {
    $launcherParams["ModelPath"] = $ModelPath
}
if ($NoEmbedding) {
    $launcherParams["NoEmbedding"] = $true
}

& $Launcher @launcherParams
