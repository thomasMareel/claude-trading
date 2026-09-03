"""Cerveau a regles : suivi de tendance volontairement simple.

Ce cerveau est un TEMOIN. Ses parametres n'ont pas ete optimises et ne
doivent pas l'etre : le but est d'avoir une reference honnete, pas une
courbe de backtest flatteuse.

Logique, sur bougies 4h :
  Regime      : EMA rapide > EMA lente  -> tendance haussiere, achats permis
  Entree      : cloture > EMA rapide, RSI sous la zone de surachat, et
                le prix vient de repasser au-dessus de l'EMA rapide dans
                les 3 dernieres bougies (rebond sur pullback)
  Sortie      : deux clotures consecutives sous l'EMA rapide, ou regime
                qui bascule en baissier
  Selection   : au plus une nouvelle position par cycle, sur le symbole
                dont le momentum 20 bougies est le plus fort
  Taille      : part fixe du book, plafonnee par la couche de risque
"""
from __future__ import annotations

from .base import BrainContext, Decision


class RulesBrain:
    name = "rules"

    def __init__(self, cfg):
        self.cfg = cfg
        self.rsi_overbought = float(cfg.get("rules.rsi_overbought", 72))
        self.max_position_pct = float(cfg.get("risk.max_position_pct", 0.4))

    def decide(self, ctx: BrainContext) -> list[Decision]:
        decisions: list[Decision] = []
        candidates: list[tuple[float, str, str]] = []

        for symbol, snap in ctx.markets.items():
            df = snap.df
            if len(df) < 5 or df["ema_slow"].isna().iloc[-1]:
                decisions.append(Decision(symbol, "hold", reasoning="historique insuffisant"))
                continue

            last, prev, prev2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
            uptrend = last["ema_fast"] > last["ema_slow"]
            above_fast = last["close"] > last["ema_fast"]
            pos = ctx.position_for(symbol)

            if pos is not None:
                below_twice = (last["close"] < last["ema_fast"]) and (prev["close"] < prev["ema_fast"])
                if not uptrend:
                    decisions.append(Decision(
                        symbol, "sell", confidence=0.8,
                        reasoning=(f"regime baissier : EMA{self.cfg.get('rules.ema_fast')} "
                                   f"({last['ema_fast']:.2f}) < EMA{self.cfg.get('rules.ema_slow')} "
                                   f"({last['ema_slow']:.2f})"),
                    ))
                elif below_twice:
                    decisions.append(Decision(
                        symbol, "sell", confidence=0.7,
                        reasoning=(f"deux clotures sous l'EMA rapide "
                                   f"({prev['close']:.2f}, {last['close']:.2f} < {last['ema_fast']:.2f})"),
                    ))
                else:
                    decisions.append(Decision(
                        symbol, "hold", confidence=0.6,
                        reasoning=f"position conservee, tendance intacte, RSI {last['rsi']:.0f}",
                    ))
                continue

            # pas de position : chercher une entree
            fresh_cross = (
                above_fast
                and (prev["close"] <= prev["ema_fast"] or prev2["close"] <= prev2["ema_fast"])
            )
            not_overbought = last["rsi"] < self.rsi_overbought
            if uptrend and fresh_cross and not_overbought:
                momentum = float(last["roc_20"]) if last["roc_20"] == last["roc_20"] else 0.0
                candidates.append((momentum, symbol,
                    f"tendance haussiere, rebond au-dessus de l'EMA rapide "
                    f"({last['close']:.2f} > {last['ema_fast']:.2f}), RSI {last['rsi']:.0f}, "
                    f"momentum 20b {momentum*100:+.1f}%"))
            else:
                why = []
                if not uptrend:
                    why.append("regime baissier")
                if not fresh_cross:
                    why.append("pas de rebond recent sur l'EMA rapide")
                if not not_overbought:
                    why.append(f"RSI {last['rsi']:.0f} en surachat")
                decisions.append(Decision(symbol, "hold", confidence=0.5, reasoning=", ".join(why)))

        if candidates:
            candidates.sort(reverse=True)
            _, best_symbol, why = candidates[0]
            size = ctx.equity * self.max_position_pct
            decisions.append(Decision(
                best_symbol, "buy", size_quote=round(size, 2), confidence=0.65, reasoning=why,
            ))
            for _, sym, why in candidates[1:]:
                decisions.append(Decision(
                    sym, "hold", confidence=0.5,
                    reasoning=f"signal valide mais {best_symbol} prefere (momentum superieur)",
                ))
        return decisions
