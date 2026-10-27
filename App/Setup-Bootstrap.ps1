#Requires -Version 5.1
# Finds (or fetches) a Python to run the component installer, then runs App\Setup-VisualForge.py with the same arguments.
# Nothing is installed system-wide: when no suitable Python exists, the official embeddable Python 3.12 is unpacked into Runtime\SetupPython.
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$SetupArguments)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $PSScriptRoot 'Setup-VisualForge.py'
$embedUrl = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
$embedUrls = @($embedUrl, 'https://python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip')
$embedSha256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
$embedDir = Join-Path $root 'Runtime\SetupPython'

function Test-Python([string]$exe) {
    try {
        $version = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $version) { return $false }
        $parts = ([string]$version).Trim().Split('.')
        return ([int]$parts[0] -gt 3) -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 9)
    } catch { return $false }
}

$python = $null
$bundled = Join-Path $embedDir 'python.exe'
if (Test-Path -LiteralPath $bundled -PathType Leaf) { if (Test-Python $bundled) { $python = $bundled } }
if (-not $python) {
    foreach ($candidate in @('py', 'python', 'python3')) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        # The Microsoft Store stub lives under WindowsApps and only opens the Store; skip it.
        if ($command -and $command.Source -notlike '*\WindowsApps\*' -and (Test-Python $command.Source)) { $python = $command.Source; break }
    }
}
if (-not $python) {
    Write-Host 'No Python 3.9+ found. Fetching the official embeddable Python 3.12 (11 MB) into Runtime\SetupPython ...'
    New-Item -ItemType Directory -Path $embedDir -Force | Out-Null
    $zip = Join-Path $embedDir 'python-embed.zip'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $downloaded = $false
    $downloadError = ''
    foreach ($sourceUrl in $embedUrls) {
        try {
            Invoke-WebRequest -Uri $sourceUrl -OutFile $zip -UseBasicParsing
            $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actual -ne $embedSha256) { throw 'Python download failed its SHA-256 check.' }
            $downloaded = $true
            break
        } catch {
            $downloadError = $_.Exception.Message
            if (Test-Path -LiteralPath $zip -PathType Leaf) { Remove-Item -LiteralPath $zip -Force }
        }
    }
    if (-not $downloaded) {
        $recoveryScript = Join-Path $root 'App\Recovery-Source.ps1'
        $answerText = & powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File $recoveryScript -Name 'python-3.12.10-embed-amd64.zip' -ExpectedHash $embedSha256
        if ($answerText) {
            $answer = $answerText | ConvertFrom-Json
            if ($answer.kind -eq 'file') {Copy-Item -LiteralPath $answer.value -Destination $zip}
            elseif ($answer.kind -eq 'url') {
                $manualUri = [Uri]$answer.value
                if ($manualUri.Scheme -notin @('https','http') -or $manualUri.UserInfo) {throw 'Invalid download URL.'}
                Invoke-WebRequest -Uri $manualUri -OutFile $zip -UseBasicParsing
            }
            if (Test-Path -LiteralPath $zip -PathType Leaf) {
                $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($actual -eq $embedSha256) {$downloaded = $true}
            }
        }
        if (-not $downloaded) {throw ('Python is required to continue setup. Run Setup again to retry. ' + $downloadError)}
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $embedDir -Force
    Remove-Item -LiteralPath $zip -Force
    $python = $bundled
}
$env:PYTHONUTF8 = '1'
$env:VISUALFORGE_PORTABLE_ROOT = $root
& $python $entry @SetupArguments
exit $LASTEXITCODE
