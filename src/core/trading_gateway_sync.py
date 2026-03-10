from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from src.core.http_client import async_client
from src.web.models import Account, Position, Stock, TradingGateway


logger = logging.getLogger(__name__)


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        s = str(value).strip().replace(",", "")
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return int(value)
    try:
        s = str(value).strip().replace(",", "")
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def _infer_market(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        return "CN"
    if s.isdigit():
        if len(s) == 6:
            return "CN"
        if len(s) == 5:
            return "HK"
        return "CN"
    return "US"


def _pick_first_numeric(d: dict[str, Any], keys: list[str]) -> float | None:
    for k in keys:
        if k in d:
            v = _coerce_float(d.get(k))
            if v is not None:
                return v
    return None


def _parse_balance(payload: Any) -> float:
    if isinstance(payload, dict):
        v = _pick_first_numeric(
            payload,
            [
                "available_funds",
                "available_cash",
                "cash",
                "可用资金",
                "可用金额",
                "可用余额",
            ],
        )
        return float(v or 0.0)
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            v = _pick_first_numeric(
                first,
                [
                    "available_funds",
                    "available_cash",
                    "cash",
                    "可用资金",
                    "可用金额",
                    "可用余额",
                ],
            )
            return float(v or 0.0)
    return 0.0


def _parse_positions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("positions"), list):
        payload = payload.get("positions")
    if not isinstance(payload, list):
        return []

    items: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        symbol = (
            raw.get("symbol")
            or raw.get("security")
            or raw.get("证券代码")
            or raw.get("代码")
            or raw.get("stock_code")
        )
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            continue

        name = raw.get("name") or raw.get("证券名称") or raw.get("名称")
        name = str(name or symbol).strip() or symbol

        quantity = (
            _coerce_int(raw.get("quantity"))
            or _coerce_int(raw.get("volume"))
            or _coerce_int(raw.get("持仓"))
            or _coerce_int(raw.get("数量"))
            or _coerce_int(raw.get("股份余额"))
            or 0
        )
        available_quantity = (
            _coerce_int(raw.get("available_quantity"))
            or _coerce_int(raw.get("available_volume"))
            or _coerce_int(raw.get("can_use_volume"))
            or _coerce_int(raw.get("可用余额"))
            or _coerce_int(raw.get("可用"))
            or quantity
        )
        cost_price = (
            _coerce_float(raw.get("cost_price"))
            or _coerce_float(raw.get("avg_price"))
            or _coerce_float(raw.get("成本价"))
            or _coerce_float(raw.get("成本"))
            or 0.0
        )
        market = raw.get("market")
        market = str(market or _infer_market(symbol)).strip().upper()
        if market not in ("CN", "HK", "US"):
            market = _infer_market(symbol)

        items.append(
            {
                "symbol": symbol,
                "name": name,
                "market": market,
                "quantity": int(quantity),
                "available_quantity": int(available_quantity),
                "cost_price": float(cost_price),
                "raw": raw,
            }
        )
    return items


async def sync_gateway_positions(
    db: Session,
    gateway: TradingGateway,
    *,
    fetch_json: Callable[[str, dict[str, str]], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    if not gateway.enabled:
        return {"skipped": True, "reason": "disabled"}

    url = (gateway.gateway_url or "").rstrip("/")
    if not url:
        return {"skipped": True, "reason": "missing_gateway_url"}

    headers: dict[str, str] = {}
    if gateway.api_key:
        headers["X-API-Key"] = gateway.api_key

    async def _default_fetch_json(path: str, h: dict[str, str]) -> Any:
        async with async_client(proxy="") as client:
            resp = await client.get(f"{url}{path}", headers=h, timeout=15)
            resp.raise_for_status()
            return resp.json()

    fetch = fetch_json or _default_fetch_json
    now = datetime.now()

    try:
        balance_resp = await fetch("/api/v1/account", headers)
        positions_resp = await fetch("/api/v1/positions", headers)

        balance_payload = (
            balance_resp.get("data")
            if isinstance(balance_resp, dict) and "data" in balance_resp
            else balance_resp
        )
        positions_payload = (
            positions_resp.get("data")
            if isinstance(positions_resp, dict) and "data" in positions_resp
            else positions_resp
        )

        available_funds = float(_parse_balance(balance_payload))
        positions = _parse_positions(positions_payload)

        account_id = gateway.panwatch_account_id
        account = None
        if account_id:
            account = db.query(Account).filter(Account.id == account_id).first()
        if account is None:
            account = Account(name=f"外部/{gateway.name}", available_funds=0.0, enabled=True)
            db.add(account)
            db.commit()
            db.refresh(account)
            gateway.panwatch_account_id = account.id
            db.add(gateway)

        account.available_funds = available_funds
        db.add(account)

        current_keys: set[tuple[str, str]] = set()
        upserted = 0

        for item in positions:
            symbol = item["symbol"]
            market = item["market"]
            current_keys.add((symbol, market))

            stock = (
                db.query(Stock)
                .filter(Stock.symbol == symbol, Stock.market == market)
                .first()
            )
            if stock is None:
                stock = Stock(symbol=symbol, name=item["name"], market=market)
                db.add(stock)
                db.flush()
            else:
                if not stock.name and item["name"]:
                    stock.name = item["name"]

            existing = (
                db.query(Position)
                .filter(Position.account_id == account.id, Position.stock_id == stock.id)
                .first()
            )
            if existing is None:
                existing = Position(
                    account_id=account.id,
                    stock_id=stock.id,
                    cost_price=float(item["cost_price"]),
                    quantity=int(item["quantity"]),
                    invested_amount=float(item["cost_price"]) * int(item["quantity"]),
                )
                db.add(existing)
            else:
                existing.cost_price = float(item["cost_price"])
                existing.quantity = int(item["quantity"])
                existing.invested_amount = float(item["cost_price"]) * int(item["quantity"])
                db.add(existing)
            upserted += 1

        if gateway.panwatch_account_id:
            existing_positions = (
                db.query(Position)
                .join(Stock, Stock.id == Position.stock_id)
                .filter(Position.account_id == account.id)
                .all()
            )
            deleted = 0
            for pos in existing_positions:
                st = pos.stock
                if not st:
                    continue
                if (st.symbol, st.market) not in current_keys:
                    db.delete(pos)
                    deleted += 1
        else:
            deleted = 0

        gateway.last_synced_at = now
        gateway.last_error = ""
        db.add(gateway)
        db.commit()

        return {
            "success": True,
            "gateway_id": gateway.id,
            "account_id": account.id,
            "available_funds": available_funds,
            "positions_upserted": upserted,
            "positions_deleted": deleted,
        }
    except Exception as e:
        gateway.last_error = str(e)[:2000]
        gateway.last_synced_at = now
        db.add(gateway)
        db.commit()
        logger.warning(f"持仓同步失败: gateway={gateway.id} err={e}")
        return {"success": False, "gateway_id": gateway.id, "error": str(e)}
