import abc
import asyncio
import logging
from typing import Awaitable, Callable, Optional


class AbstractTrader(abc.ABC):
    """
    异步交易器基础框架，提供统一的生命周期管理。
    """

    def __init__(self, loop_interval: float = 60.0) -> None:
        self.loop_interval = loop_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.logger = logging.getLogger(self.__class__.__name__)

    async def run(self) -> None:
        if self._running:
            return
        await self.initialize()
        self._running = True
        while self._running:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("交易循环异常: %s", exc)
            await asyncio.sleep(self.loop_interval)
        await self.shutdown()

    async def stop(self) -> None:
        self._running = False

    async def start_background(self, create_task: Callable[[Awaitable], asyncio.Task] = asyncio.create_task) -> None:
        if self._task and not self._task.done():
            return
        self._task = create_task(self.run())

    @abc.abstractmethod
    async def initialize(self) -> None:
        ...

    @abc.abstractmethod
    async def step(self) -> None:
        ...

    @abc.abstractmethod
    async def shutdown(self) -> None:
        ...
