# Colle les cles de la plateforme declaree dans config.yaml, puis les verifie.
# Les cles sont saisies MASQUEES dans ce terminal, sur votre machine. Elles ne
# sont jamais affichees, jamais envoyees ailleurs, jamais commitees.
#
#   double-cliquer scripts\coller_cles.bat
#   -Testnet  : renseigne les cles du bac a sable au lieu du compte reel
#
# Le nombre de secrets depend de la plateforme : OKX en exige trois (cle,
# secret, phrase secrete), Binance et Kraken deux. Le script le lit dans
# src/venues.py, il n'y a rien a coder en dur ici.
param([switch]$Testnet)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$envFile = Join-Path $root ".env"
Set-Location $root

# --- ce que la plateforme configuree attend, demande au code lui-meme ---
$suffixe = if ($Testnet) { "_TESTNET" } else { "" }
$infos = & $py -c @"
import sys; sys.path.insert(0, r'$root')
import yaml
from src import venues
cfg = yaml.safe_load(open(r'$root\config.yaml', encoding='utf-8'))
v = venues.get(str(cfg.get('exchange', {}).get('id', 'myokx')))
print(v.nom); print(v.hote); print(v.note)
for c, n in v.env_names().items():
    print(c + '|' + n.replace(v.env_prefix, v.env_prefix + '$suffixe', 1))
"@
if ($LASTEXITCODE -ne 0) { Write-Host "Impossible de lire la configuration." -ForegroundColor Red; exit 1 }
$lignes = @($infos -split "`r?`n" | Where-Object { $_ })
$nom = $lignes[0]; $hote = $lignes[1]; $note = $lignes[2]
$champs = @($lignes[3..($lignes.Count - 1)] | ForEach-Object { $_ -split '\|' })

Write-Host ""
Write-Host "Cles $nom $(if ($Testnet) {'TESTNET (fausse monnaie)'} else {'(compte reel)'})" -ForegroundColor Cyan
Write-Host "  domaine interroge : $hote"
if ($note) { Write-Host "  $note" -ForegroundColor DarkGray }
Write-Host ""
Write-Host "Sur la page de creation de la cle, verifiez AVANT de continuer :" -ForegroundColor Yellow
Write-Host "  [x] Lire            [x] Trader, marche Au comptant"
Write-Host "  [ ] Retirer         [ ] Transferer        [ ] Earn" -ForegroundColor Red
Write-Host "  restriction par adresse IP : activee"
Write-Host ""

$valeurs = @{}
for ($i = 0; $i -lt $champs.Count; $i += 2) {
  $nomVar = $champs[$i + 1]
  $libelle = switch -Wildcard ($nomVar) {
    "*PASSPHRASE" { "la PHRASE SECRETE (celle que vous avez choisie)" }
    "*SECRET"     { "le SECRET" }
    default       { "la CLE API" }
  }
  $sec = Read-Host "Collez $libelle (ne s'affiche pas)" -AsSecureString
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  $v = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b)
  $v = $v.Trim().Trim('"').Trim("'")
  if (-not $v) { Write-Host "Valeur vide : rien n'a ete modifie." -ForegroundColor Red; exit 1 }
  if ($v -like "sk-ant-*") { Write-Host "Ceci est une cle Claude, pas une cle $nom. Rien n'a ete modifie." -ForegroundColor Red; exit 1 }
  $valeurs[$nomVar] = $v
}

if (-not (Test-Path $envFile)) { New-Item -ItemType File $envFile | Out-Null }
$lines = @(Get-Content $envFile -Encoding UTF8 -ErrorAction SilentlyContinue)
foreach ($nomVar in $valeurs.Keys) {
  $fait = $false
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match "^\s*$nomVar\s*=") { $lines[$i] = "$nomVar=$($valeurs[$nomVar])"; $fait = $true }
  }
  if (-not $fait) { $lines += "$nomVar=$($valeurs[$nomVar])" }
}
[IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding $false))
$valeurs.Clear()
Write-Host ""
Write-Host "Ecrites dans .env (ignore par git, ne quitte pas cette machine)." -ForegroundColor Green
Write-Host ""
Write-Host "=== verification ===" -ForegroundColor Cyan
$env:PYTHONIOENCODING = "utf-8"
& $py (Join-Path $root "scripts\verifier_cles.py")
Write-Host ""
Write-Host "Rappel : aucun ordre reel ne partira tant que le fichier LIVE_ARMED n'existe pas," -ForegroundColor DarkGray
Write-Host "et c'est vous qui le creez. Le bot reste en mode papier." -ForegroundColor DarkGray
