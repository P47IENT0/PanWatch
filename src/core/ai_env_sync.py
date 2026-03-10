import json
import logging
import os
from collections.abc import Mapping
from typing import Any

from dotenv import dotenv_values
from sqlalchemy.orm import Session

from src.web.models import AgentConfig, AIModel, AIService

logger = logging.getLogger(__name__)


def _load_runtime_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for key, value in dotenv_values(".env").items():
        if value is not None:
            merged[key] = value
    for key, value in os.environ.items():
        merged[key] = value
    return merged


def _parse_json(value: str, field_name: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} 不是合法 JSON: {exc}") from exc


def sync_ai_from_env(db: Session, env: Mapping[str, str] | None = None) -> dict[str, int]:
    source = dict(env or _load_runtime_env())
    services_json = source.get("PANWATCH_AI_SERVICES_JSON", "").strip()
    agent_map_json = source.get("PANWATCH_AI_AGENT_MODEL_MAP_JSON", "").strip()
    default_model = source.get("PANWATCH_AI_DEFAULT_MODEL", "").strip()

    summary = {
        "services_upserted": 0,
        "models_upserted": 0,
        "agent_bindings_updated": 0,
    }

    if not services_json and not agent_map_json and not default_model:
        return summary

    model_id_by_model: dict[str, int] = {}
    model_id_by_service_model: dict[str, int] = {}

    if services_json:
        services_data = _parse_json(services_json, "PANWATCH_AI_SERVICES_JSON")
        if not isinstance(services_data, list):
            raise ValueError("PANWATCH_AI_SERVICES_JSON 必须是数组")

        for item in services_data:
            if not isinstance(item, dict):
                raise ValueError("PANWATCH_AI_SERVICES_JSON 的每项必须是对象")

            service_name = str(item.get("name", "")).strip()
            base_url = str(item.get("base_url", "")).strip()
            api_key = str(item.get("api_key", "")).strip()
            models = item.get("models", [])

            if not service_name or not base_url:
                raise ValueError("服务商必须包含 name 与 base_url")
            if not isinstance(models, list) or not models:
                raise ValueError(f"服务商 {service_name} 的 models 必须是非空数组")

            service = db.query(AIService).filter(AIService.name == service_name).first()
            if service is None:
                service = AIService(name=service_name, base_url=base_url, api_key=api_key)
                db.add(service)
                db.flush()
            else:
                service.base_url = base_url
                service.api_key = api_key
            summary["services_upserted"] += 1

            for model_item in models:
                if not isinstance(model_item, dict):
                    raise ValueError(f"服务商 {service_name} 的 model 条目必须是对象")

                model_value = str(model_item.get("model", "")).strip()
                model_name = str(model_item.get("name", "")).strip() or model_value
                if not model_value:
                    raise ValueError(f"服务商 {service_name} 的 model 不能为空")

                model = (
                    db.query(AIModel)
                    .filter(
                        AIModel.service_id == service.id,
                        AIModel.model == model_value,
                    )
                    .first()
                )
                if model is None:
                    model = AIModel(
                        name=model_name,
                        service_id=service.id,
                        model=model_value,
                        is_default=False,
                    )
                    db.add(model)
                    db.flush()
                else:
                    model.name = model_name

                summary["models_upserted"] += 1
                model_id_by_model[model_value] = model.id
                model_id_by_service_model[f"{service_name}/{model_value}"] = model.id

    all_models = db.query(AIModel).all()
    for m in all_models:
        model_id_by_model.setdefault(m.model, m.id)
        svc = db.query(AIService).filter(AIService.id == m.service_id).first()
        if svc:
            model_id_by_service_model.setdefault(f"{svc.name}/{m.model}", m.id)

    if default_model:
        target_id = model_id_by_service_model.get(default_model) or model_id_by_model.get(
            default_model
        )
        if target_id:
            db.query(AIModel).update({"is_default": False})
            target = db.query(AIModel).filter(AIModel.id == target_id).first()
            if target:
                target.is_default = True
        else:
            logger.warning("PANWATCH_AI_DEFAULT_MODEL 未匹配到模型: %s", default_model)

    if agent_map_json:
        agent_map_data = _parse_json(
            agent_map_json,
            "PANWATCH_AI_AGENT_MODEL_MAP_JSON",
        )
        if not isinstance(agent_map_data, dict):
            raise ValueError("PANWATCH_AI_AGENT_MODEL_MAP_JSON 必须是对象")

        for agent_name, ref in agent_map_data.items():
            agent_key = str(agent_name).strip()
            model_ref = str(ref).strip()
            if not agent_key or not model_ref:
                continue

            target_id = model_id_by_service_model.get(model_ref) or model_id_by_model.get(
                model_ref
            )
            if target_id is None:
                logger.warning(
                    "Agent %s 的模型引用未匹配: %s",
                    agent_key,
                    model_ref,
                )
                continue

            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_key).first()
            if not agent:
                logger.warning("环境变量映射的 Agent 不存在: %s", agent_key)
                continue

            if agent.ai_model_id != target_id:
                agent.ai_model_id = target_id
            summary["agent_bindings_updated"] += 1

    db.commit()
    return summary
