"""Alertes sortantes vers le telephone, via ntfy.sh.

Gratuit, sans compte, sans dependance : l'utilisateur installe l'application
ntfy, s'abonne a un sujet secret, et le met dans .env sous NTFY_TOPIC.

Regle : cette fonction ne leve JAMAIS. Une alerte qui echoue ne doit pas
casser un cycle de trading. Elle retourne simplement False.
"""
from __future__ import annotations

import os
import urllib.request

from .config import secret


def _ascii(s: str) -> str:
    # Les en-tetes HTTP sont en latin-1 ; on reste en ASCII pour etre sur.
    return s.encode("ascii", "ignore").decode("ascii")


def configured() -> bool:
    return bool(secret("NTFY_TOPIC"))


def notify(title: str, message: str, *, priority: str = "default", tags: str = "") -> bool:
    """priority : min | low | default | high | urgent.  tags : mots-cles ntfy, ex. 'warning'."""
    topic = secret("NTFY_TOPIC")
    if not topic:
        return False
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    try:
        req = urllib.request.Request(f"{server}/{topic}", data=message.encode("utf-8"), method="POST")
        req.add_header("Title", _ascii(title))
        req.add_header("Priority", priority)
        if tags:
            req.add_header("Tags", _ascii(tags))
        with urllib.request.urlopen(req, timeout=8) as r:
            return 200 <= r.status < 300
    except Exception:
        return False
