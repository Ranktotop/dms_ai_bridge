"""ReAct loop service for Phase IV document question answering."""
import json
import re
from collections.abc import Callable, Awaitable
from dataclasses import dataclass, field

from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.rag_search.SearchService import SearchService
from services.rag_search.helper.IdentityHelper import IdentityHelper
from services.agent.tools.AgentToolManager import AgentToolManager
from services.agent.tools.AgentToolResult import AgentToolResult, CitationRef


@dataclass
class AgentResponse:
    answer: str
    tool_calls: list[str] = field(default_factory=list)
    citations: list[CitationRef] = field(default_factory=list)


class AgentService:
    """Iterative ReAct loop: Thought -> Action -> Observation -> ... -> Final Answer."""

    def __init__(
        self,
        helper_config: HelperConfig,
        search_service: SearchService,
        llm_client: LLMClientInterface,
    ) -> None:
        self.logging = helper_config.get_logger()
        self._llm_client = llm_client
        self._tool_manager = AgentToolManager(helper_config=helper_config, search_service=search_service, llm_client=llm_client)

    ##########################################
    ################ GETTER ##################
    ##########################################

    def _get_system_prompt(self) -> str:
        """
        Generate the system prompt that instructs the LLM on how to use the ReAct tools for document search.
        The prompt includes the rules for the Thought-Action format, disambiguation guidelines, and dynamically inserts the descriptions of available tools from the AgentToolManager.

        Returns:
            A string containing the complete system prompt for the ReAct agent.
        """
        return ("""
        You are a helpful document search assistant. Answer the user's question by searching their personal document archive.

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
        - IMPORTANT: Every response MUST end with either an Action block OR a "Final Answer:" line. Never write plain text without one of these two endings.
        - Write "Final Answer: [your answer]" as the very last line when you have enough information
        - If no documents are found, say so clearly in the Final Answer
        - Always answer in the same language the user used

        Disambiguation rules (IMPORTANT):
        - If the user mentions a name, company, or any term that could match multiple correspondents or document types, ALWAYS call list_filter_options FIRST before searching
        - After calling list_filter_options, check if the term is ambiguous (multiple possible matches in the results)
        - If multiple matches exist for the user's term, ask a clarifying question as your Final Answer — do NOT call search_documents yet
        Example: User asks about "Max" → list_filter_options shows "Max Mustermann" and "Max Bellfort" → Final Answer: "Meinst du Max Mustermann oder Max Bellfort?"
        - Only call search_documents once you have an unambiguous, specific search term
        - If the user's term clearly and uniquely matches one entry in filter options, proceed directly to search_documents
        """ % self._tool_manager.get_descriptions()).strip()

    ##########################################
    ############### CORE #####################
    ##########################################

    async def do_run(
        self,
        query: str,
        chat_history: list[dict] | None = None,
        max_iterations: int = 5,
        step_callback: Callable[[str], Awaitable[None]] | None = None,
        identity_helper: IdentityHelper | None = None,
        client_settings: dict | None = None
    ) -> AgentResponse:
        """Run the ReAct loop for a query.

        Args:
            query (str): The user's natural language question.
            chat_history (list[dict] | None): Optional prior conversation turns.
            max_iterations (int): Maximum number of tool-call iterations.
            step_callback (Callable[[str], Awaitable[None]] | None): Optional async callable invoked before each tool call and
                before the final answer, to enable real-time streaming of agent progress.
            identity_helper (IdentityHelper | None): Optional resolved user identities for filtering.
            client_settings (dict | None): Optional settings from the client, e.g. to pass to tools.

        Returns:
            AgentResponse with the final answer, list of tool calls made, and collected citations.
        """
        # create the instruction prompt for the llm to know which tools it can use, and how to use them
        messages: list[dict] = [{"role": "system", "content": self._get_system_prompt()}]

        # if a history was sent, extend it
        if chat_history:
            messages.extend(chat_history)

        # add the user query as the last message
        messages.append({"role": "user", "content": query})

        tool_calls_made: list[str] = []
        collected_citations: list[CitationRef] = []
        seen_citations: set[tuple[str, str]] = set()
        llm_output = ""

        for iteration in range(max_iterations):
            self.logging.debug("AgentService iteration %d/%d", iteration + 1, max_iterations)

            # ask the llm for the next action
            llm_output = await self._llm_client.do_chat(messages)
            self.logging.debug("LLM output: %s", llm_output[:200])

            # Check for Final Answer
            final_match = re.search(r"Final Answer:\s*(.+)", llm_output, re.DOTALL | re.IGNORECASE)
            if final_match:
                answer = final_match.group(1).strip()
                self.logging.info(
                    "AgentService completed in %d iteration(s), tool calls: %s",
                    iteration + 1, tool_calls_made,
                )
                # Inform user that we will answer now
                if step_callback:
                    await step_callback("✍️ Erstelle Antwort...")
                return AgentResponse(answer=answer, tool_calls=tool_calls_made, citations=collected_citations)

            # Now try to fetch the Action and Input for the next tool call
            action_match = re.search(r"Action:\s*(\w+)", llm_output, re.IGNORECASE)
            input_match = re.search(r"Action Input:\s*(.+?)(?:\n|$)", llm_output, re.DOTALL | re.IGNORECASE)

            # If its not a final answer and its not a tool call, treat as implicit final answer
            if not action_match:
                self.logging.info(
                    "AgentService: No 'Final Answer:' prefix and no tool call in iteration %d, treating response as final answer.",
                    iteration + 1,
                )
                if step_callback:
                    await step_callback("✍️ Erstelle Antwort...")
                return AgentResponse(answer=llm_output.strip(), tool_calls=tool_calls_made, citations=collected_citations)

            # collect name of the tool and its input
            tool_name = action_match.group(1).strip()
            tool_input_raw = input_match.group(1).strip() if input_match else ""

            # run the tool
            tool_result = await self._run_agent_tool(
                tool_name=tool_name,
                tool_input_raw=tool_input_raw,
                identity_helper=identity_helper,
                step_callback=step_callback,
                client_settings=client_settings,
            )

            # track the call and deduplicate citations by (dms_doc_id, dms_engine)
            tool_calls_made.append(tool_name)
            for ref in tool_result.citations:
                key = (ref.dms_doc_id, ref.dms_engine)
                if key not in seen_citations:
                    seen_citations.add(key)
                    collected_citations.append(ref)
            self.logging.debug("Tool '%s' returned: %s", tool_name, tool_result.observation[:200])

            # add agent responses to context
            messages.append({"role": "assistant", "content": llm_output})
            messages.append({"role": "user", "content": "Observation: %s" % tool_result.observation})

        self.logging.warning(
            "AgentService: max iterations (%d) reached, using last output as answer",
            max_iterations,
        )
        return AgentResponse(answer=llm_output.strip(), tool_calls=tool_calls_made, citations=collected_citations)

    ##########################################
    ############# HELPERS ####################
    ##########################################
        

    async def _run_agent_tool(
        self,
        tool_name: str,
        tool_input_raw: str,
        identity_helper: IdentityHelper | None = None,
        step_callback: Callable[[str], Awaitable[None]] | None = None,
        client_settings: dict | None = None,
    ) -> AgentToolResult:
        """Run the tool by the given name with the arguments passed.

        Automatically parses the input as JSON if possible, to allow passing multiple
        arguments in a structured way.

        Args:
            tool_name (str): Name of the tool to run, as specified in the Action field.
            tool_input_raw (str): The raw string from the Action Input field, which may be
                a simple query or a JSON object for multiple arguments.
            identity_helper (IdentityHelper | None): Optional resolved user identities for
                filtering, passed to the tool's do_execute method.
            step_callback (Callable[[str], Awaitable[None]] | None): Optional async callable
                invoked before each tool call and before the final answer.
            client_settings (dict | None): Optional settings from the client, passed to the
                tool's do_execute method.

        Returns:
            AgentToolResult with observation text and any citations produced by the tool.
        """
        tool = self._tool_manager.get_tool_by_name(tool_name)

        # Inform the user, what we will do next
        if step_callback:
            await step_callback(tool.get_step_hint())

        try:
            #if the input is a JSON object, parse it as kwargs, otherwise pass it as the query argument
            kwargs = {
                "query": tool_input_raw,
                "identity": identity_helper,
                "client_settings": client_settings,
            }
            try:
                parsed = json.loads(tool_input_raw)
                #If we received a dict, add each key-value pair in the parsed dict to kwargs. The parsed keys do have priority over the default ones we set before, so they can override them if needed (e.g. to pass separate query)
                if isinstance(parsed, dict):
                    for k, v in parsed.items():
                        kwargs[k] = v
                #If we received a list, we use it as a list of query strings and join them with newlines to pass as the query argument
                if isinstance(parsed, list):
                    kwargs["query"] = "\n".join(parsed)
            except (json.JSONDecodeError, ValueError):
                #if it's not valid JSON, we just pass the raw string as the query argument
                pass

            return await tool.do_execute(**kwargs)
        except Exception as e:
            self.logging.error("Tool '%s' raised exception: %s", tool_name, e, color="red")
            return AgentToolResult(observation="Error executing tool '%s': %s" % (tool_name, str(e)), citations=[])
