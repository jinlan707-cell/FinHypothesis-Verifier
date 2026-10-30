import re
import json
import logging
from typing import Type, TypeVar, Optional, List, Dict, Any
from pydantic import BaseModel
from openai import OpenAI

from fin_verifier.core.config import LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, LLM_TIMEOUT
from fin_verifier.tools.resilience import retry_with_backoff, TransientAPIError, NonRetryableAPIError

logger = logging.getLogger("fin_verifier.tools.llm_client")

T = TypeVar("T", bound=BaseModel)

class LLMClient:
    """
    SiliconFlow DeepSeek / OpenAI 兼容 LLM 客户端。
    支持 JSON Mode、Pydantic 实体解析与最大 2 次格式修复。
    """
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None
    ):
        self.base_url = base_url or LLM_BASE_URL
        self.api_key = api_key or LLM_API_KEY
        self.model = model or LLM_MODEL
        self.timeout = timeout or LLM_TIMEOUT
        if not self.api_key:
            logger.warning("LLMClient 初始化但未配置 LLM_API_KEY")
        # 显式设置 max_retries=0，杜绝 SDK 内部隐式重试与外层退避装饰器重叠放大
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
            max_retries=0
        )

    @staticmethod
    def _clean_json_fence(text: str) -> str:
        """剔除可能存在的 markdown 代码块围栏"""
        clean = text.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```[a-zA-Z]*\n?", "", clean)
            clean = re.sub(r"\n?```$", "", clean)
        return clean.strip()

    @retry_with_backoff(max_attempts=3, initial_delay=1.0)
    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2000,
        response_format: Optional[Dict[str, str]] = None
    ) -> str:
        """底层标准对话调用"""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        if response_format:
            kwargs["response_format"] = response_format

        try:
            import time
            t0 = time.time()
            logger.info(f"发起 LLM 对话请求 (model: {self.model})...")
            resp = self.client.chat.completions.create(**kwargs)
            duration = time.time() - t0
            content = resp.choices[0].message.content or ""
            logger.info(f"LLM 响应成功，耗时: {duration:.2f}s，响应长度: {len(content)}")
            return content.strip()
        except Exception as e:
            err_msg = str(e)
            if "rate limit" in err_msg.lower() or "timeout" in err_msg.lower():
                raise TransientAPIError(f"LLM 接口限流或超时: {e}")
            raise NonRetryableAPIError(f"LLM 接口异常: {e}")

    def call_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        temperature: float = 0.1,
        max_tokens: int = 2500,
        max_repair_attempts: int = 2
    ) -> T:
        """
        受约束的结构化 JSON 提示词调用，自动反序列化为指定的 Pydantic 模型。
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        raw_output = ""
        for attempt in range(1, max_repair_attempts + 1):
            try:
                raw_output = self.chat_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"}
                )
                cleaned = self._clean_json_fence(raw_output)
                parsed_dict = json.loads(cleaned)
                validated_obj = response_model.model_validate(parsed_dict)
                return validated_obj
            except Exception as e:
                logger.warning(f"结构化解析失败 (第 {attempt} 次): {e}")
                if attempt >= max_repair_attempts:
                    logger.error(f"达到最大修复次数，无法反序列化为 {response_model.__name__}。原始输出: {raw_output[:300]}")
                    raise NonRetryableAPIError(f"LLM 无法生成符合 {response_model.__name__} 契约的输出: {e}")
                
                # 追加自我修复提示
                messages.append({"role": "assistant", "content": raw_output})
                messages.append({
                    "role": "user",
                    "content": f"上一次输出不符合预期的 JSON 契约或无法解析: {e}。请严格按要求重新输出合法的 JSON 对象，不要输出任何代码块以外的字符。"
                })

        raise NonRetryableAPIError(f"LLM 结构化输出失败: {response_model.__name__}")
