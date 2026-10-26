# Tray UI owns a separate STA message loop, independent of slow model/server requests.
$script:traySync = $null
$script:trayShell = $null
$script:trayAsync = $null
function Import-StudioTrayType {
    # The type is compiled here, in the supervisor's runspace, before the tray thread starts: the path tests/tray_probe.ps1 verifies.
    # When the tray runspace compiled it itself, launches through the launcher EXE repeatedly logged only the generic
    # "Не удается добавить тип. Произошли ошибки компиляции" (the individual compiler errors went to that runspace's
    # unread error stream), so the cause was never recorded. -ErrorAction Stop turns the first compiler error into the
    # logged message, the source is read as UTF-8 text instead of passing a non-ASCII path to the compiler, and a
    # transient failure gets one retry. A failure never stops the launch: the studio runs without the icon.
    if ('VisualForge.StudioTray' -as [type]) { return $true }
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $code = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'StudioTray.cs'), [Text.Encoding]::UTF8)
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        try {
            Add-Type -TypeDefinition $code -ReferencedAssemblies System.Windows.Forms, System.Drawing -ErrorAction Stop
            return $true
        } catch {
            Log ('Tray icon compile attempt ' + $attempt + ' failed: ' + $_.Exception.Message)
            if ($attempt -eq 1) { Start-Sleep -Milliseconds 700 }
        }
    }
    return $false
}
function Start-StudioTray {
    $ready = $false
    try { $ready = Import-StudioTrayType } catch { Log ('Tray icon unavailable: ' + $_.Exception.Message) }
    if (-not $ready) { Log 'Notification area icon skipped; the studio window works without it.'; return }
    $script:traySync = [hashtable]::Synchronized(@{Close=$false; Quit=$false; Open=$false; Busy=$false; Shown=$false; Error=''})
    $runspace = [RunspaceFactory]::CreateRunspace()
    $runspace.ApartmentState = 'STA'
    $runspace.ThreadOptions = 'ReuseThread'
    $runspace.Open()
    $script:trayShell = [PowerShell]::Create()
    $script:trayShell.Runspace = $runspace
    [void]$script:trayShell.AddScript({
        param($sync, $icon)
        $hostTray = $null
        try {
            # The type was compiled by Import-StudioTrayType; compiled types are shared by every runspace of the process.
            Add-Type -AssemblyName System.Windows.Forms, System.Drawing
            $hostTray = New-Object VisualForge.StudioTray($sync, $icon)
            $hostTray.Run()
        } catch { $sync.Error = $_.Exception.Message }
        finally { if ($null -ne $hostTray) { $hostTray.Dispose() } }
    }).AddArgument($script:traySync).AddArgument((Join-Path $PSScriptRoot 'frontend\visualforge-icon.png'))
    $script:trayAsync = $script:trayShell.BeginInvoke()
    Log 'Notification area host started.'
}
function Close-StudioTray {
    if ($null -eq $script:traySync) { return }
    $script:traySync.Close = $true
    if ($null -ne $script:trayShell) {
        try {
            if ($script:trayAsync.AsyncWaitHandle.WaitOne(3000)) { [void]$script:trayShell.EndInvoke($script:trayAsync) }
            else { $script:trayShell.Stop() }
        } finally {
            $runspace = $script:trayShell.Runspace
            $script:trayShell.Dispose()
            $runspace.Dispose()
            $script:trayShell = $null
        }
    }
}
