"""OpenAI-compatible AI client."""
import asyncio
import logging
from typing import List, Dict, Optional
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class AIClient:
    def __init__(self, api_key: str, base_url: str = "https://freemodel.dev/v1"):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate(
        self, model: str, messages: List[Dict[str, str]],
        max_tokens: int = 1000, temperature: float = 0.8,
        system_prompt: Optional[str] = None,
    ) -> str:
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages
        resp = await self.client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=temperature,
        )
        return resp.choices[0].message.content

    async def generate_with_retry(
        self, model, messages, max_tokens=1000, temperature=0.8, system_prompt=None, retries=3,
    ):
        for attempt in range(retries):
            try:
                return await self.generate(model, messages, max_tokens, temperature, system_prompt)
            except Exception as e:
                logger.warning("AI attempt %d failed: %s", attempt + 1, e)
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
