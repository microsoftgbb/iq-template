"""Scenario assistant: a Foundry hosted agent over the project's Fabric IQ and Foundry IQ toolbox.

The container serves the Responses protocol on port 8088. Foundry injects the project
endpoint; the deployment runner sets the model deployment and the toolbox endpoint.
The toolbox receives the platform call-id, so Fabric IQ queries run as the calling user.
"""

import asyncio
from functools import partial
import os
from pathlib import Path
import re

from agent_framework import (Agent, AgentContext, AgentMiddleware, AgentResponse, AgentResponseUpdate,
                             Content, Message, ResponseStream)
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import FoundryToolbox, ResponsesHostServer
from azure.ai.agentserver.core import configure_observability
from azure.identity import DefaultAzureCredential

PROOF_PREFIX = "AGENT_PROOF|"
# A readiness challenge carries a fresh UUID chosen by the caller.
PROOF = re.compile(r"connection proof [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
INSTRUCTIONS = Path(__file__).with_name("instructions.md").read_text(encoding="utf-8")


def proof_reply(messages):
    """The exact reply for a readiness challenge in the latest user message, otherwise None."""
    for message in reversed(messages):
        if message.role == "user":
            text = (message.text or "").strip()
            return PROOF_PREFIX + text if PROOF.fullmatch(text) else None
    return None


class ConnectionProof(AgentMiddleware):
    """Echo a fresh challenge without the model or tools, proving routing and caller access only."""

    async def process(self, context: AgentContext, call_next) -> None:
        reply = proof_reply(context.messages)
        if reply is None:
            await call_next()
            return
        if not context.stream:
            context.result = AgentResponse(messages=[Message("assistant", [reply])])
            return

        async def updates():
            yield AgentResponseUpdate(contents=[Content.from_text(reply)], role="assistant")

        context.result = ResponseStream(updates(), finalizer=AgentResponse.from_updates)


def create_agent(credential):
    return Agent(
        client=FoundryChatClient(
            project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
            credential=credential,
        ),
        name="scenario-assistant",
        instructions=INSTRUCTIONS,
        # TOOLBOX_ENDPOINT selects the project toolbox that the deployment runner publishes. Fabric IQ returns
        # its answer as content and only citations as structuredContent, so the model must see both.
        tools=FoundryToolbox(credential, tool_result_content="both"),
        middleware=[ConnectionProof()],
        # The hosting platform keeps conversation history.
        default_options={"store": False},
    )


async def main():
    # The runtime exports traces to the project's Application Insights. The platform's Agent 365 tracing would
    # also load OpenAI Agents SDK instrumentation, which this agent doesn't use; skipping it avoids a startup error.
    observability = partial(configure_observability, instrumentation_options={"openai_agents": {"enabled": False}})
    await ResponsesHostServer(create_agent(DefaultAzureCredential()), configure_observability=observability).run_async()


if __name__ == "__main__":
    asyncio.run(main())
