[CmdletBinding()]
param(
    [string]$WorkspaceId,
    [switch]$CheckOnly,
    [switch]$LibraryOnly
)

# First-time Beijing DashScope setup only. No replacement, database operation,
# supplier request, environment loading, or existing credential read is provided.
function Assert-CiteRAGModelSetupPath([string]$Path) {
    $candidate = [IO.Path]::GetFullPath($Path)
    if ($candidate.StartsWith('\\')) { throw 'A local configuration path is required.' }
    while ($candidate) {
        try { $item = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop }
        catch [Management.Automation.ItemNotFoundException] { $item = $null }
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Redirected configuration paths are not supported.'
        }
        $candidate = [IO.Path]::GetDirectoryName($candidate)
    }
}

function Get-CiteRAGTextModelSetupTarget([string]$ProjectRoot) {
    $root = [IO.Path]::GetFullPath($ProjectRoot)
    Assert-CiteRAGModelSetupPath $root
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw 'The project directory does not exist.'
    }
    $target = Join-Path $root '.local\runtime\models\dashscope.credential.xml'
    Assert-CiteRAGModelSetupPath $target
    return $target
}

function Get-CiteRAGTextModelConfigurationStatus([string]$ProjectRoot) {
    $target = Get-CiteRAGTextModelSetupTarget $ProjectRoot
    if (Test-Path -LiteralPath $target) {
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw 'The model configuration target is not a file.'
        }
        return 'dashscope: saved (not verified); existing configuration was not read.'
    }
    return 'dashscope: not saved; no configuration was created.'
}

function Set-CiteRAGModelSetupAcl([string]$Path, [bool]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $sections = [Security.AccessControl.AccessControlSections]::Owner
    $existing = if ($PSVersionTable.PSEdition -eq 'Desktop') {
        $item.GetAccessControl($sections)
    } else {
        [IO.FileSystemAclExtensions]::GetAccessControl($item, $sections)
    }
    if ($existing.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $user.Value) {
        throw 'The local configuration path must be owned by the current Windows user.'
    }
    # Only DACL changes are persisted; no owner or audit privilege is requested.
    $acl = if ($Directory) {
        [Security.AccessControl.DirectorySecurity]::new()
    } else {
        [Security.AccessControl.FileSecurity]::new()
    }
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = if ($Directory) { 'ContainerInherit,ObjectInherit' } else { 'None' }
    foreach ($sid in @($user, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $sid, 'FullControl', $inheritance, 'None', 'Allow'
        ))
    }
    if ($PSVersionTable.PSEdition -eq 'Desktop') { $item.SetAccessControl($acl) }
    else { [IO.FileSystemAclExtensions]::SetAccessControl($item, $acl) }
}

function Save-CiteRAGTextModelConfiguration {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][AllowEmptyString()][string]$WorkspaceId,
        [Parameter(Mandatory)][Security.SecureString]$ApiKey
    )
    $ErrorActionPreference = 'Stop'
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'Windows DPAPI is required for this configuration entry.'
    }
    if ($WorkspaceId -cnotmatch '^[A-Za-z0-9_-]{1,128}$') {
        throw 'Invalid Workspace ID; use 1-128 ASCII letters, digits, underscores or hyphens.'
    }
    if ($ApiKey.Length -eq 0) { throw 'API Key must not be empty.' }
    $target = Get-CiteRAGTextModelSetupTarget $ProjectRoot
    if (Test-Path -LiteralPath $target) {
        throw 'Model configuration already exists; first-time setup will not read or replace it.'
    }
    $directory = [IO.Path]::GetDirectoryName($target)
    $temporary = Join-Path $directory ([guid]::NewGuid().ToString('N') + '.tmp')
    try {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
        Assert-CiteRAGModelSetupPath $target
        Set-CiteRAGModelSetupAcl $directory $true
        $record = [pscustomobject]@{
            SchemaVersion = 3
            Provider = 'dashscope'
            AuthMode = 'api-key'
            Region = 'cn-beijing'
            WorkspaceId = $WorkspaceId
            Models = @{
                answer = 'qwen-flash'
                engine = 'qwen-plus'
                summary = 'qwen-max'
                embedding = 'text-embedding-v4'
                embedding_dimension = 1024
                rerank = 'qwen3-vl-rerank'
            }
            Credential = [Management.Automation.PSCredential]::new('dashscope', $ApiKey)
            Verified = $false
        }
        $record | Export-Clixml -LiteralPath $temporary -Encoding UTF8 -Depth 5 -NoClobber
        Set-CiteRAGModelSetupAcl $temporary $false
        Assert-CiteRAGModelSetupPath $target
        # Atomic same-directory publication refuses a concurrently created file.
        [IO.File]::Move($temporary, $target)
    } catch {
        # An encrypted temporary file may remain for explicit local recovery.
        # Preserve an existing/concurrently created record; never print inner errors.
        throw 'Model configuration was not saved; existing configuration was preserved. Any encrypted temporary file remains under .local/runtime/models.'
    } finally {
        $record = $null
    }
    return 'dashscope: saved (not verified); no supplier request was made.'
}

if ($LibraryOnly) { return }

$ErrorActionPreference = 'Stop'
$secret = $null
try {
    $project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    if ($CheckOnly) {
        Get-CiteRAGTextModelConfigurationStatus $project
        exit 0
    }
    $target = Get-CiteRAGTextModelSetupTarget $project
    if (Test-Path -LiteralPath $target) {
        throw 'Model configuration already exists; first-time setup will not read or replace it.'
    }
    if (-not $WorkspaceId) { $WorkspaceId = Read-Host 'Beijing DashScope Workspace ID' }
    if ($WorkspaceId -cnotmatch '^[A-Za-z0-9_-]{1,128}$') { throw 'Invalid Workspace ID.' }
    Write-Host 'First-time Beijing DashScope setup. The key is encrypted for this Windows user. No database or supplier request is made.'
    $secret = Read-Host 'DashScope API Key (hidden input)' -AsSecureString
    Save-CiteRAGTextModelConfiguration -ProjectRoot $project -WorkspaceId $WorkspaceId -ApiKey $secret
} catch {
    # Deliberately category-only: no driver, credential contents or inner exceptions.
    [Console]::Error.WriteLine('Model setup blocked. Check the Workspace ID, local path/permissions and whether configuration already exists. Existing configuration was not replaced.')
    exit 1
} finally {
    if ($null -ne $secret) { $secret.Dispose() }
}
