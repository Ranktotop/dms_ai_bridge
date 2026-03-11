/**
 * DMS AI Bridge — AnythingLLM Agent Skill
 *
 * Routes tool calls from AnythingLLM's LLM to the appropriate dms_ai_bridge endpoint.
 * The LLM selects the action and supplies the matching parameters; this handler
 * dispatches the call, contacts the bridge, and formats the response as Markdown.
 *
 * Available actions:
 *   search_documents     — semantic search across the personal document archive
 *   list_filter_options  — list correspondents, document types, and tags
 *   get_document_details — metadata + content for a specific document by ID
 *   get_document_full    — paginated full text of a specific document
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
 * @property {string} DMS_BRIDGE_LIMIT   - Max results returned by search_documents
 */

/**
 * A single document returned by search_documents.
 *
 * @typedef {Object} SearchResult
 * @property {string}      dms_doc_id     - Unique document ID in the DMS
 * @property {string}      title          - Document title
 * @property {number}      score          - Semantic similarity score (0–1)
 * @property {string|null} created        - ISO creation date string
 * @property {string|null} correspondent  - Correspondent name
 * @property {string|null} document_type  - Document type name
 * @property {string[]}    tags           - Applied tag names
 * @property {string|null} content        - Content preview
 * @property {string|null} view_url       - URL to open the document in the DMS UI
 */

/**
 * A full document record returned by get_document_details.
 *
 * @typedef {Object} DocumentDetail
 * @property {string}      dms_doc_id
 * @property {string}      title
 * @property {string|null} created
 * @property {string|null} correspondent
 * @property {string|null} document_type
 * @property {string[]}    tags
 * @property {string|null} content
 * @property {string|null} view_url
 */

/**
 * Paginated full-text response returned by get_document_full.
 *
 * @typedef {Object} DocumentPage
 * @property {string}      content        - Text content for the current page
 * @property {number}      total_length   - Total character count of the full document
 * @property {number|null} next_start_char - Offset to pass for the next page; null if last page
 */

/**
 * Available filter categories returned by list_filter_options.
 *
 * @typedef {Object} FilterOptions
 * @property {string[]} correspondents - Known correspondent names
 * @property {string[]} document_types - Known document type names
 * @property {string[]} tags           - Known tag names
 */

/**
 * Resolved bridge connection settings.
 *
 * @typedef {Object} BridgeConfig
 * @property {string} baseUrl
 * @property {string} apiKey
 * @property {string} userId
 * @property {number} limit
 */


// ==========================================
// ============= CONFIGURATION ==============
// ==========================================

/**
 * Extracts and validates bridge connection settings from AnythingLLM's injected args.
 * Throws if a required value is absent or malformed so the error surfaces early.
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

    const rawLimit = parseInt(runtimeArgs.DMS_BRIDGE_LIMIT || "5", 10);
    const limit = isNaN(rawLimit) || rawLimit < 1 ? 5 : rawLimit;

    return { baseUrl, apiKey, userId, limit };
}


// ==========================================
// ============= HTTP CLIENT ================
// ==========================================

const REQUEST_TIMEOUT_MS = 30_000;

/**
 * Sends a POST request to a dms_ai_bridge endpoint and returns the parsed JSON body.
 * Aborts after REQUEST_TIMEOUT_MS to avoid hanging the skill indefinitely.
 *
 * @param {string} baseUrl  - Bridge server base URL (no trailing slash)
 * @param {string} apiKey   - Value for the X-Api-Key header
 * @param {string} path     - Endpoint path (e.g. "/tools/anythingllm/search_documents")
 * @param {Object} body     - Request payload (will be JSON-serialised)
 * @returns {Promise<Object>} Parsed JSON response body
 * @throws {Error} On network error, timeout, or non-2xx HTTP status
 */
async function postJson(baseUrl, apiKey, path, body) {
    const controller = new AbortController();
    const timeoutHandle = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    let response;
    try {
        response = await fetch(`${baseUrl}${path}`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Api-Key": apiKey,
            },
            body: JSON.stringify(body),
            signal: controller.signal,
        });
    } finally {
        clearTimeout(timeoutHandle);
    }

    if (!response.ok) {
        throw new Error(`Bridge returned HTTP ${response.status} for ${path}`);
    }

    return response.json();
}


// ==========================================
// =========== RESPONSE FORMATTERS ==========
// ==========================================

/**
 * Formats a list of search results into a Markdown document list for the LLM.
 * Truncates individual content previews to 1 000 chars to keep context manageable.
 *
 * @param {SearchResult[]} results
 * @returns {string}
 */
