import base64
import logging
from pathlib import Path
from typing import cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionContentPartParam, ChatCompletionMessageParam

from src.core.http_client import async_client, resolve_proxy

logger = logging.getLogger(__name__)


class AIClient:
    """OpenAI 协议兼容的 AI 客户端"""

    def __init__(self, base_url: str, api_key: str, model: str, proxy: str = ""):
        http_client = async_client(proxy=resolve_proxy(proxy))
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key, http_client=http_client)
        self.model = model
        self.total_tokens_used = 0

    async def chat(
        self,
        system_prompt: str,
        user_content: str,
        images: list[str] | None = None,
        temperature: float = 0.4,
    ) -> str:
        """
        调用 LLM 获取文本回复。

        Args:
            system_prompt: 系统提示词
            user_content: 用户输入内容
            images: 图片路径列表（用于多模态，可选）
            temperature: 生成温度
        """
        messages: list[ChatCompletionMessageParam] = [
            cast(ChatCompletionMessageParam, {"role": "system", "content": system_prompt}),
        ]

        # 构建 user message
        if images:
            content_parts: list[ChatCompletionContentPartParam] = [
                cast(ChatCompletionContentPartParam, {"type": "text", "text": user_content})
            ]
            for img_path in images:
                img_data = self._encode_image(img_path)
                if img_data:
                    content_parts.append(
                        cast(
                            ChatCompletionContentPartParam,
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_data}"
                                },
                            },
                        )
                    )
            messages.append(
                cast(
                    ChatCompletionMessageParam,
                    {"role": "user", "content": content_parts},
                )
            )
        else:
            messages.append(
                cast(
                    ChatCompletionMessageParam,
                    {"role": "user", "content": user_content},
                )
            )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
            # 记录 token 用量
            if response.usage:
                self.total_tokens_used += response.usage.total_tokens
                logger.debug(
                    f"Token usage: {response.usage.prompt_tokens} + "
                    f"{response.usage.completion_tokens} = {response.usage.total_tokens}"
                )

            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(f"AI 调用失败: {e}")
            raise

    async def close(self) -> None:
        await self.client.close()

    async def __aenter__(self) -> "AIClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb,
    ) -> None:
        await self.close()

    def _encode_image(self, image_path: str) -> str | None:
        """将图片文件编码为 base64"""
        path = Path(image_path)
        if not path.exists():
            logger.warning(f"图片不存在: {image_path}")
            return None
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
