$code = @'
using System;
using System.Runtime.InteropServices;
using System.Collections.Generic;
public class ConsoleInject3 {
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool FreeConsole();
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool AttachConsole(uint dwProcessId);
  [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr GetStdHandle(uint nStdHandle);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool WriteConsoleInput(IntPtr h, INPUT_RECORD[] lpBuffer, uint nLength, out uint lpNumberOfEventsWritten);
  [StructLayout(LayoutKind.Sequential)] public struct INPUT_RECORD { public ushort EventType; public KEY_EVENT_RECORD KeyEvent; }
  [StructLayout(LayoutKind.Sequential)] public struct KEY_EVENT_RECORD { public bool bKeyDown; public ushort wRepeatCount; public ushort wVirtualKeyCode; public ushort wVirtualScanCode; public char UnicodeChar; public uint dwControlKeyState; }
  static INPUT_RECORD Mk(bool down, char c){ return new INPUT_RECORD{ EventType=1, KeyEvent=new KEY_EVENT_RECORD{ bKeyDown=down, wRepeatCount=1, UnicodeChar=c } }; }
  public static string Inject(uint pid, string text) {
    FreeConsole();
    if (!AttachConsole(pid)) return "AttachConsole failed: " + Marshal.GetLastWin32Error();
    IntPtr hIn = GetStdHandle(0xFFFFFFF6);
    var list = new List<INPUT_RECORD>();
    foreach (char c in text) { list.Add(Mk(true,c)); list.Add(Mk(false,c)); }
    list.Add(Mk(true,'\r')); list.Add(Mk(false,'\r'));
    INPUT_RECORD[] arr = list.ToArray(); uint written;
    bool ok = WriteConsoleInput(hIn, arr, (uint)arr.Length, out written);
    FreeConsole();
    return "ok=" + ok + " written=" + written + " err=" + Marshal.GetLastWin32Error();
  }
}
'@
Add-Type $code
$p = Get-Process powershell | Where-Object { $_.MainWindowTitle -like "*Administrator*" } | Select-Object -First 1
if (-not $p) { "no admin powershell" | Out-File "E:\Dude\dude\inject_result.txt"; exit }
[ConsoleInject3]::Inject($p.Id, "codex") | Out-File "E:\Dude\dude\inject_result.txt"
