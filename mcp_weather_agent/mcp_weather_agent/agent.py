"""mcp_weather_agent — an ADK agent that uses a third-party MCP server.

This is a deliberately minimal example to show the MCP integration pattern.
The agent has ONE responsibility: answer weather questions by calling an
external MCP server's `get_weather` tool. The MCP server is the
`weather_mcp_server` package in the sibling folder — but it could just as
easily be a third-party hosted server you don't own.

KEY DESIGN POINTS (read these before changing anything)
=======================================================

1.  CONNECTION TYPE: StreamableHTTPConnectionParams.
    For Agent Runtime deployments, the MCP server must be REACHABLE OVER
    THE NETWORK, not started as a local subprocess. stdio MCP (the default
    in many tutorials) doesn't work in the managed runtime container — it
    assumes you can spawn processes, which Agent Runtime doesn't permit.
    Streamable HTTP is the production-grade transport.

2.  SYNCHRONOUS AGENT DEFINITION.
    The ADK docs are explicit about this: when deploying to Agent Runtime
    (or other managed runtimes), the root_agent variable MUST be defined
    at import time as a plain LlmAgent, not produced by an async factory.
    The McpToolset handles its own async session initialization lazily on
    first tool call.

3.  ENV-DRIVEN MCP URL.
    The MCP server URL comes from the WEATHER_MCP_URL env var. This is
    what makes the agent portable — same code points at a local dev MCP
    server during development and at a Cloud-Run-hosted production MCP
    server in deployment, just by changing the env var.

4.  NO MCP-SPECIFIC LOGIC IN THE AGENT.
    The agent doesn't know it's talking to an MCP server. From the LLM's
    perspective, `get_weather` is just a tool — McpToolset adapts the MCP
    protocol into ADK's tool interface transparently.
"""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StreamableHTTPConnectionParams,
)


# Where to find the MCP server. In development, point this at your local
# weather_mcp_server (`http://localhost:8080/mcp`). In production, point
# it at the deployed Cloud Run URL.
#
# Trailing `/mcp` path is FastMCP's default mount point for the Streamable
# HTTP transport — if you change the mount path on the server, update
# this URL to match.
WEATHER_MCP_URL = os.getenv(
    "WEATHER_MCP_URL", "http://localhost:8080/mcp"
).strip()


# Construct the MCP toolset. This is just an object — no network call
# happens at import time. The connection opens lazily on the first tool
# invocation, and McpToolset's session manager pools connections so we
# don't pay the handshake cost on every turn.
weather_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=WEATHER_MCP_URL,
        # 30s is plenty for a small MCP server; raise this if your MCP
        # server has cold-start latency (Cloud Run min-instances=0 means
        # the first call after idle can take 1-2s).
        timeout=30,
    ),
    # Optional: restrict which tools from the MCP server are exposed.
    # Leave None to expose all of them. Useful when an MCP server has
    # 50 tools but you only want the agent to see 3.
    tool_filter=None,
)


# The agent itself. Defined SYNCHRONOUSLY at module load so it's
# picklable for Agent Runtime deployment. McpToolset handles all the
# async session management internally.
root_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="mcp_weather_agent",
    instruction=(
        "You are a friendly weather assistant. When the user asks about "
        "weather in a city, call the `get_weather` tool with the city "
        "name and report the result conversationally. If the user asks "
        "anything that isn't a weather question, politely tell them you "
        "only do weather and ask if they have a city in mind."
    ),
    tools=[weather_toolset],
)
