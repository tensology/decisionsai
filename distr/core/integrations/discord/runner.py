"""Optional Discord bot loop (TASK 16) — runs in a daemon thread when a bot token is resolved (env or Advanced settings)."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_ENV_TOKEN = "DECISIONSAI_DISCORD_BOT_TOKEN"

# ``on_ready`` may fire more than once on reconnect; start one outbound worker only.
_discord_outbound_worker_started = False


def discord_bot_token_from_env() -> str | None:
    """Prefer process env, then Advanced settings ``connected_accounts`` (``discord_bot``)."""
    from distr.core.integrations.token_resolve import resolve_discord_bot_token

    return resolve_discord_bot_token()


def start_discord_bot_background() -> bool:
    """Start ``discord.py`` client on a daemon thread if token + library are available.

    Returns True only when a thread was actually started.
    """
    token = discord_bot_token_from_env()
    if not token:
        return False
    try:
        import discord  # noqa: F401
    except ImportError:
        logger.warning(
            "%s is set but discord.py is not installed — skipping Discord bot",
            _ENV_TOKEN,
        )
        return False

    t = threading.Thread(
        target=_discord_sync_entry,
        args=(token,),
        name="decisionsai-discord-bot",
        daemon=True,
    )
    t.start()
    logger.info("Discord bot thread started (%s)", _ENV_TOKEN)
    return True


def _discord_sync_entry(token: str) -> None:
    import asyncio

    try:
        asyncio.run(_run_discord_client(token))
    except Exception:
        logger.exception("Discord bot crashed")


async def _discord_deliver_outbound(client: object, payload: dict) -> None:
    """Send text to a channel by snowflake id (runner-thread asyncio context)."""
    import discord

    cid_raw = str(payload.get("channel_id") or "").strip()
    text = (payload.get("text") or "").strip()
    if not cid_raw or not text:
        return
    try:
        channel_id = int(cid_raw)
    except ValueError:
        logger.warning("Discord outbound: invalid channel_id %r", cid_raw)
        return

    ch = client.get_channel(channel_id)
    if ch is None:
        ch = await client.fetch_channel(channel_id)
    if not isinstance(ch, discord.abc.Messageable):
        logger.warning("Discord outbound: channel %s not messageable", channel_id)
        return
    await ch.send(text[:2000])


async def _run_discord_client(token: str) -> None:
    import asyncio

    import discord

    from distr.core.integrations.discord.bridge import route_discord_inbound_to_agent
    from distr.core.integrations.outbound_state import get_discord_outbound_queue
    from distr.core.integrations.outbound_worker import IntegrationOutboundWorker

    global _discord_outbound_worker_started

    intents = discord.Intents.default()
    intents.message_content = True  # requires privileged intent in Developer Portal

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:  # noqa: ANN001 — discord.py callback
        logger.info("Discord logged in as %s", getattr(client.user, "name", "?"))
        if _discord_outbound_worker_started:
            return
        _discord_outbound_worker_started = True
        loop = asyncio.get_running_loop()

        def deliver(payload: dict) -> None:
            fut = asyncio.run_coroutine_threadsafe(_discord_deliver_outbound(client, payload), loop)
            fut.result(timeout=120)

        worker = IntegrationOutboundWorker(
            get_discord_outbound_queue(),
            deliver,
            thread_name="discord-outbound",
        )
        worker.start_daemon()

    @client.event
    async def on_message(message) -> None:  # noqa: ANN001
        if message.author.bot:
            return
        text = (message.content or "").strip()
        if not text:
            return
        try:
            route_discord_inbound_to_agent(
                channel_id=str(message.channel.id),
                author_id=str(message.author.id),
                content=text,
                attachment_paths=[],
                raw={"message_id": str(message.id)},
            )
        except Exception:
            logger.exception("Discord → MessageBus routing failed")

    async with client:
        await client.start(token)
