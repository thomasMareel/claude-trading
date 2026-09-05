"""Metriques de l'experience : ce que 100 USDT peuvent mesurer avec precision.

    python scripts/metriques.py

Rien ici ne rend de verdict. Les seuils et la lecture des cas sont dans
docs/protocole.md. Ce script calcule et affiche, dans l'ordre du protocole :

  1. la fenetre (t0, duree, regime du marche)
  2. le bilan en trois lignes : trading brut, cout API, net
  3. les reperes B0 (cash), B1 (panier), B2 (jumeau a exposition egale)
  4. la justesse directionnelle a 24 h (champ bias), la metrique qui a le
     plus de puissance statistique
  5. les metriques de processus : refus par motif, calibration de la
     confiance, exposition, mode de sortie, usage du budget, frais
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from src.config import load_config  # noqa: E402
from src.engine import BENCHMARK  # noqa: E402
from src.exchange import Exchange  # noqa: E402
from src.portfolio import build_positions, compute_cash  # noqa: E402
from src.storage import Storage  # noqa: E402

console = Console()
BRAIN = "llm"
BIAS_BAND = 0.003          # bande neutre de la prevision a 24 h
FRICTION_RT = 0.0015       # frais + slippage d'un aller-retour, pour le jumeau B2
TF_MS = 4 * 3600 * 1000


def iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def regime_label(basket_ret_pct: float) -> str:
    if basket_ret_pct > 10:
        return "haussier"
    if basket_ret_pct < -10:
        return "baissier"
    return "range"


def close_at(st: Storage, symbol: str, tf: str, ts_ms: int):
    """Close de la derniere bougie CLOTUREE a l'instant donne. La bougie en
    cours a cet instant n'est pas encore terminee : la prendre noterait la
    prevision sur une fenetre decalee de 4 heures."""
    row = st._conn.execute(
        "SELECT close FROM candles WHERE symbol=? AND timeframe=? AND ts + ? <= ? ORDER BY ts DESC LIMIT 1",
        (symbol, tf, TF_MS, ts_ms),
    ).fetchone()
    return float(row["close"]) if row else None


def main() -> int:
    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    tf = cfg.timeframe
    init = cfg.total_capital
    quote = cfg.get("exchange.quote")
    budget = int(cfg.get("risk.max_round_trips_per_week"))

    t0 = st.first_equity_ts(BENCHMARK)
    if not t0:
        console.print("[yellow]La phase n'a pas commence : aucun t0. Le repere est constitue au premier "
                      "cycle ou Claude repond vraiment (une ligne dans api_costs).[/]")
        return 0
    x = Exchange(cfg, trading=False)
    prices = x.fetch_prices(cfg.symbols)
    now = datetime.now(timezone.utc)
    days = (now - iso(t0)).total_seconds() / 86400

    # ------------------------------------------------------------ 1. fenetre
    basket = st.benchmark_basket()
    fee = float(cfg.get("exchange.fee_rate"))
    slip = float(cfg.get("exchange.slippage"))
    b1 = sum(float(r["amount_base"]) * prices[r["symbol"]] * (1 - slip) for r in basket) * (1 - fee)
    b1_pct = (b1 / init - 1) * 100
    console.rule("[bold]1. Fenetre")
    console.print(f"t0 {t0}  |  {days:.1f} jours  |  regime du panier : [bold]{regime_label(b1_pct)}[/] ({b1_pct:+.2f} %)")
    calls = st.api_calls_total()
    models = Counter(r["model"] for r in st._conn.execute("SELECT model FROM api_costs WHERE model != 'timeout-estime'"))
    if len(models) > 1:
        console.print(f"[yellow]!! plusieurs versions de modele servies dans la fenetre : {dict(models)} "
                      f"(le protocole demande de cloturer la fenetre comme incomplete)[/]")
    # la configuration a-t-elle bouge depuis t0 ? (pre-enregistrement)
    starts = st.events_by_source("protocol_start")
    if starts:
        frozen = json.loads(starts[-1]["payload"] or "{}")
        drift = [k for k, v in (frozen.get("config") or {}).items() if v != cfg.raw.get(k)]
        if drift:
            console.print(f"[bold red]!! la configuration a change depuis t0 : sections {drift}. "
                          f"Le protocole interdit toute modification en cours de fenetre.[/]")
        if frozen.get("git_commit"):
            console.print(f"[dim]code a t0 : commit {frozen['git_commit'][:12]}[/]")

    # ------------------------------------------------------------ 2. bilan
    cash = compute_cash(st, BRAIN, init)
    pos = build_positions(st, BRAIN, prices)
    eq = cash + sum(p.value_quote for p in pos) * (1 - slip) * (1 - fee)    # liquidation, comme le repere
    api = st.api_cost_total()
    console.rule("[bold]2. Bilan en trois lignes (jamais fusionnees avant le rapport final)")
    t = Table(show_header=False, pad_edge=False)
    t.add_column("ligne"); t.add_column("valeur", justify="right"); t.add_column("note")
    t.add_row("1. trading brut", f"{eq - init:+.2f} {quote}", "equity de liquidation moins capital ; frais Binance et slippage inclus ; seule ligne comparable aux reperes")
    t.add_row("2. cout API", f"{api:.2f} $", f"{calls} appels, {api / max(calls, 1):.3f} $ par appel")
    t.add_row("3. net tout compris", f"{eq - init - api:+.2f}", "ce que l'utilisateur a reellement dans la poche")
    console.print(t)

    # ------------------------------------------------------------ 3. reperes
    console.rule("[bold]3. Reperes")
    llm_curve = st.equity_curve(BRAIN)
    b_curve = st.equity_curve(BENCHMARK)
    # jumeau B2 : chainage cycle par cycle de e_(t-1) x rendement du panier
    twin, prev_b, prev_e = init, None, None
    for br in b_curve:
        # dernier releve de Claude anterieur ou egal a ce releve du repere
        cand = [r for r in llm_curve if r["ts"] <= br["ts"]]
        if not cand:
            continue
        lr = cand[-1]
        e = float(lr["positions_value"]) / float(lr["total_quote"]) if float(lr["total_quote"]) > 0 else 0.0
        bv = float(br["total_quote"])
        if prev_b is not None and prev_b > 0:
            r_basket = bv / prev_b - 1
            twin *= 1 + prev_e * r_basket
            twin -= abs(e - prev_e) * FRICTION_RT * twin
        prev_b, prev_e = bv, e
    exposures = [float(r["positions_value"]) / float(r["total_quote"]) for r in llm_curve if float(r["total_quote"]) > 0]
    mean_expo = sum(exposures) / len(exposures) if exposures else 0.0
    perf = (eq / init - 1) * 100
    twin_pct = (twin / init - 1) * 100
    t = Table(pad_edge=False)
    t.add_column("repere"); t.add_column("perf", justify="right"); t.add_column("Claude - repere", justify="right"); t.add_column("lecture")
    t.add_row("B0 cash", "+0.00 %", f"{perf:+.2f} pts", "le plancher : ne rien faire")
    t.add_row("B1 panier buy-and-hold", f"{b1_pct:+.2f} %", f"{perf - b1_pct:+.2f} pts",
              "contexte : Claude est plafonne a 80 % d'exposition, ce repere ne juge pas son timing")
    t.add_row("B2 jumeau a exposition egale", f"{twin_pct:+.2f} %", f"{perf - twin_pct:+.2f} pts",
              f"exposition moyenne de Claude {mean_expo*100:.0f} % ; l'ecart est la part du timing et du choix")
    console.print(t)
    console.print("[dim]B3, le trader aleatoire sous les memes regles : python scripts/singe.py[/]")

    # ------------------------------------------------------------ 4. bias
    console.rule("[bold]4. Justesse directionnelle a 24 h (champ bias, cycles 00:00 UTC)")
    rows = st._conn.execute(
        "SELECT cycle_id, ts, symbol, raw FROM decisions WHERE brain=? AND ts>=? AND cycle_id NOT LIKE 'WD%' ORDER BY id",
        (BRAIN, t0),
    ).fetchall()
    scored, correct, by_sym = 0, 0, defaultdict(lambda: [0, 0])
    pending = 0
    for r in rows:
        if r["cycle_id"][9:11] != "00":
            continue
        raw = json.loads(r["raw"] or "{}")
        bias = raw.get("bias")
        if bias not in ("up", "down", "flat"):
            continue
        t_ms = int(iso(r["ts"]).timestamp() * 1000)
        p0 = close_at(st, r["symbol"], tf, t_ms)
        horizon_ms = t_ms + 24 * 3600 * 1000
        # la bougie qui cloture l'horizon doit etre terminee ET rafraichie en base
        p1 = close_at(st, r["symbol"], tf, horizon_ms) if horizon_ms + TF_MS <= int(now.timestamp() * 1000) else None
        if p0 is None or p1 is None:
            pending += 1
            continue
        ret = p1 / p0 - 1
        truth = "up" if ret > BIAS_BAND else "down" if ret < -BIAS_BAND else "flat"
        ok = bias == truth
        scored += 1; correct += ok
        by_sym[r["symbol"]][0] += ok; by_sym[r["symbol"]][1] += 1
    if scored:
        acc = correct / scored
        n_eff = scored / (1 + 2 * 0.8)          # trois actifs correles a ~0.8
        se = (acc * (1 - acc) / max(n_eff, 1)) ** 0.5
        console.print(f"{correct}/{scored} justes = [bold]{acc*100:.1f} %[/]  (n effectif ~{n_eff:.0f}, erreur type {se*100:.1f} pts ; "
                      f"hasard a 3 classes ~33 %, seuil prudent du protocole 60 % sur 250 observations)")
        for s, (c, n) in by_sym.items():
            console.print(f"  {s:<9} {c}/{n} = {c/n*100:.0f} %")
    else:
        console.print("aucune prevision scorable encore")
    if pending:
        console.print(f"[dim]{pending} prevision(s) en attente de leurs 24 h[/]")

    # ------------------------------------------------------------ 5. processus
    console.rule("[bold]5. Processus")
    decs = st._conn.execute(
        "SELECT * FROM decisions WHERE brain=? AND ts>=? AND cycle_id NOT LIKE 'WD%' ORDER BY id", (BRAIN, t0)
    ).fetchall()
    skipped = sum(1 for d in decs if '"skipped": "budget"' in (d["raw"] or ""))
    active = [d for d in decs if d["action"] in ("buy", "sell") and '"forced"' not in (d["raw"] or "")]
    refused = [d for d in active if not d["accepted"]]
    console.print(f"decisions buy/sell emises : {len(active)}  |  refusees par le risque : {len(refused)} "
                  f"({len(refused)/max(len(active),1)*100:.0f} % ; seuil d'echec de processus 25 % apres la 2e semaine)")
    motifs = Counter((d["reject_reason"] or "").split(" : ")[1].split(" (")[0][:60] for d in refused if d["reject_reason"])
    for m, n in motifs.most_common():
        console.print(f"  {n:>3}  {m}")
    if skipped:
        console.print(f"[yellow]{skipped} decision(s) forcees en hold par le plafond API : exclues des metriques de processus[/]")
    cycles = defaultdict(list)
    for d in decs:
        cycles[d["cycle_id"]].append(d["action"])
    all_hold = sum(1 for acts in cycles.values() if all(a == "hold" for a in acts))
    console.print(f"cycles : {len(cycles)}  |  tout en hold : {all_hold} ({all_hold/max(len(cycles),1)*100:.0f} %)  |  "
                  f"exposition moyenne : {mean_expo*100:.0f} %")

    closed = st.closed_positions(BRAIN)
    if closed:
        reasons = Counter(c["close_reason"] for c in closed)
        console.print(f"trades clos : {len(closed)}  |  sorties : " + ", ".join(f"{k} {v}" for k, v in reasons.items()))
        cage = reasons.get("stop_loss", 0) + reasons.get("take_profit", 0)
        console.print(f"  part des sorties decidees par la cage (stop/objectif) : {cage/len(closed)*100:.0f} % "
                      f"(a 90 %, le jugement de vente n'est jamais exerce)")
        # stops sur du bruit : le prix revient au-dessus de l'entree sous 48 h
        noise = 0
        for c in closed:
            if c["close_reason"] != "stop_loss":
                continue
            t_ms = int(iso(c["closed_at"]).timestamp() * 1000)
            rows48 = st._conn.execute(
                "SELECT MAX(high) AS h FROM candles WHERE symbol=? AND timeframe=? AND ts>? AND ts<=?",
                (c["symbol"], tf, t_ms, t_ms + 48 * 3600 * 1000),
            ).fetchone()
            if rows48 and rows48["h"] and float(rows48["h"]) > float(c["entry_price"]):
                noise += 1
        if reasons.get("stop_loss"):
            console.print(f"  stops touches sur du bruit (retour au-dessus de l'entree sous 48 h) : {noise}/{reasons['stop_loss']}")
        # calibration : confiance a l'achat vs issue, hors ventes forcees
        brier, nb = 0.0, 0
        for c in closed:
            o = st._conn.execute(
                "SELECT d.confidence FROM orders o JOIN decisions d ON d.id=o.decision_id "
                "WHERE o.brain=? AND o.symbol=? AND o.side='buy' AND o.ts<=? ORDER BY o.id DESC LIMIT 1",
                (BRAIN, c["symbol"], c["opened_at"] + "z"),
            ).fetchone()
            if o and o["confidence"] is not None and float(o["confidence"]) < 1.0:
                outcome = 1.0 if float(c["pnl_quote"] or 0) > 0 else 0.0
                brier += (float(o["confidence"]) - outcome) ** 2; nb += 1
        if nb:
            console.print(f"  calibration (Brier) sur {nb} entrees : {brier/nb:.3f}  (0.25 = confiance constante 0.5 ; plus bas = mieux)")
        gross = sum(float(c["pnl_quote"] or 0) + float(c["fees_quote"] or 0) for c in closed)
        fees = sum(float(c["fees_quote"] or 0) for c in closed)
        if gross > 0:
            console.print(f"  frais / P&L brut des trades clos : {fees/gross*100:.0f} % (au-dela de 50 %, la strategie paie Binance)")
    # budget par semaine
    opens = st._conn.execute("SELECT opened_at FROM positions WHERE brain=? AND opened_at>=?", (BRAIN, t0)).fetchall()
    weeks = Counter(iso(o["opened_at"]).strftime("%G-W%V") for o in opens)
    wdays = Counter(iso(o["opened_at"]).strftime("%a") for o in opens)
    if opens:
        console.print(f"ouvertures par semaine (budget {budget}) : " + ", ".join(f"{w} {n}" for w, n in sorted(weeks.items())))
        console.print(f"  jour des ouvertures : {dict(wdays)}  (tout le lundi = impatience)")
    freezes = st.events_by_source("daily_freeze")
    console.print(f"gels journaliers : {len(freezes)}  (3 ou plus = echec de processus)")
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
