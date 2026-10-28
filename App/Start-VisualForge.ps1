#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Studio','Single','Reference','Prompt','Cleanup')][string]$Mode = 'Studio',
    [ValidateRange(1024,65535)][int]$ComfyPort = 8188,
    [ValidateRange(1024,65535)][int]$UiPort = 8189
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# One portable layout document is shared with the EXE and Python resolver.
$portableBase = Split-Path -Parent $PSScriptRoot
$layoutFile = Join-Path $portableBase 'App\config\portable-layout.json'
$layoutPaths = @{}
if (Test-Path -LiteralPath $layoutFile -PathType Leaf) {
    $layoutDocument = Get-Content -LiteralPath $layoutFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($layoutDocument.schema_version -ne 1) { throw 'Unsupported portable layout.' }
    foreach ($entry in $layoutDocument.paths.PSObject.Properties) {
        $relativeValue = [string]$entry.Value
        if ([IO.Path]::IsPathRooted($relativeValue) -or $relativeValue.Contains(':')) { throw 'Absolute layout entry rejected.' }
        $resolvedValue = [IO.Path]::GetFullPath((Join-Path $portableBase $relativeValue))
        if (-not $resolvedValue.StartsWith(($portableBase.TrimEnd('\')+'\'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Layout entry escapes portable root.' }
        $layoutPaths[$entry.Name] = $resolvedValue
    }
    $env:VISUALFORGE_PORTABLE_ROOT = $portableBase
    $runtimeConfig = $layoutPaths.ports
} else { $runtimeConfig = Join-Path $PSScriptRoot 'PortableRuntime.settings.json' }
if (Test-Path -LiteralPath $runtimeConfig -PathType Leaf) {
    $runtimePorts = Get-Content -LiteralPath $runtimeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $PSBoundParameters.ContainsKey('ComfyPort')) { $ComfyPort = [int]$runtimePorts.ComfyPort }
    if (-not $PSBoundParameters.ContainsKey('UiPort')) { $UiPort = [int]$runtimePorts.UiPort }
}
if ($ComfyPort -lt 1024 -or $ComfyPort -gt 65535 -or $UiPort -lt 1024 -or $UiPort -gt 65535) { throw 'Invalid portable port settings.' }
if ($ComfyPort -eq $UiPort) { throw 'ComfyUI and UI ports must differ.' }
$backendUrl = 'http://127.0.0.1:' + $ComfyPort
$bridgeUrl = 'http://127.0.0.1:' + $UiPort
$env:QWEN_COMFY_PORT = [string]$ComfyPort
$env:QWEN_UI_PORT = [string]$UiPort
if ($layoutPaths.Count -gt 0) {
    $root = $layoutPaths.state
    $app = $layoutPaths.server
    $comfy = $layoutPaths.comfy
    $python = $layoutPaths.python
    $logDir = $layoutPaths.logs
    $profileBase = $layoutPaths.profiles
    foreach ($key in @('state','logs','profiles','results','settings','prompts','cache','comfy_input','comfy_output','comfy_temp','comfy_user')) {
        New-Item -ItemType Directory -Path $layoutPaths[$key] -Force | Out-Null
    }
    $env:HF_HOME = Join-Path $layoutPaths.cache 'HuggingFace'
    $env:TORCH_HOME = Join-Path $layoutPaths.cache 'Torch'
    $env:TORCHINDUCTOR_CACHE_DIR = Join-Path $layoutPaths.cache 'TorchInductor'
    $env:TRITON_CACHE_DIR = Join-Path $layoutPaths.cache 'Triton'
    $env:CUDA_CACHE_PATH = Join-Path $layoutPaths.cache 'Cuda'
    $env:HF_HUB_OFFLINE = '1'
    $env:TRANSFORMERS_OFFLINE = '1'
} else {
    $root = $PSScriptRoot
    $app = Join-Path $root 'QwenDesktopApp-v3'
    $comfy = Join-Path $root 'ComfyUI_windows_portable\ComfyUI'
    $python = Join-Path $root 'ComfyUI_windows_portable\python_embeded\python.exe'
    $logDir = Join-Path $root 'logs'
    $profileBase = Join-Path $root 'browser-profiles'
}
$main = Join-Path $comfy 'main.py'
$service = Join-Path $app 'server.py'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$runLog = Join-Path $logDir ('desktop-v3-' + $Mode + '-' + (Get-Date -Format 'yyyy-MM-dd_HHmmss_fff') + '.txt')
$serverOwner = Join-Path $root 'DesktopApp-ServerOwner-v3.json'
$uiOwner = Join-Path $root 'DesktopApp-UIOwner-v3.json'
$profileDir = Join-Path $profileBase 'qwen-studio-v3'
$mutex = [System.Threading.Mutex]::new($false, 'Local\QwenImageStudioPortableV3_Lifecycle')
$hasLock = $false
$startedBrowser = $false
$shutdownStarted = ($Mode -eq 'Cleanup')
$ownedServerThisRun = $false
$ownedUiThisRun = $false
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Log([string]$msg) {
    [IO.File]::AppendAllText($runLog, ('['+(Get-Date -Format 'HH:mm:ss')+'] '+$msg+"`r`n"), $utf8)
}
# ---- launch splash: a spinning ring with the icon, above all windows, from the click until the page reports ready -----------------
# It runs in its own STA runspace, so the animation keeps turning while this script waits for ComfyUI and the bridge. It never takes
# focus and never blocks startup: any failure only skips it. Close-Splash is safe to call at any time and more than once.
$splashSync = $null
$splashShell = $null
$launchToken = [Guid]::NewGuid().ToString('N')
$splashScript = {
    param($sync, $iconPath)
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
    $xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="VisualForge AI Studio" Width="100" Height="100" WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        Topmost="True" ShowInTaskbar="False" ShowActivated="False" ResizeMode="NoResize" WindowStartupLocation="CenterScreen">
  <Grid>
    <Ellipse Fill="#FF161A22"/>
    <Ellipse Margin="6" Stroke="#30FFFFFF" StrokeThickness="5"/>
    <Ellipse Margin="6" StrokeThickness="5" StrokeDashArray="13 40" StrokeDashCap="Round" RenderTransformOrigin="0.5,0.5">
      <Ellipse.Stroke>
        <LinearGradientBrush StartPoint="0,0" EndPoint="1,1"><GradientStop Color="#FF7C5CFF" Offset="0"/><GradientStop Color="#FF2EC5FF" Offset="1"/></LinearGradientBrush>
      </Ellipse.Stroke>
      <Ellipse.RenderTransform><RotateTransform x:Name="Turn"/></Ellipse.RenderTransform>
      <Ellipse.Triggers>
        <EventTrigger RoutedEvent="Loaded">
          <BeginStoryboard><Storyboard><DoubleAnimation Storyboard.TargetName="Turn" Storyboard.TargetProperty="Angle" From="0" To="360" Duration="0:0:1.1" RepeatBehavior="Forever"/></Storyboard></BeginStoryboard>
        </EventTrigger>
      </Ellipse.Triggers>
    </Ellipse>
    <Image x:Name="Icon" Width="50" Height="50" RenderOptions.BitmapScalingMode="HighQuality"/>
  </Grid>
</Window>
'@
    $window = [Windows.Markup.XamlReader]::Parse($xaml)
    try {
        $bitmap = New-Object Windows.Media.Imaging.BitmapImage
        $bitmap.BeginInit()
        $bitmap.StreamSource = New-Object IO.MemoryStream (,[IO.File]::ReadAllBytes($iconPath))
        $bitmap.CacheOption = [Windows.Media.Imaging.BitmapCacheOption]::OnLoad
        $bitmap.EndInit()
        $window.FindName('Icon').Source = $bitmap
    } catch {}
    $started = [DateTime]::UtcNow
    $timer = New-Object Windows.Threading.DispatcherTimer
    $timer.Interval = [TimeSpan]::FromMilliseconds(150)
    $timer.Add_Tick({ if ($sync.Close -or ([DateTime]::UtcNow - $started).TotalMinutes -gt 6) { $timer.Stop(); $window.Close() } }.GetNewClosure())
    $timer.Start()
    $sync.Shown = $true
    [void]$window.ShowDialog()
}
function Start-Splash {
    try {
        $script:splashSync = [hashtable]::Synchronized(@{Close = $false; Shown = $false})
        $runspace = [RunspaceFactory]::CreateRunspace()
        $runspace.ApartmentState = 'STA'
        $runspace.ThreadOptions = 'ReuseThread'
        $runspace.Open()
        $script:splashShell = [PowerShell]::Create()
        $script:splashShell.Runspace = $runspace
        [void]$script:splashShell.AddScript($splashScript).AddArgument($script:splashSync).AddArgument((Join-Path $PSScriptRoot 'frontend\visualforge-icon.png'))
        [void]$script:splashShell.BeginInvoke()
    } catch { $script:splashSync = $null; Log ('Splash skipped: '+$_.Exception.Message) }
}
function Close-Splash {
    if ($null -ne $script:splashSync -and -not $script:splashSync.Close) { $script:splashSync.Close = $true; Log 'Splash closed.' }
}
. (Join-Path $PSScriptRoot 'StudioTray.ps1')
function Test-LaunchReady {
    $response = Get-Health ($bridgeUrl+'/api/launch-ready?launch='+$launchToken)
    if (-not $response) { return $false }
    try { return (($response | ConvertFrom-Json).ready -eq $true) } catch { return $false }
}
function Wait-LaunchReady([int]$seconds) {
    # The page reports once its ComfyUI check is done (ready to start a generation, or showing why not). A closed window ends the wait.
    $deadline = [DateTime]::UtcNow.AddSeconds($seconds)
    $windowCheck = [DateTime]::UtcNow.AddSeconds(2)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($null -ne $script:traySync -and $script:traySync.Quit) { return }
        if (Test-LaunchReady) { Log 'Page reported ready.'; return }
        if ([DateTime]::UtcNow -gt $windowCheck) {
            if ((Get-ProfileWindows $profileDir) -eq 0) { Log 'Window gone before the page reported ready.'; return }
            $windowCheck = [DateTime]::UtcNow.AddSeconds(2)
        }
        Start-Sleep -Milliseconds 250
    }
    Log 'Page did not report ready in time; splash closed anyway.'
}
function Lock-Lifecycle {
    try { $script:hasLock = $mutex.WaitOne([TimeSpan]::FromMinutes(3)) }
    catch [System.Threading.AbandonedMutexException] { $script:hasLock = $true }
    if (-not $script:hasLock) { throw 'Other VisualForge app is starting/stopping; mutex timeout.' }
}
function Unlock-Lifecycle {
    if ($script:hasLock) { $mutex.ReleaseMutex();$script:hasLock=$false }
}
function Get-Health([string]$url) {
    try {
        $req = [System.Net.HttpWebRequest]::Create($url)
        $req.Proxy=$null; $req.Timeout=1700
        $resp = $req.GetResponse()
        try { if ([int]$resp.StatusCode -ne 200) { return $null };return ([System.IO.StreamReader]::new($resp.GetResponseStream())).ReadToEnd() }
        finally { $resp.Dispose() }
    } catch { return $null }
}
function Comfy-Ready { return ($null -ne (Get-Health ($backendUrl+'/system_stats'))) }
function UI-Ready {
    $response = Get-Health ($bridgeUrl+'/health')
    if (-not $response) { return $false }
    try { return (($response | ConvertFrom-Json).app -eq 'QwenDesktopApp-v3') } catch { return $false }
}
function Wait-Healthy([scriptblock]$check, [int]$seconds, [object]$process) {
    $deadline = [DateTime]::UtcNow.AddSeconds($seconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (& $check) { return }
        if ($process.HasExited) { throw ('Process exited (code '+$process.ExitCode+'). Check stderr log.') }
        Start-Sleep -Milliseconds 600
    }
    throw 'Startup timeout. Check stdout/stderr logs.'
}
function Get-BrowserExecutable {
    foreach ($base in @($env:ProgramFiles,[Environment]::GetEnvironmentVariable('ProgramFiles(x86)'))) {
        if (-not $base) { continue }
        foreach ($p in @('Microsoft\Edge\Application\msedge.exe','Google\Chrome\Application\chrome.exe')) {
            $candidate=Join-Path $base $p
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {return $candidate}
        }
    }
    foreach ($name in @('msedge.exe','chrome.exe')) {
        $c=Get-Command $name -ErrorAction SilentlyContinue
        if ($c -and $c.Source -and (Test-Path -LiteralPath $c.Source -PathType Leaf)) {return $c.Source}
    }
    throw 'Microsoft Edge/Chrome not found.'
}
function Get-ProfileWindows([string]$profile) {
    $n=0
    foreach ($browserName in @('msedge.exe','chrome.exe')) {
        $needle=[Regex]::Escape($profile)
        try {
            foreach ($wmi in @(Get-CimInstance Win32_Process -Filter ("Name = '"+$browserName+"'") -ErrorAction Stop)) {
                if (-not $wmi.CommandLine -or $wmi.CommandLine -notmatch $needle) {continue}
                try {
                    $p=Get-Process -Id ([int]$wmi.ProcessId) -ErrorAction Stop
                    $p.Refresh()
                    if ($p.MainWindowHandle -ne [IntPtr]::Zero) { $n++ }
                } catch {}
            }
        } catch { throw ('Cannot inspect browser '+$browserName+' processes: '+$_.Exception.Message) }
    }
    return $n
}

# QWEN_UX_BUNDLE_V1: maximize ONLY the isolated Qwen browser app window.
function Maximize-QwenBrowserWindow([string]$profile) {
    $src = @'
using System;
using System.Runtime.InteropServices;
public static class QwenPortableWindow {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
'@
    if (-not ('QwenPortableWindow' -as [type])) { Add-Type -TypeDefinition $src -ErrorAction Stop | Out-Null }
    $needle = [Regex]::Escape($profile)
    foreach ($browserName in @('msedge.exe','chrome.exe')) {
        foreach ($wmi in @(Get-CimInstance Win32_Process -Filter ("Name = '"+$browserName+"'") -ErrorAction Stop)) {
            if (-not $wmi.CommandLine -or $wmi.CommandLine -notmatch $needle) { continue }
            $process = Get-Process -Id ([int]$wmi.ProcessId) -ErrorAction SilentlyContinue
            if ($null -eq $process) { continue }
            $process.Refresh()
            if ($process.MainWindowHandle -ne [IntPtr]::Zero) {
                [void][QwenPortableWindow]::ShowWindow($process.MainWindowHandle, 9)
                [void][QwenPortableWindow]::ShowWindow($process.MainWindowHandle, 3)
                [void][QwenPortableWindow]::SetForegroundWindow($process.MainWindowHandle)
                Log 'Maximized application browser window.'
            }
        }
    }
}

function Any-ManagedWindow {
    foreach ($p in @('qwen-studio-v3','qwen-single-v3','qwen-reference-v3','qwen-prompt-v3')) {
        if ((Get-ProfileWindows (Join-Path $profileBase $p)) -gt 0) {return $true}
    }
    return $false
}
function Write-Owner([string]$file, [object]$process, [string]$scriptPath) {
    $pidNumber=[int]$process.Id
    $cim=Get-CimInstance Win32_Process -Filter ('ProcessId = '+$pidNumber) -ErrorAction Stop
    if (-not $cim) {throw 'Unable to read launched process identity.'}
    $record=@{ProcessId=$pidNumber;CreationUtc=([datetime]$cim.CreationDate).ToUniversalTime().ToString('o');ScriptPath=$scriptPath}
    [IO.File]::WriteAllText($file,($record|ConvertTo-Json),$utf8)
}
function Get-ListenerProcess([int]$port, [string]$expectedScript) {
    # A port number alone is never sufficient authority to stop a process.
    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        throw 'Get-NetTCPConnection unavailable; cannot verify VisualForge listener ownership.'
    }
    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    $otherListeners = @($listeners | Where-Object { $_.LocalAddress -ne '127.0.0.1' })
    if ($otherListeners.Count -gt 0) {
        throw ('Unexpected non-loopback listener on port ' + $port + '; no process terminated.')
    }
    $localListeners = @($listeners | Where-Object { $_.LocalAddress -eq '127.0.0.1' })
    if ($localListeners.Count -eq 0) { return $null }
    $listenerIds = @($localListeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($listenerIds.Count -ne 1) { throw ('Ambiguous listener PIDs for ' + $port) }

    $listenerPid = [int]$listenerIds[0]
    $proc = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $listenerPid) -ErrorAction Stop
    if ($null -eq $proc) {
        throw ('Port ' + $port + ' listener exited before ownership check; refusing termination.')
    }

    # Evaluate individual predicates exactly as the independent read-only audit.
    # Do not rely on a complex compound expression whose behavior is hard to diagnose.
    $nameMatches = [string]::Equals([string]$proc.Name, 'python.exe', [StringComparison]::OrdinalIgnoreCase)
    $exeMatches = [string]::Equals([string]$proc.ExecutablePath, [string]$python, [StringComparison]::OrdinalIgnoreCase)
    $quotedScript = '"' + $expectedScript + '"'
    $command = [string]$proc.CommandLine
    $scriptMatches = $command.IndexOf($quotedScript, [StringComparison]::OrdinalIgnoreCase) -ge 0
    $isOwner = $nameMatches -and $exeMatches -and $scriptMatches

    if (-not $isOwner) {
        # No sensitive paths or command-line values in diagnostic logs.
        Log ('Ownership rejected port=' + $port + ' PID=' + $listenerPid +
             ' nameMatch=' + $nameMatches + ' exeMatch=' + $exeMatches +
             ' quotedScriptMatch=' + $scriptMatches)
        throw ('Port ' + $port + ' is not owned by the exact VisualForge embedded Python/script; refusing termination.')
    }
    Log ('Ownership confirmed port=' + $port + ' PID=' + $listenerPid)
    return $proc
}
function Read-OwnerPid([string]$path, [string]$expectedScript) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {return $null}
    try {
        $record = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $record.PSObject.Properties['ProcessId'] -or
            $null -eq $record.PSObject.Properties['ScriptPath'] -or
            -not [string]::Equals([string]$record.ScriptPath,$expectedScript,[StringComparison]::OrdinalIgnoreCase)) {
            Log ('Invalid/stale owner marker: '+$path)
            return $null
        }
        return [int]$record.ProcessId
    } catch {
        Log ('Cannot parse owner marker '+$path+': '+$_.Exception.Message)
        return $null
    }
}
function Get-MarkedProcess([string]$ownerFile,[string]$expectedScript) {
    $markedPid=Read-OwnerPid $ownerFile $expectedScript
    if ($null -eq $markedPid) {return $null}
    $proc=Get-CimInstance Win32_Process -Filter ('ProcessId = '+[int]$markedPid) -ErrorAction SilentlyContinue
    if ($null -eq $proc) {return $null}
    if ($proc.Name -ine 'python.exe' -or -not [string]::Equals([string]$proc.ExecutablePath,$python,[StringComparison]::OrdinalIgnoreCase) -or
        -not $proc.CommandLine -or $proc.CommandLine.IndexOf(('"'+$expectedScript+'"'),[StringComparison]::OrdinalIgnoreCase) -lt 0) {
        Log ('Owner PID '+$markedPid+' no longer refers to the exact VisualForge script; preserving it.')
        return $null
    }
    return $proc
}
function Test-V2-BackendOwner([int]$id) {
    $path=Join-Path $root 'DesktopApp-ServerOwner.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {return $false}
    try {
        $record=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        return ($null -ne $record.PSObject.Properties['ProcessId'] -and [int]$record.ProcessId -eq $id)
    } catch {
        Log ('Could not read v2 owner marker, backend preserved: '+$_.Exception.Message)
        return $true
    }
}
function Test-V2-InUse {
    foreach ($p in @('qwen-single-v2','qwen-reference-v2')) {
        if ((Get-ProfileWindows (Join-Path $profileBase $p)) -gt 0) {return $true}
    }
    return $false
}
function Stop-ExactProcess([object]$process, [string]$description, [switch]$Tree, [switch]$SkipChangedIdentity) {
    $targetPid = [int]$process.ProcessId
    # Recheck the process creation time and command line to reject PID reuse.
    $again = Get-CimInstance Win32_Process -Filter ('ProcessId = '+$targetPid) -ErrorAction Stop
    if (-not $again) { Log ($description+' already exited: PID '+$targetPid); return }
    if (-not [string]::Equals([string]$again.ExecutablePath,[string]$process.ExecutablePath,[StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([string]$again.CommandLine,[string]$process.CommandLine,[StringComparison]::Ordinal) -or
        [math]::Abs((([datetime]$again.CreationDate).ToUniversalTime()-([datetime]$process.CreationDate).ToUniversalTime()).TotalSeconds) -gt 2) {
        if ($SkipChangedIdentity) {
            Log ($description+' identity changed or became incomplete; left untouched: PID '+$targetPid)
            return
        }
        throw ('PID reuse/identity change: '+$targetPid+'. Refusing termination.')
    }
    Log ('FORCE STOP '+$description+' PID '+$targetPid+' (active jobs may be interrupted)')
    if ($Tree) {
        # Only after exact executable, command-line and creation-time checks above.
        $killer = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\taskkill.exe') -ArgumentList @('/PID',[string]$targetPid,'/T','/F') -WindowStyle Hidden -Wait -PassThru
        if ($killer.ExitCode -ne 0 -and (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) { throw ('Could not stop owned process tree: '+$description) }
    } else { Stop-Process -Id $targetPid -Force -ErrorAction Stop }
    for ($i=0; $i -lt 30; $i++) {
        if (-not (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {return}
        Start-Sleep -Milliseconds 200
    }
    throw ('Process did not exit: '+$description+' PID '+$targetPid)
}
function Test-StudioBrowserCommand([string]$command, [string]$profile) {
    $path = [Regex]::Escape($profile)
    $argument = '(?:--user-data-dir="'+$path+'"|"--user-data-dir='+$path+'")'
    if ($profile -notmatch '\s') { $argument = '(?:'+$argument+'|--user-data-dir='+$path+')' }
    return [Regex]::IsMatch($command, '(?:^|\s)'+$argument+'(?=\s|$)', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
}
function Stop-StudioBrowserWindows {
    foreach ($profileName in @('qwen-studio-v3','qwen-single-v3','qwen-reference-v3','qwen-prompt-v3')) {
        $expectedProfile = Join-Path $profileBase $profileName
        foreach ($browserName in @('msedge.exe','chrome.exe')) {
            foreach ($candidate in @(Get-CimInstance Win32_Process -Filter ("Name = '"+$browserName+"'") -ErrorAction Stop)) {
                # Match the complete dedicated user-data-dir argument, never a loose path substring.
                $command = [string]$candidate.CommandLine
                if (-not (Test-StudioBrowserCommand $command $expectedProfile)) { continue }
                if (-not [string]::Equals([string]$candidate.ExecutablePath,$browser,[StringComparison]::OrdinalIgnoreCase)) { continue }
                # Stopping a browser tree invalidates pre-enumerated child snapshots.
                # Skip stale entries without ever terminating a replacement process.
                Stop-ExactProcess $candidate 'VisualForge isolated browser' -Tree -SkipChangedIdentity
            }
        }
    }
}
function Remove-StaleOwner([string]$path, [string]$expectedScript) {
    $pidInFile = Read-OwnerPid $path $expectedScript
    if ($null -ne $pidInFile -and -not (Get-Process -Id $pidInFile -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $path -Force -ErrorAction Stop
        Log ('Removed stale marker: '+$path)
    }
}
function Stop-Managed([switch]$RecoverOrphan) {
    if (Any-ManagedWindow) {Log 'VisualForge AI Studio window is open. Shared services kept alive.';return}

    # UI: Exact listener/process identity is sufficient; timestamp in JSON is advisory.
    # This repairs the observed stale/mismatched CreationUtc orphan on 8189.
    $uiProcess = Get-ListenerProcess $UiPort $service
    if ($null -eq $uiProcess) {
        $uiProcess = Get-MarkedProcess $uiOwner $service
        if ($null -ne $uiProcess) {Log 'Found marked VisualForge UI process without listener (hung startup/shutdown).'}
    }
    if ($null -ne $uiProcess) {
        Stop-ExactProcess $uiProcess 'VisualForge AI Studio UI' -Tree
        if (Test-Path -LiteralPath $uiOwner -PathType Leaf) {
            Remove-Item -LiteralPath $uiOwner -Force -ErrorAction Stop
        }
    } else {
        Remove-StaleOwner $uiOwner $service
    }

    # Backend: exact executable/main.py AND a v3 ownership marker are mandatory.
    # Do not terminate independently started ComfyUI or one used by v2 windows.
    if (Test-V2-InUse) {Log 'v2 managed window detected; ComfyUI preserved.';return}
    $mainProcess = Get-ListenerProcess $ComfyPort $main
    $ownedPid = Read-OwnerPid $serverOwner $main
    if ($null -eq $mainProcess) {
        $mainProcess = Get-MarkedProcess $serverOwner $main
        if ($null -ne $mainProcess) {Log 'Found marked VisualForge ComfyUI process without listener (hung startup/shutdown).'}
    }
    if ($null -ne $mainProcess -and $null -ne $ownedPid -and $ownedPid -eq [int]$mainProcess.ProcessId) {
        if (Test-V2-BackendOwner ([int]$mainProcess.ProcessId)) {
            Log 'Backend also registered as v2-owned; preserving it.'
        } else {
            Stop-ExactProcess $mainProcess 'VisualForge AI Studio ComfyUI' -Tree
            Remove-Item -LiteralPath $serverOwner -Force -ErrorAction Stop
        }
    } elseif ($null -ne $mainProcess) {
        Log 'ComfyUI running but not proven v3-owned; preserving process.'
    } else {
        Remove-StaleOwner $serverOwner $main
    }
}
try {
    Log ('START v3 mode='+$Mode)
    if ($Mode -ne 'Cleanup') { Start-Splash }
    $workflowBase = $app
    $engineWorkflowBase = Join-Path $app 'workflows'
    if ($layoutPaths.Count -gt 0) { $workflowBase = $layoutPaths.workflows; $engineWorkflowBase = $layoutPaths.workflows }
    foreach ($path in @($python,$main,$service,(Join-Path $workflowBase 'single.api.json'),(Join-Path $workflowBase 'reference.api.json'),(Join-Path $app 'engines\registry.py'),(Join-Path $engineWorkflowBase 'qwen-image-21-uc-fp8\prompt.api.json'))) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {throw ('Missing file: '+$path)}
    }
    if ($Mode -ne 'Cleanup') { $browser=Get-BrowserExecutable }
    Lock-Lifecycle
    try {
        if ($Mode -ne 'Cleanup') {
            $foreignUI = Get-ListenerProcess $UiPort $service
            if ($null -ne $foreignUI) {
                $uiMarkedPid = Read-OwnerPid $uiOwner $service
                if ($null -eq $uiMarkedPid -or [int]$uiMarkedPid -ne [int]$foreignUI.ProcessId) {
                    throw ('Port '+$UiPort+' is not owned by this portable instance. Close the other application first.')
                }
            }
            $foreignComfy = Get-ListenerProcess $ComfyPort $main
            if ($null -ne $foreignComfy) {
                $comfyMarkedPid = Read-OwnerPid $serverOwner $main
                if ($null -eq $comfyMarkedPid -or [int]$comfyMarkedPid -ne [int]$foreignComfy.ProcessId) {
                    throw ('Port '+$ComfyPort+' is not owned by this portable instance. Close the other application first.')
                }
            }
        }
        if ($Mode -eq 'Cleanup') {
            Log 'Explicit cleanup requested (may interrupt active VisualForge AI Studio generation).'
            Stop-Managed -RecoverOrphan
            Log 'CLEANUP finished.'
            Write-Host ('VisualForge AI Studio cleanup completed. Log: '+$runLog)
            return
        }
        if ((Get-ProfileWindows $profileDir) -gt 0) {
            Close-Splash
            Maximize-QwenBrowserWindow $profileDir
            Log 'Existing unified window activated; no new window or services started.'
            return
        }
        # Recover services left behind by an unexpectedly terminated launcher.
        # Never interrupt a still-open v3 window: it may be sharing these services.
        if (-not (Any-ManagedWindow)) {
            Log 'No managed v3 window detected; recovering verified orphans before launch.'
            Stop-Managed -RecoverOrphan
        }
        if (-not (Comfy-Ready)) {
            Log 'ComfyUI not running. Launching the existing embedded Python.'
            $env:HF_HUB_OFFLINE='1';$env:TRANSFORMERS_OFFLINE='1';$env:HF_DATASETS_OFFLINE='1';$env:HF_HUB_DISABLE_TELEMETRY='1'
            $out=Join-Path $logDir ('comfy-v3-'+$Mode+'.stdout.txt')
            $err=Join-Path $logDir ('comfy-v3-'+$Mode+'.stderr.txt')
            $args=@('-s','-B',('"'+$main+'"'),'--listen','127.0.0.1','--port',[string]$ComfyPort,'--offline','--disable-all-custom-nodes','--disable-auto-launch','--cache-none','--reserve-vram','1')
            if ($layoutPaths.Count -gt 0) {
                $args += @('--extra-model-paths-config',('"'+$layoutPaths.model_config+'"'),'--input-directory',('"'+$layoutPaths.comfy_input+'"'),'--output-directory',('"'+$layoutPaths.comfy_output+'"'),'--temp-directory',('"'+$layoutPaths.comfy_temp+'"'),'--user-directory',('"'+$layoutPaths.comfy_user+'"'))
            }
            $p=Start-Process -FilePath $python -WorkingDirectory $comfy -ArgumentList $args -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err -ErrorAction Stop
            $ownedServerThisRun=$true
            Write-Owner $serverOwner $p $main
            Wait-Healthy { Comfy-Ready } 150 $p
            Log 'ComfyUI ready.'
        } else {Log 'Existing ComfyUI on 8188 reused. Will not claim ownership.'}
        if (-not (UI-Ready)) {
            if ($null -ne (Get-Health ($bridgeUrl+'/health'))) {throw ('Port '+$UiPort+' is occupied by another server. Not replacing it.')}
            $uiOut=Join-Path $logDir ('ui-v3-'+$Mode+'.stdout.txt')
            $uiErr=Join-Path $logDir ('ui-v3-'+$Mode+'.stderr.txt')
            $uiArgs=@('-s','-B','-u',('"'+$service+'"'))
            $pui=Start-Process -FilePath $python -WorkingDirectory $app -ArgumentList $uiArgs -WindowStyle Hidden -PassThru -RedirectStandardOutput $uiOut -RedirectStandardError $uiErr -ErrorAction Stop
            $ownedUiThisRun=$true
            Write-Owner $uiOwner $pui $service
            Wait-Healthy { UI-Ready } 20 $pui
            Log ('Local UI ready on '+$UiPort+'.')
        } else {Log 'Existing local VisualForge AI Studio UI reused.'}
        # Keep start/stop mutex while opening the window, preventing a closing app
        # from stopping services between health checks and this window becoming visible.
        New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
        $url=$bridgeUrl+'/'
        if ($Mode -in @('Single','Reference','Prompt')) { $url += '?mode='+$Mode.ToLowerInvariant()+'&launch='+$launchToken } else { $url += '?launch='+$launchToken }
        $args=@('--no-first-run','--no-default-browser-check','--disable-background-mode','--disable-background-networking','--disable-sync',('--user-data-dir="'+$profileDir+'"'),('--app="'+$url+'"'),'--start-maximized')
        Log ('Opening '+$url)
        $browserProc=Start-Process -FilePath $browser -ArgumentList $args -PassThru -ErrorAction Stop
        $startedBrowser=$true
        $deadline=[DateTime]::UtcNow.AddSeconds(25)
        while ((Get-ProfileWindows $profileDir) -eq 0) {
            if ([DateTime]::UtcNow -gt $deadline) {throw 'Browser window was not detected.'}
            Start-Sleep -Milliseconds 500
        }
        try { Maximize-QwenBrowserWindow $profileDir } catch { Log ('Maximization skipped: ' + $_.Exception.Message) }
        Log 'Window opened.'
        Start-StudioTray
    } finally {Unlock-Lifecycle}
    Wait-LaunchReady 180
    Close-Splash
    $gone=0
    while ($gone -lt 3) {
        Start-Sleep -Seconds 1
        if ($null -ne $script:traySync) {
            if ($script:traySync.Error) { Log ('Tray error: '+$script:traySync.Error); $script:traySync.Error = '' }
            if ($script:traySync.Quit) { break }
            if ($script:traySync.Open) { $script:traySync.Open = $false; Maximize-QwenBrowserWindow $profileDir }
            $snapshot = Get-Health ($bridgeUrl+'/api/busy')
            # Keep the last known animation state on a transient HTTP failure.
            if ($snapshot) { try { $script:traySync.Busy = [bool](($snapshot | ConvertFrom-Json).busy) } catch {} }
        }
        if ((Get-ProfileWindows $profileDir) -eq 0) {$gone++} else {$gone=0}
    }
    $shutdownStarted = $true
    Log 'Window closed.'
    Lock-Lifecycle
    try {
        if ($null -ne $script:traySync -and $script:traySync.Quit) {
            Log 'Explicit full exit requested from notification area.'
            Stop-StudioBrowserWindows
        }
        Stop-Managed
    } finally {Unlock-Lifecycle}
    Log 'Finished normally.'
} catch {
    Close-Splash
    Log ('ERROR: '+$_.Exception.ToString())
    try {
        Lock-Lifecycle
        try { if (-not $startedBrowser) {Stop-Managed} } finally {Unlock-Lifecycle}
    } catch {Log ('Cleanup failed: '+$_.Exception.Message)}
    $message = if ($shutdownStarted) { 'Не удалось полностью закрыть VisualForge AI Studio. Подробности в журнале: '+$runLog }
               else { 'Не удалось запустить VisualForge AI Studio. Подробности в журнале: '+$runLog }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show($message,'VisualForge AI Studio',[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Error)
    } catch {Write-Host $message -ForegroundColor Red}
    exit 1
} finally {
    Close-Splash
    Close-StudioTray
    Unlock-Lifecycle
    $mutex.Dispose()
}
