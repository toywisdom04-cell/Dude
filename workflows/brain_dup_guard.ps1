# brain_dup_guard.ps1 — find duplicate brain files that cause RAM bloat
$root = "E:\Dude\dude"
Get-ChildItem $root -Recurse -File | Where-Object { $_.Name -match "^brain" } | Group-Object Name | Where-Object { $_.Count -gt 1 } | ForEach-Object { $_.Group.FullName }
