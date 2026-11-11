# Save-PowerShellOutput.ps1
# Captures last PowerShell output to file for reliable reading
param([string]$OutputFile = "E:\\Dude\\dude\\workflows\\ps_output.txt")

# Capture current command output if piped
if ($input) {
    $input | Out-String | Out-File -FilePath $OutputFile -Encoding UTF8
}

Write-Host "Output saved to $OutputFile"
