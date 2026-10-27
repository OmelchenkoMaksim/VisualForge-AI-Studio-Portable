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
    $references = Get-StudioTrayReferences
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        try {
            Add-Type -TypeDefinition $code -ReferencedAssemblies $references -ErrorAction Stop
            return $true
        } catch {
            Log ('Tray icon compile attempt ' + $attempt + ' failed: ' + $_.Exception.Message)
            if ($attempt -eq 1) { Start-Sleep -Milliseconds 700 }
        }
    }
    return $false
}
function Get-StudioTrayReferences {
    # Windows PowerShell 5.1 (.NET Framework) finds Icon, NotifyIcon and Timer in the two classic assemblies.
    if ($PSVersionTable.PSEdition -ne 'Core') { return @('System.Windows.Forms', 'System.Drawing') }
    # PowerShell 7 (.NET) split them into forwarding facades: Icon lives in System.Drawing.Common, and the WinForms
    # assemblies lean on private ones whose names change between .NET versions (System.Private.Windows.GdiPlus on .NET 10).
    # The launcher EXE starts pwsh when it is installed, so the references are the loaded WinForms/Drawing assemblies
    # and their WinForms/Drawing/ComponentModel dependencies, as this runtime actually ships them.
    $family = '^System\.(Windows\.Forms|Drawing|Private\.Windows|ComponentModel)'
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $pending = [Collections.Generic.Queue[Reflection.AssemblyName]]::new()
    foreach ($type in @([Windows.Forms.NotifyIcon], [Drawing.Icon])) { $pending.Enqueue($type.Assembly.GetName()) }
    while ($pending.Count) {
        $name = $pending.Dequeue()
        if ($name.Name -notmatch $family -or -not $names.Add($name.Name)) { continue }
        try { foreach ($reference in [Reflection.Assembly]::Load($name).GetReferencedAssemblies()) { $pending.Enqueue($reference) } } catch {}
    }
    return @($names)
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
