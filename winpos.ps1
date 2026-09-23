Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
  public static RECT Get(IntPtr h){ RECT r; GetWindowRect(h, out r); return r; }
}
'@
Get-Process powershell -ErrorAction SilentlyContinue | ForEach-Object {
  if($_.MainWindowTitle -eq "Administrator: Windows PowerShell" -and $_.MainWindowHandle -ne [IntPtr]::Zero){
    $r = [W]::Get($_.MainWindowHandle)
    [PSCustomObject]@{PID=$_.Id; L=$r.L; T=$r.T; R=$r.R; B=$r.B; W=($r.R-$r.L); H=($r.B-$r.T)}
  }
}