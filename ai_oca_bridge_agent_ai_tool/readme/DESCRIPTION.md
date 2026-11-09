The bridge agent module keeps the transport and the data policy. This module
adds the other half of the tool story: **declared operations**.

OCA `ai.tool` records describe one curated operation with an input and output
schema — a Python function today, a server action once
`ai_tool_server_action` lands. Here an administrator assigns those tools to a
bridge agent, and a user talking to that agent can run them.

The two words stay apart:

- a **grant** (`ai.bridge.agent.grant`) bounds which data the assistant may
  read with the user's own key;
- a **tool** (`ai.tool`) declares an operation the assistant may perform.

Tools are assigned explicitly, one by one: a tool that mutates records must
never reach an agent by accident.

Everything runs with the rights of the user who is chatting. The tool is
executed in that user's environment, never the agent's AI user and never
`sudo`, so Odoo ACLs and record rules apply exactly as if the user had clicked
in the interface.

A tool is reachable only when it is assigned to the agent, and only for users
who may read the model it acts on; a record tool also checks the record itself.
`readonly` marks the tools that only read, and an agent in *Only read-only
tools* mode refuses the others. The whole trust model is described in the
*Security model* section of the `ai_oca_bridge_agent` module's `USAGE.md`.
