"""
LangChain backend for AIWrapper.

Supports two modes:
  • mode="agent"  — full create_agent loop with tool-calling (default when tools provided)
  • mode="llm"    — single direct invoke, no tool loop (used for structured extraction)
"""
from typing import List, Optional

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.callbacks.base import BaseCallbackHandler

from common_utils.logger import logger


# ── Memory helpers ────────────────────────────────────────────────────────────

class _ChatMemory:
    def __init__(self):
        self.messages: list = []

    def add_user(self, text: str):
        self.messages.append(HumanMessage(content=text))

    def add_ai(self, text: str):
        self.messages.append(AIMessage(content=text))


class SimpleMemory:
    """Lightweight drop-in for langchain.memory that works without extra deps."""
    def __init__(self):
        self.chat_memory = _ChatMemory()


# ── Streaming callback ────────────────────────────────────────────────────────

class StreamingHandler(BaseCallbackHandler):
    def on_llm_new_token(self, token: str, **kwargs):
        print(token, end="", flush=True)


# ── Main wrapper ──────────────────────────────────────────────────────────────

class LangChainWrapper:
    """
    Unified backend for both agentic (create_agent) and plain-LLM usage.

    Args:
        llm         : RotatingLLM or any LangChain chat model.
        tools       : List of @tool-decorated callables.  When non-empty the
                      wrapper defaults to agent mode.
        memory      : Optional SimpleMemory for multi-turn history.
        mode        : "agent" | "llm".  Overrides the tool-based default.
        system_prompt: Optional system instruction baked into the agent at
                      creation time.
    """

    def __init__(
        self,
        llm,
        tools: Optional[List] = None,
        memory: Optional[SimpleMemory] = None,
        mode: str = "auto",
        system_prompt: Optional[str] = None,
    ):
        self.llm = llm
        self.tools = tools or []
        self.memory = memory
        self._agent = None

        # Resolve mode: explicit override > auto-detect from tools
        if mode == "auto":
            self.mode = "agent" if self.tools else "llm"
        else:
            self.mode = mode

        self._system_prompt = system_prompt

        if self.mode == "agent":
            self._build_agent(system_prompt)

    # ── Agent construction ────────────────────────────────────────────────────

    def _build_agent(self, system_prompt: Optional[str] = None):
        prompt = system_prompt or self._system_prompt
        logger.info("Initializing LangChain agent via create_agent...")
        self._agent = create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=prompt,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """
        Run the agent or the plain LLM depending on mode.

        In agent mode   : invokes the create_agent loop until all tools are done.
        In llm mode     : sends a single chat completion with optional system msg.
        """
        if self.mode == "agent":
            return self._run_agent(prompt)
        return self._run_llm(prompt, system_prompt)

    # ── Internal: agent path ──────────────────────────────────────────────────

    def _run_agent(self, prompt: str) -> str:
        if self._agent is None:
            self._build_agent()

        logger.info(f"Running LangChain agent prompt: {prompt[:120]}…")

        messages: list = []
        if self.memory:
            messages.extend(self.memory.chat_memory.messages)
        messages.append(HumanMessage(content=prompt))

        response = self._agent.invoke({"messages": messages})

        final = response["messages"][-1]
        result = final.content or ""

        if self.memory:
            self.memory.chat_memory.add_user(prompt)
            self.memory.chat_memory.add_ai(result)

        return result

    # ── Internal: plain LLM path ──────────────────────────────────────────────

    def _run_llm(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        messages: list = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        if self.memory:
            messages.extend(self.memory.chat_memory.messages)
        messages.append(HumanMessage(content=prompt))

        logger.info(f"Running plain LLM invoke: {prompt[:80]}…")
        response = self.llm.invoke(messages)
        result = response.content or ""

        if self.memory:
            self.memory.chat_memory.add_user(prompt)
            self.memory.chat_memory.add_ai(result)

        return result


# ── Convenience factory ───────────────────────────────────────────────────────

def create_memory() -> SimpleMemory:
    return SimpleMemory()
