import os
from pathlib import Path
from dotenv import load_dotenv

# 确保路径解析基于文件模块位置，兼容 Windows 中文与空格路径
CORE_DIR = Path(__file__).resolve().parent
FIN_VERIFIER_DIR = CORE_DIR.parent
PROJECT_ROOT = FIN_VERIFIER_DIR.parent

# 依次尝试加载根目录 .env 与 script/.env（已存在环境变量优先，不覆盖）
root_env = PROJECT_ROOT / ".env"
script_env = PROJECT_ROOT / "script" / ".env"

if root_env.exists():
    load_dotenv(dotenv_path=root_env, override=False)
elif script_env.exists():
    load_dotenv(dotenv_path=script_env, override=False)

# 读取核心配置
FUYAO_KEY = os.getenv("fuyao_Key", "").strip().strip('"').strip("'")
IFIND_MCP_TOKEN = os.getenv("IFinD_MCP", "").strip().strip('"').strip("'")
LLM_BASE_URL = os.getenv("Base_url", "https://api.siliconflow.cn/v1").strip().strip('"').strip("'")
LLM_MODEL = os.getenv("Model", "deepseek-ai/DeepSeek-V4.1-Flash").strip().strip('"').strip("'")
LLM_API_KEY = os.getenv("API_Key", "").strip().strip('"').strip("'")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120.0"))

# 持久化数据目录
TASKS_DATA_DIR = FIN_VERIFIER_DIR / "data" / "tasks"
TASKS_DATA_DIR.mkdir(parents=True, exist_ok=True)

def mask_secret(secret: str, prefix_len: int = 4, suffix_len: int = 4) -> str:
    """对密钥进行脱敏处理，严禁明文打印或记录日志"""
    if not secret:
        return "<EMPTY>"
    if len(secret) <= prefix_len + suffix_len:
        return "***"
    return f"{secret[:prefix_len]}...{secret[-suffix_len:]}"
