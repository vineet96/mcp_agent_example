# MCP Weather Agent — third-party toolchain on Agent Runtime

A minimal, runnable example showing **how to build an ADK agent on GCP Agent
Platform · Agent Runtime that uses a third-party toolchain via MCP (Model
Context Protocol)**.

This repo has two pieces:

```
mcp_agent_example/
├── mcp_weather_agent/         ← the ADK agent (deploys to Agent Runtime)
│   └── mcp_weather_agent/
│       ├── agent.py           ← LlmAgent + McpToolset
│       ├── __init__.py
│       └── .env.example
│
├── weather_mcp_server/        ← the MCP server (deploys to Cloud Run)
│   ├── server.py              ← FastMCP server with a get_weather tool
│   ├── requirements.txt
│   └── Dockerfile
│
└── deploy/
    ├── deploy_mcp_server.sh   ← gcloud run deploy ...
    └── deploy_agent.py        ← agent_engines.create(...)
```

The key insight: **the agent and the MCP server are separate processes,
deployed to separate services, and connected over the network.** The
weather_mcp_server here is just a stand-in — in a real deployment you'd
replace it with a vendor's hosted MCP server, your team's internal
toolchain exposed as MCP, or any of the 500+ public MCP servers in the
wild. The agent doesn't care.

## Architecture

```
┌───────────────────────────────────┐         ┌──────────────────────┐
│  GCP Agent Platform               │         │  Cloud Run           │
│  · Agent Runtime                  │         │  (or anywhere else)  │
│                                   │         │                      │
│  ┌─────────────────────────────┐  │  HTTPS  │  ┌────────────────┐  │
│  │ mcp_weather_agent           │  │ ──────► │  │ weather_mcp    │  │
│  │ (LlmAgent + McpToolset)     │  │  /mcp   │  │ (FastMCP)      │  │
│  └─────────────────────────────┘  │ ◄────── │  └────────────────┘  │
│         Gemini 2.5 Flash          │  JSON   │                      │
└───────────────────────────────────┘         └──────────────────────┘

           User chat                                External API
              ▲                                  (Open-Meteo, in this
              │                                   example — could be
       Gemini Enterprise                          any third-party API)
       or any front-end
       that speaks Agent API
```

The MCP server can live anywhere reachable over HTTPS — Cloud Run, GKE,
an external SaaS, your own VPC. The agent connects via
`StreamableHTTPConnectionParams(url=...)` and treats the server's tools
as if they were native ADK tools.

## Local development

### 1. Run the MCP server locally

```bash
cd weather_mcp_server
pip install -r requirements.txt
python server.py
# → MCP server listening on http://localhost:8080/mcp
```

### 2. Run the agent locally

In another terminal:

```bash
cd mcp_weather_agent
pip install -r requirements.txt

# Copy the env template and set your project ID.
cp mcp_weather_agent/.env.example mcp_weather_agent/.env
# WEATHER_MCP_URL=http://localhost:8080/mcp is the default — that points
# at the local server you just started.

# Authenticate for Vertex AI.
gcloud auth application-default login

# Start the ADK web UI.
adk web
# → open http://localhost:8000 and ask "what's the weather in Berlin?"
```

The agent will call `get_weather("Berlin")` on the local MCP server,
which calls Open-Meteo, which returns a result, which the agent reports
back conversationally.

## Production deployment

Two steps: deploy the MCP server first, then deploy the agent pointing
at the deployed MCP URL.

### Step 1: Deploy the MCP server to Cloud Run

```bash
./deploy/deploy_mcp_server.sh YOUR_PROJECT_ID us-central1
```

After it finishes, grab the public URL:

```bash
MCP_URL=$(gcloud run services describe weather-mcp \
    --project YOUR_PROJECT_ID --region us-central1 \
    --format='value(status.url)')
echo "MCP server URL: $MCP_URL/mcp"
```

### Step 2: Deploy the agent to Agent Runtime

```bash
python deploy/deploy_agent.py \
    --project YOUR_PROJECT_ID \
    --bucket gs://YOUR_STAGING_BUCKET \
    --mcp-url "$MCP_URL/mcp"
```

This creates a new Agent Runtime instance and bakes the MCP URL into
its env vars. Output prints the resource ID — save it for subsequent
updates:

```bash
python deploy/deploy_agent.py \
    --project YOUR_PROJECT_ID \
    --bucket gs://YOUR_STAGING_BUCKET \
    --mcp-url "$MCP_URL/mcp" \
    --resource-id 1234567890   # ← from the previous deploy output
```

## What this example demonstrates (and what it deliberately doesn't)

### Shows:

- **External MCP toolchain.** The MCP server is not on Google's list of
  pre-built MCP servers. It's a regular FastMCP service that happens to
  be reachable over HTTPS. Same pattern works for vendor-hosted MCP
  endpoints, internal team services, or any of the public MCP servers.
- **Streamable HTTP transport.** This is the production-ready MCP
  transport. Stdio MCP servers work in `adk web` but don't survive in
  managed runtimes that can't spawn subprocesses.
- **Synchronous agent definition.** `root_agent` is a plain LlmAgent
  defined at import time — required for Agent Runtime serialization.
- **Env-driven MCP URL.** The same agent code points at a local MCP
  server during development and a Cloud Run URL in production, with
  no code changes.
- **Tar-flattening for deployment.** The deploy script chdirs to a
  staged temp directory before calling `agent_engines.create`, so the
  SDK's `tarfile.add` produces flat entries that resolve at runtime.

### Doesn't show:

- **Authentication on the MCP server.** This example uses
  `--allow-unauthenticated` for the Cloud Run service so you can run
  it without setting up service accounts. For production, you'd want
  to route MCP traffic through Agent Gateway, attach an Agent Identity
  to the agent, and require authenticated requests on the MCP server.
- **Agent Registry.** Production deployments should register the MCP
  server in Agent Registry so it's discoverable, governed, and
  versioned across the organization. For this example we just hardcode
  the URL.
- **Memory.** The agent has no long-term memory. Adding Memory Bank /
  Memory Profiles would let it remember user preferences across
  sessions, but that's out of scope here.
- **Multi-agent orchestration.** This is a single LlmAgent with one
  toolset. The patterns scale to SequentialAgent / parallel / graph
  workflows from the broader ADK 2.0 surface.

## How to adapt this to your own toolchain

If you have an existing toolchain — internal API, SaaS, database, etc.
— that you want to expose to ADK agents on Agent Runtime, the path is:

1. Write a FastMCP server (or use an existing MCP server for your
   tool) that exposes your tools via `@mcp.tool()` decorators. See
   `weather_mcp_server/server.py` for the shape.
2. Deploy that MCP server to a reachable HTTPS endpoint (Cloud Run is
   the easiest; GKE, Cloud Functions, or your own infra also work).
3. Point your ADK agent at that URL via `StreamableHTTPConnectionParams`.
4. (Production) Route the connection through Agent Gateway and register
   the MCP server in Agent Registry for centralized governance.

The agent code never needs to know what your tool *does* — it just
calls `get_widget` (or whatever) and the MCP server handles the rest.
# mcp_agent_example
