"""Deploy the charter-agent hosted agent to Foundry.

Builds nothing — assumes the image is already pushed to ACR. Calls
project.agents.create_version(...) and polls until active.

Self-contained: handles tenant switching, az login, and PIM hints so you
don't have to remember the wiring. Just run:

    python scripts/deploy.py                # uses latest charter-agent:vN tag in ACR
    python scripts/deploy.py --image ...:v8 # or pin one explicitly

Env overrides (rarely needed): AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID,
FOUNDRY_PROJECT_ENDPOINT, AGENT_NAME, AGENT_IMAGE, ACR_NAME.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentProtocol,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)
from azure.core.exceptions import HttpResponseError
from azure.identity import AzureCliCredential

# Load agent/.env so all config (tenant, subscription, endpoint, model, toolbox)
# lives in one place. Nothing is hardcoded here — .env is the source of truth.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(
            f"Missing required env var {name}. Set it in {_ENV_PATH} or the shell."
        )
    return val


TENANT_ID = _required("AZURE_TENANT_ID")
SUBSCRIPTION_ID = _required("AZURE_SUBSCRIPTION_ID")
PROJECT_ENDPOINT = _required("FOUNDRY_PROJECT_ENDPOINT")
MODEL_DEPLOYMENT = _required("AZURE_AI_MODEL_DEPLOYMENT_NAME")
TOOLBOX_NAME = _required("TOOLBOX_NAME")
AGENT_NAME = os.environ.get("AGENT_NAME", "charter-agent")
ACR_NAME = os.environ.get("ACR_NAME", "pcdotaiagentd10b5a")
ACR_REPOSITORY = f"{ACR_NAME}.azurecr.io/{AGENT_NAME}"


def _run_az(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    cmd = ["az", *args]
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        shell=(os.name == "nt"),  # `az` is a .cmd on Windows
    )


def ensure_az_login() -> None:
    """Make sure `az` is logged in to the right tenant + subscription."""
    show = _run_az(["account", "show", "-o", "json"])
    needs_login = show.returncode != 0
    if not needs_login:
        try:
            acct = json.loads(show.stdout)
            if acct.get("tenantId", "").lower() != TENANT_ID.lower():
                print(
                    f"az is on tenant {acct.get('tenantId')} but we need {TENANT_ID}; re-logging in..."
                )
                needs_login = True
        except json.JSONDecodeError:
            needs_login = True

    if needs_login:
        print(f"Running: az login --tenant {TENANT_ID}")
        rc = _run_az(["login", "--tenant", TENANT_ID], capture=False).returncode
        if rc != 0:
            raise SystemExit(f"az login failed (exit {rc})")

    sub = _run_az(["account", "set", "--subscription", SUBSCRIPTION_ID])
    if sub.returncode != 0:
        raise SystemExit(f"az account set failed: {sub.stderr.strip()}")
    print(f"az: tenant={TENANT_ID} subscription={SUBSCRIPTION_ID}")


def resolve_latest_image(explicit: str | None) -> str:
    """If user passed --image, use it. Otherwise find latest vN tag in ACR."""
    if explicit:
        return explicit
    env_override = os.environ.get("AGENT_IMAGE")
    if env_override:
        return env_override

    print(f"Querying ACR {ACR_NAME} for latest {AGENT_NAME} tag...")
    res = _run_az(
        [
            "acr",
            "repository",
            "show-tags",
            "-n",
            ACR_NAME,
            "--repository",
            AGENT_NAME,
            "-o",
            "json",
        ]
    )
    if res.returncode != 0:
        raise SystemExit(
            f"Couldn't list ACR tags: {res.stderr.strip()}\n"
            "Pass --image pcdotaiagentd10b5a.azurecr.io/charter-agent:vN explicitly."
        )
    tags = json.loads(res.stdout or "[]")
    versioned = [t for t in tags if re.fullmatch(r"v\d+", t)]
    if not versioned:
        raise SystemExit(f"No vN tags found in {ACR_REPOSITORY}. Tags seen: {tags}")
    latest = max(versioned, key=lambda t: int(t[1:]))
    image = f"{ACR_REPOSITORY}:{latest}"
    print(f"  -> {image}")
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy charter-agent to Foundry.")
    parser.add_argument(
        "--image",
        help="Full ACR image ref, e.g. pcdotaiagentd10b5a.azurecr.io/charter-agent:v8. "
        "Defaults to the latest vN tag in ACR.",
    )
    parser.add_argument(
        "--skip-az-check",
        action="store_true",
        help="Skip the `az login`/tenant check (use if you've already wired it).",
    )
    args = parser.parse_args()

    if not args.skip_az_check:
        ensure_az_login()
    image = resolve_latest_image(args.image)

    print(f"Project: {PROJECT_ENDPOINT}")
    print(f"Agent:   {AGENT_NAME}")
    print(f"Image:   {image}")
    print(f"Model:   {MODEL_DEPLOYMENT}")
    print(f"Toolbox: {TOOLBOX_NAME}")
    print()

    tenant_id = TENANT_ID
    credential = AzureCliCredential(tenant_id=tenant_id, process_timeout=120)
    project = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=credential,
        allow_preview=True,
    )

    definition = HostedAgentDefinition(
        container_protocol_versions=[
            ProtocolVersionRecord(protocol=AgentProtocol.RESPONSES, version="1.0.0")
        ],
        cpu="0.5",
        memory="1Gi",
        image=image,
        environment_variables={
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": MODEL_DEPLOYMENT,
            "TOOLBOX_NAME": TOOLBOX_NAME,
        },
    )

    print("Creating agent version...")
    try:
        agent = project.agents.create_version(agent_name=AGENT_NAME, definition=definition)
    except HttpResponseError as e:
        msg = str(e)
        if "AuthorizationFailed" in msg or "does not have authorization" in msg:
            print(
                "\n*** RBAC denied. You probably need to activate PIM. ***\n"
                "  1. Open https://portal.azure.com → Privileged Identity Management\n"
                "  2. My roles → Azure resources → Eligible assignments\n"
                "  3. Activate 'Foundry Account Owner' on subscription\n"
                f"     {SUBSCRIPTION_ID}\n"
                "  4. Re-run this script.\n",
                file=sys.stderr,
            )
        raise
    version = agent.version
    print(f"  -> name={agent.name} version={version}")
    print()

    print("Polling for status=active ...")
    deadline = time.time() + 600  # 10 min
    while True:
        info = project.agents.get_version(agent_name=AGENT_NAME, agent_version=version)
        status = info["status"]
        print(f"  status={status}")
        if status == "active":
            break
        if status == "failed":
            err = info.get("error") or {}
            print(f"FAILED: {err}", file=sys.stderr)
            return 1
        if time.time() > deadline:
            print("Timed out waiting for active.", file=sys.stderr)
            return 2
        time.sleep(5)

    print()
    print("Agent is active.")
    print(
        "Responses endpoint (Foundry-gated):\n"
        f"  {PROJECT_ENDPOINT}/agents/{AGENT_NAME}/endpoint/protocols/openai/responses?api-version=v1"
    )
    print(
        "\nInvocations URL pattern (per Foundry docs):\n"
        f"  {PROJECT_ENDPOINT}/agents/{AGENT_NAME}/endpoint/protocols/invocations?api-version=v1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
