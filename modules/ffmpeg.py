import re
import shlex
import shutil
import asyncio
import subprocess
from pathlib import Path
from functools import partial
from typing import Callable, Awaitable

import aiofiles

from modules.timeparse import timeparse

HAS_NICE = shutil.which("nice") is not None

CHECK_ALPHA_COMMAND = shlex.split(r"""
ffmpeg
-i inp
-vf alphaextract
-frames 1
-f null
-
""")

TO_AVIF_COMMAND = shlex.split(r"""
ffmpeg -hide_banner -stats -stats_period 5
-i inp
-filter_complex 'crop=iw-mod(iw\,2):ih-mod(ih\,2)
                ,scale=out_range=pc,format=yuva420p
                ,split [video][v2]
                ; [v2] alphaextract [alpha]
                '
-map '[video]' -map '[alpha]'
-fpsmax 60
-crf 32
-c:v:0 libsvtav1 -preset 4 -svtav1-params fast-decode=1
-c:v:1 libaom-av1
-cpu-used 4 -tiles 2x2
out.avif
""")

TO_AVIF_COMMAND_NOALPHA = shlex.split(r"""
ffmpeg -hide_banner -stats -stats_period 5
-i inp
-filter_complex 'crop=iw-mod(iw\,2):ih-mod(ih\,2)
                ,scale=out_range=pc,format=yuv420p [video]
                '
-map '[video]'
-fpsmax 60
-crf 32
-c:v:0 libsvtav1 -preset 4 -svtav1-params fast-decode=1
-cpu-used 4 -tiles 2x2
out.avif
""")

TIME_PATTERN = r"(?P<time>\d{2,}:\d\d:\d\d\.\d\d)"
DURATION_RE = re.compile(rf"(?i)Duration:\s*({TIME_PATTERN}|N/A)")
TIME_RE = re.compile(rf"(?i)time\s*=\s*{TIME_PATTERN}")


class ConversionError(Exception):
    pass


def _prepare_command(command: list[str]) -> list[str]:
    command = command.copy()

    if HAS_NICE:
        command.insert(0, "nice")

    return command


async def convert_to_avif(
    data: bytes,
    progress_callback: Callable[[float | None, float], Awaitable] | None = None,
) -> bytes:
    acall = partial(asyncio.get_running_loop().run_in_executor, None)

    async with aiofiles.tempfile.TemporaryDirectory() as tmp:
        async with aiofiles.open(Path(tmp) / "inp", "wb") as in_file:
            await in_file.write(data)

        process = subprocess.Popen(
            _prepare_command(CHECK_ALPHA_COMMAND),
            cwd=tmp,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if await acall(process.wait) != 0:
            if "Requested planes not available." not in await acall(process.stderr.read):
                raise ConversionError()
            command = TO_AVIF_COMMAND_NOALPHA
        else:
            command = TO_AVIF_COMMAND

        process = subprocess.Popen(
            _prepare_command(command),
            cwd=tmp,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        async def areadline():
            return (await acall(process.stdout.readline)).decode()

        duration = None
        while line := await areadline():
            if match := DURATION_RE.search(line):
                if (time := match.group("time")) is not None:
                    duration = timeparse(time)
                break

        while line := await areadline():
            if progress_callback and (match := TIME_RE.search(line)):
                await progress_callback(duration, timeparse(match.group("time")))

        if process.wait(0) != 0:
            raise ConversionError()

        async with aiofiles.open(Path(tmp) / "out.avif", "rb") as out_file:
            return await out_file.read()
