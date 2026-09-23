param([string]$PathA = 'C:\dude', [string]$PathB = 'C:\duode')
Write-Host "Comparing $PathA vs $PathB" -ForegroundColor Cyan
Compare-Object (Get-ChildItem $PathA -Recurse -File) (Get-ChildItem $PathB -Recurse -File) -Property Name, Length | Format-Table -AutoSize
