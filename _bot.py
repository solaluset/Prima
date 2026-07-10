import sys
import logging as log

import discord
from discord.app_commands import AppInstallationType, AppCommandContext

from modules.prima import PrimaBot
from modules.i18n import init as init_i18n

log.basicConfig(
    level=log.WARNING,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[log.FileHandler("log.txt"), log.StreamHandler()],
)

log.info("Configuring the bot...")

init_i18n()

bot = PrimaBot(
    description="bot_description",
    intents=discord.Intents.all(),
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
    allowed_installs=AppInstallationType(guild=True, user=True),
    allowed_contexts=AppCommandContext(
        guild=True, dm_channel=True, private_channel=True
    ),
    case_insensitive=True,
    test_mode="--test" in sys.argv,
)


bot.add_extensions(
    "alias",
    "autorole",
    "avif",
    "ban",
    "brainfuck",
    "calc",
    "choose",
    "clean",
    "convert",
    "developer",
    "emoji",
    "etime",
    "help",
    "invite",
    "language",
    "layout",
    "prefix",
    "react",
    "reactionrole",
    "remind",
    "say",
    "server",
    "tictac",
    "user",
    package="commands",
)


if __name__ == "__main__":
    log.info("Connecting...")
    try:
        bot.run()
    except RuntimeError as e:
        if e.args != ("Session is closed",):
            raise
    log.info("Stopped.")
