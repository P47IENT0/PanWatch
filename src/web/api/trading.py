from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.core.trading_gateway_sync import sync_gateway_positions
from src.web.database import get_db
from src.web.models import Account, Position, Stock, TradingGateway


router = APIRouter()


class TradingGatewayCreate(BaseModel):
    name: str
    gateway_url: str
    api_key: str = ""
    broker_type: str = "ths"
    enabled: bool = True
    broker_account_id: str = ""


class TradingGatewayUpdate(BaseModel):
    name: str | None = None
    gateway_url: str | None = None
    api_key: str | None = None
    broker_type: str | None = None
    enabled: bool | None = None
    broker_account_id: str | None = None


class TradingGatewayResponse(BaseModel):
    id: int
    name: str
    broker_type: str
    gateway_url: str
    enabled: bool
    panwatch_account_id: int | None
    broker_account_id: str
    last_synced_at: datetime | None
    last_error: str


def _gateway_to_response(g: TradingGateway) -> TradingGatewayResponse:
    return TradingGatewayResponse(
        id=int(g.id),
        name=str(g.name),
        broker_type=str(g.broker_type or ""),
        gateway_url=str(g.gateway_url),
        enabled=bool(g.enabled),
        panwatch_account_id=int(g.panwatch_account_id)
        if g.panwatch_account_id
        else None,
        broker_account_id=str(g.broker_account_id or ""),
        last_synced_at=g.last_synced_at,
        last_error=str(g.last_error or ""),
    )


@router.get("/trading/gateways", response_model=list[TradingGatewayResponse])
def list_gateways(db: Session = Depends(get_db)):
    gateways = db.query(TradingGateway).order_by(TradingGateway.id.asc()).all()
    return [_gateway_to_response(g) for g in gateways]


@router.post("/trading/gateways", response_model=TradingGatewayResponse)
def create_gateway(payload: TradingGatewayCreate, db: Session = Depends(get_db)):
    g = TradingGateway(
        name=payload.name,
        broker_type=payload.broker_type,
        gateway_url=payload.gateway_url,
        api_key=payload.api_key,
        enabled=payload.enabled,
        broker_account_id=payload.broker_account_id,
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    return _gateway_to_response(g)


@router.put("/trading/gateways/{gateway_id}", response_model=TradingGatewayResponse)
def update_gateway(gateway_id: int, payload: TradingGatewayUpdate, db: Session = Depends(get_db)):
    g = db.query(TradingGateway).filter(TradingGateway.id == gateway_id).first()
    if not g:
        raise HTTPException(404, "gateway not found")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(g, k, v)
    db.add(g)
    db.commit()
    db.refresh(g)
    return _gateway_to_response(g)


@router.post("/trading/gateways/{gateway_id}/sync")
async def sync_gateway(gateway_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    g = db.query(TradingGateway).filter(TradingGateway.id == gateway_id).first()
    if not g:
        raise HTTPException(404, "gateway not found")
    return await sync_gateway_positions(db, g)


@router.get("/trading/gateways/{gateway_id}/portfolio")
def get_gateway_portfolio(gateway_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    g = db.query(TradingGateway).filter(TradingGateway.id == gateway_id).first()
    if not g:
        raise HTTPException(404, "gateway not found")
    if not g.panwatch_account_id:
        return {"account": None, "positions": []}

    acc = db.query(Account).filter(Account.id == g.panwatch_account_id).first()
    if not acc:
        return {"account": None, "positions": []}

    positions = (
        db.query(Position, Stock)
        .join(Stock, Stock.id == Position.stock_id)
        .filter(Position.account_id == acc.id)
        .order_by(Position.sort_order.asc(), Position.id.asc())
        .all()
    )
    pos_data = []
    for pos, st in positions:
        pos_data.append(
            {
                "position_id": int(pos.id),
                "stock_id": int(st.id),
                "symbol": str(st.symbol),
                "name": str(st.name),
                "market": str(st.market),
                "cost_price": float(pos.cost_price),
                "quantity": int(pos.quantity),
                "invested_amount": float(pos.invested_amount or 0.0),
                "trading_style": str(pos.trading_style or "swing"),
            }
        )

    return {
        "account": {
            "id": int(acc.id),
            "name": str(acc.name),
            "available_funds": float(acc.available_funds or 0.0),
            "enabled": bool(acc.enabled),
        },
        "positions": pos_data,
    }
