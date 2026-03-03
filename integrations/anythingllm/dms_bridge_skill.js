/**
 * DMS AI Bridge — AnythingLLM Agent Skill
 *
 * Searches the personal document archive via dms_ai_bridge.
 * Deploy to the AnythingLLM skills directory and restart.
 *
 * Tries POST /chat/anythingllm first (Phase IV ReAct agent),
 * falls back to POST /query/anythingllm (Phase III semantic search).
 */

const skill = {
  name: "DMS Document Search",
  description:
    "Search personal document archive (invoices, contracts, letters, receipts, etc.)",

  settings: {
    API_URL: {
      type: "string",
      default: "http://dms-bridge:8000",
      description: "dms_ai_bridge server base URL",
    },
    API_KEY: {
      type: "string",
      default: "",
      description: "X-Api-Key header value",
    },
    USER_ID: {
      type: "string",
      default: "1",
      description:
        "AnythingLLM user ID (must be mapped in config/user_mapping.yml)",
    },
    LIMIT: {
      type: "number",
      default: 5,
      description: "Maximum number of search results",
    },
    TIMEOUT_MS: {
      type: "number",
      default: 30000,
      description: "HTTP request timeout in milliseconds",
    },
  },

  parameters: {
    query: {
      type: "string",
      required: true,
      description: "Natural language search query for the document archive",
    },
  },

  async handler(args, config) {
    const { query } = args;
    const baseUrl = (config.API_URL || "http://dms-bridge:8000").replace(
      /\/+$/,
      ""
    );
    const headers = {
      "Content-Type": "application/json",
      "X-Api-Key": config.API_KEY || "",
    };
    const body = JSON.stringify({
      query,
      user_id: config.USER_ID || "1",
      limit: config.LIMIT || 5,
    });
    const timeoutMs = config.TIMEOUT_MS || 30000;

    // Helper: fetch with timeout
    async function fetchWithTimeout(url, options) {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(url, {
          ...options,
          signal: controller.signal,
        });
        return response;
      } finally {
        clearTimeout(timeoutId);
      }
    }

    // Attempt Phase IV endpoint: /chat/anythingllm
    try {
      const chatUrl = baseUrl + "/chat/anythingllm";
      const response = await fetchWithTimeout(chatUrl, {
        method: "POST",
        headers,
        body,
      });
      if (response.ok) {
        const data = await response.json();
        if (data.answer) {
          return data.answer;
        }
      }
    } catch (_) {
      // Phase IV not available or timed out — fall through to Phase III
    }

    // Fallback: Phase III endpoint: /query/anythingllm
    try {
      const queryUrl = baseUrl + "/query/anythingllm";
      const response = await fetchWithTimeout(queryUrl, {
        method: "POST",
        headers,
        body,
      });
      if (!response.ok) {
        return (
          "Document search failed: server returned status " + response.status
        );
      }
      const data = await response.json();
      const points = data.points || [];
      if (!points.length) {
        return "No documents found matching: " + query;
      }
      const lines = ["Found " + points.length + " document(s):"];
      points.forEach((p, i) => {
        const title = p.title || p.dms_doc_id || "Unknown";
        const score = p.score != null ? p.score.toFixed(3) : "—";
        lines.push((i + 1) + ". " + title + " (score: " + score + ")");
        if (p.chunk_text) {
          const preview = p.chunk_text.slice(0, 200).replace(/\n/g, " ");
          lines.push("   " + preview + "...");
        }
      });
      return lines.join("\n");
    } catch (err) {
      return "Document search error: " + (err.message || String(err));
    }
  },
};

module.exports = skill;
