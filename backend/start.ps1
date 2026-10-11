param(
    [ValidateSet('api', 'frontend')][string]$Role = 'api',
    [ValidateSet('text', 'knowledge')][string]$Profile = 'text',
    [switch]$CheckOnly,
    [switch]$EnableAgent,
    [switch]$EnableWeather
)

# Foreground daily startup. No install, migration, Docker control or voice worker.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$startupRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$startupPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$startupVite = Join-Path $startupRoot 'frontend\node_modules\vite\bin\vite.js'
$startupStage = 'dependencies'
try {
    if ($Role -eq 'frontend') {
        # Frontend Node receives no inherited backend/database/provider secrets.
        foreach ($entry in @(Get-ChildItem Env:)) {
            if ($entry.Name -match '^(CITERAG_|PG|POSTGRES|OPENAI_|DASHSCOPE_|ANTHROPIC_|VOLCENGINE_|MINIMAX_|LIVEKIT_)' -or
                $entry.Name -in @('DATABASE_URL', 'WEB_CONCURRENCY')) {
                Remove-Item -LiteralPath ('Env:' + $entry.Name)
            }
        }
        if (-not (Test-Path -LiteralPath $startupVite -PathType Leaf)) {
            throw 'Frontend dependencies are missing; use the explicit first-install steps in STARTUP.md.'
        }
        $nodeCommand = Get-Command node.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
        $nodeVersion = & $nodeCommand.Source --version
        if ($LASTEXITCODE -ne 0 -or [version]($nodeVersion.TrimStart('v')) -lt [version]'22.12.0') {
            throw 'Node.js 22.12+ is required.'
        }
        $startupStage = 'frontend-port'
        if ($CheckOnly) {
            $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 5173)
            try { $listener.Start(); Write-Host 'Frontend port and Vite entry are ready; no service started.' }
            finally { $listener.Stop() }
            exit 0
        }
        Set-Location -LiteralPath (Join-Path $startupRoot 'frontend')
        & $nodeCommand.Source $startupVite --host 127.0.0.1 --port 5173 --strictPort
        exit $LASTEXITCODE
    }
    if (-not (Test-Path -LiteralPath $startupPython -PathType Leaf)) {
        throw 'Python 3.12 environment is missing; use the explicit first-install steps in STARTUP.md.'
    }
    # Use controlled DB configuration only in this backend process; never argv.
    if (-not $env:CITERAG_DATABASE_URL) {
        $startupStage = 'business-configuration'
        . (Join-Path $PSScriptRoot 'business-config.ps1')
        $env:CITERAG_DATABASE_URL = Get-CiteRAGBusinessUrl (
            Join-Path $startupRoot '.local\runtime\business-postgres\credential.xml'
        )
    }
    $env:PYTHON_DOTENV_DISABLED = '1'
    $env:CITERAG_VOICE_TRANSPORT_ENABLED = 'false'
    $env:CITERAG_VOICE_ASSISTANT_ENABLED = 'false'
    $env:CITERAG_AGENT_ENABLED = $EnableAgent.IsPresent.ToString().ToLowerInvariant()
    $env:CITERAG_MCP_ENABLED = 'false'
    $env:CITERAG_BACKUP_ENABLED = 'false'
    $env:CITERAG_QWEATHER_ENABLED = $EnableWeather.IsPresent.ToString().ToLowerInvariant()
    if (-not $EnableWeather) {
        # Prevent disabled daily startup from loading private weather configuration.
        $env:CITERAG_QWEATHER_API_HOST = 'startup.qweatherapi.com'
        $env:CITERAG_QWEATHER_API_KEY = 'unused-local-startup'
    }
    $env:CITERAG_API_WORKERS = '1'
    $env:WEB_CONCURRENCY = '1'
    # Existing answer/ingestion switches are respected; they default to false.
    Set-Location -LiteralPath $PSScriptRoot
    $startupStage = 'read-only-preflight'
    $startupCheckArguments = @('-m', 'app.doctor', '--profile', $Profile, '--database', '--backend-only')
    if ($EnableAgent) { $startupCheckArguments += '--agent' }
    & $startupPython @startupCheckArguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($CheckOnly) { exit 0 }
    Write-Host 'Starting the text API. Startup recovers records and applies retention; it is not read-only.'
    Write-Host 'Answer/ingestion switches are explicit. Supplier requests can charge when enabled or checked in the status page.'
    if ($EnableAgent) { Write-Host 'Automatic tool planning enabled; only registered tools are available. MCP remains disabled.' }
    if ($EnableWeather) { Write-Host 'Weather queries enabled when configured. Location parameters are sent to QWeather and may incur fees.' }
    $startupStage = 'api-startup'
    & $startupPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
    exit $LASTEXITCODE
} catch {
    # Do not render PowerShell exception text: configuration errors may contain secrets.
    Write-Host ("CiteRAG startup blocked at {0}. See STARTUP.md for this stage. No migration or service termination was performed." -f $startupStage) -ForegroundColor Red
    exit 1
}
