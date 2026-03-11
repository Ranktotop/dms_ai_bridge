/**
 * DMS AI Bridge — AnythingLLM Agent Skill
 *
 * Searches the personal document archive via dms_ai_bridge.
 * Tries POST /chat/anythingllm first (Phase IV ReAct agent),
 * falls back to POST /query/anythingllm (Phase III semantic search).
 */

module.exports.runtime = {
  handler: async function ({ query }) {
    const callerId = `${this.config.name}-v${this.config.version}`;

    try {
      this.introspect(`${callerId}: Searching for "${query}"...`);
      this.logger(`Query: ${query}`);

      if (!query || query.trim() === "") {
        return "Error: Query cannot be empty.";
      }

      const baseUrl = (this.runtimeArgs.DMS_BRIDGE_URL || "http://dms-bridge:8000").replace(/\/+$/, "");
      const apiKey = this.runtimeArgs.DMS_BRIDGE_API_KEY || "";
      const userId = this.runtimeArgs.DMS_BRIDGE_USER_ID || "1";
      const limit = parseInt(this.runtimeArgs.DMS_BRIDGE_LIMIT || "5", 10) || 5;

      const headers = {
        "Content-Type": "application/json",
        "X-Api-Key": apiKey,
      };
      const body = JSON.stringify({ query, user_id: userId, limit });

      // Helper: fetch with timeout
      async function fetchWithTimeout(url, options, timeoutMs) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        try {
          return await fetch(url, { ...options, signal: controller.signal });
        } finally {
          clearTimeout(timeoutId);
        }
      }

      // Attempt Phase IV endpoint: /chat/anythingllm/stream (SSE)
      try {
        this.introspect(`${callerId}: Denke nach...`);
        this.logger(`POST ${baseUrl}/chat/anythingllm/stream`);

        const response = await fetchWithTimeout(
          `${baseUrl}/chat/anythingllm/stream`,
          { method: "POST", headers, body },
          120000,
        );

        if (response.ok) {
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let answer = "";
          let buffer = "";
          const citations = [];

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            // SSE events are separated by double newlines
            const parts = buffer.split("\n\n");
            buffer = parts.pop(); // keep incomplete trailing chunk

            for (const part of parts) {
              const line = part.trim();
              if (!line.startsWith("data: ")) continue;
              const raw = line.slice(6).trim();
              if (raw === "[DONE]") break;
              try {
                const parsed = JSON.parse(raw);
                if (parsed.type === "step") {
                  this.introspect(`${callerId}: ${parsed.chunk}`);
                } else if (parsed.type === "answer") {
                  answer += parsed.chunk;
                } else if (parsed.type === "citation") {
                  const name = parsed.data?.source?.name || parsed.data?.metadata?.[0]?.source || "";
                  if (name && !citations.includes(name)) citations.push(name);
                }
              } catch (_) {
                // ignore malformed events
              }
            }
          }

          if (answer.trim()) {
            let result = answer.trim();
            if (citations.length > 0) {
              result += "\n\n**Sources:**\n" + citations.map((c, i) => `${i + 1}. ${c}`).join("\n");
            }
            return result;
          }
        }
        this.logger(`Stream endpoint returned status ${response.status}, falling back.`);
      } catch (err) {
        this.logger(`Stream endpoint failed: ${err.message} — falling back to query endpoint.`);
      }

      // Fallback: Phase III endpoint: /query/anythingllm
      this.introspect(`${callerId}: Falling back to semantic search...`);
      this.logger(`POST ${baseUrl}/query/anythingllm`);

      const response = await fetchWithTimeout(
        `${baseUrl}/query/anythingllm`,
        { method: "POST", headers, body },
        30000,
      );

      if (!response.ok) {
        return `Document search failed: server returned status ${response.status}`;
      }

      const data = await response.json();
      const results = data.results || [];

      if (!results.length) {
        return `No documents found matching: ${query}`;
      }

      const lines = [`Found ${results.length} document(s):`];
      results.forEach((r, i) => {
        const title = r.title || r.dms_doc_id || "Unknown";
        const score = r.score != null ? r.score.toFixed(3) : "—";
        lines.push(`${i + 1}. ${title} (score: ${score})`);
        if (r.chunk_text) {
          lines.push(`   ${r.chunk_text.slice(0, 200).replace(/\n/g, " ")}...`);
        }
      });

      this.introspect(`${callerId}: Found ${results.length} result(s).`);
      return lines.join("\n");
    } catch (err) {
      const msg = `${callerId} failed: ${err.message}`;
      this.introspect(msg);
      this.logger(msg, err.stack);
      return `Document search error: ${err.message}`;
    }
  },
};
