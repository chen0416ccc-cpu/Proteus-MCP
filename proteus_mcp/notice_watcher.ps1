param(
    [Parameter(Mandatory=$true)][int]$OwnerPid,
    [Parameter(Mandatory=$true)][string]$Created,
    [Parameter(Mandatory=$true)][string]$ReadyPath,
    [Parameter(Mandatory=$true)][string]$StopPath,
    [Parameter(Mandatory=$true)][string]$ReportPath
)
$ErrorActionPreference = 'Stop'
$result = @{transport='UIAutomation_metadata_and_WM_CLOSE'; desktop_input=$false; notices=@(); errors=@()}
try {
    $identity = Get-CimInstance Win32_Process -Filter "ProcessId=$OwnerPid"
    if (!$identity -or $identity.Name -ne 'PDS.exe' -or $identity.CreationDate.ToUniversalTime().ToString('o') -cne $Created) {
        throw 'NOTICE_WATCHER_PROCESS_IDENTITY_CONFLICT'
    }
    $process = Get-Process -Id $OwnerPid
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class ProteusNoticeWindows {
    private delegate bool EnumProc(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool PostMessageW(IntPtr window, uint message, UIntPtr wp, IntPtr lp);
    public static IntPtr[] OwnedWindows(int pid) {
        var result = new List<IntPtr>();
        EnumWindows((w,d) => { uint owner; GetWindowThreadProcessId(w,out owner); if(owner==pid) result.Add(w); return true; },IntPtr.Zero);
        return result.ToArray();
    }
    public static string Title(IntPtr window) { var text=new StringBuilder(512); GetWindowTextW(window,text,text.Capacity); return text.ToString(); }
}
'@
    $titleZh = 'PCB '+[char]0x5e03+[char]0x5c40
    $expected = "One or more design rule (DRC) errors have been detected.`n`nTo see a list of the errors, click left on the DRC status box at the bottom of the screen, near to the status bar."
    [IO.File]::WriteAllText($ReadyPath,'ready')
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt 45 -and !(Test-Path -LiteralPath $StopPath) -and !$process.HasExited) {
        foreach ($window in [ProteusNoticeWindows]::OwnedWindows($OwnerPid)) {
            if ([ProteusNoticeWindows]::Title($window) -notin @('PCB Layout',$titleZh)) { continue }
            $element = [System.Windows.Automation.AutomationElement]::FromHandle($window)
            if ($element.Current.ClassName -cne 'Appln::MessageBox' -or $element.Current.ProcessId -ne $OwnerPid) { continue }
            $items = @($element.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))
            $text = @($items | Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text} | ForEach-Object {$_.Current.Name.TrimEnd("`r","`n")})
            $buttons = @($items | Where-Object {$_.Current.ClassName -ceq 'QPushButton'} | ForEach-Object {$_.Current.Name})
            if ($text.Count -ne 1 -or $text[0] -cne $expected -or $buttons.Count -ne 1 -or $buttons[0] -cne 'OK') { continue }
            [uint32]$owner = 0
            [void][ProteusNoticeWindows]::GetWindowThreadProcessId($window,[ref]$owner)
            if ($owner -ne $OwnerPid -or $process.HasExited) { throw 'NOTICE_WATCHER_WINDOW_IDENTITY_CONFLICT' }
            $title = $element.Current.Name
            $posted = [ProteusNoticeWindows]::PostMessageW($window,0x10,[UIntPtr]::Zero,[IntPtr]::Zero)
            $result.notices += @{kind='native_drc_errors_detected'; owner_pid=$OwnerPid; handle=$window.ToInt64();
                title=$title; text=$text[0]; buttons=$buttons; normal_close_posted=$posted; settings_changed=$false}
            if (!$posted) { throw 'NOTICE_WATCHER_CLOSE_MESSAGE_FAILED' }
            break
        }
        if ($result.notices.Count) { break }
        Start-Sleep -Milliseconds 100
    }
} catch {
    $result.errors += $_.Exception.Message
} finally {
    [IO.File]::WriteAllText($ReportPath,($result | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
}
