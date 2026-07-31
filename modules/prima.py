import os
import ast
import json
import asyncio
import logging as log
from pydoc import render_doc
from typing import Iterable
from threading import Thread
from types import FunctionType
from code import InteractiveConsole
from traceback import format_exception
from concurrent.futures import Future
from inspect import isawaitable, iscoroutine, isfunction

import ring
import aiohttp
import discord
from discord import Guild, Message
from discord.ext import commands
from sqlalchemy import ARRAY, JSON, Column, String
from sqlalchemy.ext.asyncio import create_async_engine

from . import regexps
from .db import WrappedTable, metadata
from .i18n import Translator, t
from .converters import RoleTooHighForUser, RoleTooHighForBot, NotSameGuild

CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "token": "",
    "db_url": "",
    "activity": {"name": "", "type": 0},
    "status": "online",
    "owner_ids": [],
    "test_channel_id": 0,
    "prefix": ["p1", "P1"],
    "default_language": "en",
    "support_invite": "",
    "piston": {
        "endpoint": "",
        "token": "",
    },
}


MAX_PREFIX_LEN = 100
guilds_data = WrappedTable(
    "guilds",
    None,
    Column("guild_id", String(25), primary_key=True),
    Column("prefixes", ARRAY(String(MAX_PREFIX_LEN))),
    Column("aliases", JSON),
    Column("autorole", String(25)),
    Column("language", String(10)),
)


ADD_ALIASES = ("create", "new")
REMOVE_ALIASES = ("rem", "delete", "del")


def _translate_permissions(permissions: Iterable[str], language: str) -> str:
    return ", ".join(t(f"permissions.{p}", language) for p in permissions)


ERRORS = {
    commands.CommandNotFound: None,
    commands.UserInputError: lambda ctx, error: ctx.send_help(ctx.command),
    commands.NoPrivateMessage: "errors.guild_only",
    commands.NotOwner: "errors.owner_only",
    commands.UserNotFound: "errors.not_found.user",
    commands.RoleNotFound: "errors.not_found.role",
    commands.ChannelNotFound: "errors.not_found.channel",
    commands.EmojiNotFound: "errors.not_found.emoji",
    commands.BadArgument: "errors.bad_argument",
    commands.BadUnionArgument: "errors.bad_argument",
    commands.MissingPermissions: lambda ctx, error: t(
        "errors.user_missing_permissions",
        ctx.language,
        permissions=_translate_permissions(error.missing_permissions, ctx.language),
    ),
    commands.BotMissingPermissions: lambda ctx, error: t(
        "errors.bot_missing_permissions",
        ctx.language,
        permissions=_translate_permissions(error.missing_permissions, ctx.language),
    ),
    RoleTooHighForUser: "errors.user_needs_higher_role",
    RoleTooHighForBot: "errors.bot_needs_higher_role",
    NotSameGuild: "errors.not_same_guild",
    commands.ChannelNotReadable: lambda ctx, error: t(
        "errors.channel_not_readable", ctx.language, channel=error.argument.mention
    ),
    commands.CheckFailure: None,
}


HTTP_INVALID_FORM_BODY = 50035
HTTP_UNKNOWN_EMOJI = 10014
HTTP_ERRORS = {
    HTTP_INVALID_FORM_BODY: "errors.message_failed",
    HTTP_UNKNOWN_EMOJI: "errors.unknown_emoji",
    30010: "errors.reaction_limit_reached",
    50013: "errors.bot_forbidden",
}


def unknown_error(ctx, error):
    log.error("".join(format_exception(error)).rstrip())
    return t("errors.unknown", ctx.language)


class PrimaBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        self.test_mode = kwargs.pop("test_mode", False)
        if self.test_mode:
            log.info("Running in test mode.")
        self.load_config()
        self._extensions = []

        self.test_channel_id = self.config["test_channel_id"]
        self.sql = (
            create_async_engine(self.config["db_url"]) if self.config["db_url"] else None
        )
        self.guilds_data = guilds_data
        self.guilds_data.engine = self.sql

        try:
            activity = discord.Activity(
                name=self.config["activity"]["name"],
                type=discord.ActivityType(self.config["activity"]["type"]),
            )
            status = discord.Status(self.config["status"])
        except Exception as e:
            log.error("Error while trying to load status from config: %s", e)
            activity = None
            status = None

        async def prefixes(bot, message):
            return commands.when_mentioned_or(*(await bot.get_prefixes(message.guild)))(
                bot, message
            )

        super().__init__(
            prefixes,
            *args,
            activity=activity,
            status=status,
            owner_ids=self.config["owner_ids"],
            **kwargs,
        )

    def __ring_key__(self):
        return f"PrimaBot({self.user.id})"

    async def on_ready(self):
        log.info("The bot is, like, ready")
        if os.path.isfile("restart"):
            with open("restart") as f:
                await self._notify_restart(f.read())
            os.remove("restart")

    async def _notify_restart(self, content: str):
        if not content:
            return
        try:
            id = int(content)
            target = self.get_channel(id) or self.get_user(id)
            await target.send(
                t(
                    "restart.restarted",
                    await self.get_language(getattr(target, "guild", None)),
                )
            )
        except Exception as e:
            log.error("Failed to send restart message: %s", e)

    async def on_message(self, message: Message):
        if self.test_mode != (message.channel.id == self.test_channel_id):
            return

        if message.author.bot:
            return

        ctx = await self.get_context(message, cls=PrimaContext)
        perms = ctx.channel.permissions_for(ctx.me)
        if perms.send_messages:
            add_zipper = False
            await self.invoke(ctx)
        else:
            add_zipper = ctx.command is not None

        pinged = regexps.PING.fullmatch(message.content)
        if pinged and pinged.group("id") == str(self.user.id):
            if perms.send_messages:
                await message.channel.send(
                    await self.get_prefixes_string(message.guild)
                    + "\n"
                    + t(
                        "ping_additional_info",
                        ctx.language,
                    )
                )
            else:
                add_zipper = True

        if add_zipper and perms.add_reactions:
            await message.add_reaction("🤐")

    async def on_command_error(self, ctx, error):
        error = getattr(error, "original", error)
        if hasattr(error, "code") and error.code in HTTP_ERRORS:
            result = HTTP_ERRORS[error.code]
        else:
            e_type = type(error)
            while e_type and e_type not in ERRORS:
                e_type = e_type.__base__
            result = ERRORS.get(e_type, unknown_error)
        if isfunction(result):
            result = result(ctx, error)
            if isawaitable(result):
                result = await result
        elif isinstance(result, str):
            result = t(result, ctx.language)
        if result:
            await ctx.send(result)

    def run(self):
        if token := self.config["token"]:
            return super().run(token)
        log.fatal("Please enter the token in %s", CONFIG_FILE)

    async def setup_hook(self):
        await super().setup_hook()

        self.session = aiohttp.ClientSession()

        count = len(
            await asyncio.gather(
                *(
                    self.load_extension("." + name, package=package)
                    for name, package in self._extensions
                )
            )
        )
        log.info(f"{count} extensions loaded.")

        async with self.sql.begin() as conn:
            await conn.run_sync(metadata.create_all)

        await self.tree.set_translator(Translator())
        await self.tree.sync()

    async def start(self, token, **kwargs):
        if self.test_mode:
            start_console(self)

        await super().start(token, **kwargs)

    async def close(self):
        await self.session.close()
        await super().close()

    async def get_context(self, message: Message, *, cls=None):
        if cls is None:
            cls = PrimaContext
        ctx = await super().get_context(message, cls=cls)
        ctx.language = await self.get_language(message.guild)
        return ctx

    async def get_application_context(self, interaction, *, cls=None):
        kwargs = {}
        if cls is not None:
            kwargs["cls"] = cls
        ctx = await super().get_application_context(interaction, **kwargs)
        ctx.language = await self.get_language(interaction.guild)
        return ctx

    async def get_guild_property(self, guild: Guild, prop: str):
        res = await self.guilds_data.select(prop, guild_id=str(guild.id))
        if res:
            return res[0][0]
        return None

    @ring.lru(force_asyncio=True)
    async def get_prefixes(self, guild: Guild | None, use_default=True):
        if guild:
            return (await self.get_guild_property(guild, "prefixes")) or (
                self.config["prefix"] if use_default else []
            )
        return self.config["prefix"]

    @ring.lru(force_asyncio=True)
    async def get_aliases(self, guild: Guild | None):
        if guild:
            return (await self.get_guild_property(guild, "aliases")) or {}
        return {}

    async def get_prefixes_string(self, guild: Guild | None):
        return t(
            "prefixes",
            await self.get_language(guild),
            count=bool(guild),
            prefixes="\n".join(await self.get_prefixes(guild)),
        )

    @ring.lru(force_asyncio=True)
    async def get_language(self, guild: Guild | None):
        if guild:
            return (await self.get_guild_property(guild, "language")) or self.config[
                "default_language"
            ]
        return self.config["default_language"]

    def load_config(self):
        log.info("Loading configuration...")
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                self.config = json.load(f)
        else:
            self.config = DEFAULT_CONFIG
            self.save_config()

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=4)

    def add_extensions(self, *names: str, package: str):
        self._extensions.extend((name, package) for name in names)


class PrimaContext(commands.Context):
    async def send(self, content=None, **kwargs):
        if not self.channel.permissions_for(self.me).send_messages:
            # an alias can bring us here
            return
        allowed_mentions = kwargs.pop("allowed_mentions", None)
        if allowed_mentions is None:
            perms = self.channel.permissions_for(self.author).mention_everyone
            allowed_mentions = discord.AllowedMentions(
                everyone=perms,
                roles=perms or [r for r in self.message.role_mentions if r.mentionable],
                users=True,
            )
        return await super().send(content, allowed_mentions=allowed_mentions, **kwargs)


class PrimaConsole(InteractiveConsole):
    def __init__(
        self,
        bot,
        locals=None,
    ):
        super().__init__(locals)
        self.bot = bot
        self.compile.compiler.flags |= ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        # make help non-blocking
        self.locals["help"] = lambda obj: print(render_doc(obj))

    def raw_input(self, prompt=""):
        try:
            line = super().raw_input(prompt)
        except EOFError:
            line = "exit"
        if line in ("exit", "restart"):
            if line == "restart":
                open("restart", "w").close()
            self.bot.loop.create_task(self.bot.close())
            raise EOFError
        return line

    def runcode(self, code):
        func = FunctionType(code, self.locals)
        future = Future()

        def _set_result(task):
            try:
                future.set_result(task.result())
            except BaseException as e:
                future.set_exception(e)

        def callback():
            try:
                coro = func()
            except BaseException as e:
                future.set_exception(e)
                return
            if iscoroutine(coro):
                task = self.bot.loop.create_task(coro)
                task.add_done_callback(_set_result)
            else:
                future.set_result(coro)

        self.bot.loop.call_soon_threadsafe(callback)

        try:
            future.result()
        except BaseException:
            self.showtraceback()


def start_console(bot):
    try:
        import readline  # noqa: F401
    except ImportError:
        pass
    Thread(
        target=PrimaConsole(
            bot, locals={"bot": bot, "commands": commands, "discord": discord}
        ).interact,
        args=("Console activated",),
        daemon=True,
    ).start()
