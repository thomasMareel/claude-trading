"""Metriques de l'experience, dans l'ordre du protocole (docs/protocole.md).

    python scripts/metriques.py

Affichage console de src/metrics.py : le meme calcul que le tableau de bord.
Rien ici ne rend de verdict.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from src import metrics  # noqa: E402
from src.config import load_config  # noqa: E402
from src.exchange import Exchange  # noqa: E402
from src.storage import Storage  # noqa: E402

console = Console()


def main() -> int:
    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    quote = cfg.get("exchange.quote")
    if not st.first_equity_ts(metrics.BENCHMARK):
        console.print("[yellow]La phase n'a pas commence : aucun t0. Le repere est constitue au premier "
                      "cycle ou Claude repond vraiment (une ligne dans api_costs).[/]")
        return 0
    prices = Exchange(cfg, trading=False).fetch_prices(cfg.symbols)
    m = metrics.compute_all(st, cfg, prices)
    win, b, bench, bias, proc, status = m["window"], m["bilan"], m["benchmarks"], m["bias"], m["process"], m["status"]

    console.rule("[bold]1. Fenetre")
    console.print(f"t0 {win['t0']}  |  {win['days']:.1f} jours sur {win['target_days']} ({win['phase']})  |  "
                  f"mandat [bold]{win['mandate']}[/]  |  regime du panier : [bold]{win['regime']}[/] ({win['basket_pct']:+.2f} %)")
    if win["multiple_models"]:
        console.print(f"[yellow]!! plusieurs versions de modele servies : {win['models_served']} "
                      f"(le protocole demande de cloturer la fenetre comme incomplete)[/]")
    if win["config_drift"]:
        console.print(f"[bold red]!! la configuration a change depuis t0 : {win['config_drift']}. "
                      f"Le protocole interdit toute modification en cours de fenetre.[/]")
    if win["git_commit"]:
        console.print(f"[dim]code a t0 : commit {win['git_commit'][:12]}[/]")

    console.rule("[bold]2. Bilan en trois lignes (jamais fusionnees avant le rapport final)")
    t = Table(show_header=False, pad_edge=False)
    t.add_column("ligne"); t.add_column("valeur", justify="right"); t.add_column("note")
    t.add_row("1. trading brut", f"{b['trading_brut']:+.2f} {quote}",
              "equity de liquidation moins capital ; frais et slippage inclus ; seule ligne comparable aux reperes")
    t.add_row("2. cout API", f"{b['api_cost']:.2f} $", f"{b['api_calls']} appels, {b['cost_per_call']:.3f} $ par appel")
    t.add_row("3. net tout compris", f"{b['net']:+.2f}", "ce que l'utilisateur a reellement dans la poche")
    console.print(t)

    console.rule("[bold]3. Reperes")
    t = Table(pad_edge=False)
    t.add_column("repere"); t.add_column("perf", justify="right"); t.add_column("Claude - repere", justify="right"); t.add_column("lecture")
    t.add_row("B0 cash", "+0.00 %", f"{bench['excess_b0']:+.2f} pts", "le plancher : ne rien faire")
    t.add_row("B1 panier buy-and-hold", f"{bench['b1_pct']:+.2f} %", f"{bench['excess_b1']:+.2f} pts",
              "contexte : Claude est plafonne a 80 % d'exposition, ce repere ne juge pas son timing")
    t.add_row("B2 jumeau a exposition egale", f"{bench['b2_pct']:+.2f} %", f"{bench['excess_b2']:+.2f} pts",
              f"exposition moyenne {bench['mean_exposure']*100:.0f} % ; l'ecart est la part du timing et du choix")
    console.print(t)
    console.print(f"drawdown maximal : {bench['max_drawdown_pct']:.2f} %   |   "
                  "[dim]B3, le trader aleatoire sous les memes regles : python scripts/singe.py[/]")

    console.rule("[bold]4. Justesse directionnelle a 24 h (champ bias)")
    p = bias["protocol"]
    if p["scored"]:
        console.print(f"protocole (cycles 00:00 UTC) : {p['correct']}/{p['scored']} = [bold]{p['accuracy']*100:.1f} %[/]  "
                      f"(n effectif ~{p['n_eff']:.0f}, erreur type {p['se']*100:.1f} pts ; hasard ~33 %, seuil 60 %)")
        for s, v in p["by_symbol"].items():
            console.print(f"  {s:<9} {v['correct']}/{v['n']} = {v['correct']/v['n']*100:.0f} %")
    else:
        console.print("aucune prevision scorable encore selon le protocole")
    a = bias["all_cycles"]
    if a["scored"]:
        console.print(f"[dim]indicatif, tous cycles (chevauchants) : {a['correct']}/{a['scored']} = {a['accuracy']*100:.1f} %[/]")
    if p["pending"]:
        console.print(f"[dim]{p['pending']} prevision(s) en attente de leurs 24 h[/]")

    console.rule("[bold]5. Processus")
    console.print(f"decisions buy/sell emises : {proc['active']}  |  refusees par le risque : {proc['refused']} "
                  f"({proc['refusal_rate']*100:.0f} % ; seuil d'echec 25 % apres la 2e semaine)")
    for m_, n in proc["refusal_motifs"].items():
        console.print(f"  {n:>3}  {m_}")
    if proc["skipped_budget"]:
        console.print(f"[yellow]{proc['skipped_budget']} decision(s) forcees en hold par le plafond API : exclues[/]")
    console.print(f"cycles : {proc['cycles']}  |  tout en hold : {proc['all_hold_cycles']} ({proc['all_hold_share']*100:.0f} %)  |  "
                  f"exposition moyenne : {bench['mean_exposure']*100:.0f} %")
    if proc["trades_closed"]:
        console.print(f"trades clos : {proc['trades_closed']} ({proc['wins']} gagnants)  |  sorties : "
                      + ", ".join(f"{k} {v}" for k, v in proc["exit_reasons"].items()))
        console.print(f"  part des sorties decidees par la cage : {proc['cage_share']*100:.0f} % (a 90 %, le jugement de vente n'est jamais exerce)")
        if proc["stops"]:
            console.print(f"  stops touches sur du bruit (retour au-dessus de l'entree sous 48 h) : {proc['noise_stops']}/{proc['stops']}")
        if proc["brier"] is not None:
            console.print(f"  calibration (Brier) sur {proc['brier_n']} entrees : {proc['brier']:.3f}  (0.25 = confiance constante ; plus bas = mieux)")
        if proc["fees_over_gross"] is not None:
            console.print(f"  frais / P&L brut des trades clos : {proc['fees_over_gross']*100:.0f} % (au-dela de 50 %, la strategie paie Binance)")
    if proc["opens"]:
        console.print(f"ouvertures par semaine (budget {proc['budget_per_week']}) : "
                      + ", ".join(f"{w} {n}" for w, n in proc["opens_per_week"].items())
                      + f"  |  usage du budget {proc['budget_use']*100:.0f} %")
        console.print(f"  jour des ouvertures : {proc['open_weekdays']}  (tout le lundi = impatience)")
    console.print(f"gels journaliers : {proc['daily_freezes']}  (3 ou plus = echec de processus)")

    console.rule("[bold]6. Etat des criteres du protocole (pas un verdict)")
    for c in status["criteria"]:
        mark = "[green]ok[/]" if c["ok"] else "[dim]..[/]" if c["ok"] is None else "[red]NON[/]"
        console.print(f"  {mark:<14} {c['label']:<48} {str(c['value']):>14}   cible {c['target']}")
    console.print(f"[dim]{status['note']}[/]")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
