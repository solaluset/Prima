import discord
from discord import utils
from discord.ext import commands

from modules import embed
from modules.i18n import t
from modules.converters import SmartUserConverter


@commands.command(usage="user.usage")
async def user(ctx, *, user: SmartUserConverter = None):
    "user.help"
    user = user or ctx.author
    res = embed.Embed(
        ctx,
        title=utils.escape_markdown(str(user)),
        description=t("user.bot" if user.bot else "user.user", ctx.language),
    )
    res.set_thumbnail(url=user.display_avatar)
    res.add_field(name=t("user.id", ctx.language), value=user.id, inline=False)
    res.add_field(
        name=t("user.created", ctx.language),
        value=t(
            "user.created_ago",
            ctx.language,
            time=f"<t:{int(user.created_at.timestamp())}:F>",
            count=(utils.utcnow() - user.created_at).days,
        ),
        inline=False,
    )
    if isinstance(user, discord.Member):
        res.add_field(
            name=t("user.joined", ctx.language),
            value=t(
                "user.joined_ago",
                ctx.language,
                time=f"<t:{int(user.joined_at.timestamp())}:F>",
                count=(utils.utcnow() - user.joined_at).days,
            ),
            inline=False,
        )
    if user.primary_guild.tag is not None:
        res.add_field(
            name=t("user.guild_tag", ctx.language), value=user.primary_guild.tag
        )
    await res.send()


@commands.command(usage="avatar.usage")
async def avatar(ctx, *, user: SmartUserConverter = None):
    "avatar.help"
    user = user or ctx.author
    await ctx.send(user.display_avatar)


@commands.command(usage="banner.usage")
async def banner(ctx, *, user: SmartUserConverter = None):
    "banner.help"
    user = user or ctx.author
    if not user.accent_color and not user.banner:
        user = await ctx.bot.fetch_user(user.id)
    await ctx.send(
        (
            t(
                "banner.banner",
                ctx.language,
                user=utils.escape_markdown(str(user)),
                url=user.banner.with_size(4096).url,
            )
            if user.banner
            else t(
                "banner.absent",
                ctx.language,
                user=user,
            )
        )
        + "\n"
        + t(
            "banner.profile_color",
            ctx.language,
            count=bool(user.accent_color),
            color=user.accent_color,
        )
    )


async def setup(bot):
    bot.add_command(user)
    bot.add_command(avatar)
    bot.add_command(banner)
