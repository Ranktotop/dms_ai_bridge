"""Robust JSON extractor and validator for LLM ReAct responses."""
from __future__ import annotations

import json
import logging

from services.agent.models.AgentToolCall import AgentToolCall


class AgentResponseParser:
    """Extracts and validates structured tool calls from raw LLM output.

    The parser is tolerant of leading reasoning text before the JSON block —
    the model does not need to start its reply with '{'.
    """

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    ##########################################
    ############### CORE #####################
    ##########################################

    def parse(self, llm_response: str, known_tools: list[str]) -> AgentToolCall | None:
        """
        Transforms a text response from LLM into a structured AgentToolCall by extracting and validating the JSON content.

        Args:
            llm_response: Raw string output from the LLM.
            known_tools: List of valid tool names (used for action validation).

        Returns:
            AgentToolCall|None: Parsed AgentToolCall, or None on parse/validation failure.
        """
        # try to extract the JSON block from the response
        raw_json = self._extract_json_from_response(llm_response)
        if raw_json is None:
            self._logger.debug("Unable to parse LLM-Response to AgentToolCall: no JSON block found in response.")
            return None

        # try to parse the JSON block into a dict
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            self._logger.debug("Unable to parse LLM-Response to AgentToolCall: JSON decode error: %s", e)
            return None
        # try to validate the dict and convert it into an AgentToolCall
        return self._parse_dict_to_agent_tool_call(data, known_tools)

    ##########################################
    ############# HELPERS ####################
    ##########################################

    def _extract_json_from_response(self, text: str) -> str | None:
        """
        Find and extract the first balanced JSON object from text.
        Scans from the first '{' and counts braces while respecting
        string literals and escape sequences.
        This is useful for parsing LLM responses that may contain reasoning text before the JSON block.

        Args:
            text: Raw text that may contain a JSON object somewhere.

        Returns:
            The JSON string if found and balanced, otherwise None.
        """
        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape_next = False

        for i, ch in enumerate(text[start:], start=start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]

        # JSON block was never closed
        return None

    def _parse_dict_to_agent_tool_call(self, data: dict, known_tools: list[str]) -> AgentToolCall | None:
        """
        Validate that the parsed dict has all required ReAct fields.

        Args:
            data: Parsed JSON dict from the LLM.
            known_tools: Valid tool names for action validation.

        Returns:
            AgentToolCall if valid, None otherwise.
        """
        #make sure the action exists
        action = data.get("action")
        if not action or not isinstance(action, str) or not action.strip():
            self._logger.debug("Unable to parse dict to AgentToolCall: action field missing or invalid")
            return None

        # save thought if it exists. Default to empty string
        thought = data.get("thought", "")

        # if this is the final answer...
        if action == "final_answer":
            # get the answer text...
            answer = data.get("answer")
            # make sure it's valid...
            if not isinstance(answer, str) or not answer.strip():
                self._logger.debug("Unable to parse dict to AgentToolCall: final answer has no content")
                return None
            # return the AgentToolCall with valid values
            return AgentToolCall(thought=thought, action=action, args={}, answer=answer)

        # if the action is not a registered tool call, something went wrong...
        if action not in known_tools:
            self._logger.debug(
                "Unable to parse dict to AgentToolCall: action '%s' does not exists. Valids are: %s.",
                action,
                known_tools)
            return None
        
        # lastly read the parameters, if there are any
        args = data.get("args", {})
        # ensure args is a dict if it exists, otherwise default to empty dict
        if not isinstance(args, dict):
            args = {}
        return AgentToolCall(thought=thought, action=action, args=args, answer=None)
