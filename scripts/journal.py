"""Lit le carnet de bord : les decisions, avec leur raisonnement.

    python scripts/journal.py                 # 20 dernieres decisions, tous cerveaux
    python scripts/journal.py --brain llm -n 50
    python scripts/journal.py --refused       # uniquement les refus de la couche de risque
    python scripts/journal.py --cycle 20260903T120000Z
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402

from src.config import load_config  # noqa: E402
from src.storage import Storage  # noqa: E402

console = Console()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", choices=["llm", "rules"])
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--refused", action="store_true")
    ap.add_argument("--cycle")
    ap.add_argument("--thinking", action="store_true", help="affiche aussi le resume de reflexion du LLM")
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    q = "SELECT * FROM decisions WHERE 1=1"
    params: list = []
    if args.brain:
        q += " AND brain=?"; params.append(args.brain)
    if args.refused:
        q += " AND accepted=0"
    if args.cycle:
        q += " AND cycle_id=?"; params.append(args.cycle)
    q += " ORDER BY id DESC LIMIT ?"; params.append(args.n)
    rows = list(reversed(st._conn.execute(q, params).fetchall()))
    if not rows:
        console.print("[dim]aucune decision enregistree[/]")
        return 0

    last_cycle = None
    view_shown: set[tuple[str, str]] = set()
    for r in rows:
        if r["cycle_id"] != last_cycle:
            label = "chien de garde " if r["cycle_id"].startswith("WD") else ""
            console.rule(f"[bold]{label}{r['cycle_id']}")
            last_cycle = r["cycle_id"]
        raw = json.loads(r["raw"] or "{}")
        status = "[green]acceptee[/]" if r["accepted"] else f"[yellow]refusee[/] : {r['reject_reason']}"
        size = f"  taille {r['size_quote']:.2f}" if r["size_quote"] else ""
        conf = f"  confiance {r['confidence']:.2f}" if r["confidence"] is not None else ""
        title = f"{r['brain']}  {r['symbol']}  [bold]{r['action'].upper()}[/]{size}{conf}   {status}"
        body = r["reasoning"] or ""
        key = (r["cycle_id"], r["brain"])
        if raw.get("market_view") and key not in view_shown:
            view_shown.add(key)
            body = f"[italic]Lecture du marche : {raw['market_view']}[/]\n\n{body}"
        if args.thinking and raw.get("thinking"):
            body += f"\n\n[dim]reflexion : {raw['thinking'][:1500]}[/]"
        console.print(Panel(body, title=title, title_align="left", border_style="dim"))
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
