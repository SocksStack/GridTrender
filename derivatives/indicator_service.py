import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


class TrendDirection(Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass(slots=True)
class IndicatorSnapshot:
    close: float
    ema_fast: float
    ema_slow: float
    adx: float
    atr: float
    donchian_high: float
    donchian_low: float
    keltner_upper: float
    keltner_lower: float
    trend_strength: float
    volatility_ratio: float

    @property
    def direction(self) -> TrendDirection:
        if self.ema_fast > self.ema_slow and self.adx > 0:
            return TrendDirection.LONG
        if self.ema_fast < self.ema_slow and self.adx > 0:
            return TrendDirection.SHORT
        return TrendDirection.FLAT


class IndicatorService:
    """
    提供趋势策略所需指标的计算与封装。
    输入为 ccxt fetch_ohlcv 返回的数据列表，输出为 IndicatorSnapshot。
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)

    @staticmethod
    def _to_dataframe(ohlcv: list[list[float]]) -> pd.DataFrame:
        df = pd.DataFrame(
            ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        return df

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        prev_close = close.shift(1)
        tr_components = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        )
        tr = tr_components.max(axis=1)
        atr = tr.ewm(alpha=1 / period, adjust=False).mean()
        return atr

    @staticmethod
    def _adx(df: pd.DataFrame, period: int) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]

        up_move = high.diff()
        down_move = low.diff() * -1

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        tr_components = pd.concat(
            [
                high - low,
                (high - close.shift(1)).abs(),
                (low - close.shift(1)).abs(),
            ],
            axis=1,
        )
        tr = tr_components.max(axis=1)
        atr = tr.ewm(alpha=1 / period, adjust=False).mean()

        plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
            alpha=1 / period, adjust=False
        ).mean() / atr
        minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
            alpha=1 / period, adjust=False
        ).mean() / atr

        data_sum = plus_di + minus_di
        dx = (plus_di - minus_di).abs() / data_sum.replace(0, np.nan) * 100
        adx = dx.ewm(alpha=1 / period, adjust=False).mean()
        return adx.fillna(0)

    @staticmethod
    def _donchian(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series]:
        return (
            df["high"].rolling(period).max(),
            df["low"].rolling(period).min(),
        )

    def build_snapshot(
        self,
        signal_ohlcv: list[list[float]],
        execution_ohlcv: list[list[float]],
        *,
        ema_fast: int,
        ema_slow: int,
        adx_period: int,
        atr_period: int,
        donchian_period: int,
        keltner_multiplier: float,
    ) -> Optional[IndicatorSnapshot]:
        if not signal_ohlcv or not execution_ohlcv:
            self.logger.warning("指标计算失败：OHLCV 数据为空")
            return None

        signal_df = self._to_dataframe(signal_ohlcv)
        exec_df = self._to_dataframe(execution_ohlcv)

        ema_fast_series = self._ema(signal_df["close"], ema_fast)
        ema_slow_series = self._ema(signal_df["close"], ema_slow)
        adx_series = self._adx(signal_df, adx_period)
        atr_series = self._atr(exec_df, atr_period)
        donchian_high, donchian_low = self._donchian(signal_df, donchian_period)
        keltner_mid = self._ema(signal_df["close"], ema_fast)
        keltner_upper = keltner_mid + keltner_multiplier * atr_series.reindex(
            signal_df.index, method="ffill"
        )
        keltner_lower = keltner_mid - keltner_multiplier * atr_series.reindex(
            signal_df.index, method="ffill"
        )

        latest_index = signal_df.index[-1]
        close_price = float(signal_df["close"].iloc[-1])
        atr_value = float(atr_series.iloc[-1])
        ema_fast_value = float(ema_fast_series.iloc[-1])
        ema_slow_value = float(ema_slow_series.iloc[-1])
        adx_value = float(adx_series.iloc[-1])
        donchian_high_value = float(donchian_high.iloc[-1])
        donchian_low_value = float(donchian_low.iloc[-1])
        keltner_upper_value = float(keltner_upper.iloc[-1])
        keltner_lower_value = float(keltner_lower.iloc[-1])

        if atr_value <= 0:
            self.logger.debug("ATR 无效，跳过指标快照记录")
            return None

        trend_strength = (close_price - ema_slow_value) / atr_value
        volatility_ratio = atr_value / close_price if close_price else 0.0

        snapshot = IndicatorSnapshot(
            close=close_price,
            ema_fast=ema_fast_value,
            ema_slow=ema_slow_value,
            adx=adx_value,
            atr=atr_value,
            donchian_high=donchian_high_value,
            donchian_low=donchian_low_value,
            keltner_upper=keltner_upper_value,
            keltner_lower=keltner_lower_value,
            trend_strength=float(trend_strength),
            volatility_ratio=float(volatility_ratio),
        )

        self.logger.debug(
            "指标快照@%s | close=%.4f ema_fast=%.4f ema_slow=%.4f adx=%.2f atr=%.4f",
            latest_index.isoformat(),
            close_price,
            ema_fast_value,
            ema_slow_value,
            adx_value,
            atr_value,
        )
        return snapshot
