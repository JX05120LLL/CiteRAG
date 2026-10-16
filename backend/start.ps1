param(
    [ValidateSet('api', 'frontend')][string]$Role = 'api',
    [ValidateSet('text', 'knowledge')][string]$Profile = 'text',
    [switch]$CheckOnly,
    [switch]$EnableAgent,
    [switch]$EnableWeather,
    [switch]$Guide,
    [switch]$EnableAnswer,
    [switch]$EnableIngestion
)

# This branch needs no installed environment and never loads private configuration.
if ($Guide) {
    Write-Host 'CiteRAG configuration guide. No configuration was read; no service or supplier request was started.'
    Write-Host 'Run the following commands manually from the repository root. This guide changes no files.'
    Write-Host 'Mode: -Guide prints instructions; -CheckOnly runs bounded read-only preflight; neither starts the API.'
    if ($Role -eq 'frontend') {
        Write-Host 'First install: Node.js 22.12+ and pnpm 10.33.0 are required.'
        Write-Host '  Set-Location .\frontend'
        Write-Host '  pnpm install --frozen-lockfile'
        Write-Host '  Set-Location ..'
        Write-Host 'Check frontend: powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\start.ps1 -Role frontend -CheckOnly'
        Write-Host 'Start frontend: powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\start.ps1 -Role frontend'
    } else {
        $guideInstall = 'uv sync --locked'
        if ($Profile -eq 'knowledge') { $guideInstall += ' --extra rag' }
        if ($EnableAgent) { $guideInstall += ' --extra agent' }
        Write-Host '1. First install only (Python 3.12 and uv required; keep all optional extras you need):'
        Write-Host '  Set-Location .\backend'
        Write-Host ('  ' + $guideInstall)
        Write-Host '  Set-Location ..'
        Write-Host '2. Prepare the loopback PostgreSQL business DB and controlled CITERAG_DATABASE_URL, or the supported Windows DPAPI record.'
        Write-Host '  Get-Content -LiteralPath .\backend\STARTUP.md'
        Write-Host 'Database creation/migration requires separate authorization, stopped writes, backup and recovery planning. Startup does not perform it.'
        Write-Host '3. Inspect model record metadata only; existing records need no replacement:'
        Write-Host '  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\configure-model.ps1 -CheckOnly'
        Write-Host 'Only if not saved: replace YOUR_DASHSCOPE_WORKSPACE_ID with the real Beijing-region workspace ID; enter the API Key at the secure prompt in a non-elevated shell:'
        Write-Host '  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\configure-model.ps1 -WorkspaceId YOUR_DASHSCOPE_WORKSPACE_ID'
        Write-Host 'This saves local DPAPI configuration, refuses existing records, and sends no supplier request.'
        if ($Profile -eq 'knowledge') {
            Write-Host 'Knowledge mode also needs separate engine DB configuration and genuinely indexed, ready documents:'
            Write-Host '  Get-Content -LiteralPath .\backend\README.md'
        }
        $guideCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\start.ps1 -Profile ' + $Profile
        if ($EnableAgent) { $guideCommand += ' -EnableAgent' }
        if ($EnableWeather) { $guideCommand += ' -EnableWeather' }
        if ($EnableAnswer) { $guideCommand += ' -EnableAnswer' }
        if ($EnableIngestion) { $guideCommand += ' -EnableIngestion' }
        Write-Host '4. Check selected options (does not call suppliers, migrate, recover tasks or start services):'
        Write-Host ('  ' + $guideCommand + ' -CheckOnly')
        Write-Host '5. After checking data and supplier authorization, start in the foreground:'
        Write-Host ('  ' + $guideCommand)
        Write-Host '-EnableAnswer permits model answers; -EnableIngestion permits document indexing. Both default false and preserve existing controlled environment values.'
        Write-Host '-EnableAgent enables automatic tool planning; -EnableWeather enables configured manual weather tools. Automatic weather needs both and an enabled answer flow.'
        Write-Host 'Agent requires installed --extra agent and separately authorized checkpoint preparation: Get-Content -LiteralPath .\backend\AGENT.md'
        Write-Host 'Weather requires controlled Host/Key configuration: Get-Content -LiteralPath .\backend\QWEATHER.md'
        Write-Host 'Actual API startup recovers records, applies retention and may probe the engine DB. The status page can send paid supplier checks even when answers/indexing are disabled.'
    }
    exit 0
}

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
function Get-CiteRAGStartupBoolean([string]$Value) {
    # Match the supported Pydantic boolean spellings without displaying raw input.
    if ($Value.ToLowerInvariant() -in @('true', '1', 'yes', 'on', 'y', 't')) { return 'true' }
    if ($Value.ToLowerInvariant() -in @('false', '0', 'no', 'off', 'n', 'f')) { return 'false' }
    return 'invalid (value hidden)'
}
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
    # Explicit enable flags override only these values; controlled inherited values remain valid.
    if ($EnableAnswer) { $env:CITERAG_ANSWER_ENABLED = 'true' }
    elseif (-not $env:CITERAG_ANSWER_ENABLED) { $env:CITERAG_ANSWER_ENABLED = 'false' }
    if ($EnableIngestion) { $env:CITERAG_INGESTION_ENABLED = 'true' }
    elseif (-not $env:CITERAG_INGESTION_ENABLED) { $env:CITERAG_INGESTION_ENABLED = 'false' }
    $startupAnswerState = Get-CiteRAGStartupBoolean $env:CITERAG_ANSWER_ENABLED
    $startupIngestionState = Get-CiteRAGStartupBoolean $env:CITERAG_INGESTION_ENABLED
    Write-Host ('Selected API options: Profile={0}; Answer={1}; Ingestion={2}; Agent={3}; Weather={4}.' -f
        $Profile, $startupAnswerState, $startupIngestionState,
        $env:CITERAG_AGENT_ENABLED, $env:CITERAG_QWEATHER_ENABLED)
    $startupStage = 'feature-options'
    if ($startupAnswerState.StartsWith('invalid') -or $startupIngestionState.StartsWith('invalid')) {
        throw 'Invalid answer/ingestion boolean; no child process was started.'
    }
    Set-Location -LiteralPath $PSScriptRoot
    $startupStage = 'read-only-preflight'
    $startupCheckArguments = @('-m', 'app.doctor', '--profile', $Profile, '--database', '--backend-only')
    if ($EnableAgent) { $startupCheckArguments += '--agent' }
    & $startupPython @startupCheckArguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($CheckOnly) {
        Write-Host 'Read-only preflight completed; no API or supplier request was started.'
        exit 0
    }
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
    Write-Host 'Next steps from the repository root:'
    Write-Host ('  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\start.ps1 -Role {0} -Profile {1} -Guide' -f $Role, $Profile)
    if ($startupStage -eq 'dependencies') {
        if ($Role -eq 'frontend') {
            Write-Host '  Check Node.js 22.12+; then Set-Location .\frontend and pnpm install --frozen-lockfile.'
        } else {
            $startupInstall = 'uv sync --locked'
            if ($Profile -eq 'knowledge') { $startupInstall += ' --extra rag' }
            if ($EnableAgent) { $startupInstall += ' --extra agent' }
            Write-Host ('  Prepare Python 3.12 and uv; then Set-Location .\backend and ' + $startupInstall + '.')
        }
    } elseif ($startupStage -eq 'feature-options') {
        Write-Host '  Set controlled CITERAG_ANSWER_ENABLED/CITERAG_INGESTION_ENABLED to valid booleans (true/false); values were not printed.'
    } else {
        Write-Host '  Get-Content -LiteralPath .\backend\STARTUP.md'
    }
    exit 1
}
