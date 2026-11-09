## Assign tools to an agent

Tools reach an agent two ways, and both are explicit:

- directly, on the agent form, **Tools** tab: add the `ai.tool` records this
  agent may run;
- through a **skill**: a skill carries its tools like a bundle, so putting
  "Search Database" on an agent brings its tools with it, exactly as a reusable
  prompt pack should. A tool listed both ways is offered once.

An agent with no tool and no skill runs none. **Only read-only tools** refuses
every tool not flagged `readonly`, which is how an assistant stays useful
without being able to change a record.

A tool is offered only when the user talking to the agent may read the model
the tool acts on. The check is a courtesy: the boundary is the user's own
access rights, applied when the tool runs.

## Endpoints the responder calls

Both are authenticated with the agent token (`Authorization: Bearer <token>`).

    POST /ai_agent/tools/declared
    {"user_id": 42}
    -> {"status": "ok", "tools": [{"name": ..., "description": ...,
                                   "inputSchema": {...}, "outputSchema": {...}}]}

    POST /ai_agent/tools/call
    {"user_id": 42, "tool": "get_date", "arguments": {},
     "record_model": "res.partner", "record_id": 7}
    -> {"status": "ok", "result": {...}}

`record_model` and `record_id` are only needed by tools that work on a record;
`record_model` is the model of that record for a `generic_model` tool, whose
own model is the one holding the function.

Refusals are explicit: `unknown_user`, `user_not_allowed`, `missing_tool`,
`tool_not_assigned`, `tool_not_readonly`, `bad_arguments`, `tool_failed` with a
short message. Each definition carries `readonly` and `kind` so the responder
can present mutating tools differently.

## Server actions as tools

Install `ai_tool_server_action` (once merged, or the review branch) and the
same tab offers server-action tools with no extra configuration here: they are
`ai.tool` records like the others, and they run with the user's rights.
