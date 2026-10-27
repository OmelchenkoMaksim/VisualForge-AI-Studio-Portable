#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Root,
    [ValidateSet('Plan','Apply','Rollback')][string]$Mode='Plan',
    [string]$BackupDirectory
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$targetRoot=[IO.Path]::GetFullPath($Root).TrimEnd('\')
if (-not (Test-Path -LiteralPath $targetRoot -PathType Container)) { throw 'Target installation does not exist.' }
$manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$allowed=@('App/frontend/app.js','App/frontend/index.html','App/frontend/style.css','App/server/server.py','App/server/job_history.py','Support/Diagnostics/ImageReliabilityValidation.json')
if ($manifest.schema_version -ne 1 -or @($manifest.files).Count -ne $allowed.Count -or @(Compare-Object ($manifest.files.path | Sort-Object) ($allowed | Sort-Object)).Count) { throw 'Unexpected update file list.' }

function Assert-NoReparse([string]$path) {
    $cursor=[IO.Path]::GetFullPath($path)
    while ($cursor -eq $targetRoot -or $cursor.StartsWith($targetRoot+'\',[StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw ('Reparse point is not allowed in update paths: '+$cursor) }
        }
        if ($cursor -eq $targetRoot) { break }
        $cursor=Split-Path -Parent $cursor
    }
}
function Target([string]$relative) {
    if ([IO.Path]::IsPathRooted($relative) -or $relative.Contains(':') -or ($relative -split '[/\\]') -contains '..') { throw 'Unsafe relative path.' }
    $path=[IO.Path]::GetFullPath((Join-Path $targetRoot $relative))
    if (-not $path.StartsWith($targetRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Path escapes target installation.' }
    Assert-NoReparse $path
    return $path
}
function Hash([string]$path) {
    if (Test-Path -LiteralPath $path -PathType Leaf) { return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
    return $null
}
function Assert-Closed {
    $active=@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ([string]$_.ExecutablePath).StartsWith($targetRoot+'\',[StringComparison]::OrdinalIgnoreCase) })
    if ($active.Count) { throw 'Close VisualForge and its owned services before applying or rolling back. No process was terminated.' }
}
function Write-Json([string]$path,$value) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $path)) | Out-Null
    [IO.File]::WriteAllText($path,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
}
function Restore-Receipt($receipt,[string]$backup,[switch]$Partial) {
    if ($receipt.schema_version -ne 1 -or $receipt.root -ine $targetRoot -or $receipt.release_version -ne $manifest.release_version) { throw 'Backup does not belong to this installation/update.' }
    $expectedPaths=@($allowed)+@('Support/Diagnostics/InstalledRelease.json')
    if (@($receipt.files).Count -ne $expectedPaths.Count -or @(Compare-Object ($receipt.files.path | Sort-Object) ($expectedPaths | Sort-Object)).Count) { throw 'Unexpected backup file list.' }
    foreach ($row in $receipt.files) {
        $current=Hash (Target $row.path)
        if ($current -ne $row.new_sha256 -and (-not $Partial -or $current -ne $row.old_sha256)) { throw ('Modified file prevents safe rollback: '+$row.path) }
        if ($row.existed -and (Hash (Join-Path $backup ('files/'+$row.path))) -ne $row.old_sha256) { throw ('Backup hash mismatch: '+$row.path) }
    }
    foreach ($row in $receipt.files) {
        $destination=Target $row.path
        if ($row.existed) {
            Copy-Item -LiteralPath (Join-Path $backup ('files/'+$row.path)) -Destination $destination -Force
        } elseif (Test-Path -LiteralPath $destination -PathType Leaf) {
            Remove-Item -LiteralPath $destination -Force
        }
        if ((Hash $destination) -ne $row.old_sha256) { throw ('Rollback verification failed: '+$row.path) }
    }
}

if ($Mode -eq 'Rollback') {
    Assert-Closed
    if (-not $BackupDirectory -or -not [IO.Path]::IsPathRooted($BackupDirectory)) { throw 'Rollback requires an absolute BackupDirectory.' }
    $backup=[IO.Path]::GetFullPath($BackupDirectory).TrimEnd('\')
    $backupBase=Target 'Support/Backups'
    if (-not $backup.StartsWith($backupBase+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'BackupDirectory must be inside this installation Support/Backups.' }
    Assert-NoReparse $backup
    $receipt=Get-Content -LiteralPath (Join-Path $backup 'receipt.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Restore-Receipt $receipt $backup
    Write-Output ([pscustomobject]@{status='rolled_back';root=$targetRoot;backup=$backup} | ConvertTo-Json)
    return
}

$installedPath=Target 'Support/Diagnostics/InstalledRelease.json'
if ((Hash $installedPath) -ne $manifest.base_installed_manifest_sha256) { throw 'Installed release metadata differs from the tested baseline. Nothing changed.' }
$installed=Get-Content -LiteralPath $installedPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($installed.release_version -ne $manifest.base_release_version) { throw 'Wrong base release. Nothing changed.' }
foreach ($row in $manifest.files) {
    if ((Hash (Target $row.path)) -ne $row.old_sha256) { throw ('Newer/modified installed file: '+$row.path+'. Nothing changed.') }
    $payload=Join-Path $PSScriptRoot ('payload/'+$row.path)
    if ((Hash $payload) -ne $row.sha256) { throw ('Payload hash mismatch: '+$row.path+'. Nothing changed.') }
}
if ($Mode -eq 'Plan') {
    Write-Output ([pscustomobject]@{status='verified_plan';root=$targetRoot;release=$manifest.release_version;files=@($manifest.files.path);runtime_modified=$false;models_modified=$false;userdata_modified=$false} | ConvertTo-Json -Depth 5)
    return
}
Assert-Closed
$backup=Target ('Support/Backups/'+$manifest.release_version+'-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8))
$rows=@()
foreach ($row in $manifest.files) { $rows+=@([pscustomobject]@{path=$row.path;existed=($null -ne $row.old_sha256);old_sha256=$row.old_sha256;new_sha256=$row.sha256}) }
$oldValidation=$installed.validation_reports
$installed.release_version=$manifest.release_version
$installed.git_commit=$manifest.source_commit
$installed.status='installed_files_verified_startup_pending'
$installed.validation_reports=@('ImageReliabilityValidation.json')
$installed.PSObject.Properties.Remove('production_approval_required')
$installed | Add-Member -NotePropertyName previous_validation_reports -NotePropertyValue $oldValidation -Force
$installed | Add-Member -NotePropertyName baseline_installed_git_commit -NotePropertyValue $manifest.base_installed_commit -Force
$installed | Add-Member -NotePropertyName restored_source_history -NotePropertyValue $true -Force
$updatedFiles=@($installed.files)
foreach ($row in $manifest.files) {
    $match=@($updatedFiles | Where-Object {$_.path -eq $row.path})
    if ($match.Count) { $match[0].sha256=$row.sha256; $match[0].size=$row.size }
    else { $updatedFiles+=@([pscustomobject]@{path=$row.path;size=$row.size;sha256=$row.sha256}) }
}
$installed.files=$updatedFiles
$newMetadata=($installed | ConvertTo-Json -Depth 40)
$bytes=[Text.UTF8Encoding]::new($false).GetBytes($newMetadata)
$sha=[Security.Cryptography.SHA256]::Create()
try { $newMetadataHash=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
$rows+=@([pscustomobject]@{path='Support/Diagnostics/InstalledRelease.json';existed=$true;old_sha256=(Hash $installedPath);new_sha256=$newMetadataHash})
$receipt=[pscustomobject]@{schema_version=1;root=$targetRoot;release_version=$manifest.release_version;created_at=(Get-Date -Format o);files=$rows}
foreach ($row in $rows) {
    if ($row.existed) {
        $destination=Join-Path $backup ('files/'+$row.path)
        [IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
        Copy-Item -LiteralPath (Target $row.path) -Destination $destination
        if ((Hash $destination) -ne $row.old_sha256) { throw 'Backup verification failed; installation has not started.' }
    }
}
Write-Json (Join-Path $backup 'receipt.json') $receipt
try {
    foreach ($row in $manifest.files) {
        $destination=Target $row.path
        [IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot ('payload/'+$row.path)) -Destination $destination -Force
        if ((Hash $destination) -ne $row.sha256) { throw ('Installed hash mismatch: '+$row.path) }
    }
    [IO.File]::WriteAllBytes($installedPath,$bytes)
    if ((Hash $installedPath) -ne $newMetadataHash) { throw 'Installed metadata hash mismatch.' }
} catch {
    $failure=$_.Exception.Message
    Restore-Receipt $receipt $backup -Partial
    throw ('Update failed and prior files were restored: '+$failure)
}
Write-Output ([pscustomobject]@{status='installed_files_verified_startup_pending';root=$targetRoot;release=$manifest.release_version;backup=$backup} | ConvertTo-Json)
