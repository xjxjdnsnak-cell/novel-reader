$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginRoot = Resolve-Path (Join-Path $ScriptDir "..")
$env:PYTHONPATH = (Join-Path $PluginRoot "src") + [IO.Path]::PathSeparator + $env:PYTHONPATH
python -m novel_reader.cli @args

