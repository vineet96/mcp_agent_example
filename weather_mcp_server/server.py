"""weather_mcp_server — a tiny MCP server with a get_weather tool.

This is what an external/third-party MCP server looks like. It's a
standalone process — NOT part of the ADK agent. The ADK agent connects
to it over HTTP. You could replace this with any MCP server (a vendor's
hosted MCP, your own internal toolchain, an open-source server like
@modelcontextprotocol/server-postgres) and the agent code wouldn't
change beyond the URL.

The transport here is "Streamable HTTP" (FastMCP's default for
production deployments). Streamable HTTP is what makes the server
work behind a load balancer, in Cloud Run, behind an Agent Gateway,
etc. — the older stdio transport assumed local subprocess communication
and doesn't survive in managed runtimes.

To run locally:
    pip install fastmcp httpx
    python server.py
    # → listens on http://localhost:8080/mcp

To deploy to Cloud Run (see deploy/deploy_mcp_server.sh):
    gcloud run deploy weather-mcp \\
        --source . --region us-central1 --allow-unauthenticated
    # → returns a https URL like https://weather-mcp-xxx.a.run.app
    #   Use https://weather-mcp-xxx.a.run.app/mcp as WEATHER_MCP_URL.
"""

from __future__ import annotations

import os

import httpx
from fastmcp import FastMCP


# FastMCP server with Streamable HTTP transport.
#
# The `name` shows up in the MCP handshake — useful for the client to
# verify it connected to the right server. `stateless_http=True` is the
# right default for Cloud Run / Agent Runtime: each request is
# independent, so the server scales horizontally without needing
# sticky sessions or shared memory.
mcp = FastMCP(name="weather-mcp")


@mcp.tool()
async def get_weather(city: str) -> dict:
    """Get the current weather for a city.

    Uses the Open-Meteo API (no API key required, free for non-commercial
    use). The flow is:

      1. Geocode the city name to lat/lon via Open-Meteo's geocoding API.
      2. Pull the current weather for those coordinates.
      3. Return a flat dict — the LLM is good at consuming structured
         JSON and the MCP protocol passes it through as the tool result.

    Args:
        city: A city name in English. Spelling matters for the geocoder.

    Returns:
        A dict with: city, country, temperature_c, wind_kmh, conditions,
        or an `error` field if the lookup failed.
    """
    geocode_url = "https://geocoding-api.open-meteo.com/v1/search"
    weather_url = "https://api.open-meteo.com/v1/forecast"

    async with httpx.AsyncClient(timeout=10) as client:
        # Step 1: geocode.
        try:
            geo = await client.get(
                geocode_url,
                params={"name": city, "count": 1, "language": "en"},
            )
            geo.raise_for_status()
            results = (geo.json() or {}).get("results") or []
        except httpx.HTTPError as e:
            return {"error": f"Geocoding failed: {e}"}
        if not results:
            return {
                "error": f"Couldn't find a city called '{city}'. "
                         f"Check the spelling?"
            }
        place = results[0]

        # Step 2: current weather.
        try:
            wx = await client.get(
                weather_url,
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,wind_speed_10m,weather_code",
                },
            )
            wx.raise_for_status()
            current = (wx.json() or {}).get("current") or {}
        except httpx.HTTPError as e:
            return {"error": f"Weather lookup failed: {e}"}

    # Step 3: translate Open-Meteo's WMO weather codes into plain English.
    # Full table: https://open-meteo.com/en/docs#weather_variable_documentation
    wmo = {
        0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 48: "depositing rime fog",
        51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
        61: "slight rain", 63: "moderate rain", 65: "heavy rain",
        71: "slight snow", 73: "moderate snow", 75: "heavy snow",
        80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
        95: "thunderstorm", 96: "thunderstorm with hail",
    }
    code = current.get("weather_code")

    return {
        "city": place.get("name"),
        "country": place.get("country"),
        "temperature_c": current.get("temperature_2m"),
        "wind_kmh": current.get("wind_speed_10m"),
        "conditions": wmo.get(code, f"unknown (WMO code {code})"),
    }


if __name__ == "__main__":
    # Streamable HTTP transport on port 8080 (the Cloud Run convention).
    # The /mcp path is FastMCP's default mount; the ADK agent's
    # WEATHER_MCP_URL must point at the full URL including /mcp.
    #
    # stateless_http=True is the right default for Cloud Run / Agent
    # Runtime: each request is independent, so the server scales
    # horizontally without needing sticky sessions or shared memory.
    port = int(os.getenv("PORT", "8080"))
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=port,
        stateless_http=True,
    )
