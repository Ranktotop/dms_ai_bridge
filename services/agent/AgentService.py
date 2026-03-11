"""ReAct agent orchestrator — runs the reasoning loop and streams typed events."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from shared.helper.HelperConfig import HelperConfig
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.prompt.PromptClientInterface import PromptClientInterface, PromptConfigMessage, PromptConfig
from services.rag_search.SearchService import SearchService
from services.rag_search.helper.IdentityHelper import IdentityHelper
from services.agent.models.AgentEvent import (
    AgentEvent,
    AgentThoughtEvent,
    AgentStepEvent,
    AgentRetryEvent,
    AgentAnswerEvent,
    AgentErrorEvent,
    CitationRef,
)
from services.agent.models.AgentResponse import AgentResponse
from services.agent.models.AgentToolCall import AgentToolCall
from services.agent.parser.AgentResponseParser import AgentResponseParser
from services.agent.tools.AgentToolManager import AgentToolManager
_MAX_RETRIES_PER_ITERATION = 2


class AgentService:
    """Runs the ReAct (Reason + Act) loop over the user's document store.

    Streaming variant: do_run_stream() is an async generator that yields
    typed AgentEvent objects — callers translate these to SSE, WebSocket
    frames, or plain text as needed.

    Non-streaming variant: do_run() collects all events and returns a
    single AgentResponse.
    """

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(
        self,
        helper_config: HelperConfig,
        search_service: SearchService,
        llm_client: LLMClientInterface,
        prompt_client: PromptClientInterface,
    ) -> None:
        self.logging = helper_config.get_logger()
        self._helper_config = helper_config
        self._llm_client = llm_client
        self._prompt_client = prompt_client
        self._tool_manager = AgentToolManager(
            helper_config=helper_config,
            search_service=search_service,
            llm_client=llm_client,
        )
        self._parser = AgentResponseParser(logger=helper_config.get_logger())

    ##########################################
    ############## GETTER ####################
    ##########################################

    async def _get_system_prompt_messages(self, max_iterations: int) -> list[PromptConfigMessage]:
        """Fetch and render the ReAct system prompt via the prompt client.

        Args:
            max_iterations: Maximum number of iterations to embed in the prompt.

        Returns:
            list[PromptConfigMessage]: List of PromptConfigMessage objects (typically just the system message).
        """
        prompt_config: PromptConfig = await self._prompt_client.do_fetch_prompt(id="agent_react_system_prompt")
        tool_descriptions = self._tool_manager.get_descriptions()
        return self._prompt_client.render_prompt(
            prompt=prompt_config,
            replacements={
                "tool_descriptions": tool_descriptions,
                "max_iterations": max_iterations,
            },
        )        

    ##########################################
    ############### CORE #####################
    ##########################################

    async def do_run(
        self,
        query: str,
        identity_helper: IdentityHelper,
        chat_history: list[dict] | None = None,
        max_iterations: int = 6,
        tool_context: dict | None = None,
    ) -> AgentResponse:
        """Run the ReAct loop and return a complete response.

        Args:
            query: The user's natural language question.
            identity_helper: Resolved user identity for search isolation.
            chat_history: Optional prior conversation turns.
            max_iterations: Maximum number of reasoning steps.

        Returns:
            AgentResponse with answer, citations, and tool call names.
        """
        answer = ""
        citations: list[CitationRef] = []
        tool_calls_made: list[str] = []

        async for event in self.do_run_stream(
            query=query,
            identity_helper=identity_helper,
            chat_history=chat_history,
            max_iterations=max_iterations,
            tool_context=tool_context,
        ):
            if isinstance(event, AgentAnswerEvent):
                answer = event.text
                citations = event.citations
            elif isinstance(event, AgentStepEvent):
                tool_calls_made.append(event.tool_name)
            elif isinstance(event, AgentErrorEvent):
                answer = "Error: %s" % event.message

        return AgentResponse(
            query=query,
            answer=answer,
            citations=citations,
            tool_calls=tool_calls_made,
        )

    async def do_run_stream(
        self,
        query: str,
        identity_helper: IdentityHelper,
        chat_history: list[dict] | None = None,
        max_iterations: int = 6,
        tool_context: dict | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run the ReAct loop and yield typed events as they occur.

        Args:
            query: The user's natural language question.
            identity_helper: Resolved user identity for search isolation.
            chat_history: Optional prior conversation turns.
            max_iterations: Maximum number of reasoning steps before giving up.

        Yields:
            Typed AgentEvent objects in the order they occur.
        """
        #fetch the names of all registered tools
        known_tools = self._tool_manager.get_tool_names()
        accumulated_citations: list[CitationRef] = []

        try:
            # get the system prompt messages
            system_messages = await self._get_system_prompt_messages(
                max_iterations=max_iterations
            )
        except Exception as e:
            # if something went wrong with system prompt, send info to clients
            yield self._send_event(AgentErrorEvent(message="Failed to load system prompt: %s" % str(e)))
            return

        # Create a conversation by merging all messages together
        messages = self._build_conversation(
            system_messages=system_messages,
            chat_history=chat_history or [],
            query=query,
        )

        # Now talk iterative to llm until we are finished or reach max iterations
        # On each iteration we append the llm response and tool results to the messages, 
        # so the llm has the full context each time (until we hit the max context window and old history is truncated)
        for iteration in range(1, max_iterations + 1):

            # Try to send the messages to the llm
            try:
                raw_response = await self._llm_client.do_chat(messages=[m.to_llm_message_dict() for m in messages])
            except Exception as e:
                # if the llm call fails, send info to clients and end the loop
                yield self._send_event(AgentErrorEvent(message="LLM call failed: %s" % str(e)))
                return

            # Prepare the variables for the retry loop
            tool_call: AgentToolCall | None = None
            retry_count = 0
            current_raw = raw_response

            # until we reach the retry-limit...
            # note: since we using events, we can't simply put the inner retry into another method
            while retry_count <= _MAX_RETRIES_PER_ITERATION:
                # try to load the tool call from the llm response
                tool_call = self._parser.parse(current_raw, known_tools)
                # on success, break the loop...
                if tool_call is not None:
                    break
                # on error, perform a correction attempt
                # retry the llm call with the same messages + correction prompt
                correction_prompt = (
                    "Your previous response could not be parsed as a valid JSON tool call. "
                    "You MUST respond with ONLY a JSON object matching the required schema. "
                    "Do not add any text before or after the JSON. "
                    "Valid actions are: %s or 'final_answer'." % ", ".join(known_tools)
                )
                yield self._send_event(AgentRetryEvent(reason="Hm, got some trouble... Let me try some things...", iteration=iteration))
                # add the failed response and the correction prompt to the messages for retry. 
                # This gives the llm the full context of what went wrong and how to fix it.
                messages = self._trim_to_context_limit(messages + [
                    PromptConfigMessage(role="assistant", content=current_raw),
                    PromptConfigMessage(role="user", content=correction_prompt),
                ])
                # increase the counter
                retry_count += 1
                # do the call only if we are still within the retry limit
                if retry_count <= _MAX_RETRIES_PER_ITERATION:
                    try:
                        current_raw = await self._llm_client.do_chat(messages=[m.to_llm_message_dict() for m in messages])
                    except Exception as e:                        
                        # if the llm call fails, send info to clients and end the loop
                        yield self._send_event(AgentErrorEvent(message="LLM call failed on retry: %s" % str(e)))
                        return

            # if tool_call is still None after the retries, we have to give up on this iteration and inform the clients about the issue.
            if tool_call is None:
                yield self._send_event(AgentErrorEvent(
                    message="I'm having trouble finding the right tool... Let me start over from scratch..."))
                return

            # if the llm added his thoughts in the response, we forward them to the clients for better visibility into the agent's reasoning process
            if tool_call.thought:
                yield self._send_event(AgentThoughtEvent(thought=tool_call.thought, iteration=iteration))

            # if the llm thinks the answer is the final answer, we send it to the clients and end the loop
            if tool_call.action == "final_answer":         
                # take the saved citations if there are any and deduplicate them       
                deduped = self._deduplicate_citations(accumulated_citations)
                # now inform the client and forward the answer and citations
                yield self._send_event(AgentAnswerEvent(text=tool_call.answer, citations=deduped))
                return

            # if we reach this we have a clear tool to call
            # at first, we inform the user what we're doing here
            hint = self._tool_manager.get_step_hint(tool_call.action)
            yield self._send_event(AgentStepEvent(tool_name=tool_call.action, hint=hint, iteration=iteration))

            # run the tool and get the result
            tool_result = await self._tool_manager.validate_and_execute(
                tool_call=tool_call,
                identity_helper=identity_helper,
                tool_context=tool_context or {},
            )

            # add the citations if there are any
            accumulated_citations.extend(tool_result.citations)

            # lets add the tool result as json string to the messages for the next iteration
            assistant_msg = json.dumps({
                "thought": tool_call.thought, 
                "action": tool_call.action, 
                "args": tool_call.args})
            
            # add the json and the tools observation to the messages
            messages = self._trim_to_context_limit(messages + [
                PromptConfigMessage(role="assistant", content=assistant_msg),
                # prefix must match what the system prompt teaches the model — "[Tool-Ergebnis]:" signals
                # that this is a tool return value, NOT a new user message
                PromptConfigMessage(role="user", content="[Tool-Ergebnis]: %s" % tool_result.observation),
            ])

        # if we reach this point, we have exhausted the maximum iterations without a final answer
        yield self._send_event(AgentErrorEvent(
            message="Sorry, but I failed after %d tries without a final answer." % max_iterations
        ))

    ##########################################
    ############# HELPERS ####################
    ##########################################

    def _send_event(self, event: AgentEvent) -> AgentEvent:
        """Log an event and return it for yielding.

        Centralises event logging so yield sites stay clean:
            yield self._send_event(AgentStepEvent(...))
        """
        event.log(self.logging)
        return event

    def _build_conversation(
        self,
        system_messages: list[PromptConfigMessage],
        chat_history: list[dict],
        query: str,
    ) -> list[PromptConfigMessage]:
        """Assemble the initial message list from system prompt, history, and query.

        System messages and the current query are always kept. If the combined
        character count exceeds the configured chat model limit, the oldest
        non-system messages are dropped one by one until it fits.

        Args:
            system_messages: Rendered system prompt messages.
            chat_history: Prior conversation turns in OpenAI format.
            query: The current user query.

        Returns:
            list[PromptConfigMessage]: Ordered list of OpenAI-format message dicts.
        """
        # flat copy — we don't want to mutate the caller's system_messages list
        messages: list[PromptConfigMessage] = list(system_messages)

        # convert chat_history dicts to PromptConfigMessage, skipping entries with empty role or content
        # (frontends sometimes send None or empty strings for padding)
        for m in chat_history:
            content = m.get("content", "") if m.get("content") is not None else ""
            role = m.get("role", "") if m.get("role") is not None else ""
            if content.strip() and role.strip():
                messages.append(PromptConfigMessage(role=role, content=content))

        # the current user turn always goes last
        messages.append(PromptConfigMessage(role="user", content=query))

        # trim old history if the combined size already exceeds the model's context window
        return self._trim_to_context_limit(messages)

    def _trim_to_context_limit(self, messages: list[PromptConfigMessage]) -> list[PromptConfigMessage]:
        """Drop the oldest non-system messages until the total character count fits the model limit.

        System messages are never dropped. If only system messages remain and the
        limit is still exceeded, the list is returned as-is.

        Args:
            messages: Current message list, may exceed the context limit.

        Returns:
            list[PromptConfigMessage]: Trimmed copy that fits within the limit (best effort).
        """
        max_chars = self._llm_client.get_chat_model_max_chars()

        # if no limit is configured (0 or negative), nothing to trim
        if max_chars <= 0:
            return messages

        # work on a copy so we never mutate the caller's list
        result = list(messages)

        # iterate until we're within the character limit
        while sum(len(m.content) for m in result) > max_chars:
            # find the oldest message that is not a system message — system messages are sacred
            idx = next((i for i, m in enumerate(result) if m.role != "system"), None)

            # no droppable message left (only system messages remain) — can't do anything more
            if idx is None:
                break

            dropped = result.pop(idx)
            self.logging.debug(
                "AgentService: dropped oldest '%s' message (%d chars) to fit context window of %d chars.",
                dropped.role,
                len(dropped.content),
                max_chars,
            )
        return result

    def _deduplicate_citations(self, citations: list[CitationRef]) -> list[CitationRef]:
        """Remove duplicate citations, keeping the first occurrence of each doc_id.

        Args:
            citations: List of CitationRef objects, possibly with duplicates.

        Returns:
            Deduplicated list preserving insertion order.
        """
        seen: set[str] = set()
        unique: list[CitationRef] = []
        for ref in citations:
            key = "%s:%s" % (ref.dms_engine, ref.dms_doc_id)
            if key not in seen:
                seen.add(key)
                unique.append(ref)
        return unique
