# DUDE Phase C: relocate pagefile to D:, shrink hibernation file
# Run as Administrator. Reboot required for pagefile change.

Write-Output "== Current pagefile settings =="
Get-CimInstance Win32_PageFileSetting | Format-Table Name, InitialSize, MaximumSize
Get-CimInstance Win32_ComputerSystem | Select-Object AutomaticManagedPagefile

Write-Output "== Disabling automatic management =="
Set-CimInstance -Query "SELECT * FROM Win32_ComputerSystem" -Property @{AutomaticManagedPagefile=$false}

Write-Output "== Removing C: pagefile =="
Get-CimInstance Win32_PageFileSetting | Where-Object Name -like "C:*" | Remove-CimInstance

Write-Output "== Creating D: system-managed pagefile =="
New-CimInstance -ClassName Win32_PageFileSetting -Property @{Name="D:\pagefile.sys"; InitialSize=0; MaximumSize=0} | Out-Null

Write-Output "== Shrinking hibernation to 40% =="
powercfg /h /type reduced

Write-Output "== DONE. Reboot required for pagefile move. =="
Get-CimInstance Win32_PageFileSetting | Format-Table Name, InitialSize, MaximumSize
pause
