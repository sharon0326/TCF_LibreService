import time
import asyncio
import logging
import os
from typing import Any, Dict, List, Tuple

import requests

logger = logging.getLogger("webscripting.llm")

DEFAULT_API_KEY = "9c9ddc7c-9466-4236-87af-77a262ed2ce8"

API_CONFIG = {
    "api_key": os.getenv("GENAI_API_KEY", "") or DEFAULT_API_KEY,
    "api_base": os.getenv("GENAI_API_BASE", "https://genai-nexus.api.corpinter.net/apikey"),
    "api_version": os.getenv("GENAI_API_VERSION", "2025-04-01-preview"),
    "deployment": os.getenv("GENAI_DEPLOYMENT", "gpt-5"),
}


def _chat(messages: List[Dict[str, Any]], max_retries: int = 3, timeout: int = 120) -> Tuple[bool, str]:
    if not API_CONFIG["api_key"]:
        return False, "Missing API key: set GENAI_API_KEY or DEFAULT_API_KEY"

    message_lengths = [len(m.get("content", "") or "") for m in messages]
    headers = {"Content-Type": "application/json", "api-key": API_CONFIG["api_key"]}
    payload = {
        "model": API_CONFIG["deployment"],
        "max_completion_tokens": 2048,
        "messages": messages,
        "stream": False,
    }
    api_url = (
        f"{API_CONFIG['api_base']}/openai/deployments/{API_CONFIG['deployment']}"
        f"/chat/completions?api-version={API_CONFIG['api_version']}"
    )

    logger.info(
        "[LLM] Starting API call | model=%s max_retries=%s timeout=%ss messages=%s lengths=%s",
        API_CONFIG["deployment"],
        max_retries,
        timeout,
        len(messages),
        message_lengths,
    )

    for attempt in range(max_retries):
        try:
            logger.debug("[LLM] Attempt %s/%s", attempt + 1, max_retries)
            time.sleep(0.5 if attempt == 0 else 2)
            start_time = time.time()
            response = requests.post(api_url, json=payload, headers=headers, timeout=timeout)
            duration_ms = int((time.time() - start_time) * 1000)

            logger.info("[LLM] API response: status_code=%s duration_ms=%s", response.status_code, duration_ms)

            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                usage = result.get("usage", {})
                logger.info(
                    "[LLM] SUCCESS - Content length: %s chars | prompt_tokens=%s completion_tokens=%s",
                    len(content),
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                )
                return True, content.strip()

            if response.status_code == 429:
                wait_time = (2**attempt) * 3
                logger.warning("[LLM] Rate limit (429), waiting %ss before retry", wait_time)
                time.sleep(wait_time)
                continue

            logger.error("[LLM] API Error %s: %s", response.status_code, response.text[:500])
            if attempt < max_retries - 1:
                wait_time = (2**attempt) * 2
                logger.info("[LLM] Retrying in %ss...", wait_time)
                time.sleep(wait_time)
                continue
            return False, f"API Error: {response.status_code}"

        except requests.exceptions.Timeout:
            logger.error("[LLM] Timeout after %ss on attempt %s", timeout, attempt + 1)
            if attempt < max_retries - 1:
                wait_time = (2**attempt) * 2
                logger.info("[LLM] Retrying in %ss...", wait_time)
                time.sleep(wait_time)
                continue
            return False, "Request timeout"
        except Exception as e:
            logger.error("[LLM] Exception: %s: %s", type(e).__name__, e)
            return False, str(e)

    logger.error("[LLM] Failed after %s retries", max_retries)
    return False, f"Failed after {max_retries} retries"


async def call_llm(prompt: str, *, timeout: int = 120) -> str:
    loop = asyncio.get_event_loop()
    success, result = await loop.run_in_executor(None, _chat, [{"role": "user", "content": prompt}], 3, timeout)
    return result if success else f"[AI生成失败: {result}]"