function formatSearchResults(results) {
    if (!results.length) {
        return "No matching documents found.";
    }

    const lines = [`Found ${results.length} document(s):\n`];

    results.forEach((r, index) => {
        const score = r.score != null ? r.score.toFixed(3) : "—";
        lines.push(`## ${index + 1}. ${r.title || "Untitled"} (ID: ${r.dms_doc_id}, Score: ${score})`);
        if (r.created)        lines.push(`Date: ${r.created}`);
        if (r.correspondent)  lines.push(`Correspondent: ${r.correspondent}`);
        if (r.document_type)  lines.push(`Type: ${r.document_type}`);
        if (r.tags?.length)   lines.push(`Tags: ${r.tags.join(", ")}`);
        if (r.view_url)       lines.push(`URL: ${r.view_url}`);
        if (r.content)        lines.push(`\n${r.content.slice(0, 1_000)}`);
        lines.push("");
    });

    return lines.join("\n");
}

/**
 * Formats available filter options as a structured Markdown list.
 * The LLM can use these values to refine a subsequent search query.
 *
 * @param {FilterOptions} options
 * @returns {string}
 */
function formatFilterOptions(options) {
    const sections = [];

    if (options.correspondents?.length) {
        sections.push("**Correspondents:**\n" + options.correspondents.map(c => `- ${c}`).join("\n"));
    }
    if (options.document_types?.length) {
        sections.push("**Document types:**\n" + options.document_types.map(t => `- ${t}`).join("\n"));
    }
    if (options.tags?.length) {
        sections.push("**Tags:**\n" + options.tags.map(t => `- ${t}`).join("\n"));
    }

    return sections.length ? sections.join("\n\n") : "No filter options available.";
}

/**
 * Formats a list of document detail records as Markdown.
 * Multiple records occur when the user has access to the same document
 * via more than one DMS engine, resulting in separate RAG entries.
 *
 * @param {DocumentDetail[]} docs
 * @returns {string}
 */
function formatDocumentDetails(docs) {
    if (!docs.length) {
        return "Document not found.";
    }

    return docs.map(d => {
        const lines = [`## ${d.title || "Untitled"} (ID: ${d.dms_doc_id})`];
        if (d.created)        lines.push(`Date: ${d.created}`);
        if (d.correspondent)  lines.push(`Correspondent: ${d.correspondent}`);
        if (d.document_type)  lines.push(`Type: ${d.document_type}`);
        if (d.tags?.length)   lines.push(`Tags: ${d.tags.join(", ")}`);
        if (d.view_url)       lines.push(`URL: ${d.view_url}`);
        if (d.content)        lines.push(`\n${d.content}`);
        return lines.join("\n");
    }).join("\n\n---\n\n");
}

/**
 * Formats a single paginated document page, appending a continuation hint
 * whenever more text remains so the LLM knows to call again.
 *
 * @param {DocumentPage} page
 * @param {string}       documentId - Included in the hint so the LLM can repeat the call
 * @returns {string}
 */
function formatDocumentPage(page, documentId) {
    const lines = [page.content];

    if (page.next_start_char != null) {
        lines.push(
            `\n[Document continues — ${page.next_start_char} of ${page.total_length} chars shown.` +
            ` Call get_document_full with document_id="${documentId}" and start_char=${page.next_start_char} to read the next page.]`
        );
    }

    return lines.join("\n");
}


// ==========================================
// ============= ACTION HANDLERS ============
// ==========================================

/**
 * Searches the user's document archive by semantic similarity and returns ranked results.
 *
 * @param {Object} ctx     - Skill context (this) — used for introspect/logger
 * @param {BridgeConfig} config
 * @param {string} query   - Natural language search query (must be non-empty)
 * @returns {Promise<string>} Formatted Markdown result list
 */
async function handleSearchDocuments(ctx, config, query) {
    if (!query?.trim()) {
        return "Error: query is required for search_documents.";
    }

    ctx.introspect(`Searching for: "${query.trim()}"…`);

    const data = await postJson(config.baseUrl, config.apiKey, "/tools/anythingllm/search_documents", {
        user_id: config.userId,
        query: query.trim(),
        limit: config.limit,
    });

    const results = /** @type {SearchResult[]} */ (data.results || []);
    ctx.introspect(`Found ${results.length} result(s).`);
    return formatSearchResults(results);
}

/**
 * Fetches all filter options (correspondents, document types, tags) available to the user.
 * Call this before search_documents when the user mentions a name or category that should
 * be matched exactly — the returned values can be included in the search query.
 *
 * @param {Object} ctx
 * @param {BridgeConfig} config
 * @returns {Promise<string>} Formatted filter option list
 */
async function handleListFilterOptions(ctx, config) {
    ctx.introspect("Loading filter options…");

    const data = /** @type {FilterOptions} */ (await postJson(
        config.baseUrl, config.apiKey, "/tools/anythingllm/list_filter_options",
        { user_id: config.userId }
    ));

    return formatFilterOptions(data);
}

