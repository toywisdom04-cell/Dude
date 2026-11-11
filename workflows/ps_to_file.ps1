param([string]$Command)
$outputFile = [System.IO.Path]::GetTempFileName() + '.txt'
Invoke-Expression $Command *>&1 | Out-File -Encoding utf8 $outputFile
Write-Output $outputFile
