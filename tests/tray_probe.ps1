param([string]$Root, [string]$Scratch)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
# Compile through the supervisor's own helper, so the probe exercises the launch path rather than a separate Add-Type.
function Log([string]$message) { [Console]::Error.WriteLine($message) }
. (Join-Path $Root 'App\StudioTray.ps1')
if (-not (Import-StudioTrayType)) {throw 'Tray type did not compile'}
$state=[hashtable]::Synchronized(@{Close=$false;Busy=$false;Open=$false;Quit=$false;Shown=$false})
$icon=Join-Path $Root 'App\frontend\visualforge-icon.png'
$hostTray=New-Object VisualForge.StudioTray($state,$icon)
try {
    if (-not $state.Shown -or $hostTray.Menu.Items.Count -ne 2) {throw 'Tray/menu not ready'}
    if ($hostTray.Menu.Items[0].Text -ne 'Открыть приложение на весь экран' -or $hostTray.Menu.Items[1].Text -ne 'Закрыть приложение полностью') {throw 'Menu labels differ'}
    $hostTray.Menu.Items[0].PerformClick()
    $hostTray.Menu.Items[1].PerformClick()
    if (-not $state.Open -or -not $state.Quit) {throw 'Menu signals not delivered'}
    $state.Busy=$true
    $hostTray.Update(); $a=$hostTray.Frame
    $hostTray.Update(); $b=$hostTray.Frame
    if ($a -eq $b) {throw 'Animation did not advance'}
    $state.Busy=$false
    $hostTray.Update()
    if ($hostTray.Frame -ne 0) {throw 'Idle did not restore logo'}
    $state.Close=$true
    $hostTray.Update()
} finally { $hostTray.Dispose(); $hostTray.Dispose() }
if ($state.Shown) {throw 'Tray remained visible after disposal'}
Start-StudioTray
try {
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    while (-not $script:traySync.Shown -and -not $script:traySync.Error -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    if (-not $script:traySync.Shown -or $script:traySync.Error) {throw ('STA tray failed: '+$script:traySync.Error)}
    $script:traySync.Busy=$true
    Start-Sleep -Milliseconds 250
    $script:traySync.Busy=$false
} finally { Close-StudioTray }
if ($script:traySync.Shown) {throw 'STA tray failed to close'}
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Root 'App\Start-VisualForge.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) {throw 'Supervisor parse failed'}
foreach ($name in @('Stop-ExactProcess','Test-StudioBrowserCommand')) {
    $fn=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    . ([ScriptBlock]::Create($fn.Extent.Text))
}
$profile=Join-Path $Scratch 'Studio Profile'
foreach ($command in @(('edge --user-data-dir="'+$profile+'" --app=x'), ('edge "--user-data-dir='+$profile+'" --app=x'))) {
    if (-not (Test-StudioBrowserCommand $command $profile)) {throw 'Valid profile rejected'}
}
foreach ($command in @(('edge --user-data-dir="'+$profile+' other"'), ('edge --user-data-dir="'+$profile+'"suffix'), ('edge "'+$profile+'"'))) {
    if (Test-StudioBrowserCommand $command $profile) {throw 'Foreign profile matched'}
}
$parentFile=Join-Path $Scratch 'tray-parent.ps1'
$marker=Join-Path $Scratch 'tray-child.txt'
$parentScript=@'
param([string]$Marker)
$p=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList @('-NoProfile','-Command','Start-Sleep -Seconds 60') -PassThru -WindowStyle Hidden
[IO.File]::WriteAllText($Marker,[string]$p.Id)
Start-Sleep -Seconds 60
'@
[IO.File]::WriteAllText($parentFile,$parentScript)
$parent=$null; $unrelated=$null; $childId=0
try {
    $shell=Join-Path $PSHOME 'powershell.exe'
    $unrelated=Start-Process -FilePath $shell -ArgumentList @('-NoProfile','-Command','Start-Sleep -Seconds 60') -PassThru -WindowStyle Hidden
    $parent=Start-Process -FilePath $shell -ArgumentList @('-NoProfile','-File',('"'+$parentFile+'"'),'-Marker',('"'+$marker+'"')) -PassThru -WindowStyle Hidden
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $marker) -and [DateTime]::UtcNow -lt $deadline) {Start-Sleep -Milliseconds 50}
    $childId=[int](Get-Content -LiteralPath $marker)
    $identity=Get-CimInstance Win32_Process -Filter ('ProcessId = '+$parent.Id)
    $wrong=$identity | Select-Object ProcessId,ExecutablePath,CommandLine,CreationDate
    $wrong.CommandLine='wrong identity'
    $rejected=$false
    try {Stop-ExactProcess $wrong 'test identity mismatch' -Tree} catch {$rejected=$true}
    if (-not $rejected -or $parent.HasExited) {throw 'Identity mismatch was not preserved'}
    Stop-ExactProcess $identity 'owned test parent and child' -Tree
    if (Get-Process -Id $childId -ErrorAction SilentlyContinue) {throw 'Owned child survived tree exit'}
    $unrelated.Refresh()
    if ($unrelated.HasExited) {throw 'Unrelated process was stopped'}
} finally {
    foreach ($p in @($parent,$unrelated)) { if ($null -ne $p) {$p.Refresh();if (-not $p.HasExited) {$p.Kill()};$p.Dispose()} }
    if ($childId -gt 0 -and (Get-Process -Id $childId -ErrorAction SilentlyContinue)) {Stop-Process -Id $childId -Force}
}
'Native tray, animation, idle reset, two menu actions, STA shutdown, profile matching, identity rejection and owned tree cleanup passed.'
