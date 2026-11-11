$code = @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public class ConDump {
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool FreeConsole();
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool AttachConsole(uint pid);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr GetStdHandle(uint n);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetConsoleScreenBufferInfo(IntPtr h, out CONSOLE_SCREEN_BUFFER_INFO i);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool ReadConsoleOutputCharacter(IntPtr h, StringBuilder s, uint len, COORD c, out uint read);
  [StructLayout(LayoutKind.Sequential)] public struct COORD { public short X; public short Y; }
  [StructLayout(LayoutKind.Sequential)] public struct SMALL_RECT { public short L,T,R,B; }
  [StructLayout(LayoutKind.Sequential)] public struct CONSOLE_SCREEN_BUFFER_INFO {
    public COORD Size; public COORD Cursor; public ushort Attrs; public SMALL_RECT Window; public COORD Max;
  }
  public static string Dump(uint pid) {
    FreeConsole();
    if (!AttachConsole(pid)) return "attach fail " + Marshal.GetLastWin32Error();
    IntPtr h = GetStdHandle(0xFFFFFFF5);
    CONSOLE_SCREEN_BUFFER_INFO info; GetConsoleScreenBufferInfo(h, out info);
    int w = info.Window.R - info.Window.L + 1;
    int top = info.Window.T; int bottom = info.Window.B;
    var sb = new StringBuilder();
    for (int y = top; y <= bottom; y++) {
      var line = new StringBuilder(w);
      COORD c = new COORD { X = 0, Y = (short)y };
      uint read; ReadConsoleOutputCharacter(h, line, (uint)w, c, out read);
      sb.AppendLine(line.ToString().TrimEnd());
    }
    FreeConsole();
    return sb.ToString();
  }
}
'@
Add-Type $code
$p = Get-Process powershell | Where-Object { $_.MainWindowTitle -like "*Administrator*" } | Select-Object -First 1
[ConDump]::Dump($p.Id) | Out-File "E:\Dude\dude\console_dump.txt"
