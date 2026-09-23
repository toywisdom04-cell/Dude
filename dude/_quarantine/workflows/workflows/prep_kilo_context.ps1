$tree = Get-ChildItem -Path 'E:\Dude' -Recurse -Depth 2 | Select-Object -ExpandProperty FullName
$context = @"
=== PROJECT STRUCTURE ===
$($tree -join "`n")

=== INFLUENCE STRATEGY CONSTRAINTS ===
- Preserve existing workflows
- Inspect before modifying
- Verify backups before destructive changes
"@
$context | Set-Clipboard
Write-Host 'Kilo_code context copied to clipboard.'
