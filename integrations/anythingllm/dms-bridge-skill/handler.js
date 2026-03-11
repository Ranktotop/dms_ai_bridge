/**
 * DMS AI Bridge — AnythingLLM Agent Skill
 *
 * Delegates every request to the bridge's /chat/anythingllm/stream endpoint.
 * The bridge runs the full ReAct reasoning loop internally; this handler only
 * streams the results back to AnythingLLM via introspect() and returns the
 * final answer text.
 *
 * Required action: "chat"  — pass the user's question as "query".
 */

"use strict";


// ==========================================
// ============= TYPE DEFINITIONS ===========
// ==========================================

/**
 * Configuration values injected by AnythingLLM from plugin.json setup_args.
 *
 * @typedef {Object} RuntimeArgs
 * @property {string} DMS_BRIDGE_URL     - Base URL of the dms_ai_bridge server
 * @property {string} DMS_BRIDGE_API_KEY - Authentication key (X-Api-Key header)
 * @property {string} DMS_BRIDGE_USER_ID - User ID as mapped in user_mapping.yml
 */

/**
 * Resolved bridge connection settings.
 *
 * @typedef {Object} BridgeConfig
 * @property {string} baseUrl
 * @property {string} apiKey
 * @property {string} userId
 */


// ==========================================
// ============= CONFIGURATION ==============
// ==========================================

/**
 * Extracts and validates bridge connection settings from AnythingLLM's injected args.
 *
 * @param {RuntimeArgs} runtimeArgs
 * @returns {BridgeConfig}
 * @throws {Error} If DMS_BRIDGE_URL or DMS_BRIDGE_USER_ID are missing
 */
function readConfig(runtimeArgs) {
    const baseUrl = (runtimeArgs.DMS_BRIDGE_URL || "").replace(/\/+$/, "");
    if (!baseUrl) {
        throw new Error("DMS_BRIDGE_URL is not configured in the skill setup.");
    }

    const userId = (runtimeArgs.DMS_BRIDGE_USER_ID || "").trim();
    if (!userId) {
        throw new Error("DMS_BRIDGE_USER_ID is not configured in the skill setup.");
    }

    const apiKey = runtimeArgs.DMS_BRIDGE_API_KEY || "";

    const limit = runtimeArgs.DMS_BRIDGE_LIMIT || 10;
    if (isNaN(limit) || limit <= 0) {
        throw new Error("DMS_BRIDGE_LIMIT must be a positive number if set.");
    }

    return { baseUrl, apiKey, userId, limit };
}


// ==========================================
// ============= SSE STREAM READER ==========
// ==========================================

/**
 * Reads a server-sent event stream from a fetch Response body and yields
 * each parsed event object. Stops when the stream closes or "[DONE]" is received.
 *
 * @param {Response} response - A fetch Response whose body is an SSE stream
 * @yields {{ type: string, [key: string]: any }}
 */
async function* readSseStream(response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            // Split on newlines and process complete "data: ..." lines
            const lines = buffer.split("\n");
            buffer = lines.pop(); // keep the last (potentially incomplete) line

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed.startsWith("data:")) continue;

                const payload = trimmed.slice("data:".length).trim();
                if (payload === "[DONE]") return;

                try {
                    yield JSON.parse(payload);
                } catch (_) {
                    // Skip malformed SSE lines
                }
            }
        }
    } finally {
        reader.releaseLock();
    }
}


// ==========================================
// ============= ACTION HANDLER =============
// ==========================================

/**
 * Timeout for the /chat/stream endpoint. The full ReAct loop can span several
 * LLM calls and tool executions, so a much longer budget is needed than for
 * direct HTTP calls.
 */
const CHAT_TIMEOUT_MS = 120_000;

/**
 * Delegates the full ReAct reasoning loop to the bridge's /chat/stream endpoint.
 * Step and thought events are forwarded to AnythingLLM via introspect() so the
 * user sees live progress; the final answer text is returned as the skill result.
 *
 * @param {Object}      ctx
 * @param {BridgeConfig} config
 * @param {string}      query        - The user's natural language question
 * @param {Object[]}    chatHistory  - Prior conversation turns in OpenAI format
 * @returns {Promise<string>} The agent's final answer
 */
async function handleChat(ctx, config, query, chatHistory) {
    if (!query?.trim()) {
        return "Error: query is required.";
    }

    ctx.introspect("Starting DMS AI Agent…");

    const controller = new AbortController();
    const timeoutHandle = setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS);

    let response;
    try {
        response = await fetch(`${config.baseUrl}/chat/anythingllm/stream`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Api-Key": config.apiKey,
            },
            body: JSON.stringify({
                user_id: config.userId,
                query: query.trim(),
                tool_context: { limit: config.limit },
                chat_history: chatHistory || [],
            }),
            signal: controller.signal,
        });
    } finally {
        clearTimeout(timeoutHandle);
    }

    if (!response.ok) {
        throw new Error(`Bridge returned HTTP ${response.status} for /chat/anythingllm/stream`);
    }

    let answer = "";

    for await (const event of readSseStream(response)) {
        switch (event.type) {
            case "thought":
                ctx.introspect(`💭 ${event.thought}`);
                break;
            case "step":
                ctx.introspect(event.hint || `⚙️ Running: ${event.tool_name}…`);
                break;
            case "retry":
                ctx.introspect("🔄 Retrying response parsing…");
                break;
            case "answer":
                ctx.introspect("✅ Suche abgeschlossen.");
                answer = event.text || "";
                // append citations as a markdown source list so the user can trace answers back to documents
                if (Array.isArray(event.citations) && event.citations.length > 0) {
                    const lines = event.citations.map((c, i) => {
                        const label = c.title || c.dms_doc_id;
                        // prefer a clickable link when a view URL is available
                        return c.view_url
                            ? `${i + 1}. [${label}](${c.view_url})`
                            : `${i + 1}. ${label}`;
                    });
                    answer += `\n\n---\n**Quellen:**\n${lines.join("\n")}`;
                }
                // bypass AnythingLLM's second LLM pass so the answer is displayed verbatim —
                // without this flag, AnythingLLM feeds our answer back into its own LLM which
                // reformulates it and strips the citations block
                if (ctx.super) ctx.super.skipHandleExecution = true;
                break;
            case "error":
                throw new Error(event.message || "Agent returned an error.");
        }
    }

    return answer || "No answer received from the agent.";
}


// ==========================================
// ============== ENTRYPOINT ================
// ==========================================

module.exports.runtime = {
    /**
     * Entry point called by AnythingLLM's agent runtime.
     *
     * @param {Object}   params
     * @param {string}   params.action       - Must be "chat"
     * @param {string}   [params.query]      - The user's natural language question
     * @param {Object[]} [params.chat_history] - Prior conversation turns
     * @returns {Promise<string>}
     */
    handler: async function ({ action, query, chat_history }) {
        const callerId = `${this.config.name}-v${this.config.version}`;
        this.logger(`[${callerId}] action=${action} query=${query ?? ""}`);

        try {
            const config = readConfig(this.runtimeArgs);

            if (action !== "chat") {
                return `Unknown action "${action}". This skill only supports action="chat".`;
            }

            return await handleChat(
                this,
                config,
                query ?? "",
                Array.isArray(chat_history) ? chat_history : [],
            );
        } catch (err) {
            const message = `${callerId} failed: ${err.message}`;
            this.introspect(message);
            this.logger(message, err.stack);
            return `Error: ${err.message}`;
        }
    },
};
