"""Boucle continue : un cycle toutes les N heures, aligne sur les clotures
de bougie (00h, 04h, 08h... UTC pour du 4h), avec un petit delai pour
laisser l'exchange finaliser la bougie. Entre deux cycles, le chien de
garde verifie stops, objectifs et coupe-circuit.

    python scripts/run_loop.py --paper
    python scripts/run_loop.py --live      # exige LIVE_ARMED

Codes de sortie, que start_paper_detached.bat lit :
  0  arret demande (Ctrl+C)
  2  coupe-circuit : arret definitif
  3  reconciliation refusee : le compte reel ne correspond pas au book
  4  demarrage impossible : configuration invalide ou exchange injoignable
Un crash DANS un cycle est journalise et la boucle reprend au cycle suivant.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from rich.console import Console  # noqa: E402

from src.alerts import configured as alerts_configured, notify  # noqa: E402
from src.config import ROOT, load_config  # noqa: E402
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
    mode = "paper" if args.paper else "live" if args.live else None

    # ---- demarrage : tout echec ici est fatal et signale, jamais relance en boucle ----
    load_dotenv(ROOT / ".env")               # pour que notify() puisse deja alerter
    try:
        cfg = load_config()
        engine = build_engine(cfg, mode_override=mode)
        engine.assert_live_consistent()      # ne fait rien en paper ; SystemExit(3) en live si ecart
    except SystemExit as e:
        return int(e.code or 0)
    except Exception as e:
        msg = f"demarrage impossible : {e}"
        console.print(f"[bold red]{msg}[/]\n{traceback.format_exc()}")
        notify("Bot : demarrage impossible", msg, priority="urgent", tags="rotating_light")
        return 4

    cycle_hours = int(cfg.get("engine.cycle_hours", 4))
    watchdog_min = int(cfg.get("engine.watchdog_minutes", 5))
    alerts_on = bool(cfg.get("alerts.enabled", True))
    console.print(f"[bold]Boucle demarree[/] mode={engine.executor.mode} cycle={cycle_hours}h "
                  f"chien de garde={watchdog_min}min symboles={cfg.symbols} "
                  f"alertes={'ntfy' if alerts_configured() else 'AUCUNE (NTFY_TOPIC absent)'}")
    if alerts_on:
        notify("Bot demarre", f"mode {engine.executor.mode}, cycle {cycle_hours}h, {', '.join(cfg.symbols)}",
               priority="low", tags="robot")

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
                    try:
                        n = engine.check_stops()
                        if n:
                            console.print(f"[yellow]chien de garde : {n} sortie(s) forcee(s)[/]")
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        # le chien de garde est la protection du capital : son echec est critique
                        engine.storage.event("critical", "watchdog", f"echec : {e!r}", {"traceback": traceback.format_exc()})
                        console.print(f"[bold red]chien de garde en echec : {e!r}[/]")
                        if alerts_on:
                            notify("Chien de garde en echec", f"{e!r}", priority="high", tags="warning")
                    if engine.storage.kill_switch_tripped():
                        console.print("[bold red]Coupe-circuit declenche par le chien de garde. Boucle arretee.[/]")
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
                if alerts_on:
                    notify("Cycle en echec", f"{e!r}", priority="high", tags="warning")
                # en live, un cycle en echec peut avoir laisse le compte et le book en desaccord
                try:
                    engine.assert_live_consistent()
                except SystemExit as se:
                    return int(se.code or 3)
                except Exception as re:
                    console.print(f"[yellow]reconciliation impossible apres l'echec : {re!r}[/]")
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
