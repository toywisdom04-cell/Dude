$wshell = New-Object -ComObject wscript.shell
$ok = $wshell.AppActivate("Administrator: Windows PowerShell")
Start-Sleep -Milliseconds 500
# clear line first
$wshell.SendKeys("{ESC}")
Start-Sleep -Milliseconds 200
$wshell.SendKeys("codex")
Start-Sleep -Milliseconds 300
$wshell.SendKeys("{ENTER}")
Start-Sleep -Seconds 4
# copy the whole buffer to clipboard
$wshell.AppActivate("Administrator: Windows PowerShell") | Out-Null
Start-Sleep -Milliseconds 300
$wshell.SendKeys("^a")
Start-Sleep -Milliseconds 300
$wshell.SendKeys("^c")
Start-Sleep -Milliseconds 500
$clip = Get-Clipboard -Raw
$clip | Out-File -Encoding utf8 E:\Dude\codex_out.txt
"FOCUS_OK=$ok" | Out-File -Append -Encoding utf8 E:\Dude\codex_out.txt