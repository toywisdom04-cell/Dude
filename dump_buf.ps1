param([int]$procid=7688)
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public class Con {
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool FreeConsole();
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool AttachConsole(int dwProcessId);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr GetStdHandle(int nStdHandle);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetConsoleScreenBufferInfo(IntPtr h, out CONSOLE_SCREEN_BUFFER_INFO i);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool ReadConsoleOutputCharacter(IntPtr h, StringBuilder s, int n, COORD c, out int r);
  public const int STD_OUTPUT_HANDLE = -11;
  [StructLayout(LayoutKind.Sequential)] public struct COORD { public short X; public short Y; }
  [StructLayout(LayoutKind.Sequential)] public struct SMALL_RECT { public short Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct CONSOLE_SCREEN_BUFFER_INFO {
    public COORD dwSize; public COORD dwCursorPosition; public int wAttributes;
    public SMALL_RECT srWindow; public COORD dwMaximumWindowSize;
  }
  public static string Dump(int pid) {
    FreeConsole();
    if(!AttachConsole(pid)) return "ATTACH_FAILED";
    IntPtr h = GetStdHandle(STD_OUTPUT_HANDLE);
    CONSOLE_SCREEN_BUFFER_INFO info;
    if(!GetConsoleScreenBufferInfo(h, out info)) return "INFO_FAILED";
    int w = info.dwSize.X; int hgt = info.dwSize.Y;
    var sb = new StringBuilder();
    for(int y=0; y<hgt; y++){
      var line = new StringBuilder(w);
      int read;
      if(ReadConsoleOutputCharacter(h, line, w, new COORD(){X=0,Y=(short)y}, out read)){
        sb.Append(line.ToString().Replace("\0"," "));
        sb.Append("\n");
      }
    }
    return sb.ToString();
  }
}
'@
try { [Con]::Dump($procid) } catch { "ERR: $_" }