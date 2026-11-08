# Copyright 2026-TODAY Akretion - Raphael Valyi <raphael.valyi@akretion.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Soft dependency on ai_tool
try:
    from odoo.addons.ai_tool.tools import aitool

    AI_TOOL_AVAILABLE = True
except ImportError:
    aitool = None  # noqa: F841
    AI_TOOL_AVAILABLE = False


class AgentToolsController(http.Controller):
    """Endpoint for AI agent to execute Odoo tools.

    This controller provides a native API for AI agent to call Odoo tools.
    If ai_tool is installed, tools are also available via MCP.

    Tools are discovered dynamically from:
    1. ai.tool records (when ai_tool is installed)
    2. ai.bridge.agent.tools.mock methods (always available for testing)
    """

    @http.route(
        "/ai_agent/tools/execute",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_tools_execute(self, **kwargs):
        """Execute an Odoo tool and return the result.

        Expected JSON body:
        {
            "tool": "hello_world",
            "args": {"name": "Raphaël"}
        }
        """
        # Validate auth token from Authorization header
        auth_header = request.httprequest.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip() if auth_header else ""
        agent = request.env["ai.bridge.agent"].sudo()._find_by_token(token)
        if not agent:
            return {"status": "error", "message": "Invalid token"}

        data = request.get_json_data()
        if not data:
            return {"status": "error", "message": "No JSON data"}

        tool_name = data.get("tool")
        args = data.get("args", {})

        if not tool_name:
            return {"status": "error", "message": "Missing 'tool' parameter"}

        # Get the AI user from the agent for permission context
        ai_user = agent.ai_user_id
        if not ai_user:
            return {"status": "error", "message": "No AI user configured"}

        # Execute tool with the AI user's permissions
        try:
            result = self._execute_tool(request.env(user=ai_user.id), tool_name, args)
            return {"status": "ok", "result": result}
        except Exception as e:
            _logger.warning("AI agent tool execution failed: %s - %s", tool_name, e)
            return {"status": "error", "message": str(e)}

    @http.route(
        "/ai_agent/tools/list",
        type="json",
        auth="public",
        methods=["GET", "POST"],
        csrf=False,
    )
    def agent_tools_list(self, **kwargs):
        """List available tools with their schemas."""
        auth_header = request.httprequest.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip() if auth_header else ""
        agent = request.env["ai.bridge.agent"].sudo()._find_by_token(token)
        if not agent:
            return {"tools": [], "error": "Invalid token"}

        tools = self._get_available_tools(request.env)
        return {"tools": tools}

    @http.route(
        "/ai_agent/tools/available",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_tools_available(self, **kwargs):
        """Tools opened to one user, from the Odoo-side whitelist."""
        agent = self._get_gateway()
        if not agent:
            return {"status": "error", "error": "invalid_token"}
        data = request.get_json_data() or {}
        user = self._get_user(data.get("user_id"))
        if isinstance(user, dict):
            return user
        if not agent._is_user_allowed(user):
            return {"status": "error", "error": "user_not_allowed"}
        tools = request.env["ai.bridge.agent.tool"].sudo()
        return {
            "status": "ok",
            "mode": agent.tool_access_mode,
            "tools": [tool._as_tool_dict() for tool in tools._for_user(user)],
            "models": tools._readable_models(user, agent)[:500],
        }

    @http.route(
        "/ai_agent/tools/models",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_tools_models(self, **kwargs):
        """Models the assistant may query for this user, so it can discover."""
        agent = self._get_gateway()
        if not agent:
            return {"status": "error", "error": "invalid_token"}
        data = request.get_json_data() or {}
        user = self._get_user(data.get("user_id"))
        if isinstance(user, dict):
            return user
        if not agent._is_user_allowed(user):
            return {"status": "error", "error": "user_not_allowed"}
        models = (
            request.env["ai.bridge.agent.tool"]
            .sudo()
            ._readable_models(user, agent)[:500]
        )
        return {"status": "ok", "mode": agent.tool_access_mode, "models": models}

    @http.route(
        "/ai_agent/tools/resolve",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agent_tools_resolve(self, **kwargs):
        """May this user use this tool, and with which limits?

        The agent checks every call here before running the query with the
        user's own API key, so a stale cache or a prompt injection cannot widen
        what the whitelist allows.
        """
        agent = self._get_gateway()
        if not agent:
            return {"status": "error", "error": "invalid_token"}
        data = request.get_json_data() or {}
        user = self._get_user(data.get("user_id"))
        if isinstance(user, dict):
            return user
        if not agent._is_user_allowed(user):
            return {"status": "error", "error": "user_not_allowed"}
        tools = request.env["ai.bridge.agent.tool"].sudo()
        if data.get("tool"):
            tool = tools._resolve_for_user(user, (data.get("tool") or "").strip())
        else:
            tool = tools._resolve_for_user_model(
                user,
                (data.get("model") or "").strip(),
                (data.get("method") or "search_read").strip(),
                agent=agent,
            )
        if not tool:
            return {"status": "error", "error": "tool_not_allowed"}
        return {"status": "ok", "tool": tool}

    def _get_gateway(self):
        auth_header = request.httprequest.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip() if auth_header else ""
        if not token:
            return None
        return request.env["ai.bridge.agent"].sudo()._find_by_token(token)

    def _get_user(self, user_id):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return {"status": "error", "error": "unknown_user"}
        user = request.env["res.users"].sudo().browse(user_id).exists()
        if not user or not user.active:
            return {"status": "error", "error": "unknown_user"}
        return user

    def _get_available_tools(self, env):
        """Return list of available tool definitions.

        Discovers tools from:
        1. ai.tool records (if ai_tool installed)
        2. ai.bridge.agent.tools.mock methods (always available)
        """
        tools = []

        # 1. ai.tool records (when ai_tool is installed)
        if AI_TOOL_AVAILABLE and hasattr(env, "ai.tool"):
            try:
                ai_tools = env["ai.tool"].search([])
                for tool in ai_tools:
                    try:
                        definition = tool._get_tool_definition()
                        tools.append(
                            {
                                "name": definition.get("name", tool.name),
                                "description": tool.description or "",
                                "input_schema": definition.get("inputSchema", {}),
                                "output_schema": definition.get("outputSchema", {}),
                                "source": "ai.tool",
                            }
                        )
                    except Exception as e:
                        _logger.debug("Skipping ai.tool %s: %s", tool.name, e)
            except Exception as e:
                _logger.debug("Could not load ai.tool records: %s", e)

        # 2. Mock tools (always available for testing)
        mock_model = env.get("ai.bridge.agent.tools.mock")
        if mock_model:
            for method_name in dir(mock_model):
                if not method_name.startswith("_ai_"):
                    continue
                method = getattr(mock_model, method_name)
                if not callable(method):
                    continue

                # Build simple schema from docstring/defaults
                doc = (method.__doc__ or "").strip().split("\n")[0]
                tools.append(
                    {
                        "name": method_name[4:],  # Remove _ai_ prefix
                        "description": doc,
                        "input_schema": {"type": "object", "properties": {}},
                        "output_schema": {"type": "object", "properties": {}},
                        "source": "ai.bridge.agent.tools.mock",
                    }
                )

        return tools

    def _execute_tool(self, env, tool_name, args):
        """Execute a tool by name with the given environment."""
        # Try ai.tool first (if ai_tool is installed)
        if AI_TOOL_AVAILABLE:
            try:
                ai_tool = env["ai.tool"].search([("name", "=", tool_name)], limit=1)
                if ai_tool:
                    return ai_tool._execute_tool(**args)
            except Exception as e:
                _logger.debug("ai.tool execution failed: %s", e)

        # Fallback to mock tools
        method_name = f"_ai_{tool_name}"
        mock_model = env.get("ai.bridge.agent.tools.mock")
        if mock_model and hasattr(mock_model, method_name):
            method = getattr(mock_model, method_name)
            return method(**args)

        raise ValueError(f"Unknown tool: {tool_name}")
