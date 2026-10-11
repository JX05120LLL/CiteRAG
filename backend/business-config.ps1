# Read only the explicitly prepared Docker business record. Never select tests.
function Get-CiteRAGBusinessUrl([string]$RecordPath) {
    if (-not (Test-Path -LiteralPath $RecordPath -PathType Leaf)) {
        throw 'Business configuration is missing. Review STARTUP.md; initialization or migration requires confirmation.'
    }
    $candidate = Get-Item -LiteralPath $RecordPath -Force
    if ($candidate.Length -gt 65536) { throw 'Business configuration size is invalid.' }
    while ($candidate) {
        if ($candidate.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Business configuration path is redirected; loading refused.'
        }
        $candidate = if ($candidate -is [IO.FileInfo]) { $candidate.Directory } else { $candidate.Parent }
    }
    $configuration = $null
    $password = $null
    try {
        $acl = Get-Acl -LiteralPath $RecordPath
        $allowed = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18')
        if (-not $acl.AreAccessRulesProtected) { throw 'unprotected' }
        foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
            if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
                $rule.IdentityReference.Value -notin $allowed) { throw 'unprotected' }
        }
        $configuration = Import-Clixml -LiteralPath $RecordPath
        $expected = @{
            Provider = 'postgresql'; Host = '127.0.0.1'; Port = 55433
            Database = 'assistant_app'; Username = 'citerag_app'; Container = 'citerag-rag-postgres'
        }
        foreach ($name in $expected.Keys) {
            if ($configuration.$name -ne $expected[$name]) { throw 'incompatible' }
        }
        if ($configuration.Credential -isnot [Management.Automation.PSCredential] -or
            $configuration.Credential.UserName -ne 'citerag_app') { throw 'incompatible' }
        $password = $configuration.Credential.GetNetworkCredential().Password
        if (-not $password) { throw 'incompatible' }
        return ('postgresql+asyncpg://citerag_app:{0}@127.0.0.1:55433/assistant_app' -f [uri]::EscapeDataString($password))
    } catch {
        throw 'Controlled business configuration could not be loaded. No credential or connection content was printed.'
    } finally {
        $configuration = $null
        $password = $null
    }
}
