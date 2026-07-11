import copy

import regex
from discord import Message
from discord.ext import commands

from modules.i18n import t
from modules.prima import ADD_ALIASES, REMOVE_ALIASES
from modules.regexps import (
    ARG,
    VAR_ARG,
    PING,
    ARGUMENT_TEMPLATE,
    SIDE_QUOTES,
)


class Aliases(commands.Cog):
    MAX_ALIASES_PER_GUILD = 20

    def __init__(self, bot):
        self.bot = bot

    @commands.group(aliases=("aliases",), invoke_without_command=True)
    @commands.guild_only()
    async def alias(self, ctx):
        "alias.help"
        aliases = await ctx.bot.get_aliases(ctx.guild)
        if aliases:
            await ctx.send(
                t(
                    "alias.list",
                    ctx.language,
                    aliases="\n".join(
                        f"{key}: {value}" for key, value in aliases.items()
                    ),
                )
            )
        else:
            await ctx.send(t("alias.no_aliases", ctx.language))

    @alias.command(usage="alias.add.usage", aliases=ADD_ALIASES)
    @commands.has_permissions(manage_guild=True)
    async def add(self, ctx, pattern: str, command: str):
        "alias.add.help"
        aliases = await ctx.bot.get_aliases(ctx.guild)
        pattern = self.uniping(pattern.lower())
        if pattern in aliases:
            await ctx.send(t("alias.add.already_exists", ctx.language, alias=pattern))
            return
        if len(aliases) >= self.MAX_ALIASES_PER_GUILD:
            await ctx.send(
                t(
                    "alias.add.limit_reached",
                    ctx.language,
                    limit=self.MAX_ALIASES_PER_GUILD,
                )
            )
            return
        args = ARG.findall(pattern)
        if any(arg.startswith("_") for arg in args):
            await ctx.send(t("alias.add.reject_underscore", ctx.language))
            return
        if len(set(args)) != len(args) or len(VAR_ARG.findall(pattern)) > 1:
            await ctx.send(t("alias.add.reject_duplicates", ctx.language))
            return
        if await self.format_message(ctx.message, command) is None:
            return
        aliases[pattern] = command
        await ctx.bot.guilds_data.upsert(
            guild_id=str(ctx.guild.id),
            aliases=aliases,
        )
        await ctx.bot.get_aliases.delete(ctx.guild)
        await ctx.send(t("alias.add.added", ctx.language, alias=pattern))

    @alias.command(usage="alias.remove.usage", aliases=REMOVE_ALIASES)
    @commands.has_permissions(manage_guild=True)
    async def remove(self, ctx, pattern: str):
        "alias.remove.help"
        aliases = await ctx.bot.get_aliases(ctx.guild)
        pattern = self.uniping(pattern.lower())
        try:
            del aliases[pattern]
        except KeyError:
            await ctx.send(t("alias.remove.not_found", ctx.language))
            return
        await ctx.bot.guilds_data.upsert(
            guild_id=str(ctx.guild.id),
            aliases=aliases,
        )
        await ctx.bot.get_aliases.delete(ctx.guild)
        await ctx.send(t("alias.remove.removed", ctx.language, alias=pattern))

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if self.bot.test_mode != (message.channel.id == self.bot.test_channel_id):
            return
        if not message.content:
            return

        for key, value in (await self.bot.get_aliases(message.guild)).items():
            alias = self.fetch_alias(message.content, key, value)
            if not alias:
                continue
            alias = await self.format_message(message, alias)
            if not alias:
                continue

            prefix = (await self.bot.get_prefixes(message.guild))[0]
            message_copy = copy.copy(message)
            for command in alias.split("\n"):
                message_copy.content = prefix + command
                await self.bot.process_commands(message_copy)

    @staticmethod
    def uniping(text: str) -> str:
        "Support deprecated <@!id> ping"
        return PING.sub(lambda x: x.group().replace("!", "").replace("@", "@!?"), text)

    @staticmethod
    def fetch_alias(text: str, pattern: str, command: str) -> str | None:
        pattern = ARG.sub(lambda g: ARGUMENT_TEMPLATE % g.group(1), pattern)
        pattern = VAR_ARG.sub("(?P<_VAR_ARG>.+?)", pattern)
        try:
            match = regex.fullmatch(pattern, text, regex.I | regex.DOTALL, timeout=1)
        except (regex.error, TimeoutError):
            return  # silent exit (should be changed?)
        if not match:
            return None
        match = match.groupdict()
        command = VAR_ARG.sub(match.get("_VAR_ARG", "$*"), command)
        command = ARG.sub(
            lambda g: (
                SIDE_QUOTES.sub("", match.get(g.group(1), g.group())).replace("\\", "")
            ),
            command,
        )
        return command

    async def format_message(self, message: Message, text: str) -> str | None:
        try:
            return text.format(message=message, user=message.author)
        except (KeyError, AttributeError) as e:
            language = await self.bot.get_language(message.guild)
            name = getattr(e, "name", e.args[0])
            await message.channel.send(t("alias.invalid_id", language, id=name))
            return None
        except IndexError:
            return "dirty workaround"


async def setup(bot):
    await bot.add_cog(Aliases(bot))
