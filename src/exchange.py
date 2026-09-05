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

from .config import Config, secret


class ExchangeError(RuntimeError):
    pass


class OrderUncertainError(ExchangeError):
    """Un ordre a ete ENVOYE et son resultat est inconnu (delai reseau
    depasse apres l'envoi, relectures en echec). Il a peut-etre ete
    execute. Ne JAMAIS le renvoyer aveuglement : c'est ainsi qu'un achat
    devient deux achats."""


class Exchange:
    def __init__(self, cfg: Config, *, trading: bool = False, testnet: bool = False):
        self.cfg = cfg
        self.trading = trading
        self.testnet = testnet
        self.id = str(cfg.get("exchange.id", "binance"))
        self.quote = str(cfg.get("exchange.quote", "USDT"))

        params: dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": 20_000,  # ms ; un appel qui traine ne doit pas geler la boucle
            "options": {"defaultType": "spot", "adjustForTimeDifference": True},
        }
        if trading:
            if testnet:
                key, sec = secret("BINANCE_TESTNET_API_KEY"), secret("BINANCE_TESTNET_API_SECRET")
                label = "BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET"
            else:
                key, sec = secret("BINANCE_API_KEY"), secret("BINANCE_API_SECRET")
                label = "BINANCE_API_KEY / BINANCE_API_SECRET"
            if not key or not sec:
                raise ExchangeError(
                    f"Mode trading demande mais {label} absent(s) du .env"
                )
            params["apiKey"] = key
            params["secret"] = sec

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
    @staticmethod
    def client_order_id(tag: str) -> str:
        """Identifiant d'ordre deterministe accepte par Binance
        (^[.A-Z:/a-z0-9_-]{1,36}$). Le meme tag redonne le meme id : si le
        reseau coupe apres l'envoi, on RETROUVE l'ordre au lieu de le refaire."""
        cleaned = "".join(ch for ch in tag if ch.isalnum() or ch in "-_")
        return cleaned[:36]

    def market_buy_quote(self, symbol: str, quote_amount: float, *, tag: str = "") -> dict[str, Any]:
        """Achat au marche pour un montant en quote (ex: 40 USDT).

        La creation d'ordre n'est JAMAIS rejouee aveuglement : sur un delai
        reseau, on relit l'ordre par son clientOrderId. S'il n'existe pas,
        l'achat n'a pas eu lieu et on le dit. S'il existe, on le prend.
        Sinon on leve OrderUncertainError et le moteur gele le book.
        """
        self._require_trading()
        cid = self.client_order_id(tag or f"B-{symbol}-{int(time.time())}")
        try:
            order = self._x.create_market_buy_order_with_cost(symbol, quote_amount, {"newClientOrderId": cid})
        except (ccxt.NetworkError, ccxt.RequestTimeout, ccxt.DDoSProtection) as e:
            order = self._recover_order(symbol, cid, e)
        except ccxt.ExchangeError as e:
            raise ExchangeError(str(e)) from e
        return self._normalize_fill(order, symbol, "buy")

    def market_sell_base(self, symbol: str, base_amount: float, *, tag: str = "") -> dict[str, Any]:
        self._require_trading()
        amt = self.amount_to_precision(symbol, base_amount)
        cid = self.client_order_id(tag or f"S-{symbol}-{int(time.time())}")
        try:
            order = self._x.create_order(symbol, "market", "sell", amt, None, {"newClientOrderId": cid})
        except (ccxt.NetworkError, ccxt.RequestTimeout, ccxt.DDoSProtection) as e:
            order = self._recover_order(symbol, cid, e)
        except ccxt.ExchangeError as e:
            raise ExchangeError(str(e)) from e
        return self._normalize_fill(order, symbol, "sell")

    def _recover_order(self, symbol: str, cid: str, err: Exception) -> dict[str, Any]:
        """Apres un delai reseau sur l'ENVOI d'un ordre : existe-t-il ?"""
        for _ in range(3):
            time.sleep(2.0)
            try:
                o = self._x.fetch_order(None, symbol, {"origClientOrderId": cid})
            except ccxt.OrderNotFound:
                # Binance ne l'a jamais recu : l'ordre n'a pas eu lieu, c'est un echec propre.
                raise ExchangeError(f"ordre {cid} jamais recu par l'exchange (delai reseau) : {err}") from err
            except (ccxt.NetworkError, ccxt.RequestTimeout, ccxt.DDoSProtection):
                continue
            except ccxt.ExchangeError as e:
                raise ExchangeError(str(e)) from e
            status = (o or {}).get("status")
            if status in ("closed", "filled") or float((o or {}).get("filled") or 0) > 0:
                return o
            if status in ("canceled", "rejected", "expired"):
                raise ExchangeError(f"ordre {cid} {status} chez l'exchange")
        raise OrderUncertainError(f"ordre {cid} envoye, resultat inconnu apres 3 relectures : {err}")

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
        if not order.get("average") or not order.get("filled"):
            time.sleep(0.5)
            order = self._retry(self._x.fetch_order, oid, symbol)
        base = symbol.split("/")[0]
        filled = float(order.get("filled") or 0.0)
        avg = float(order.get("average") or order.get("price") or 0.0)
        cost = float(order.get("cost") or filled * avg)
        if filled <= 0 or avg <= 0:
            raise ExchangeError(f"ordre {oid} non rempli : {order}")

        fee_quote, fee_base, fee_other = 0.0, 0.0, 0.0
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
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
                fee_other += cost * float(self.cfg.get("exchange.fee_rate", 0.001))

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
