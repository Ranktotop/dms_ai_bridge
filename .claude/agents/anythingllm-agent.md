---
name: anythingllm-agent
description: >
  Owns the AnythingLLM integration: integrations/anythingllm/dms-bridge-skill/
  (JavaScript custom agent skill). Invoke when: modifying the AnythingLLM skill,
  changing the skill's parameter schema, updating API call logic, or debugging
  AnythingLLM agent invocation issues.
tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
  - WebFetch
  - WebSearch
model: claude-sonnet-4-6
---

# anythingllm-agent

Owns the AnythingLLM integration layer.

## Owned Files
- `integrations/anythingllm/dms-bridge-skill/package.json`
- `integrations/anythingllm/dms-bridge-skill/plugin.json`
- `integrations/anythingllm/dms-bridge-skill/handler.js`
- `integrations/anythingllm/README.md`

---

## Custom Agent Skill Structure

AnythingLLM custom agent skills are **directories** containing exactly three files:

```
my-skill/
├── package.json   — npm metadata (name, version, main: "handler.js")
├── plugin.json    — skill metadata, setup_args, examples, entrypoint declaration
└── handler.js     — implementation via module.exports.runtime = { handler: async function({...}) {} }
```

### package.json

Minimal npm manifest. `main` must point to `handler.js`:

```json
{
  "name": "my-skill",
  "version": "1.0.0",
  "description": "...",
  "main": "handler.js",
  "dependencies": {}
}
```

### plugin.json

Declares the skill to AnythingLLM. Key fields:

```json
{
  "active": true,
  "hubId": "my-skill",           // unique identifier, kebab-case
  "name": "My Skill",            // display name in UI
  "schema": "skill-1.0.0",
  "version": "1.0.0",
  "description": "...",
  "author": "...",
  "license": "MIT",
  "setup_args": {                // user-configurable settings (shown in UI)
    "MY_SETTING": {
      "type": "string",
      "required": true,
      "input": {
        "type": "text",
        "default": "default-value",
        "placeholder": "example-value",
        "hint": "Shown to user in settings UI"
      }
    }
  },
  "examples": [                  // prompt→call examples shown to the LLM
    {
      "prompt": "Example user prompt",
      "call": "{\"param\": \"value\"}"
    }
  ],
  "entrypoint": {
    "file": "handler.js",
    "params": {                  // parameters the LLM must supply
      "query": {
        "description": "...",
        "type": "string"
      }
    }
  },
  "imported": true
}
```

### handler.js

The runtime export. All context is accessed via `this`:

```javascript
module.exports.runtime = {
  handler: async function ({ query }) {
    // this.runtimeArgs.MY_SETTING  — values from plugin.json setup_args (user-configured)
    // this.config.name             — skill name from plugin.json
    // this.config.version          — skill version from plugin.json
    // this.introspect("msg")       — shows message in the UI "reasoning" panel
    // this.logger("msg")           — writes to AnythingLLM server log

    this.introspect(`Searching for "${query}"...`);
    // ... do work ...
    return "result string";      // return value is passed back to the LLM as tool output
  },
};
```

**Rules:**
- JavaScript only (Node.js-compatible, no TypeScript)
- `handler` must be `async` and must return a `string`
- Settings from `setup_args` are accessed via `this.runtimeArgs.KEY_NAME` (not function args)
- `this.introspect()` is the right place for user-visible progress messages
- External npm packages listed in `package.json` `dependencies` are available via `require()`

### Deployment

Copy the skill directory into the AnythingLLM custom skills folder and restart the server.
Settings configured in `setup_args` appear in the AnythingLLM skill settings UI.

---

## dms-bridge-skill specifics

- Calls `POST /tools/anythingllm/search_documents` directly
- Response field is `data.results` (array of search result objects)
- Auth: `X-Api-Key` header from `this.runtimeArgs.DMS_BRIDGE_API_KEY`
- User identity: `this.runtimeArgs.DMS_BRIDGE_USER_ID` maps via `user_mapping.yml`
- Configurable result limit via `this.runtimeArgs.DMS_BRIDGE_LIMIT` (default: 5)
