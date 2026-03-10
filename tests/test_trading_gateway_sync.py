import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.trading_gateway_sync import sync_gateway_positions
from src.web.models import Account, Base, Position, Stock, TradingGateway


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_trading_gateway_sync_creates_account_stocks_positions_and_deletes_missing():
    db = _make_session()
    try:
        g = TradingGateway(name="ths", gateway_url="http://example", api_key="k")
        db.add(g)
        db.commit()
        db.refresh(g)

        async def fetch_json(path: str, headers: dict[str, str]):
            assert headers.get("X-API-Key") == "k"
            if path == "/api/v1/account":
                return {"data": [{"available_funds": 1234.5}]}
            if path == "/api/v1/positions":
                return {
                    "data": [
                        {
                            "symbol": "600519",
                            "name": "贵州茅台",
                            "market": "CN",
                            "quantity": 10,
                            "cost_price": 100.0,
                        },
                        {
                            "symbol": "00700",
                            "name": "腾讯控股",
                            "market": "HK",
                            "quantity": 20,
                            "cost_price": 200.0,
                        },
                    ]
                }
            raise AssertionError(path)

        res = asyncio.run(sync_gateway_positions(db, g, fetch_json=fetch_json))
        assert res["success"] is True
        assert res["positions_upserted"] == 2

        db.refresh(g)
        assert g.panwatch_account_id is not None

        acc = db.query(Account).filter(Account.id == g.panwatch_account_id).first()
        assert acc is not None
        assert float(acc.available_funds) == 1234.5

        stocks = {(s.symbol, s.market): s for s in db.query(Stock).all()}
        assert ("600519", "CN") in stocks
        assert ("00700", "HK") in stocks

        positions = db.query(Position).filter(Position.account_id == acc.id).all()
        assert len(positions) == 2

        async def fetch_json2(path: str, headers: dict[str, str]):
            if path == "/api/v1/account":
                return {"data": [{"available_funds": 2000.0}]}
            if path == "/api/v1/positions":
                return {
                    "data": [
                        {
                            "symbol": "600519",
                            "name": "贵州茅台",
                            "market": "CN",
                            "quantity": 11,
                            "cost_price": 110.0,
                        }
                    ]
                }
            raise AssertionError(path)

        res2 = asyncio.run(sync_gateway_positions(db, g, fetch_json=fetch_json2))
        assert res2["success"] is True
        assert res2["positions_upserted"] == 1
        assert res2["positions_deleted"] == 1

        positions2 = db.query(Position).filter(Position.account_id == acc.id).all()
        assert len(positions2) == 1
        st = db.query(Stock).filter(Stock.symbol == "600519", Stock.market == "CN").first()
        pos = (
            db.query(Position)
            .filter(Position.account_id == acc.id, Position.stock_id == st.id)
            .first()
        )
        assert int(pos.quantity) == 11
        assert float(pos.cost_price) == 110.0
    finally:
        db.close()
