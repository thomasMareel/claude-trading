"""Le trader : Claude recoit un dossier de marche et rend des decisions
structurees, avec son raisonnement ecrit.

Ce que ce cerveau NE fait PAS :
  - il ne passe aucun ordre, il propose
  - il ne connait pas les cles API
  - il ne peut pas depasser les limites de la couche de risque, qui
    relit et tranche apres lui

Cout : chaque appel est mesure et cumule. Un appel plus cher que
llm.alert_cost_per_call_usd declenche une alerte. Au-dela du plafond
journalier (protection anti-emballement, volontairement large), le
cerveau rend "hold" sur tout et le journal l'indique.
"""
from __future__ import annotations

import json
import os
from typing import Any

import anthropic

from .. import mandates
from ..alerts import notify
from .base import BrainContext, Decision

# Schema de sortie impose au modele. Redige a la main pour garantir
# additionalProperties=false a chaque niveau (exige par structured outputs).
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "market_view": {
            "type": "string",
            "description": "Lecture globale du marche en 2 a 4 phrases, en francais.",
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
                    "bias": {
                        "type": "string",
                        "enum": ["up", "down", "flat"],
                        "description": "Ta prevision de direction du prix a 24 h pour ce symbole, obligatoire meme en hold. flat = variation attendue dans une bande de plus ou moins 0.3 %.",
                    },
                    "size_pct_of_equity": {
                        "type": "number",
                        "description": "Pour buy uniquement : part du book a engager, entre 0 et la limite max_position_pct. 0 sinon.",
                    },
                    "confidence": {"type": "number", "description": "Entre 0 et 1."},
                    "reasoning": {
                        "type": "string",
                        "description": "Justification en francais, 2 a 5 phrases, concrete et chiffree.",
                    },
                },
                "required": ["symbol", "action", "bias", "size_pct_of_equity", "confidence", "reasoning"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["market_view", "decisions"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Tu es le gestionnaire d'un petit book de trading crypto spot, long-only, sur bougies de 4 heures. Tu es evalue sur un horizon de plusieurs semaines contre un seul repere : la detention passive d'un panier equipondere des memes actifs, achete au premier jour et jamais touche, aux memes frais. Faire mieux que ce repere, net de tous les frais, est l'objectif. Faire moins bien que lui, c'est avoir detruit de la valeur en s'agitant.

Regles du jeu, non negociables (une couche de risque deterministe relit chaque decision et refusera tout ce qui les viole) :
- Spot uniquement. Pas de short, pas de levier. "sell" ne s'applique qu'a une position ouverte.
- Une seule position par symbole, pas de renforcement.
- Au plus `max_open_positions` positions ouvertes en meme temps.
- Une position engage au plus `max_position_pct` du book.
- Budget de nouvelles positions : `round_trips_budget` par semaine. Chaque ouverture consomme une unite. Il t'est indique combien il en reste.
- Chaque position recoit automatiquement un stop de perte et un objectif de gain fixes. Tu n'as pas a les gerer, mais tu peux vendre avant.
- Les frais sont de `fee_rate` par ordre, soit le double pour un aller-retour. Sur un book de cette taille, trader souvent est le moyen le plus sur de perdre. "hold" est une reponse legitime et frequente.

Ce que l'on attend de toi :
- Une lecture honnete du marche, puis une decision par symbole.
- Un raisonnement concret, chiffre, en francais, qui cite les indicateurs et le contexte fournis. Ce texte est lu par un humain qui cherche a comprendre comment tu raisonnes. Pas de langue de bois, pas de "le marche est incertain" sans suite.
- Tiens compte de tes decisions passees et de leur resultat : si tu as eu tort, dis pourquoi.
- Une confiance calibree : 0.9 doit etre rare.
- Pour chaque symbole, un `bias` : ta prevision de direction a 24 h (up, down, ou flat si tu attends moins de 0.3 % de variation), meme quand tu ne trades pas. C'est ta lecture du marche que l'on mesure ainsi, independamment de tes trades, et c'est la mesure qui aura le plus de poids statistique.
- Un indicateur a `null` signifie qu'il n'est pas encore calculable. Ne l'invente pas.

Tu ne connais ni l'avenir ni les news. Tu ne vois que les prix et les indicateurs fournis. Ne pretends pas savoir ce que tu ne sais pas."""


def _fmt_candles(df, n: int = 12) -> list[dict[str, Any]]:
    tail = df.tail(n)
    out = []
    for _, r in tail.iterrows():
        out.append({
            "t": r["dt"].strftime("%m-%d %Hh"),
            "o": round(float(r["open"]), 4),
            "h": round(float(r["high"]), 4),
            "l": round(float(r["low"]), 4),
            "c": round(float(r["close"]), 4),
            "v": round(float(r["volume"]), 1),
        })
    return out


def build_packet(ctx: BrainContext) -> dict[str, Any]:
    """Le dossier complet remis au modele. Journalise tel quel."""
    return {
        "horodatage_utc": ctx.now_iso,
        "book": {
            "capital_initial": round(ctx.initial_capital, 2),
            "cash_disponible": round(ctx.cash, 2),
            "valeur_positions": round(ctx.positions_value, 2),
            "equity": round(ctx.equity, 2),
            "performance_totale_pct": round((ctx.equity / ctx.initial_capital - 1) * 100, 2),
            "pnl_du_jour_pct": round(ctx.daily_pnl_pct, 2),
        },
        "limites": {
            **ctx.limits,
            "fee_rate": ctx.fee_rate,
            "round_trips_budget": ctx.round_trips_budget,
            "round_trips_restants_cette_semaine": max(0, ctx.round_trips_budget - ctx.round_trips_used),
        },
        "positions_ouvertes": [
            {
                "symbol": p.symbol,
                "entree": p.entry_price,
                "prix_actuel": p.current_price,
                "pnl_pct": round(p.pnl_pct, 2),
                "valeur": round(p.value_quote, 2),
                "ouverte_le": p.opened_at,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
            }
            for p in ctx.positions
        ],
        "marches": {
            sym: {
                "prix": snap.price,
                "indicateurs": snap.indicators,
                "dernieres_bougies_4h": _fmt_candles(snap.df),
            }
            for sym, snap in ctx.markets.items()
        },
        "tes_decisions_recentes": ctx.recent_decisions,
        "tes_trades_clotures_recents": ctx.recent_trades,
    }


class LLMBrain:
    name = "llm"
    requires_api = True   # le repere attend son premier appel reussi (t0)

    def __init__(self, cfg, storage):
        self.cfg = cfg
        self.storage = storage
        self.model = str(cfg.get("llm.model", "claude-opus-5"))
        self.effort = str(cfg.get("llm.effort", "medium"))
        self.max_tokens = int(cfg.get("llm.max_tokens", 8000))
        self.timeout = float(cfg.get("llm.timeout_seconds", 90.0))
        self.max_daily_cost = float(cfg.get("llm.max_daily_api_cost_usd", 2.0))
        self.alert_cost = float(cfg.get("llm.alert_cost_per_call_usd", 0.30))
        self.p_in = float(cfg.get("llm.price_input_per_mtok", 5.0))
        self.p_out = float(cfg.get("llm.price_output_per_mtok", 25.0))
        self.max_position_pct = float(cfg.get("risk.max_position_pct", 0.4))
        self.alerts_on = bool(cfg.get("alerts.enabled", True))
        # Le mandat : un brief ajoute au prompt de base. Choisi avant t0, fige ensuite.
        self.mandate = mandates.get(str(cfg.get("experiment.mandate", mandates.DEFAULT)))
        self.system = SYSTEM_PROMPT + mandates.prompt_section(self.mandate)
        self._client: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            # Lit ANTHROPIC_API_KEY depuis l'env. Le delai borne l'appel : sans
            # lui, un appel bloque gelerait toute la boucle, chien de garde compris.
            # Aucune relance automatique : un appel qui a depasse le delai a pu etre
            # facture entierement cote serveur, le rejouer doublerait la facture.
            # Le cycle suivant est dans 4 heures, on peut attendre.
            self._client = anthropic.Anthropic(timeout=self.timeout, max_retries=0)
        return self._client

    @staticmethod
    def _has_credentials() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    # ------------------------------------------------------------------
    def decide(self, ctx: BrainContext) -> list[Decision]:
        if not self._has_credentials():
            return self._fail(ctx, "cle API Claude absente : renseigne ANTHROPIC_API_KEY "
                                   "dans .env pour activer le trader (voir docs/cles-api.md)")

        spent = self.storage.api_cost_today()
        if spent >= self.max_daily_cost:
            msg = (f"plafond API journalier atteint ({spent:.2f} $ >= {self.max_daily_cost:.2f} $) : "
                   f"protection anti-emballement, aucune decision demandee au modele, hold force")
            self.storage.event("warning", "llm_brain", msg)
            if self.alerts_on:
                notify("Plafond API atteint", msg, priority="high", tags="warning")
            return [Decision(s, "hold", reasoning=msg, raw={"skipped": "budget"}) for s in ctx.markets]

        packet = build_packet(ctx)
        user_msg = (
            "Voici le dossier de ce cycle. Rends ta lecture du marche puis une decision "
            "pour CHAQUE symbole liste dans `marches`.\n\n"
            + json.dumps(packet, ensure_ascii=False, indent=1)
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
                },
                messages=[{"role": "user", "content": user_msg}],
            )
        except anthropic.AuthenticationError as e:
            return self._fail(ctx, f"cle API refusee par Anthropic ({e.status_code}) : verifier ANTHROPIC_API_KEY")
        except anthropic.RateLimitError as e:
            return self._fail(ctx, f"rate limit API : {e}")
        except anthropic.APITimeoutError:
            # Le serveur a peut-etre genere et facture la reponse entiere. On
            # provisionne un cout plafond pour que le garde-fou journalier le voie.
            est_in = (len(self.system) + len(user_msg)) // 3
            est = (est_in * self.p_in + self.max_tokens * self.p_out) / 1_000_000
            self.storage.record_api_cost("timeout-estime", est_in, self.max_tokens, est)
            return self._fail(ctx, f"appel API sans reponse apres {self.timeout:.0f} s "
                                   f"(cout provisionne {est:.3f} $)")
        except anthropic.APIStatusError as e:
            return self._fail(ctx, f"erreur API {e.status_code} : {e.message}")
        except anthropic.APIConnectionError as e:
            return self._fail(ctx, f"connexion API impossible : {e}")
        except Exception as e:  # rien venant de l'API ne doit tuer un cycle
            return self._fail(ctx, f"appel API en echec ({type(e).__name__}) : {e}")

        # --- cout ---
        u = response.usage
        cost = (u.input_tokens * self.p_in + u.output_tokens * self.p_out) / 1_000_000
        # la chaine renvoyee par l'API, pas celle de la config : si le modele
        # change sous nos pieds en cours de fenetre, on le verra
        served_model = str(getattr(response, "model", None) or self.model)
        self.storage.record_api_cost(served_model, u.input_tokens, u.output_tokens, cost)
        if cost > self.alert_cost:
            msg = (f"appel API couteux : {cost:.3f} $ (entree {u.input_tokens}, sortie {u.output_tokens} tokens), "
                   f"seuil d'alerte {self.alert_cost:.2f} $")
            self.storage.event("warning", "llm_brain", msg)
            if self.alerts_on:
                notify("Appel API couteux", msg, priority="default", tags="moneybag")

        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            return self._fail(ctx, f"le modele a refuse de repondre ({detail})")
        if response.stop_reason == "max_tokens":
            return self._fail(ctx, "reponse tronquee (max_tokens) : augmenter llm.max_tokens")

        thinking = " ".join(
            b.thinking for b in response.content if b.type == "thinking" and b.thinking
        ).strip()
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return self._fail(ctx, f"JSON invalide renvoye par le modele : {e}")

        market_view = str(data.get("market_view", "")).strip()
        by_symbol: dict[str, dict[str, Any]] = {}
        for d in data.get("decisions", []):
            by_symbol[str(d.get("symbol", ""))] = d

        out: list[Decision] = []
        for sym in ctx.markets:
            d = by_symbol.get(sym)
            if d is None:
                out.append(Decision(sym, "hold", reasoning="(symbole omis par le modele) hold",
                                    raw={"market_view": market_view, "thinking": thinking}))
                continue
            action = d.get("action", "hold")
            pct = max(0.0, min(float(d.get("size_pct_of_equity", 0.0)), self.max_position_pct))
            size = round(ctx.equity * pct, 2) if action == "buy" else None
            conf = max(0.0, min(float(d.get("confidence", 0.5)), 1.0))
            out.append(Decision(
                sym, action, size_quote=size, confidence=conf,
                reasoning=str(d.get("reasoning", "")).strip(),
                raw={
                    "market_view": market_view,
                    "bias": d.get("bias"),
                    "size_pct_of_equity": pct,
                    "thinking": thinking,
                    "usage": {"input": u.input_tokens, "output": u.output_tokens, "cost_usd": round(cost, 4)},
                },
            ))
        return out

    def _fail(self, ctx: BrainContext, why: str) -> list[Decision]:
        self.storage.event("warning", "llm_brain", why)
        return [Decision(s, "hold", reasoning=f"hold par defaut : {why}", raw={"error": why})
                for s in ctx.markets]
