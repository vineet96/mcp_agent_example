"""Deploy mcp_weather_agent to GCP Agent Platform · Agent Runtime.

Usage:
    python deploy/deploy_agent.py \\
        --project YOUR_PROJECT \\
        --bucket gs://YOUR_STAGING_BUCKET \\
        --mcp-url https://weather-mcp-xxx.a.run.app/mcp

The MCP server URL is the most important runtime config — it's what
tells the deployed agent where to find its tools. Set it via --mcp-url
or the WEATHER_MCP_URL env var.

ARCHITECTURAL NOTES (carried over from Image Studio's deploy.py, where
these patterns were debugged painfully):

1.  RESERVED ENV VARS. Agent Runtime injects GOOGLE_CLOUD_PROJECT,
    GOOGLE_CLOUD_LOCATION, PORT, K_SERVICE, K_REVISION, K_CONFIGURATION,
    GOOGLE_APPLICATION_CREDENTIALS, and GOOGLE_CLOUD_QUOTA_PROJECT
    automatically. Trying to set any of these via env_vars at deploy
    time gets the deploy REJECTED with FailedPrecondition. We filter
    them out.

2.  EXPLICIT VERTEX MODE. genai.Client() without args reads
    GOOGLE_GENAI_USE_VERTEXAI env to pick its mode — but at deploy
    import time that may not be set. The agent imports work regardless
    because google-adk handles Vertex setup, but if we ever add direct
    genai.Client() calls we should pass vertexai=True explicitly.

3.  TARFILE.ADD PRESERVES ABSOLUTE PATHS. The SDK calls tar.add(path)
    with no arcname, so absolute paths become deeply-nested tar entries
    that don't appear on sys.path at runtime, producing
    "ModuleNotFoundError: No module named 'mcp_weather_agent'". We fix
    this by copying the inner package to a temp dir and chdir'ing there
    so the tar gets flat basenames.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIREMENTS = [
    "google-cloud-aiplatform[agent_engines,adk]>=1.112",
    "google-adk>=1.15.0",
    "google-genai>=1.0.0",
    "mcp>=1.0.0",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Deploy mcp_weather_agent to Agent Runtime.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--project", required=True,
                   help="GCP project ID.")
    p.add_argument("--location", default="us-central1",
                   help="Vertex AI region.")
    p.add_argument("--bucket", required=True,
                   help="Staging bucket (gs://...) for deployment archive.")
    p.add_argument("--mcp-url",
                   default=os.getenv("WEATHER_MCP_URL", ""),
                   help="URL of the deployed weather MCP server "
                        "(e.g. https://weather-mcp-xxx.a.run.app/mcp). "
                        "Falls back to $WEATHER_MCP_URL.")
    p.add_argument("--resource-id", default=None,
                   help="If set, UPDATE this Agent Runtime instead of "
                        "creating a new one.")
    p.add_argument("--display-name", default="MCP Weather Agent",
                   help="Display name in the GCP console.")
    p.add_argument("--description",
                   default="ADK agent demonstrating MCP integration with a "
                           "third-party weather toolchain over Streamable "
                           "HTTP transport.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the deploy plan and exit without calling "
                        "the API.")
    return p.parse_args()


def require(cond: bool, msg: str) -> None:
    if not cond:
        print(f"❌ {msg}", file=sys.stderr)
        sys.exit(1)


def stage_extra_packages() -> tuple[Path, list[str]]:
    """Copy the inner package into a flat temp dir, return (dir, basenames).

    Caller should chdir to the returned dir before invoking the SDK so the
    tar entries become bare basenames. See module docstring section 3.
    """
    inner = REPO_ROOT / "mcp_weather_agent" / "mcp_weather_agent"
    require((inner / "agent.py").is_file(),
            f"Inner package missing agent.py at {inner}")
    require((inner / "__init__.py").is_file(),
            f"Inner package missing __init__.py at {inner}")

    staging = Path(tempfile.mkdtemp(prefix="mcp_weather_deploy_"))
    shutil.copytree(
        inner, staging / inner.name,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return staging, [inner.name]


RESERVED_ENV = {
    "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_QUOTA_PROJECT",
    "GOOGLE_CLOUD_LOCATION", "PORT", "K_SERVICE", "K_REVISION",
    "K_CONFIGURATION", "GOOGLE_APPLICATION_CREDENTIALS",
}


def build_env(mcp_url: str) -> dict[str, str]:
    """Env vars baked into the deployed runtime.

    Reserved names are stripped defensively (and warned about).
    """
    env = {}
    if mcp_url:
        env["WEATHER_MCP_URL"] = mcp_url
    dropped = sorted(set(env) & RESERVED_ENV)
    if dropped:
        print(f"⚠️  Dropping reserved env var names: {dropped}")
        for k in dropped:
            env.pop(k, None)
    return env


def main() -> None:
    args = parse_args()
    require(args.bucket.startswith("gs://"),
            "--bucket must be a gs:// URL.")
    require(args.mcp_url and args.mcp_url.startswith("http"),
            "--mcp-url is required (e.g. https://.../mcp). Deploy the "
            "MCP server first with deploy/deploy_mcp_server.sh.")

    # Set vertex mode in env BEFORE the SDK / agent imports, so any
    # genai client constructed at import time picks the right mode.
    os.environ["GOOGLE_CLOUD_PROJECT"] = args.project
    os.environ["GOOGLE_CLOUD_LOCATION"] = args.location
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

    # Import SDK lazily so --help works without it installed.
    import vertexai
    from vertexai import agent_engines
    from vertexai.preview.reasoning_engines import AdkApp

    print(f"Init Vertex AI SDK (project={args.project}, "
          f"location={args.location})...")
    vertexai.init(
        project=args.project,
        location=args.location,
        staging_bucket=args.bucket,
    )

    # Import the agent. This is where the McpToolset gets constructed,
    # but the MCP connection is lazy so no network call happens here.
    sys.path.insert(0, str(REPO_ROOT / "mcp_weather_agent"))
    from mcp_weather_agent.agent import root_agent

    print("Wrapping root_agent in AdkApp...")
    app = AdkApp(agent=root_agent, enable_tracing=True)

    staging_dir, basenames = stage_extra_packages()
    print(f"Staged package at: {staging_dir}")
    print(f"Basenames for tar: {basenames}")

    env_vars = build_env(args.mcp_url)

    print("\nDeployment plan")
    print("---------------")
    print(f"  Project       : {args.project}")
    print(f"  Location      : {args.location}")
    print(f"  Bucket        : {args.bucket}")
    print(f"  MCP URL       : {args.mcp_url}")
    print(f"  Display name  : {args.display_name}")
    print(f"  Mode          : "
          f"{'UPDATE ' + args.resource_id if args.resource_id else 'CREATE'}")
    print(f"  Env vars      : {list(env_vars.keys())}")
    print(f"  Requirements  : {len(REQUIREMENTS)} packages")

    if args.dry_run:
        print("\n--dry-run set — exiting.")
        return

    # chdir to staging so SDK's tar.add(basename) produces flat entries.
    cwd = os.getcwd()
    os.chdir(staging_dir)
    try:
        t0 = time.time()
        kwargs = dict(
            agent_engine=app,
            requirements=REQUIREMENTS,
            extra_packages=basenames,
            env_vars=env_vars,
            display_name=args.display_name,
            description=args.description,
        )
        if args.resource_id:
            full_name = (f"projects/{args.project}/locations/"
                         f"{args.location}/reasoningEngines/"
                         f"{args.resource_id}")
            print(f"\nUpdating {full_name}...")
            remote = agent_engines.get(full_name).update(**kwargs)
        else:
            print("\nCreating new Agent Runtime "
                  "(usually 3-5 min)...")
            remote = agent_engines.create(**kwargs)
    finally:
        os.chdir(cwd)

    dt = time.time() - t0
    short_id = remote.resource_name.rsplit("/", 1)[-1]
    print(f"\n✅ Done in {dt:.0f}s")
    print(f"Resource name : {remote.resource_name}")
    print(f"Resource ID   : {short_id}")
    print(f"\nFor next UPDATE, use:  --resource-id {short_id}")


if __name__ == "__main__":
    main()
