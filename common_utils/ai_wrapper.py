"""
AIWrapper — unified interface for all three agent nodes.

Usage
-----
    # Agent mode (tool-calling loop via create_agent):
    ai = AIWrapper(config, tools=[tool_a, tool_b], system_prompt="…")
    answer = ai.run("Fix the failing test.")

    # LLM mode (single structured call, no tools):
    ai = AIWrapper(config, mode="llm")
    json_str = ai.run("Extract failures.", system_prompt="Return JSON only.")
"""
from typing import List, Optional

from rich.console import Console

from common_utils.llm_config import LLMConfig
from common_utils.logger import logger
from common_utils.lang_wrapper import create_llm, ToolRegistry
from common_utils.langchain_wrapper import LangChainWrapper, StreamingHandler, create_memory

console = Console()


class AIWrapper:
    """
    Dynamic wrapper that auto-selects agent vs. plain-LLM mode and provides
    unified logging via Rich.

    Parameters
    ----------
    config        : LLMConfig with provider / model / temperature.
    tools         : @tool-decorated callables to bind.  If non-empty, defaults
                    to agent mode.
    mode          : "agent" | "llm" | "auto" (default).
    system_prompt : System instruction baked into the agent at creation.
    enable_memory : Whether to keep multi-turn history (default True).
    tool_registry : Existing ToolRegistry to extend (optional).
    """

    def __init__(
        self,
        config: LLMConfig,
        tools: Optional[List] = None,
        mode: str = "auto",
        system_prompt: Optional[str] = None,
        enable_memory: bool = True,
        tool_registry: Optional[ToolRegistry] = None,
    ):
        self._config = config

        # ── Build callbacks ───────────────────────────────────────────────────
        self._callbacks = []
        if config.streaming:
            self._callbacks.append(StreamingHandler())

        # ── Build underlying LLM (RotatingLLM) ───────────────────────────────
        self.llm = create_llm(config, self._callbacks)

        # ── Register tools ────────────────────────────────────────────────────
        self._registry = tool_registry or ToolRegistry()
        for t in (tools or []):
            self._registry.register(t)

        all_tools = self._registry.get_tools()

        # ── Resolve effective mode ────────────────────────────────────────────
        if mode == "auto":
            effective_mode = "agent" if all_tools else "llm"
        else:
            effective_mode = mode

        self._mode = effective_mode

        # ── Rich banner ───────────────────────────────────────────────────────
        mode_label = "[bold cyan]agent[/bold cyan]" if effective_mode == "agent" \
                     else "[bold yellow]llm[/bold yellow]"
        tool_names = ", ".join(t.name for t in all_tools) if all_tools else "none"
        console.print(
            f"  [dim]AIWrapper[/dim] mode={mode_label}  "
            f"model=[magenta]{config.model}[/magenta]  "
            f"tools=[green]{tool_names or 'none'}[/green]"
        )

        # ── Build memory ──────────────────────────────────────────────────────
        self._memory = create_memory() if enable_memory else None

        # ── Build backend ─────────────────────────────────────────────────────
        self.backend = LangChainWrapper(
            llm=self.llm,
            tools=all_tools,
            memory=self._memory,
            mode=effective_mode,
            system_prompt=system_prompt,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """
        Run the agent or plain LLM.

        In agent mode  : system_prompt is already baked in at construction;
                         the argument is ignored unless you explicitly want to
                         override it by rebuilding the agent first.
        In llm mode    : system_prompt is injected as the first message.
        """
        logger.info(f"AIWrapper run (mode={self._mode})")
        return self.backend.run(prompt, system_prompt=system_prompt)

    # ── Convenience ───────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode
