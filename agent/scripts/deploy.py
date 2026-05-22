"""Deploy the charter-agent hosted agent to Foundry.

Builds nothing — assumes the image is already pushed to ACR. Calls
project.agents.create_version(...) and polls until active.
"""

from __future__ import annotations

import os
import sys
import time

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentProtocol,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)
from azure.identity import DefaultAzureCredential

PROJECT_ENDPOINT = os.environ.get(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://ocvp-agent-svc-resource.services.ai.azure.com/api/projects/ocvp-agent-svc",
)
AGENT_NAME = os.environ.get("AGENT_NAME", "charter-agent")
IMAGE = os.environ.get(
    "AGENT_IMAGE", "pcdotaiagentd10b5a.azurecr.io/charter-agent:v1"
)
MODEL_DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
TOOLBOX_NAME = os.environ.get("TOOLBOX_NAME", "Charter-Agent-Tools")


def main() -> int:
    print(f"Project: {PROJECT_ENDPOINT}")
    print(f"Agent:   {AGENT_NAME}")
    print(f"Image:   {IMAGE}")
    print(f"Model:   {MODEL_DEPLOYMENT}")
    print(f"Toolbox: {TOOLBOX_NAME}")
    print()

    credential = DefaultAzureCredential()
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
        image=IMAGE,
        environment_variables={
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": MODEL_DEPLOYMENT,
            "TOOLBOX_NAME": TOOLBOX_NAME,
        },
    )

    print("Creating agent version...")
    agent = project.agents.create_version(agent_name=AGENT_NAME, definition=definition)
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
