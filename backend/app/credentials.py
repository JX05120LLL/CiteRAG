"""Strict loading of local Windows DPAPI credential records."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

CLIXML_NAMESPACE = "http://schemas.microsoft.com/powershell/2004/04"
MAX_CREDENTIAL_BYTES = 1_048_576
SYSTEM_SID = "S-1-5-18"
WORKSPACE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class CredentialError(RuntimeError):
    """A deliberately sanitized credential configuration failure."""


@dataclass(frozen=True)
class PathSecurity:
    is_file: bool
    has_reparse_point: bool
    owner_sid: str
    dacl_protected: bool
    allowed_sids: frozenset[str]


@dataclass(frozen=True)
class CredentialRecord:
    schema_version: int
    provider: str
    auth_mode: str
    region: str
    workspace_id: str | None
    models: dict[str, str | int]
    username: str
    secret: SecretStr
    ciphertext_sha256: str


@dataclass(frozen=True)
class DashScopeConfig:
    region: str
    workspace_id: str
    models: dict[str, str]
    embedding_dimension: int
    api_key: SecretStr
    credential_ciphertext_sha256: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _current_user_sid() -> str:
    if os.name != "nt":
        raise CredentialError("Windows credential protection is required")
    token_query = 0x0008
    token_user = 1
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    process = kernel32.GetCurrentProcess()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, token_query, ctypes.byref(token)):
        raise CredentialError("credential path security could not be verified")
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, token_user, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token, token_user, buffer, size, ctypes.byref(size)
        ):
            raise CredentialError("credential path security could not be verified")
        sid_pointer = ctypes.c_void_p.from_buffer(buffer).value
        return _sid_to_string(sid_pointer)
    finally:
        kernel32.CloseHandle(token)


def _sid_to_string(sid_pointer: int | None) -> str:
    if not sid_pointer:
        raise CredentialError("credential path security could not be verified")
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    value = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(value)):
        raise CredentialError("credential path security could not be verified")
    try:
        return value.value
    finally:
        kernel32.LocalFree(value)


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.WORD),
    ]


def _windows_acl(path: Path) -> tuple[str, bool, frozenset[str]]:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        1,
        0x00000001 | 0x00000004,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0 or not descriptor.value or not dacl.value:
        if descriptor.value:
            kernel32.LocalFree(descriptor)
        raise CredentialError("credential path security could not be verified")
    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise CredentialError("credential path security could not be verified")
        info = _AclSizeInformation()
        if not advapi32.GetAclInformation(
            dacl, ctypes.byref(info), ctypes.sizeof(info), 2
        ):
            raise CredentialError("credential path security could not be verified")
        allowed: set[str] = set()
        for index in range(info.AceCount):
            ace = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                raise CredentialError("credential path security could not be verified")
            header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
            if header.AceType == 0:
                allowed.add(_sid_to_string(ace.value + 8))
            elif header.AceType not in {1}:
                raise CredentialError("credential path is not private")
        return _sid_to_string(owner.value), bool(control.value & 0x1000), frozenset(allowed)
    finally:
        kernel32.LocalFree(descriptor)


def _has_reparse_ancestor(path: Path) -> bool:
    current = path.absolute()
    while True:
        try:
            attributes = getattr(os.lstat(current), "st_file_attributes", 0)
            if attributes & 0x0400:
                return True
        except FileNotFoundError:
            pass
        if current.parent == current:
            return False
        current = current.parent


def _probe_path_security(path: Path) -> PathSecurity:
    owner, protected, allowed = _windows_acl(path)
    return PathSecurity(
        is_file=path.is_file(),
        has_reparse_point=_has_reparse_ancestor(path),
        owner_sid=owner,
        dacl_protected=protected,
        allowed_sids=allowed,
    )


def validate_path_security(
    path: Path, security: PathSecurity, *, current_user_sid: str
) -> None:
    permitted = {current_user_sid, SYSTEM_SID}
    if (
        not security.is_file
        or security.has_reparse_point
        or security.owner_sid != current_user_sid
        or not security.dacl_protected
        or not security.allowed_sids
        or not security.allowed_sids.issubset(permitted)
        or current_user_sid not in security.allowed_sids
    ):
        raise CredentialError("credential path is not private")


def _validate_credential_path(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    if tuple(part.lower() for part in absolute.parent.parts[-3:]) != (
        ".local",
        "runtime",
        "models",
    ):
        raise CredentialError("credential path is outside the local model directory")
    current_sid = _current_user_sid()
    validate_path_security(absolute, _probe_path_security(absolute), current_user_sid=current_sid)
    directory_security = _probe_path_security(absolute.parent)
    validate_path_security(
        absolute.parent,
        PathSecurity(
            is_file=absolute.parent.is_dir(),
            has_reparse_point=directory_security.has_reparse_point,
            owner_sid=directory_security.owner_sid,
            dacl_protected=directory_security.dacl_protected,
            allowed_sids=directory_security.allowed_sids,
        ),
        current_user_sid=current_sid,
    )


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def decrypt_dpapi(ciphertext: bytes) -> str:
    if os.name != "nt":
        raise CredentialError("Windows credential protection is required")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source_buffer = (ctypes.c_ubyte * len(ciphertext)).from_buffer_copy(ciphertext)
    source = _DataBlob(len(ciphertext), source_buffer)
    output = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise CredentialError("credential could not be decrypted for this Windows user")
    try:
        if not output.pbData or output.cbData == 0 or output.cbData % 2:
            raise CredentialError("decrypted credential is invalid")
        return ctypes.wstring_at(output.pbData, output.cbData // 2).rstrip("\x00")
    finally:
        if output.pbData:
            kernel32.LocalFree(output.pbData)


def _named_children(element: ET.Element) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    for child in element:
        name = child.attrib.get("N")
        if name is None:
            continue
        if name in result:
            raise CredentialError("invalid credential structure")
        result[name] = child
    return result


def _required_text(fields: dict[str, ET.Element], name: str, tag: str = "S") -> str:
    element = fields.get(name)
    if element is None or _local_name(element.tag) != tag or element.text is None:
        raise CredentialError("invalid credential structure")
    return element.text


def _parse_models(element: ET.Element) -> dict[str, str | int]:
    dictionary = next((item for item in element if _local_name(item.tag) == "DCT"), None)
    if dictionary is None:
        raise CredentialError("invalid credential structure")
    result: dict[str, str | int] = {}
    for entry in dictionary:
        if _local_name(entry.tag) != "En":
            raise CredentialError("invalid credential structure")
        fields = _named_children(entry)
        key = _required_text(fields, "Key")
        if key in result or set(fields) != {"Key", "Value"}:
            raise CredentialError("invalid credential structure")
        value = fields["Value"]
        if _local_name(value.tag) == "S" and value.text is not None:
            result[key] = value.text
        elif _local_name(value.tag) in {"I32", "I64"} and value.text is not None:
            try:
                result[key] = int(value.text)
            except ValueError:
                raise CredentialError("invalid credential structure") from None
        else:
            raise CredentialError("invalid credential structure")
    return result


def _parse_credential(element: ET.Element) -> tuple[str, str]:
    props = next((item for item in element if _local_name(item.tag) == "Props"), None)
    if props is None:
        raise CredentialError("invalid credential structure")
    fields = _named_children(props)
    if set(fields) != {"UserName", "Password"}:
        raise CredentialError("invalid credential structure")
    password = fields["Password"]
    if _local_name(password.tag) != "SS":
        raise CredentialError("invalid credential structure")
    return _required_text(fields, "UserName"), password.text or ""


def _parse_document(data: bytes) -> tuple[int, dict[str, ET.Element]]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise CredentialError("invalid credential structure") from None
    if _local_name(root.tag) != "Objs":
        raise CredentialError("invalid credential structure")
    objects = [item for item in root if _local_name(item.tag) == "Obj"]
    if len(objects) != 1:
        raise CredentialError("invalid credential structure")
    containers = [
        item for item in objects[0] if _local_name(item.tag) in {"Props", "MS"}
    ]
    if len(containers) != 1:
        raise CredentialError("invalid credential structure")
    fields = _named_children(containers[0])
    raw_schema = _required_text(fields, "SchemaVersion", "I32")
    try:
        schema = int(raw_schema)
    except ValueError:
        raise CredentialError("invalid credential structure") from None
    return schema, fields


def _allowed_top_fields(schema: int) -> set[str]:
    base = {"SchemaVersion", "Provider", "Region", "Models", "Credential", "Verified"}
    if schema >= 2:
        base.add("AuthMode")
    if schema >= 3:
        base.add("WorkspaceId")
    return base


def _allowed_models(provider: str, schema: int) -> set[str]:
    if provider == "dashscope":
        base = {"answer", "engine", "summary", "embedding"}
        if schema >= 3:
            return base | {"embedding_dimension", "rerank"}
        return base | {"embedding_dimension_target"}
    if provider == "volcengine":
        return {"asr"}
    if provider == "minimax":
        return {"tts"}
    raise CredentialError("unsupported credential provider")


def load_credential(path: Path, expected_provider: str) -> CredentialRecord:
    path = Path(path)
    _validate_credential_path(path)
    try:
        initial = os.stat(path, follow_symlinks=False)
        if initial.st_size < 1 or initial.st_size > MAX_CREDENTIAL_BYTES:
            raise CredentialError("invalid credential structure")
        with path.open("rb") as handle:
            if not os.path.samestat(initial, os.fstat(handle.fileno())):
                raise CredentialError("credential file changed while loading")
            data = handle.read(MAX_CREDENTIAL_BYTES + 1)
    except CredentialError:
        raise
    except (OSError, ValueError):
        raise CredentialError("credential file could not be read") from None
    if len(data) > MAX_CREDENTIAL_BYTES:
        raise CredentialError("invalid credential structure")
    schema, fields = _parse_document(data)
    if schema not in {1, 2, 3} or set(fields) != _allowed_top_fields(schema):
        raise CredentialError("invalid credential structure")
    provider = _required_text(fields, "Provider")
    if provider not in {"dashscope", "volcengine", "minimax"}:
        raise CredentialError("unsupported credential provider")
    if provider != expected_provider:
        raise CredentialError("credential provider mismatch")
    region = _required_text(fields, "Region")
    auth_mode = (
        _required_text(fields, "AuthMode")
        if schema >= 2
        else ("app-token" if provider == "volcengine" else "api-key")
    )
    workspace_id = _required_text(fields, "WorkspaceId") if schema >= 3 else None
    models_element = fields["Models"]
    if _local_name(models_element.tag) != "Obj":
        raise CredentialError("invalid credential structure")
    models = _parse_models(models_element)
    if set(models) != _allowed_models(provider, schema):
        raise CredentialError("invalid credential structure")
    credential_element = fields["Credential"]
    if _local_name(credential_element.tag) != "Obj":
        raise CredentialError("invalid credential structure")
    username, encrypted_hex = _parse_credential(credential_element)
    try:
        if not encrypted_hex or len(encrypted_hex) % 2:
            raise ValueError
        ciphertext = bytes.fromhex(encrypted_hex)
    except ValueError:
        raise CredentialError("invalid encrypted credential") from None
    secret = decrypt_dpapi(ciphertext)
    if not secret:
        raise CredentialError("empty credential is not allowed")
    return CredentialRecord(
        schema_version=schema,
        provider=provider,
        auth_mode=auth_mode,
        region=region,
        workspace_id=workspace_id,
        models=models,
        username=username,
        secret=SecretStr(secret),
        ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
    )


def load_dashscope_config(path: Path) -> DashScopeConfig:
    record = load_credential(path, "dashscope")
    if record.schema_version != 3:
        raise CredentialError("DashScope metadata update is required")
    if (
        record.auth_mode != "api-key"
        or record.region != "cn-beijing"
        or record.workspace_id is None
        or not WORKSPACE_PATTERN.fullmatch(record.workspace_id)
    ):
        raise CredentialError("DashScope configuration is incompatible")
    expected: dict[str, str | int] = {
        "answer": "qwen-flash",
        "engine": "qwen-plus",
        "summary": "qwen-max",
        "embedding": "text-embedding-v4",
        "embedding_dimension": 1024,
        "rerank": "qwen3-vl-rerank",
    }
    if record.models != expected:
        raise CredentialError("DashScope model configuration is incompatible")
    return DashScopeConfig(
        region=record.region,
        workspace_id=record.workspace_id,
        models={
            key: str(value)
            for key, value in record.models.items()
            if key != "embedding_dimension"
        },
        embedding_dimension=1024,
        api_key=record.secret,
        credential_ciphertext_sha256=record.ciphertext_sha256,
    )
