"""Acces a l'exchange via ccxt.

Deux usages :
  - donnees de marche (toujours autorise, meme sans cle API)
  - passage d'ordres (uniquement si le client a ete construit avec
    trading=True, ce que seul le mode live fait)

Les cles ne sont lues qu'ici, depuis l'environnement, et ne sortent
jamais de cet objet.
"""
from __future__ import annotations

import time
from typing import Any

import ccxt

from . import venues
from .config import Config, secret


class ExchangeError(RuntimeError):
    pass


class OrderUncertainError(ExchangeError):
    """Un ordre a ete ENVOYE et son resultat est inconnu (delai reseau
    depasse apres l'envoi, relectures en echec). Il a peut-etre ete
    execute. Ne JAMAIS le renvoyer aveuglement : c'est ainsi qu'un achat
    devient deux achats."""


class FeesUnknownError(ExchangeError):
    """Les frais reellement preleves n'ont pas pu etre lus. Sur une strategie
    qui vise 2 % de marge, compter des frais estimes revient a fausser le prix
    de revient, donc le prix de revente, donc le resultat. On refuse plutot
    que de deviner."""


class Exchange:
    def __init__(self, cfg: Config, *, trading: bool = False, testnet: bool = False):
        self.cfg = cfg
        self.trading = trading
        self.testnet = testnet
        self.id = str(cfg.get("exchange.id", "myokx"))
        self.quote = str(cfg.get("exchange.quote", "EUR"))
        self.venue = venues.get(self.id)

        params: dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": 20_000,  # ms ; un appel qui traine ne doit pas geler la boucle
            "options": {"defaultType": "spot", "adjustForTimeDifference": True},
        }
        if trading:
            prefix = self.venue.env_prefix + ("_TESTNET" if testnet else "")
            manquants = []
            for champ, nom in self.venue.env_names().items():
                nom = nom.replace(self.venue.env_prefix, prefix, 1)
                val = secret(nom)
                if not val:
                    manquants.append(nom)
                else:
                    params[champ] = val
            if manquants:
                raise ExchangeError(
                    f"Mode trading demande sur {self.venue.nom} mais {', '.join(manquants)} "
                    f"absent(s) du .env. Lance scripts/coller_cles.bat."
                )

        klass = getattr(ccxt, self.id)
        self._x = klass(params)
        if testnet:
            self._x.set_sandbox_mode(True)
        self._markets_loaded = False

    # ---------------- marches ----------------
    def load_markets(self) -> None:
        if not self._markets_loaded:
            self._retry(self._x.load_markets)
            self._markets_loaded = True

    def market(self, symbol: str) -> dict[str, Any]:
        self.load_markets()
        if symbol not in self._x.markets:
            raise ExchangeError(f"symbole inconnu sur {self.id} : {symbol}")
        return self._x.markets[symbol]

    def min_notional(self, symbol: str) -> float:
        """Valeur minimale d'un ordre en quote, selon l'exchange."""
        m = self.market(symbol)
        cost_min = (m.get("limits", {}).get("cost") or {}).get("min")
        return float(cost_min) if cost_min else 5.0

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        self.load_markets()
        return float(self._x.amount_to_precision(symbol, amount))

    # ---------------- donnees ----------------
    def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 300, since: int | None = None
    ) -> list[list[float]]:
        return self._retry(self._x.fetch_ohlcv, symbol, timeframe, since, limit)

    def fetch_ohlcv_full(
        self, symbol: str, timeframe: str, since: int, page: int = 1000
    ) -> list[list[float]]:
        """Pagination complete depuis `since` jusqu'a maintenant."""
        out: list[list[float]] = []
        cursor = since
        tf_ms = self._x.parse_timeframe(timeframe) * 1000
        while True:
            batch = self.fetch_ohlcv(symbol, timeframe, limit=page, since=cursor)
            if not batch:
                break
            out.extend(batch)
            last = int(batch[-1][0])
            if len(batch) < page or last + tf_ms >= int(time.time() * 1000):
                break
            cursor = last + tf_ms
        return out

    def fetch_price(self, symbol: str) -> float:
        t = self._retry(self._x.fetch_ticker, symbol)
        px = t.get("last") or t.get("close")
        if not px:
            raise ExchangeError(f"pas de prix pour {symbol}")
        return float(px)

    def fetch_prices(self, symbols: list[str], *, strict: bool = True) -> dict[str, float]:
        """strict=True (cycle) : tous les prix ou une erreur.
        strict=False (chien de garde) : ce qui est disponible, le reste manque."""
        tickers = self._retry(self._x.fetch_tickers, symbols)
        out: dict[str, float] = {}
        for s in symbols:
            t = tickers.get(s)
            if t and (t.get("last") or t.get("close")):
                out[s] = float(t.get("last") or t.get("close"))
        missing = [s for s in symbols if s not in out]
        if missing and strict:
            raise ExchangeError(f"pas de prix pour {missing}")
        return out

    def fetch_balances(self) -> dict[str, float]:
        """Soldes libres par actif, pour la reconciliation avec le book."""
        if not self.trading:
            raise ExchangeError("fetch_balances necessite trading=True")
        bal = self._retry(self._x.fetch_balance)
        free = bal.get("free") or {}
        return {k: float(v) for k, v in free.items() if v}

    def fetch_balance_quote(self) -> float:
        return self.fetch_balances().get(self.quote, 0.0)

    # ---------------- ordres (live uniquement) ----------------
    def client_order_id(self, tag: str) -> str:
        """Identifiant deterministe, au format accepte par CETTE plateforme."""
        return self.venue.client_order_id(tag)

    def market_buy_quote(self, symbol: str, quote_amount: float, *, tag: str = "") -> dict[str, Any]:
        """Achat au marche pour un montant en quote. Paie le tarif taker."""
        self._require_trading()
        cid = self.client_order_id(tag or f"B{symbol}{int(time.time())}")
        return self._submit(
            lambda: self._x.create_market_buy_order_with_cost(
                symbol, quote_amount, {self.venue.cid_pose: cid}),
            symbol, "buy", cid,
        )

    def market_sell_base(self, symbol: str, base_amount: float, *, tag: str = "") -> dict[str, Any]:
        self._require_trading()
        amt = self.amount_to_precision(symbol, base_amount)
        cid = self.client_order_id(tag or f"S{symbol}{int(time.time())}")
        return self._submit(
            lambda: self._x.create_order(
                symbol, "market", "sell", amt, None, {self.venue.cid_pose: cid}),
            symbol, "sell", cid,
        )

    # ---------------- ordres limites : le coeur de la grille ----------------
    def limit_order(
        self, symbol: str, side: str, amount_base: float, price: float, *,
        tag: str = "", post_only: bool = True,
    ) -> dict[str, Any]:
        """Pose un ordre limite et RETOURNE SANS ATTENDRE son execution.

        C'est la difference de fond avec un ordre au marche : l'ordre reste
        dans le carnet jusqu'a ce que le prix vienne le chercher, et il paie
        le tarif maker, moins cher. post_only garantit qu'il ne sera jamais
        execute immediatement contre le carnet : si le prix a bouge au point
        de rendre l'ordre preneur, la plateforme le REFUSE au lieu de le
        passer au tarif taker. Sans cela, une grille peut payer le tarif fort
        exactement quand le marche s'emballe.
        """
        self._require_trading()
        if side not in ("buy", "sell"):
            raise ExchangeError(f"sens invalide : {side!r}")
        if post_only and not self.venue.post_only:
            raise ExchangeError(f"{self.venue.nom} ne propose pas d'ordre strictement maker")
        amt = self.amount_to_precision(symbol, amount_base)
        px = float(self._x.price_to_precision(symbol, price))
        cid = self.client_order_id(tag or f"L{side}{symbol}{int(time.time())}")
        params: dict[str, Any] = {self.venue.cid_pose: cid}
        if post_only:
            params["postOnly"] = True
        try:
            order = self._x.create_order(symbol, "limit", side, amt, px, params)
        except ccxt.OperationFailed as e:
            order = self._recover_order(symbol, cid, e)
        except ccxt.ExchangeError as e:
            raise ExchangeError(str(e)) from e
        return {
            "exchange_id": str(order.get("id")),
            "client_order_id": cid,
            "symbol": symbol, "side": side,
            "amount_base": float(order.get("amount") or amt),
            "price": float(order.get("price") or px),
            "status": str(order.get("status") or "open"),
        }

    def cancel(self, symbol: str, order_id: str | None = None, *, tag: str = "") -> bool:
        """Annule un ordre. Vrai s'il n'est plus au carnet apres l'appel,
        y compris s'il avait deja disparu (deja execute ou deja annule)."""
        self._require_trading()
        params = {} if order_id else {self.venue.cid_relecture: self.client_order_id(tag)}
        try:
            self._retry(self._x.cancel_order, order_id, symbol, params)
            return True
        except ccxt.OrderNotFound:
            return True
        except ExchangeError:
            raise
        except ccxt.BaseError as e:
            raise ExchangeError(f"annulation impossible : {e}") from e

    def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Les ordres encore au carnet. Source de verite pour reconstituer
        une grille apres un redemarrage."""
        self._require_trading()
        out = []
        for o in self._retry(self._x.fetch_open_orders, symbol) or []:
            out.append({
                "exchange_id": str(o.get("id")),
                "client_order_id": o.get("clientOrderId"),
                "symbol": o.get("symbol"), "side": o.get("side"),
                "price": float(o.get("price") or 0), "amount_base": float(o.get("amount") or 0),
                "filled": float(o.get("filled") or 0), "status": o.get("status"),
            })
        return out

    def _submit(self, create, symbol: str, side: str, cid: str) -> dict[str, Any]:
        """Envoi d'un ordre puis lecture de son remplissage.

        Trois issues, et seulement trois :
          - echec PROPRE (ExchangeError) : l'exchange a refuse avant d'executer,
            ou n'a jamais recu l'ordre, ou l'a annule ; rien n'est detenu ;
          - remplissage lu : on le rend ;
          - OrderUncertainError : l'ordre a pu etre execute et l'on ne sait pas
            ce que l'on detient. Le moteur gele alors les achats.
        Une fois l'envoi parti, AUCUNE autre exception ne peut sortir d'ici :
        c'est ainsi qu'un achat ne devient jamais deux achats.
        """
        try:
            order = create()
        except ccxt.OperationFailed as e:
            # parent de NetworkError, RequestTimeout, DDoSProtection, BadResponse
            # et des codes Binance -1000/-1001/-1006 "execution status unknown"
            order = self._recover_order(symbol, cid, e)
        except ccxt.ExchangeError as e:
            # refus clair AVANT execution : solde, LOT_SIZE, MIN_NOTIONAL...
            raise ExchangeError(str(e)) from e
        try:
            return self._normalize_fill(order, symbol, side)
        except ExchangeError:
            raise                       # incertitude, ou refus avere (annule, rejete)
        except Exception as e:
            raise OrderUncertainError(f"ordre {cid} accepte, remplissage illisible : {e!r}") from e

    def _recover_order(self, symbol: str, cid: str, err: Exception) -> dict[str, Any]:
        """L'envoi a echoue cote reseau : l'ordre existe-t-il chez l'exchange ?

        Seul OrderNotFound, confirme deux fois, vaut "jamais recu". Tout le
        reste, relecture illisible comprise, est incertain.
        """
        not_found = 0
        for _ in range(4):
            time.sleep(2.0)
            try:
                o = self._x.fetch_order(None, symbol, {self.venue.cid_relecture: cid})
            except ccxt.OrderNotFound:
                not_found += 1
                if not_found >= 2:
                    raise ExchangeError(f"ordre {cid} jamais recu par l'exchange (delai reseau) : {err}") from err
                continue
            except ccxt.BaseError:
                continue
            o = o or {}
            status = str(o.get("status") or "").lower()
            if float(o.get("filled") or 0) > 0 or status in ("closed", "filled"):
                return o
            if status in ("canceled", "rejected", "expired"):
                raise ExchangeError(f"ordre {cid} {status} chez l'exchange, rien execute")
        raise OrderUncertainError(f"ordre {cid} envoye, resultat inconnu apres relectures : {err}")

    def _fees_from_trades(self, oid, symbol: str) -> list[dict[str, Any]]:
        """Un ordre relu par fetch_order n'a pas toujours ses frais : on les
        cherche dans les executions.

        Un echec est signale, jamais avale : compter des frais faux sur une
        strategie a 2 % de marge est pire qu'un plantage, car le prix de
        revente calcule serait faux sans que rien ne l'indique.
        """
        if not self.venue.frais_lisibles:
            raise FeesUnknownError(
                f"{self.venue.nom} ne permet pas de relire les frais reellement preleves "
                f"(ordre {oid}). Le prix de revient serait une estimation."
            )
        try:
            trades = self._retry(self._x.fetch_order_trades, oid, symbol)
        except ExchangeError as e:
            raise FeesUnknownError(f"frais illisibles pour l'ordre {oid} : {e}") from e
        return [t["fee"] for t in (trades or []) if t.get("fee")]

    def _normalize_fill(self, order: dict[str, Any], symbol: str, side: str) -> dict[str, Any]:
        """Ramene un ordre ccxt a : prix moyen, quantite NETTE detenue,
        valeur en quote et frais en quote.

        Binance preleve les frais sur l'actif recu : en base pour un achat,
        en quote pour une vente, ou en BNB si la remise est activee. Le book
        doit refleter ce que l'on detient vraiment, sinon la vente suivante
        echouerait pour solde insuffisant. Invariant pour un achat :
        value_quote + fee_quote == quote reellement sorti du compte.
        """
        oid = order.get("id")
        # Les market orders Binance reviennent parfois sans 'average' : on re-lit.
        # L'ordre EXISTE a ce stade : une relecture impossible est une incertitude.
        if not order.get("average") or not order.get("filled"):
            time.sleep(0.5)
            try:
                order = self._retry(self._x.fetch_order, oid, symbol)
            except Exception as e:
                raise OrderUncertainError(f"ordre {oid} cree, relecture impossible : {e}") from e
        base = symbol.split("/")[0]
        filled = float(order.get("filled") or 0.0)
        avg = float(order.get("average") or order.get("price") or 0.0)
        cost = float(order.get("cost") or filled * avg)
        if filled <= 0 or avg <= 0:
            status = str(order.get("status") or "").lower()
            if status in ("canceled", "rejected", "expired"):
                raise ExchangeError(f"ordre {oid} {status}, rien execute")
            raise OrderUncertainError(f"ordre {oid} sans remplissage lisible (statut {status!r})")

        fee_rate = float(self.cfg.get("exchange.fee_rate", 0.001))
        estimation_ok = bool(self.cfg.get("exchange.autoriser_frais_estimes", False))
        fee_quote, fee_base, fee_other = 0.0, 0.0, 0.0
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        if not fees:
            try:
                fees = self._fees_from_trades(oid, symbol)
            except FeesUnknownError:
                # plateforme incapable de les rendre, ou panne reseau : c'est
                # fatal, sauf si l'utilisateur a explicitement accepte l'estimation
                if not estimation_ok:
                    raise
                fees = []
        for f in fees:
            if not f:
                continue
            cur, amt = f.get("currency"), float(f.get("cost") or 0.0)
            if cur == self.quote:
                fee_quote += amt
            elif cur == base:
                fee_base += amt
            else:
                # BNB ou autre : paye hors de ce book, estime prudemment
                fee_other += cost * fee_rate
        if not fees:
            # Aucun frais lisible malgre la relecture des executions. On applique
            # le taux configure, mais on le DIT : sur une marge de 2 %, une erreur
            # de frais se voit dans le resultat sans qu'on sache d'ou elle vient.
            if not estimation_ok:
                raise FeesUnknownError(
                    f"ordre {oid} : aucun frais lisible, ni sur l'ordre ni sur ses executions. "
                    f"Passer exchange.autoriser_frais_estimes a true pour accepter une estimation "
                    f"au taux de {fee_rate:.3%}, en sachant que le prix de revient sera approximatif."
                )
            if side == "buy":
                fee_base = filled * fee_rate
            else:
                fee_quote = cost * fee_rate

        if side == "buy":
            return {
                "exchange_id": str(oid), "price": avg,
                "amount_base": filled - fee_base,
                "value_quote": cost - fee_base * avg,
                "fee_quote": fee_base * avg + fee_quote + fee_other,
            }
        return {
            "exchange_id": str(oid), "price": avg, "amount_base": filled,
            "value_quote": cost, "fee_quote": fee_quote + fee_base * avg + fee_other,
        }

    def _require_trading(self) -> None:
        if not self.trading:
            raise ExchangeError(
                "Tentative de passer un ordre sur un client sans droits de trading."
                " C'est un bug de garde-fou, pas un cas normal."
            )

    # ---------------- resilience ----------------
    def _retry(self, fn, *args, attempts: int = 4, **kwargs):
        delay = 1.0
        last: Exception | None = None
        for _ in range(attempts):
            try:
                return fn(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.RequestTimeout, ccxt.DDoSProtection) as e:
                last = e
                time.sleep(delay)
                delay *= 2
            except ccxt.ExchangeError as e:
                raise ExchangeError(str(e)) from e
        raise ExchangeError(f"echec reseau apres {attempts} tentatives : {last}")
