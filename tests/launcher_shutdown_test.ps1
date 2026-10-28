param([Parameter(Mandatory=$true)][string]$Source)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Load only function definitions, never the supervisor's launch/cleanup body.
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$null, [ref]$parseErrors)
if ($parseErrors) { throw 'Supervisor source does not parse.' }
foreach ($name in @('Stop-ExactProcess','Test-StudioBrowserCommand','Stop-StudioBrowserWindows')) {
    $definition = $ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name}, $false)
    if (@($definition).Count -ne 1) { throw ('Missing function: '+$name) }
    Invoke-Expression $definition.Extent.Text
}

function Assert([bool]$condition, [string]$message) { if (-not $condition) { throw $message } }
function Identity([int]$number, [string]$exe, [string]$command, [datetime]$created) {
    return [pscustomobject]@{ProcessId=$number;ExecutablePath=$exe;CommandLine=$command;CreationDate=$created}
}
$browser = 'C:\fixture\msedge.exe'
$profileBase = 'C:\fixture\browser profiles'
$profile = Join-Path $profileBase 'qwen-studio-v3'
$command = '"'+$browser+'" --user-data-dir="'+$profile+'" --app=http://127.0.0.1:18189'
$created = [datetime]'2026-10-28T00:00:00Z'
$script:snapshot = @(101..108 | ForEach-Object {Identity $_ $browser $command $created})
$script:snapshot[5] = Identity 106 $browser ($command.Replace($profile, $profile+'-foreign')) $created
$script:snapshot[6] = Identity 107 'C:\foreign\msedge.exe' $command $created
$script:live = @{}
foreach ($record in $script:snapshot) { $script:live[[int]$record.ProcessId] = $record }
$script:live[103] = Identity 103 '' '' $created
$script:live[104] = Identity 104 'C:\foreign\other.exe' 'foreign' $created.AddMinutes(1)
$script:live[105] = Identity 105 $browser $command $created.AddSeconds(10)
$script:killed = [System.Collections.Generic.List[int]]::new()
$script:logs = [System.Collections.Generic.List[string]]::new()
function Log([string]$message) { $script:logs.Add($message) }

# Every operating-system process operation is replaced. Any unexpected call fails closed.
function Get-CimInstance([string]$ClassName, [string]$Filter, [string]$ErrorAction) {
    Assert ($ClassName -eq 'Win32_Process') 'Unexpected CIM class.'
    if ($Filter -eq "Name = 'msedge.exe'") { return $script:snapshot }
    if ($Filter -eq "Name = 'chrome.exe'") { return @() }
    if ($Filter -match '^ProcessId = (\d+)$') { return $script:live[[int]$Matches[1]] }
    throw ('Unexpected process query: '+$Filter)
}
function Get-Process([int]$Id, [string]$ErrorAction) {
    if ($script:live.ContainsKey($Id)) { return [pscustomobject]@{Id=$Id} }
    return $null
}
function Start-Process([string]$FilePath, [string[]]$ArgumentList, [string]$WindowStyle, [switch]$Wait, [switch]$PassThru) {
    Assert ($FilePath -eq (Join-Path $env:SystemRoot 'System32\taskkill.exe')) 'Unexpected executable.'
    Assert (($ArgumentList.Count -eq 4) -and $ArgumentList[0] -eq '/PID' -and $ArgumentList[2] -eq '/T' -and $ArgumentList[3] -eq '/F') 'Termination must use an exact PID.'
    Assert ($WindowStyle -eq 'Hidden') 'Taskkill must stay hidden.'
    $number = [int]$ArgumentList[1]
    $script:killed.Add($number)
    $script:live.Remove($number)
    if ($number -eq 101) { $script:live.Remove(102) } # Parent exit invalidates a later child snapshot.
    return [pscustomobject]@{ExitCode=0}
}
function Stop-Process([int]$Id, [switch]$Force, [string]$ErrorAction) {
    $script:killed.Add($Id)
    $script:live.Remove($Id)
}

Stop-StudioBrowserWindows
Assert (($script:killed -join ',') -eq '101,108') 'Only the two unchanged dedicated browser identities may be terminated.'
foreach ($number in @(103,104,105,106,107)) {
    Assert ($script:live.ContainsKey($number)) ('Foreign/incomplete/reused identity was touched: '+$number)
}
Assert (($script:logs -join '|') -match 'already exited: PID 102') 'Exited child should be harmless.'
Assert (($script:logs -join '|') -match 'left untouched: PID 103') 'Incomplete identity must be logged and skipped.'
Assert (($script:logs -join '|') -match 'left untouched: PID 104') 'PID reuse must be logged and skipped.'
Assert (($script:logs -join '|') -match 'left untouched: PID 105') 'Changed creation time must be skipped.'

# Backend callers retain strict failure semantics; skipping is opt-in for browser snapshots only.
$refused = $false
try { Stop-ExactProcess $script:snapshot[2] 'strict backend' -Tree }
catch { $refused = $_.Exception.Message -like '*PID reuse/identity change*' }
Assert $refused 'A strict identity mismatch must still throw.'
Assert (($script:killed -join ',') -eq '101,108') 'A strict mismatch must not terminate anything.'

$stable = Identity 109 'C:\fixture\python.exe' 'owned fixture' $created
$script:live[109] = $stable
Stop-ExactProcess $stable 'strict stable process'
Assert (($script:killed -join ',') -eq '101,108,109') 'An unchanged strict identity must still stop normally.'
Write-Output 'launcher shutdown ok: exited children, incomplete identities, PID reuse, exact profile, strict backend'
