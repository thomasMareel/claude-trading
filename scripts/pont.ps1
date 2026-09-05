# Pont GitHub : traite les demandes ouvertes (label "demande") avec Claude Code
# en mode non interactif, dans une copie de travail git dediee, sur une branche
# temporaire. Le bot de trading n'est jamais touche : il travaille dans le depot
# principal, le pont dans un worktree separe.
#
#   powershell -ExecutionPolicy Bypass -File scripts\pont.ps1
#
# Prerequis : gh connecte (gh auth status), claude connecte (claude login).
# Journal : logs\pont.log. Aucune demande ouverte = "aucune demande" et fin.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force (Join-Path $root "logs") | Out-Null
$log = Join-Path $root "logs\pont.log"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmm")
$branch = "pont-$stamp"
$wt = Join-Path $env:TEMP "claude-trading-pont"
"===== pont $stamp UTC =====" | Add-Content $log

# l'environnement Python du projet, pour que `python -m pytest` marche dans la copie
$env:PATH = (Join-Path $root ".venv\Scripts") + ";" + $env:PATH

if (Test-Path $wt) { git worktree remove --force $wt 2>$null | Out-Null }
git fetch -q origin master
git worktree add -q -b $branch $wt origin/master
if (-not (Test-Path $wt)) { "worktree impossible" | Add-Content $log; exit 1 }

try {
  Push-Location $wt
  $prompt = Get-Content (Join-Path $root "scripts\pont_prompt.md") -Raw -Encoding UTF8
  $out = $prompt | claude -p --max-turns 40 --allowedTools "Read,Grep,Glob,Edit,Write,Bash(gh *),Bash(git *),Bash(python *)"
  $out | Add-Content $log
  $out
} finally {
  Pop-Location
  git worktree remove --force $wt 2>$null | Out-Null
  git branch -D $branch 2>$null | Out-Null       # la branche distante, si poussee, reste pour la PR
}
"===== fin $((Get-Date).ToUniversalTime().ToString('HH:mm')) UTC =====" | Add-Content $log
