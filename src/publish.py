"""Publication des releves sur GitHub : add, commit, push, en best-effort.

Ne leve jamais. Retourne (succes, message). Le bot ne detient aucun jeton :
git utilise le gestionnaire d'identifiants deja configure sur la machine par
l'utilisateur (gh auth login). Si la publication echoue, le trading continue
et l'evenement est journalise ; la page affichera simplement des releves
plus anciens.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

AUTHOR = ["-c", "user.name=claude-trading bot", "-c", "user.email=bot@claude-trading.invalid"]


def _git(root: Path, *args: str, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *AUTHOR, *args], cwd=str(root), capture_output=True, text=True,
        timeout=timeout, encoding="utf-8", errors="replace",
    )


def publish(root: Path, paths: list[str], message: str, *, push: bool = True) -> tuple[bool, str]:
    try:
        r = _git(root, "add", "--", *paths)
        if r.returncode != 0:
            return False, f"git add : {r.stderr.strip()[:200]}"
        r = _git(root, "diff", "--cached", "--quiet", "--", *paths)
        if r.returncode == 0:
            return True, "rien de nouveau a publier"
        r = _git(root, "commit", "-q", "-m", message, "--", *paths)
        if r.returncode != 0:
            return False, f"git commit : {(r.stderr or r.stdout).strip()[:200]}"
        if not push:
            return True, "commit local, pas de push"
        r = _git(root, "push", "-q", "origin", "HEAD", timeout=120)
        if r.returncode != 0:
            return False, f"git push : {r.stderr.strip()[:200]}"
        return True, "releves publies"
    except subprocess.TimeoutExpired:
        return False, "git : delai depasse"
    except Exception as e:  # jamais d'exception vers l'appelant
        return False, f"git : {e!r}"
