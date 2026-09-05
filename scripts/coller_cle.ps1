# Colle la cle Claude dans .env, la verifie, redemarre le bot.
# La cle est saisie masquee dans CE terminal et n'est jamais affichee.
#
#   double-cliquer scripts\coller_cle.bat
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$envFile = Join-Path $root ".env"
Set-Location $root

Write-Host ""
Write-Host "Cle Claude : creez-la sur https://console.anthropic.com/settings/keys" -ForegroundColor Cyan
Write-Host "(des credits doivent etre charges sur https://console.anthropic.com/settings/billing, sinon chaque appel est refuse)"
Write-Host ""
$sec = Read-Host "Collez la cle (elle ne s'affiche pas) puis Entree" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
$key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
$key = $key.Trim().Trim('"').Trim("'")

if (-not ($key -like "sk-ant-*") -or $key.Length -lt 40) {
  Write-Host "Ce n'est pas une cle Claude (elle commence par sk-ant- et fait plus de 40 caracteres). Rien n'a ete modifie." -ForegroundColor Red
  exit 1
}

if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $root ".env.example") $envFile }
$lines = @(Get-Content $envFile -Encoding UTF8)
$done = $false
for ($i = 0; $i -lt $lines.Count; $i++) {
  if ($lines[$i] -match '^\s*ANTHROPIC_API_KEY\s*=') { $lines[$i] = "ANTHROPIC_API_KEY=$key"; $done = $true }
}
if (-not $done) { $lines += "ANTHROPIC_API_KEY=$key" }
[IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding $false))
$key = $null
Write-Host "Cle ecrite dans .env." -ForegroundColor Green
Write-Host ""

Write-Host "=== verification ===" -ForegroundColor Cyan
& $py (Join-Path $root "scripts\verifier_cles.py")
if ($LASTEXITCODE -ne 0) {
  Write-Host ""
  Write-Host "La verification a echoue. Causes habituelles : pas de credits sur la console, cle copiee incompletement, cle revoquee." -ForegroundColor Yellow
  Write-Host "Corrigez, puis relancez ce script. Le bot n'a pas ete redemarre."
  exit 1
}

Write-Host ""
Write-Host "=== redemarrage du bot ===" -ForegroundColor Cyan
Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -and $_.CommandLine -like '*run_loop*') -or ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*start_paper*') } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
Start-Sleep -Seconds 2
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "cmd.exe /c `"$root\start_paper_detached.bat`""; CurrentDirectory = $root }
if ($r.ReturnValue -eq 0) {
  Write-Host "Bot relance. Il execute un cycle tout de suite : Claude repond pour la premiere fois, le repere se constitue (t0)." -ForegroundColor Green
  Write-Host "Dans une minute : https://thomasmareel.github.io/claude-trading/  (journal : logs\loop.log)"
} else {
  Write-Host "Relance impossible (code $($r.ReturnValue)). Double-cliquez start_paper_detached.bat." -ForegroundColor Yellow
}
