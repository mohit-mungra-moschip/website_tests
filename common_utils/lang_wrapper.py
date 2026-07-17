"""
lang_wrapper.py — LLM factory + ToolRegistry.

Bridges LLMConfig → RotatingLLM from common_utils.llm_config.
"""
import os
from common_utils.llm_config import get_llm
from common_utils.logger import logger


def create_llm(config, callbacks=None):
    """Build a RotatingLLM from an LLMConfig dataclass."""
    if config.api_key:
        provider = config.provider.lower()
        key_map = {
            "openai":   "OPENAI_API_KEY",
            "anthropic":"ANTHROPIC_API_KEY",
            "google":   "GEMINI_API_KEY",
            "gemini":   "GEMINI_API_KEY",
            "groq":     "GROQ_API_KEY",
        }
        env_key = key_map.get(provider)
        if env_key:
            os.environ[env_key] = config.api_key

    kwargs = {}
    if callbacks:
        kwargs["callbacks"] = callbacks
    return get_llm(config.model, temperature=config.temperature, **kwargs)


class ToolRegistry:
    """Collects @tool-decorated callables and logs each registration."""
    def __init__(self):
        self._tools = []

    def register(self, tool):
        logger.info(f"Registering tool: {tool.name}")
        self._tools.append(tool)

    def get_tools(self):
        return self._tools
