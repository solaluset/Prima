from io import BytesIO

from discord import Attachment, File
from discord.ext import commands

from modules import ffmpeg
from modules.i18n import t

# max size 10 MiB
MAX_FILE_SIZE = 10 * 1024**2


@commands.hybrid_command()
async def avif(ctx, file: Attachment):
    "avif.help"

    if file.size > MAX_FILE_SIZE:
        return await ctx.send(t("avif.file-too-big", ctx.language))

    msg = await ctx.send(t("avif.starting", ctx.language))

    async def progress_callback(duration: float | None, processed: float):
        if not duration and msg.edited_at:
            return
        await msg.edit(
            content=t(
                "avif.progress",
                ctx.language,
                percent=(
                    round(processed / duration * 100)
                    if duration
                    else t("avif.unknown-duration", ctx.language)
                ),
            )
        )

    try:
        data = await ffmpeg.convert_to_avif(await file.read(), progress_callback)
    except ffmpeg.ConversionError:
        return await msg.edit(content=t("avif.failed", ctx.language))
    await msg.edit(
        content=t("avif.done", ctx.language),
        attachments=[File(BytesIO(data), "result.avif")],
    )


async def setup(bot):
    bot.add_command(avif)
