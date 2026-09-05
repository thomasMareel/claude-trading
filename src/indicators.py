"""Indicateurs techniques en pandas pur. Pas de dependance lourde.

Convention : pendant la periode de chauffe, un indicateur vaut NaN, jamais
une valeur neutre inventee. latest_snapshot le rend alors `null` dans le
dossier de Claude, qui sait ainsi qu'il ne sait pas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def candles_to_df(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(
        [tuple(r) for r in rows],
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI de Wilder. 100 quand il n'y a eu que des hausses, 0 que des
    baisses, 50 quand rien n'a bouge, NaN pendant la chauffe."""
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_down = down.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_up / avg_down                      # inf si avg_down == 0
        out = 100 - 100 / (1 + rs)                  # -> 100 quand rs = inf
    flat = (avg_down == 0) & (avg_up == 0)          # 0/0 : rien n'a bouge
    return out.mask(flat, 50.0)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def roc(close: pd.Series, n: int) -> pd.Series:
    return close.pct_change(n)


def enrich(
    df: pd.DataFrame,
    *,
    ema_fast: int = 50,
    ema_slow: int = 200,
    rsi_period: int = 14,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Ajoute les colonnes d'indicateurs fournies a Claude."""
    df = df.copy()
    df["ema_fast"] = ema(df["close"], ema_fast)
    df["ema_slow"] = ema(df["close"], ema_slow)
    df["rsi"] = rsi(df["close"], rsi_period)
    df["atr"] = atr(df, atr_period)
    df["atr_pct"] = df["atr"] / df["close"]
    df["roc_5"] = roc(df["close"], 5)
    df["roc_20"] = roc(df["close"], 20)
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]
    df["hh_20"] = df["high"].rolling(20).max()
    df["ll_20"] = df["low"].rolling(20).min()
    return df


def latest_snapshot(df: pd.DataFrame) -> dict[str, float | str | None]:
    """Dernieres valeurs, arrondies, pour le journal et le dossier de Claude."""
    last = df.iloc[-1]

    def f(x, nd=4):
        try:
            v = float(x)
            return round(v, nd) if np.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    ef, es = f(last["ema_fast"], 6), f(last["ema_slow"], 6)
    trend = None if ef is None or es is None else ("up" if ef > es else "down" if ef < es else "flat")
    return {
        "close": f(last["close"], 6),
        "ema_fast": ef,
        "ema_slow": es,
        "rsi": f(last["rsi"], 1),
        "atr_pct": f(last["atr_pct"] * 100, 2),
        "roc_5_pct": f(last["roc_5"] * 100, 2),
        "roc_20_pct": f(last["roc_20"] * 100, 2),
        "vol_ratio": f(last["vol_ratio"], 2),
        "dist_hh20_pct": f((last["close"] / last["hh_20"] - 1) * 100, 2),
        "dist_ll20_pct": f((last["close"] / last["ll_20"] - 1) * 100, 2),
        "trend": trend,
    }