/**
 * Fetches full metadata and the complete content of a specific document by its ID.
 * Use this after search_documents when the user wants to inspect a particular result.
 *
 * @param {Object} ctx
 * @param {BridgeConfig} config
 * @param {string} documentId - DMS document ID (from search results)
 * @returns {Promise<string>} Formatted document details
 */
async function handleGetDocumentDetails(ctx, config, documentId) {
    if (!documentId?.trim()) {
        return "Error: document_id is required for get_document_details.";
    }

    ctx.introspect(`Loading document details for ID ${documentId.trim()}…`);

    const data = await postJson(config.baseUrl, config.apiKey, "/tools/anythingllm/get_document_details", {
        user_id: config.userId,
        document_id: documentId.trim(),
    });

    // The endpoint returns either a single object or an array of objects
    const docs = /** @type {DocumentDetail[]} */ (Array.isArray(data) ? data : [data]);
    return formatDocumentDetails(docs);
}

/**
 * Fetches one page of the full text of a document.
 * For long documents, repeat the call with the returned next_start_char until it is null.
 *
 * @param {Object} ctx
 * @param {BridgeConfig} config
 * @param {string} documentId - DMS document ID
 * @param {number} startChar  - Character offset to begin reading from (default: 0)
 * @returns {Promise<string>} One page of document text with optional continuation hint
 */
async function handleGetDocumentFull(ctx, config, documentId, startChar) {
    if (!documentId?.trim()) {
        return "Error: document_id is required for get_document_full.";
    }

    const offset = Number.isInteger(startChar) && startChar >= 0 ? startChar : 0;
    ctx.introspect(`Loading document ${documentId.trim()} from char offset ${offset}…`);

    const data = /** @type {DocumentPage} */ (await postJson(
        config.baseUrl, config.apiKey, "/tools/anythingllm/get_document_full",
        { user_id: config.userId, document_id: documentId.trim(), start_char: offset }
    ));

    return formatDocumentPage(data, documentId.trim());
}


// ==========================================
// ============== ACTION ROUTER =============
// ==========================================

/**
 * Human-readable description of each action and its required parameters.
 * Returned verbatim when the LLM supplies an unknown action name.
 *
 * @type {Record<string, string>}
 */
const ACTION_DESCRIPTIONS = {
    search_documents:     "Semantic search — required: query",
    list_filter_options:  "List correspondents / types / tags — no extra params needed",
    get_document_details: "Full metadata + content — required: document_id",
    get_document_full:    "Paginated full text — required: document_id; optional: start_char",
};

/**
 * Dispatches the incoming skill call to the matching action handler.
 * Returns a human-readable error string for unknown action names so the
 * LLM can self-correct on the next turn.
 *
 * @param {Object}      ctx
 * @param {string}      action
 * @param {BridgeConfig} config
 * @param {string|null} query
 * @param {string|null} documentId
 * @param {number}      startChar
 * @returns {Promise<string>}
 */
async function dispatchAction(ctx, action, config, query, documentId, startChar) {
    switch (action) {
        case "search_documents":
            return handleSearchDocuments(ctx, config, query);
        case "list_filter_options":
            return handleListFilterOptions(ctx, config);
        case "get_document_details":
            return handleGetDocumentDetails(ctx, config, documentId);
        case "get_document_full":
            return handleGetDocumentFull(ctx, config, documentId, startChar);
        default: {
            const actionList = Object.entries(ACTION_DESCRIPTIONS)
                .map(([name, desc]) => `  ${name}: ${desc}`)
                .join("\n");
            return `Unknown action "${action}". Valid actions:\n${actionList}`;
        }
    }
}


// ==========================================
// ============== ENTRYPOINT ================
// ==========================================

module.exports.runtime = {
    /**
     * Entry point called by AnythingLLM's agent runtime.
     * The LLM selects the action and supplies the matching parameters.
     *
     * @param {Object}      params
     * @param {string}      params.action       - Tool action to perform
     * @param {string}      [params.query]      - Search query (search_documents only)
     * @param {string}      [params.document_id] - Document ID (detail/full actions)
     * @param {number}      [params.start_char]  - Page offset (get_document_full only)
     * @returns {Promise<string>}
     */
    handler: async function ({ action, query, document_id, start_char }) {
        const callerId = `${this.config.name}-v${this.config.version}`;
        this.logger(`[${callerId}] action=${action} query=${query ?? ""} doc=${document_id ?? ""} start=${start_char ?? 0}`);

        try {
            // Validate bridge connection settings before any network call
            const config = readConfig(this.runtimeArgs);

            return await dispatchAction(
                this,
                action,
                config,
                query   ?? null,
                document_id ?? null,
                typeof start_char === "number" ? start_char : 0,
            );
        } catch (err) {
            const message = `${callerId} failed: ${err.message}`;
            this.introspect(message);
            this.logger(message, err.stack);
            return `Error: ${err.message}`;
        }
    },
};
