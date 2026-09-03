"""Boucle continue : un cycle toutes les N heures, aligne sur les clotures
de bougie (00h, 04h, 08h... UTC pour du 4h), avec un petit delai pour
laisser l'exchange finaliser la bougie.

    python scripts/run_loop.py --paper
    python scripts/run_loop.py --live      # exige LIVE_ARMED

Ctrl+C arrete proprement. Un crash dans un cycle est journalise et la
boucle reprend au cycle suivant : le bot ne meurt pas silencieusement.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402

from src.config import load_config  # noqa: E402
from src.engine import build_engine  # noqa: E402

console = Console()
GRACE_SECONDS = 90  # apres la cloture de bougie


def next_boundary(cycle_hours: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    hour = (now.hour // cycle_hours) * cycle_hours
    base = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    nxt = base + timedelta(hours=cycle_hours)
    return nxt + timedelta(seconds=GRACE_SECONDS)


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--paper", action="store_true")
    g.add_argument("--live", action="store_true")
    ap.add_argument("--now", action="store_true", help="lance un cycle immediatement puis attend")
    args = ap.parse_args()

    cfg = load_config()
    cycle_hours = int(cfg.get("engine.cycle_hours", 4))
    watchdog_min = int(cfg.get("engine.watchdog_minutes", 5))
    mode = "paper" if args.paper else "live" if args.live else None
    engine = build_engine(cfg, mode_override=mode)
    console.print(f"[bold]Boucle demarree[/] mode={engine.executor.mode} cycle={cycle_hours}h "
                  f"chien de garde={watchdog_min}min symboles={cfg.symbols}")

    run_now = args.now
    try:
        while True:
            if not run_now:
                target = next_boundary(cycle_hours)
                wait = (target - datetime.now(timezone.utc)).total_seconds()
                console.print(f"[dim]prochain cycle a {target:%Y-%m-%d %H:%M:%S} UTC "
                              f"(dans {wait/60:.0f} min)[/]")
                while wait > 0:
                    time.sleep(min(wait, max(60, watchdog_min * 60)))
                    wait = (target - datetime.now(timezone.utc)).total_seconds()
                    if wait <= 0 or watchdog_min <= 0:
                        continue
                    # entre deux cycles : stops, objectifs, coupe-circuit
                    try:
                        n = engine.check_stops()
                        if n:
                            console.print(f"[yellow]chien de garde : {n} sortie(s) forcee(s)[/]")
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        engine.storage.event("warning", "watchdog", f"echec : {e!r}")
                        console.print(f"[yellow]chien de garde en echec : {e!r}[/]")
                    if engine.storage.kill_switch_tripped():
                        console.print("[bold red]Coupe-circuit declenche par le chien de garde. "
                                      "Boucle arretee.[/]")
                        return 2
            run_now = False
            try:
                engine.run_cycle()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                tb = traceback.format_exc()
                engine.storage.event("critical", "loop", f"cycle en echec : {e!r}", {"traceback": tb})
                console.print(f"[bold red]cycle en echec : {e!r}[/]\n{tb}")
            if engine.storage.kill_switch_tripped():
                console.print("[bold red]Coupe-circuit declenche. Boucle arretee.[/]")
                return 2
    except KeyboardInterrupt:
        console.print("\n[bold]Arret demande. A bientot.[/]")
    finally:
        engine.storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
