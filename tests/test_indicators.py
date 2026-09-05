"""Les indicateurs alimentent le dossier de Claude : une valeur inventee
pendant la chauffe ou un RSI qui masque le sur-achat sont des mensonges
envoyes au trader."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.indicators import candles_to_df, enrich, latest_snapshot, rsi  # noqa: E402


def _series(values):
    return pd.Series([float(v) for v in values])


def test_rsi_is_nan_during_warmup_not_a_fake_50():
    r = rsi(_series(range(100, 140)), 14)
    assert r.iloc[:14].isna().all()
    assert not math.isnan(r.iloc[14])


def test_rsi_is_100_on_pure_uptrend():
    r = rsi(_series(range(100, 160)), 14)
    assert abs(r.iloc[-1] - 100.0) < 1e-9


def test_rsi_is_0_on_pure_downtrend():
    r = rsi(_series(range(160, 100, -1)), 14)
    assert abs(r.iloc[-1] - 0.0) < 1e-9


def test_rsi_is_50_when_nothing_moves():
    r = rsi(_series([100.0] * 40), 14)
    assert abs(r.iloc[-1] - 50.0) < 1e-9


def test_rsi_is_between_0_and_100_on_noise():
    rng = np.random.default_rng(7)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.01, 300))
    r = rsi(pd.Series(closes), 14).dropna()
    assert (r >= 0).all() and (r <= 100).all()
    assert 30 < r.mean() < 70


def test_snapshot_reports_null_not_fake_values_when_history_is_short():
    t0 = 1_700_000_000_000
    rows = [[t0 + i * 14_400_000, 100 + i, 101 + i, 99 + i, 100 + i, 10.0] for i in range(60)]
    snap = latest_snapshot(enrich(candles_to_df(rows)))
    assert snap["ema_fast"] is not None      # 60 bougies suffisent a l'EMA50
    assert snap["ema_slow"] is None          # mais pas a l'EMA200
    assert snap["trend"] is None             # donc pas de tendance inventee
    assert snap["rsi"] == 100.0              # hausse pure : sur-achat visible, pas masque
