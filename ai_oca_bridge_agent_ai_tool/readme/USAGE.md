## Assign tools to an agent

Open **Hermes → Configuration → Agents**, pick an agent and use the **Tools**
tab. Add the `ai.tool` records this agent may run. An agent with no tool runs
none: assignment is explicit on purpose.

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
`tool_not_assigned`, `bad_arguments`, `tool_failed` with a short message.

## Server actions as tools

Install `ai_tool_server_action` (once merged, or the review branch) and the
same tab offers server-action tools with no extra configuration here: they are
`ai.tool` records like the others, and they run with the user's rights.
