import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents.crypto import Identity
from uagents.resolver import RulesBasedResolver
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

from agents.routing import submit_url


def _chat(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=text)],
    )


def _extract_text(msg: ChatMessage) -> str:
    return " ".join(
        c.text for c in msg.content if isinstance(c, TextContent)
    ).strip()


async def main():
    # Ensure an event loop exists for Python 3.14+
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    gate_seed = os.environ.get("GATE_AGENT_SEED", "gate-agent-seed-social-army-v1")
    gate_address = os.environ.get("GATE_AGENT_ADDRESS") or Identity.from_seed(gate_seed, 0).address

    sim_seed = os.environ.get("SIM_AGENT_SEED", "local-sim-seed-v1")
    sim_port = int(os.environ.get("SIM_AGENT_PORT", "8010"))
    _sim_submit = submit_url("SIM_SUBMIT_URL", sim_port)
    sim = Agent(
        name="local-sim-client",
        seed=sim_seed,
        mailbox=False,
        port=sim_port,
        endpoint=_sim_submit,
        resolve=RulesBasedResolver(
            {
                gate_address: submit_url("GATE_SUBMIT_URL", int(os.environ.get("GATE_AGENT_PORT", "8001"))),
                os.environ.get("ORCHESTRATOR_AGENT_ADDRESS", ""): submit_url(
                    "ORCHESTRATOR_SUBMIT_URL", int(os.environ.get("ORCHESTRATOR_AGENT_PORT", "8002"))
                ),
                os.environ.get("CONTENT_AGENT_ADDRESS", ""): submit_url(
                    "CONTENT_SUBMIT_URL", int(os.environ.get("CONTENT_AGENT_PORT", "8003"))
                ),
                os.environ.get("YOUTUBE_AGENT_ADDRESS", ""): submit_url(
                    "YOUTUBE_SUBMIT_URL", int(os.environ.get("YOUTUBE_AGENT_PORT", "8004"))
                ),
                os.environ.get("LINKEDIN_AGENT_ADDRESS", ""): submit_url(
                    "LINKEDIN_SUBMIT_URL", int(os.environ.get("LINKEDIN_AGENT_PORT", "8005"))
                ),
                os.environ.get("SIM_AGENT_ADDRESS", ""): _sim_submit,
            }
        ),
    )

    done = asyncio.Event()

    chat = Protocol(spec=chat_protocol_spec)

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(
            timestamp=datetime.now(timezone.utc),
            acknowledged_msg_id=msg.msg_id,
        ))
        text = _extract_text(msg)
        safe = text.encode("ascii", "backslashreplace").decode("ascii")
        ctx.logger.info(f"[sim] <- {safe}")
        if re.search(r"pipeline complete", text.lower()):
            done.set()

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        return

    sim.include(chat, publish_manifest=True)

    async def drive_conversation(ctx: Context):
        await asyncio.sleep(2)
        await ctx.send(gate_address, _chat("start"))
        await asyncio.sleep(2)
        await ctx.send(
            gate_address,
            _chat(
                "https://drive.google.com/file/d/SIM_VIDEO_ID/view?usp=sharing\n"
                "https://drive.google.com/file/d/SIM_SCRIPT_ID/view?usp=sharing"
            ),
        )
        await asyncio.sleep(2)
        await ctx.send(gate_address, _chat("mock"))
        await asyncio.sleep(2)
        await ctx.send(gate_address, _chat("mock"))
        await asyncio.sleep(2)
        await ctx.send(gate_address, _chat("go"))

    @sim.on_event("startup")
    async def startup(ctx: Context):
        ctx.logger.info(f"[sim] Gate address: {gate_address}")
        asyncio.create_task(drive_conversation(ctx))

    # Run until "done" or timeout
    async def runner():
        await asyncio.wait_for(done.wait(), timeout=180)

    # Start agent (non-blocking) then await
    task = asyncio.create_task(sim.run_async())
    try:
        await runner()
    finally:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())

