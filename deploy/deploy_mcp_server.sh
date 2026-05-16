#!/bin/bash
# Deploy the weather MCP server to Cloud Run.
#
# Usage:
#   ./deploy_mcp_server.sh YOUR_PROJECT_ID [us-central1]
#
# After deploy, grab the printed URL and set:
#   export WEATHER_MCP_URL=https://<that-url>/mcp
# Then pass that to deploy_agent.py via --mcp-url, or set it in your
# agent's .env for local runs.

set -euo pipefail

PROJECT="${1:?Usage: $0 PROJECT_ID [REGION]}"
REGION="${2:-us-central1}"
SERVICE="weather-mcp"

cd "$(dirname "$0")/../weather_mcp_server"

echo "Deploying $SERVICE to Cloud Run in $REGION (project: $PROJECT)..."
echo "Working dir: $(pwd)"
echo

gcloud run deploy "$SERVICE" \
    --source . \
    --project "$PROJECT" \
    --region "$REGION" \
    --platform managed \
    --allow-unauthenticated \
    --memory 512Mi \
    --cpu 1 \
    --max-instances 10 \
    --timeout 60s

echo
echo "✅ Done. Get the URL with:"
echo "   gcloud run services describe $SERVICE --project $PROJECT \\"
echo "       --region $REGION --format='value(status.url)'"
echo
echo "Then set WEATHER_MCP_URL=<that-url>/mcp for the agent."
