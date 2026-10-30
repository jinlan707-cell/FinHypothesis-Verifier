import time
import logging
from typing import Callable, Any, Type, Tuple
import requests

logger = logging.getLogger("fin_verifier.tools.resilience")

class TransientAPIError(Exception):
    """可重试的暂时性网络错误或限流"""
    pass

class NonRetryableAPIError(Exception):
    """不可重试的致命错误（如鉴权失败、非法参数等）"""
    pass

def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = (
        TransientAPIError,
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    )
):
    """
    协议感知指数退避重试装饰器。
    严格满足：
    1. 最多尝试 max_attempts 次（含首次，默认 3 次总尝试）；
    2. 仅对暂时性网络故障、超时和限流（HTTP 429 或平台限流码 4001/429）执行重试；
    3. 鉴权失败、400、Schema 不匹配等直接终止抛出，不进行无差别重试。
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 1
            delay = initial_delay
            while attempt <= max_attempts:
                try:
                    return func(*args, **kwargs)
                except NonRetryableAPIError:
                    raise
                except retryable_exceptions as e:
                    if attempt >= max_attempts:
                        logger.error(f"[{func.__name__}] 达到最大重试次数 ({max_attempts})，最终失败: {e}")
                        raise
                    logger.warning(
                        f"[{func.__name__}] 第 {attempt} 次调用捕获可重试异常: {e}，将在 {delay:.2f}s 后进行第 {attempt + 1} 次尝试..."
                    )
                    time.sleep(delay)
                    delay *= backoff_factor
                    attempt += 1
                except Exception as e:
                    # 未知非协议异常直接透传，不滥用重试
                    logger.error(f"[{func.__name__}] 捕获未知异常，不重试: {e}")
                    raise
        return wrapper
    return decorator
