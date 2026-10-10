# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 zhing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

# ============================================================================
# STTP .env loader for PowerShell
# Usage:  . .\env.ps1
# (ASCII-only on purpose: avoids encoding issues on non-ASCII paths)
# ============================================================================
$sttpEnvFile = Join-Path $PSScriptRoot '.env'

if (-not (Test-Path $sttpEnvFile)) {
    Write-Error "env.ps1: not found: $sttpEnvFile"
    return
}

Get-Content $sttpEnvFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq '') { return }
    if ($line.StartsWith('#')) { return }
    $idx = $line.IndexOf('=')
    if ($idx -lt 1) { return }
    $k = $line.Substring(0, $idx).Trim()
    $v = $line.Substring($idx + 1).Trim()
    switch ($k) {
        'NEO4J_URI'       { $env:NEO4J_URI = $v }
        'NEO4J_USER'      { $env:NEO4J_USER = $v }
        'NEO4J_PASSWORD'  { $env:NEO4J_PASSWORD = $v }
        'NEO4J_DATABASE'  { $env:NEO4J_DATABASE = $v }
        'GRAPH_DATA_FILE' { $env:GRAPH_DATA_FILE = $v }
        'VIZ_PORT'        { $env:VIZ_PORT = $v }
        'STTP_PYTHON'     { $env:STTP_PYTHON = $v }
    }
}

Write-Host "[STTP] env loaded -> NEO4J_URI=$env:NEO4J_URI  NEO4J_DATABASE=$env:NEO4J_DATABASE"
