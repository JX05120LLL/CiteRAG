"""Bounded document parser. Documents are opened only in a disposable process."""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.ingestion.storage import MAX_FILE_BYTES, validate_filename
from app.services.errors import ServiceError

PARSER_TIMEOUT_SECONDS = 20
_ERROR_MESSAGES = {
    "invalid_document": "文件损坏、格式不符或正文不可可靠读取，请转换为 UTF-8 TXT 后重试",
    "unsupported_document": "文件包含不支持的结构，请转换为普通段落或简单表格后重试",
    "empty_document": "未读取到可靠正文，扫描文件需先转换为文字资料",
    "parse_limit": "文件解析超出资源限制，请拆分或简化资料后重试",
    "parse_unavailable": "受限解析进程不可用，请检查本机环境后重试",
}


@dataclass(frozen=True)
class ParsedBlock:
    text: str
    locator: dict[str, int | str]
    start: int
    end: int


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    blocks: list[ParsedBlock]


async def parse_document(path: Path, filename: str) -> ParsedDocument:
    validate_filename(filename)
    try:
        if not 0 < path.stat().st_size <= MAX_FILE_BYTES or not path.is_file():
            raise ServiceError(422, "invalid_document", _ERROR_MESSAGES["invalid_document"])
        # No application settings or credentials are inherited by the parser.
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(Path(__file__).with_name("parser_process.py")),
            str(path.resolve()),
            Path(filename).suffix.lower(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
        )
    except OSError:
        raise ServiceError(422, "parse_unavailable", _ERROR_MESSAGES["parse_unavailable"]) from None
    try:
        output, _ = await asyncio.wait_for(process.communicate(), PARSER_TIMEOUT_SECONDS)
    except (TimeoutError, asyncio.CancelledError) as error:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.communicate()
        if isinstance(error, asyncio.CancelledError):
            raise
        raise ServiceError(422, "parse_timeout", "文件解析超时，请拆分或简化资料后重试") from None
    if process.returncode:
        raise ServiceError(422, "parse_limit", _ERROR_MESSAGES["parse_limit"])
    try:
        result = json.loads(output)
        if "error" in result:
            code = result["error"]
            if code not in _ERROR_MESSAGES:
                code = "invalid_document"
            raise ServiceError(422, code, _ERROR_MESSAGES[code])
        return ParsedDocument(result["text"], [ParsedBlock(**block) for block in result["blocks"]])
    except (ValueError, KeyError, TypeError):
        raise ServiceError(422, "invalid_document", _ERROR_MESSAGES["invalid_document"]) from None
