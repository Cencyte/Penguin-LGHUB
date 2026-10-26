# Runs only inside the disposable Windows guest. No activation keys are collected.
$ErrorActionPreference = 'Stop'
$share = '\\host.lan\Data'
for ($i = 0; $i -lt 120 -and !(Test-Path $share); $i++) { Start-Sleep -Seconds 5 }
if (!(Test-Path $share)) { throw 'Dockur shared folder is unavailable' }
$out = Join-Path $share 'collected'
New-Item -ItemType Directory -Path $out -Force | Out-Null
try {
    $lock = Get-Content C:\OEM\lock.json -Raw | ConvertFrom-Json
    $sources = @{}
    foreach ($name in $lock.baseline_sha256.PSObject.Properties.Name) {
        $sources[$name] = Join-Path $env:WINDIR "System32\$name"
    }
    foreach ($name in $lock.fonts) { $sources[$name] = Join-Path $env:WINDIR "Fonts\$name" }
    $files = @()
    foreach ($name in ($sources.Keys | Sort-Object)) {
        $source = $sources[$name]
        if (!(Test-Path -LiteralPath $source)) { throw "Required file missing: $name" }
        $target = Join-Path $out $name
        Copy-Item -LiteralPath $source -Destination $target
        $sig = Get-AuthenticodeSignature -LiteralPath $source
        $file = Get-Item -LiteralPath $source
        $files += [ordered]@{
            name = $name; source = $source; size = $file.Length
            sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLower()
            file_version = $file.VersionInfo.FileVersion
            signature_status = [string]$sig.Status
            signer = $(if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { $null })
        }
    }
    $os = Get-CimInstance Win32_OperatingSystem
    $licenses = @(Get-CimInstance SoftwareLicensingProduct -Filter "ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f'" |
        Where-Object { $_.PartialProductKey } |
        Select-Object Name, Description, LicenseStatus, GracePeriodRemaining, EvaluationEndDate)
    $eula = Join-Path $env:WINDIR 'System32\license.rtf'
    if (Test-Path $eula) { Copy-Item $eula (Join-Path $out 'guest-license.rtf') }
    [ordered]@{
        schema = 1; collected_utc = [DateTime]::UtcNow.ToString('o')
        windows = [ordered]@{ caption = $os.Caption; version = $os.Version; build = $os.BuildNumber; architecture = $os.OSArchitecture }
        licensing = $licenses; files = $files
        notice = 'Private test evidence. Activation, signatures and hashes do not establish permission for use outside Windows.'
    } | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $out 'guest-manifest.json') -Encoding UTF8
    # The host treats this final marker as an atomic completion signal.
    Set-Content (Join-Path $share 'collection.complete') 'complete' -Encoding ASCII
} catch {
    $_ | Out-String | Set-Content (Join-Path $share 'collection.error') -Encoding UTF8
    exit 1
}
