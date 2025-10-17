import asyncio
from typing import Optional, Sequence

from .exchange_client import DerivativeExchangeClient
from .indicator_service import IndicatorService
from .order_executor import OrderExecutor
from .risk_manager import DerivativeRiskLimits, DerivativeRiskManager
from .trend_trader import DerivativeStrategyConfig, DerivativeTrendTrader


async def run_trend_strategy(
    symbol: str,
    config: Optional[DerivativeStrategyConfig] = None,
    leverage: float = 3.0,
    margin_mode: str = "cross",
) -> None:
    """
    运行单标的趋势策略，简化外部调用。
    """
    strategy_config = config or DerivativeStrategyConfig(symbol=symbol)
    exchange = DerivativeExchangeClient(leverage=leverage, margin_mode=margin_mode)
    indicator_service = IndicatorService()
    risk_manager = DerivativeRiskManager(
        strategy_config.risk_limits,
        contract_multiplier=strategy_config.contract_multiplier,
    )
    order_executor = OrderExecutor(exchange)
    trader = DerivativeTrendTrader(
        exchange_client=exchange,
        config=strategy_config,
        indicator_service=indicator_service,
        order_executor=order_executor,
        risk_manager=risk_manager,
    )
    await trader.run()


def launch(symbols: Sequence[str], **kwargs) -> None:
    """
    简单的同步入口，按顺序运行每个交易对策略。
    """
    async def _main():
        tasks = [run_trend_strategy(symbol, **kwargs) for symbol in symbols]
        await asyncio.gather(*tasks)

    asyncio.run(_main())
