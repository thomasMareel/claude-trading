# Colle les cles Binance dans .env, puis les verifie.
# Les cles sont saisies masquees dans CE terminal, sur VOTRE machine.
# Elles ne sont jamais affichees, jamais envoyees ailleurs, jamais commitees.
#
#   double-cliquer scripts\coller_cles_binance.bat
#
#   -t  ou  --testnet   : renseigne les cles du bac a sable au lieu du compte reel
param([switch]$Testnet)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$envFile = Join-Path $root ".env"
Set-Location $root

$prefixe = if ($Testnet) { "BINANCE_TESTNET" } else { "BINANCE" }
$quoi = if ($Testnet) { "TESTNET (fausse monnaie)" } else { "COMPTE REEL" }
$url = if ($Testnet) { "https://testnet.binance.vision" } else { "https://www.binance.com > profil > API Management" }

Write-Host ""
Write-Host "Cles Binance : $quoi" -ForegroundColor Cyan
Write-Host "  a creer sur $url"
Write-Host ""
if (-not $Testnet) {
  Write-Host "AVANT DE CONTINUER, sur Binance, dans Edit restrictions :" -ForegroundColor Yellow
  Write-Host "  [x] Enable Reading                 lire les soldes"
  Write-Host "  [x] Enable Spot & Margin Trading   passer les ordres"
  Write-Host "  [ ] Enable Withdrawals             NE JAMAIS COCHER" -ForegroundColor Red
  Write-Host "  [ ] Enable Futures                 inutile ici"
  Write-Host "  Restrict access to trusted IPs only : ajoutez votre IP (https://ifconfig.me)"
  Write-Host ""
  Write-Host "Le script verifiera lui-meme que les retraits sont bien desactives." -ForegroundColor Yellow
  Write-Host ""
}

$k = Read-Host "Collez la API Key (ne s'affiche pas)" -AsSecureString
$s = Read-Host "Collez la Secret Key (ne s'affiche pas)" -AsSecureString
function Clair($sec) {
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  $t = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b)
  return $t.Trim().Trim('"').Trim("'")
}
$key = Clair $k; $sec = Clair $s

if ($key.Length -lt 20 -or $sec.Length -lt 20) {
  Write-Host "Cle ou secret trop court : rien n'a ete modifie." -ForegroundColor Red
  exit 1
}
if ($key -like "sk-ant-*") {
  Write-Host "Ceci est une cle Claude, pas une cle Binance. Rien n'a ete modifie." -ForegroundColor Red
  exit 1
}

if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $root ".env.example") $envFile }
$lines = @(Get-Content $envFile -Encoding UTF8)
foreach ($pair in @(@("${prefixe}_API_KEY", $key), @("${prefixe}_API_SECRET", $sec))) {
  $nom = $pair[0]; $val = $pair[1]; $fait = $false
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match "^\s*$nom\s*=") { $lines[$i] = "$nom=$val"; $fait = $true }
  }
  if (-not $fait) { $lines += "$nom=$val" }
}
[IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding $false))
$key = $null; $sec = $null
Write-Host ""
Write-Host "Cles ecrites dans .env (ignore par git, ne quitte pas cette machine)." -ForegroundColor Green
Write-Host ""
Write-Host "=== verification ===" -ForegroundColor Cyan
& $py (Join-Path $root "scripts\verifier_cles.py")
Write-Host ""
if ($LASTEXITCODE -eq 0) {
  Write-Host "Tout est bon. Le bot reste en mode papier : aucun ordre reel ne partira" -ForegroundColor Green
  Write-Host "tant que le fichier LIVE_ARMED n'existe pas, et c'est vous qui le creez."
} else {
  Write-Host "Verification en echec. Relisez les lignes ci-dessus." -ForegroundColor Yellow
  Write-Host "Si elle indique 'retraits ACTIVES', retournez sur Binance immediatement." -ForegroundColor Red
}
