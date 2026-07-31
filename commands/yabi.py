# Modified version of
# https://github.com/engineer-man/piston-bot/blob/04305f142abbff0733fec02844773158c153c2be/src/cogs/run.py

"""
MIT License

Copyright (c) 2021 Brian Seymour and EMKC Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import os
import re
import asyncio
from traceback import print_exc
from dataclasses import dataclass

from discord import Attachment, Message, NotFound as MessageNotFound
from discord.ext import commands

from modules.i18n import t
from modules.prima import PrimaBot

MAX_MESSAGE_LENGTH = 2000
MAX_FILE_SIZE = 65535
REPLY_TTL = 5 * 60


@dataclass
class ExecutionReply:
    message: Message
    remove_task: asyncio.Task


@dataclass
class Language:
    name: str
    version: str


@dataclass
class ExecutionParams:
    language: str
    output_syntax: str
    source: str
    args: list[str]
    stdin: str


class ExecutionException(Exception):
    pass


class FileException(ExecutionException):
    def __init__(self, file: Attachment):
        super().__init__()
        self.file = file


class FileTooBigException(FileException):
    pass


class FileDecodeException(FileException):
    pass


class APICallException(ExecutionException):
    pass


class CodeExecution(commands.Cog):
    def __init__(self, bot: PrimaBot):
        self.bot = bot
        self.endpoint = bot.config["piston"]["endpoint"]
        self.token = bot.config["piston"]["token"]
        # Map request message id to reply
        self.processed_messages: dict[int, ExecutionReply] = {}
        # Store the supported languages and aliases
        self.languages: dict[str, Language] = {}

    async def cog_load(self) -> None:
        async with self.bot.session.get(self.endpoint + "/runtimes") as response:
            runtimes = await response.json()
        for runtime in runtimes:
            language = Language(runtime["language"], runtime["version"])
            self.languages[language.name] = language
            for alias in runtime["aliases"]:
                self.languages[alias] = language

    async def parse_execution_params(
        self, content: str, file: Attachment | None
    ) -> ExecutionParams:
        if file is None:
            before_source, _, content = content.partition("```")
            source, _, stdin = content.partition("```")
            extension, _, source = source.partition("\n")
        else:
            if file.size > MAX_FILE_SIZE:
                raise FileTooBigException(file)

            extension = os.path.splitext(file.filename)[1].lstrip(".")

            source = await file.read()
            try:
                source = source.decode("utf-8")
            except UnicodeDecodeError as e:
                raise FileDecodeException(file) from e

            before_source, _, stdin = content.partition("\n\n")

        first_line, _, args = before_source.partition("\n")
        language, _, output_syntax = first_line.partition("->")

        language = language.strip()
        output_syntax = output_syntax.strip()
        extension = extension.strip()
        args = args.splitlines()
        stdin = stdin.lstrip()

        return ExecutionParams(language or extension, output_syntax, source, args, stdin)

    async def call_api(self, params: ExecutionParams) -> dict:
        language = self.languages[params.language]

        headers = {"Authorization": self.token}
        data = {
            "language": language.name,
            "version": language.version,
            # TODO: main.by is simple workaround for YABI
            # (extension is important)
            # remake this if needed
            "files": [{"name": "main.by", "content": params.source}],
            "args": params.args,
            "stdin": params.stdin,
        }
        async with self.bot.session.post(
            self.endpoint + "/execute", headers=headers, json=data
        ) as response:
            try:
                response.raise_for_status()
                return await response.json()
            except Exception as e:
                raise APICallException(await response.text()) from e

    def format_output(
        self, api_response: dict, output_syntax: str, user_language: str
    ) -> str:
        compile_stderr = (
            api_response["compile"]["stderr"] if "compile" in api_response else ""
        )
        run = api_response["run"]

        language_info = f"{api_response['language']}({api_response['version']})"

        if compile_stderr:
            introduction = "code_execution.results.compile_error"
            compile_stderr += "\n"
        elif not run["stdout"] and run["stderr"]:
            introduction = "code_execution.results.stderr_only"
        elif run["output"]:
            introduction = "code_execution.results.output"
        else:
            introduction = "code_execution.results.no_output"
        introduction = t(introduction, user_language, language_info=language_info)

        if msg := run["message"]:
            introduction = f"{introduction}\n{msg}"

        output = f"{compile_stderr}{run['output']}"

        if not output:
            return introduction

        # Limit output to 30 lines maximum
        output = "\n".join(output.splitlines()[:30])

        # Remove null bytes
        output = output.replace("\0", "")

        # Prevent code block escaping by adding zero width spaces to backticks
        output = output.replace("`", "`\u200b")

        # Truncate output to be below 2000 char discord limit
        END = "\n```"
        TRUNCATED = " [...]"
        result = f"{introduction}\n```{output_syntax}\n{output}"

        if len(result) + len(END) > MAX_MESSAGE_LENGTH:
            result = result[: MAX_MESSAGE_LENGTH - len(TRUNCATED) - len(END)] + TRUNCATED

        return result + END

    async def expire_reply(self, message_id: int) -> None:
        await asyncio.sleep(REPLY_TTL)
        del self.processed_messages[message_id]

    async def _strip_command(self, message: Message) -> str:
        content = message.content
        prefixes = await self.bot.get_prefix(message)
        if isinstance(prefixes, str):
            prefixes = [prefixes]
        for prefix in prefixes:
            if content.startswith(prefix):
                content = content.removeprefix(prefix)
                break
        else:
            return content

        # remove command
        space = re.search(r"\s+", content)
        if not space:
            return ""
        _, space, content = content.partition(space.group())
        if "\n" in space:
            content = "\n" + content
        return content

    async def process_message(self, message: Message) -> str:
        source = await self._strip_command(message)
        file = message.attachments[0] if message.attachments else None
        user_language = await self.bot.get_language(message.guild)

        try:
            params = await self.parse_execution_params(source, file)
        except FileTooBigException as e:
            return t(
                "code_execution.errors.file_too_big",
                user_language,
                size=e.file.size,
                max_size=MAX_FILE_SIZE,
            )
        except FileDecodeException:
            return t("code_execution.errors.file_decode_failed", user_language)

        if not params.source:
            if file:
                return t("code_execution.errors.file_empty", user_language)
            else:
                return t("code_execution.errors.missing_codeblock", user_language)
        if not params.language:
            params.language = "yabi"
        if params.language not in self.languages:
            return t(
                "code_execution.errors.language_not_supported",
                user_language,
                language=params.language,
            )

        try:
            response = await self.call_api(params)
        except Exception:
            print_exc()
            return t("code_execution.errors.api_error", user_language)

        return self.format_output(response, params.output_syntax, user_language)

    @commands.command(aliases=("bython", "by"), usage="yabi.usage")
    async def yabi(self, ctx, *, source: str = ""):
        "yabi.help"
        async with ctx.typing():
            msg = await ctx.reply(await self.process_message(ctx.message))
        self.processed_messages[ctx.message.id] = ExecutionReply(
            msg, asyncio.create_task(self.expire_reply(ctx.message.id))
        )

    @commands.Cog.listener()
    async def on_message_delete(self, message: Message) -> None:
        reply = self.processed_messages.pop(message.id, None)
        if reply is None:
            return
        reply.remove_task.cancel()
        try:
            await reply.message.delete()
        except MessageNotFound:
            pass

    @commands.Cog.listener()
    async def on_message_edit(self, before: Message, after: Message) -> None:
        reply = self.processed_messages.get(before.id)
        if reply is None:
            return
        reply.remove_task.cancel()
        new_content = await self.process_message(after)
        try:
            await reply.message.edit(content=new_content)
        except MessageNotFound:
            del self.processed_messages[before.id]
        else:
            reply.remove_task = asyncio.create_task(self.expire_reply(after.id))


async def setup(bot: PrimaBot) -> None:
    await bot.add_cog(CodeExecution(bot))
