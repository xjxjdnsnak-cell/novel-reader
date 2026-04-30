---
name: novel-web
description: Open or start the Novel Reader local Web console from Claude Code, with optional Claude bridge support.
argument-hint: [--port 8765] [--no-embedding] [--dangerous]
allowed-tools: [Bash]
---

# Novel Reader Web Panel

Use this command when the user wants to open the Novel Reader Web console from inside Claude Code.

## Arguments

The user invoked this with: `$ARGUMENTS`

Supported user-facing arguments:

- `--port <number>`: preferred Web port, default `8765`.
- `--no-embedding`: skip Qwen Embedding detection/startup.
- `--dangerous`: request Claude bridge dangerous permission mode.

## Instructions

When invoked:

1. Locate the Novel Reader plugin root in this order:
   - current workspace if `bin/start-web.ps1` exists
   - `$HOME\.claude\plugins\cache\local-novel-tools\novel-reader\0.1.0`
   - newest directory under `$HOME\.claude\plugins\cache\local-novel-tools\novel-reader\`
2. Start the Web console in background with PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<plugin-root>\bin\start-web.ps1" -EnableClaudeChat -ClaudeMode both -ClaudePermissionMode normal -Background -OpenBrowser
```

3. If the user included `--dangerous`, use `-ClaudePermissionMode dangerous`. The launcher will ask the user to type `DANGEROUS`; do not bypass that confirmation.
4. If the user included `--no-embedding`, add `-NoEmbedding`.
5. If the user included `--port <number>`, add `-Port <number>`.
6. Tell the user the final URL printed by the launcher.

## Ready-Made PowerShell

If you need a robust one-shot command, run this from Claude Code:

```powershell
$candidates = @()
if (Test-Path -LiteralPath ".\bin\start-web.ps1") {
  $candidates += (Resolve-Path -LiteralPath ".").Path
}
$cacheRoot = Join-Path $HOME ".claude\plugins\cache\local-novel-tools\novel-reader"
if (Test-Path -LiteralPath $cacheRoot) {
  $dirs = Get-ChildItem -LiteralPath $cacheRoot -Directory | Sort-Object LastWriteTime -Descending
  foreach ($dir in $dirs) { $candidates += $dir.FullName }
}
$root = $candidates | Where-Object { Test-Path -LiteralPath (Join-Path $_ "bin\start-web.ps1") } | Select-Object -First 1
if (-not $root) { throw "Novel Reader plugin root was not found." }
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "bin\start-web.ps1") -EnableClaudeChat -ClaudeMode both -ClaudePermissionMode normal -Background -OpenBrowser
```

The Web console always binds to `127.0.0.1` by default.
