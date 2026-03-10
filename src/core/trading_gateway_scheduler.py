from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import Settings
from src.core.trading_gateway_sync import sync_gateway_positions
from src.web.database import SessionLocal
from src.web.models import TradingGateway


logger = logging.getLogger(__name__)


class TradingGatewayScheduler:
    def __init__(self, *, timezone: str = "UTC", interval_seconds: int = 30):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.interval_seconds = max(10, int(interval_seconds))
        self._running = False

    async def _sync_job(self):
        if self._running:
            return
        self._running = True
        db = SessionLocal()
        try:
            gateways = db.query(TradingGateway).filter(TradingGateway.enabled == True).all()
            ok = 0
            failed = 0
            for g in gateways:
                res = await sync_gateway_positions(db, g)
                if res.get("success") is True:
                    ok += 1
                elif res.get("skipped") is True:
                    continue
                else:
                    failed += 1
            if gateways:
                logger.info(
                    "交易网关同步完成: enabled=%s ok=%s failed=%s",
                    len(gateways),
                    ok,
                    failed,
                )
        except Exception as e:
            logger.exception(f"交易网关同步异常: {e}")
        finally:
            db.close()
            self._running = False

    def start(self):
        self.scheduler.add_job(
            self._sync_job,
            "interval",
            seconds=self.interval_seconds,
            id="trading_gateway_sync",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        logger.info(f"交易网关同步调度器已启动，间隔 {self.interval_seconds}s")

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("交易网关同步调度器已关闭")


def build_trading_gateway_scheduler() -> TradingGatewayScheduler:
    settings = Settings()
    return TradingGatewayScheduler(
        timezone=settings.app_timezone,
        interval_seconds=settings.trading_sync_interval_seconds,
    )
