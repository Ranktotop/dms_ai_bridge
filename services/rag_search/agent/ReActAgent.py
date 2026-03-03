"""Custom ReAct loop for Phase IV document question answering.

No AgentExecutor — uses a simple iterative loop to avoid async complexity.
"""
import json
import re
from dataclasses import dataclass, field

from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.rag_search.SearchService import SearchService
from services.rag_search.helper.IdentityHelper import IdentityHelper
from services.rag_search.agent.tools import TOOL_REGISTRY, TOOL_DESCRIPTIONS, AgentToolContext


REACT_SYSTEM_PROMPT = """You are a helpful document search assistant. Answer the user's question by searching their personal document archive.

%s

Use the ReAct format:
Thought: [your reasoning about what to do next]
Action: [tool name]
Action Input: [tool argument or JSON object if multiple args]

When you have enough information:
Final Answer: [your complete answer to the user's question]

Rules:
- Always use Thought before Action
- Action must be one of the available tool names
- Action Input is the main argument (or JSON for multiple args)
- Write Final Answer when you have enough information to answer
- If no documents are found, say so clearly""" % TOOL_DESCRIPTIONS


@dataclass
class AgentResponse:
    answer: str
    tool_calls: list[str] = field(default_factory=list)


class ReActAgent:
    """Iterative ReAct loop: Thought -> Action -> Observation -> ... -> Final Answer."""

    def __init__(
        self,
        helper_config: HelperConfig,
        search_service: SearchService,
        llm_client: LLMClientInterface,
    ) -> None:
        self.logging = helper_config.get_logger()
        self._search_service = search_service
        self._llm_client = llm_client

    ##########################################
    ############### CORE #####################
    ##########################################

    async def do_run(
        self,
        query: str,
        identity_helper: IdentityHelper,
        chat_history: list[dict] | None = None,
        max_iterations: int = 5,
    ) -> AgentResponse:
        """Run the ReAct loop for a query.

        Args:
            query: The user's natural language question.
            identity_helper: Resolved user identities for filtering.
            chat_history: Optional prior conversation turns.
            max_iterations: Maximum number of tool-call iterations.

        Returns:
            AgentResponse with the final answer and list of tool calls made.
        """
        context = AgentToolContext(
            search_service=self._search_service,
            identity_helper=identity_helper,
        )
        return await self._run_react_loop(
            query=query,
            context=context,
            chat_history=chat_history,
            max_iterations=max_iterations,
        )

    ##########################################
    ############# HELPERS ####################
    ##########################################

    async def _run_react_loop(
        self,
        query: str,
        context: AgentToolContext,
        chat_history: list[dict] | None,
        max_iterations: int,
    ) -> AgentResponse:
        messages: list[dict] = [{"role": "system", "content": REACT_SYSTEM_PROMPT}]

        if chat_history:
            messages.extend(chat_history)

        messages.append({"role": "user", "content": query})

        tool_calls_made: list[str] = []
        llm_output = ""

        for iteration in range(max_iterations):
            self.logging.debug("ReActAgent iteration %d/%d", iteration + 1, max_iterations)
            llm_output = await self._llm_client.do_chat(messages)
            self.logging.debug("LLM output: %s", llm_output[:200])

            # Check for Final Answer
            final_match = re.search(r"Final Answer:\s*(.+)", llm_output, re.DOTALL | re.IGNORECASE)
            if final_match:
                answer = final_match.group(1).strip()
                self.logging.info(
                    "ReActAgent completed in %d iteration(s), tool calls: %s",
                    iteration + 1, tool_calls_made,
                )
                return AgentResponse(answer=answer, tool_calls=tool_calls_made)

            # Parse Action and Action Input
            action_match = re.search(r"Action:\s*(\w+)", llm_output, re.IGNORECASE)
            input_match = re.search(r"Action Input:\s*(.+?)(?:\n|$)", llm_output, re.DOTALL | re.IGNORECASE)

            if not action_match:
                # No action found — treat the whole output as final answer
                self.logging.warning(
                    "ReActAgent: no action found in iteration %d, using output as answer",
                    iteration + 1,
                )
                return AgentResponse(answer=llm_output.strip(), tool_calls=tool_calls_made)

            tool_name = action_match.group(1).strip()
            tool_input_raw = input_match.group(1).strip() if input_match else ""

            # Dispatch tool
            observation = await self._dispatch_tool(tool_name, tool_input_raw, context)
            tool_calls_made.append(tool_name)
            self.logging.debug("Tool '%s' returned: %s", tool_name, observation[:200])

            # Append to message history
            messages.append({"role": "assistant", "content": llm_output})
            messages.append({"role": "user", "content": "Observation: %s" % observation})

        # Max iterations reached — use last LLM output
        self.logging.warning(
            "ReActAgent: max iterations (%d) reached, using last output as answer",
            max_iterations,
        )
        return AgentResponse(answer=llm_output.strip(), tool_calls=tool_calls_made)

    async def _dispatch_tool(self, tool_name: str, tool_input_raw: str, context: AgentToolContext) -> str:
        """Parse tool input and call the tool function."""
        if tool_name not in TOOL_REGISTRY:
            return "Unknown tool '%s'. Available tools: %s" % (tool_name, ", ".join(TOOL_REGISTRY.keys()))

        tool_fn = TOOL_REGISTRY[tool_name]

        try:
            try:
                kwargs = json.loads(tool_input_raw)
                if not isinstance(kwargs, dict):
                    kwargs = {"query": tool_input_raw}
            except (json.JSONDecodeError, ValueError):
                # Treat as single positional arg
                if tool_name == "search_documents":
                    kwargs = {"query": tool_input_raw}
                elif tool_name == "get_document_details":
                    kwargs = {"document_id": tool_input_raw}
                else:
                    kwargs = {}

            return await tool_fn(context=context, **kwargs)
        except Exception as exc:
            self.logging.warning("Tool '%s' raised exception: %s", tool_name, exc)
            return "Tool error: %s" % str(exc)
