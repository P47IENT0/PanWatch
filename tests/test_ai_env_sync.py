import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.ai_env_sync import sync_ai_from_env
from src.web.models import AgentConfig, AIModel, AIService, Base


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_sync_ai_from_env_creates_services_models_and_agent_bindings():
    db = _make_session()
    try:
        db.add(
            AgentConfig(
                name="premarket_outlook",
                display_name="盘前分析",
            )
        )
        db.add(
            AgentConfig(
                name="intraday_monitor",
                display_name="盘中监测",
            )
        )
        db.commit()

        env = {
            "PANWATCH_AI_SERVICES_JSON": json.dumps(
                [
                    {
                        "name": "CodeYY OpenAI Group",
                        "base_url": "https://codeyy.top/v1",
                        "api_key": "sk-openai",
                        "models": [
                            {"name": "GPT-5.2", "model": "gpt-5.2-codex-xhigh"},
                            {"name": "GPT-4o Mini", "model": "gpt-4o-mini"},
                        ],
                    },
                    {
                        "name": "CodeYY Gemini",
                        "base_url": "https://codeyy.top/v1beta",
                        "api_key": "sk-gemini",
                        "models": [
                            {
                                "name": "Gemini 3 Flash",
                                "model": "gemini-3-flash-preview",
                            }
                        ],
                    },
                ]
            ),
            "PANWATCH_AI_AGENT_MODEL_MAP_JSON": json.dumps(
                {
                    "premarket_outlook": "gpt-5.2-codex-xhigh",
                    "intraday_monitor": "gpt-4o-mini",
                }
            ),
            "PANWATCH_AI_DEFAULT_MODEL": "gpt-4o-mini",
        }

        result = sync_ai_from_env(db, env)

        models = {m.model: m for m in db.query(AIModel).all()}
        premarket = (
            db.query(AgentConfig)
            .filter(AgentConfig.name == "premarket_outlook")
            .first()
        )
        intraday = (
            db.query(AgentConfig)
            .filter(AgentConfig.name == "intraday_monitor")
            .first()
        )

        assert result["services_upserted"] == 2
        assert result["models_upserted"] == 3
        assert result["agent_bindings_updated"] == 2
        assert models["gpt-4o-mini"].is_default is True
        assert premarket.ai_model_id == models["gpt-5.2-codex-xhigh"].id
        assert intraday.ai_model_id == models["gpt-4o-mini"].id
    finally:
        db.close()


def test_sync_ai_from_env_updates_existing_records_idempotently():
    db = _make_session()
    try:
        db.add(
            AgentConfig(
                name="daily_report",
                display_name="收盘复盘",
            )
        )
        db.commit()

        env = {
            "PANWATCH_AI_SERVICES_JSON": json.dumps(
                [
                    {
                        "name": "CodeYY OpenAI Group",
                        "base_url": "https://codeyy.top/v1",
                        "api_key": "sk-old",
                        "models": [{"name": "GPT-4o", "model": "gpt-4o"}],
                    }
                ]
            ),
            "PANWATCH_AI_AGENT_MODEL_MAP_JSON": json.dumps(
                {"daily_report": "gpt-4o"}
            ),
            "PANWATCH_AI_DEFAULT_MODEL": "gpt-4o",
        }
        sync_ai_from_env(db, env)

        env["PANWATCH_AI_SERVICES_JSON"] = json.dumps(
            [
                {
                    "name": "CodeYY OpenAI Group",
                    "base_url": "https://codeyy.top/v1",
                    "api_key": "sk-new",
                    "models": [{"name": "GPT-4o", "model": "gpt-4o"}],
                }
            ]
        )
        result = sync_ai_from_env(db, env)

        services = (
            db.query(AIService).filter(AIService.name == "CodeYY OpenAI Group").count()
        )
        models = db.query(AIModel).filter(AIModel.model == "gpt-4o").count()
        service_key = (
            db.query(AIService)
            .filter(AIService.name == "CodeYY OpenAI Group")
            .first()
            .api_key
        )

        assert services == 1
        assert models == 1
        assert service_key == "sk-new"
        assert result["agent_bindings_updated"] == 1
    finally:
        db.close()
