"""Explicit local verification commands; model calls are disabled by default."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.config import PROJECT_ROOT
from app.credentials import CredentialError, DashScopeConfig, load_dashscope_config
from app.providers.dashscope import (
    EMBEDDING_SINGLE_TEXT_TOKEN_LIMIT as _EMBEDDING_SINGLE_TEXT_TOKEN_LIMIT,
)
from app.providers.dashscope import DashScopeClient
from app.providers.errors import ProviderBudgetExceeded, ProviderDiagnostic, ProviderError
from app.providers.types import Message, ProviderUsage, RequestBudget
from app.validation import ValidationReport, ValidationStore, ValidationUsage, model_fingerprint

MODEL_CREDENTIAL_RECORD = PROJECT_ROOT / ".local/runtime/models/dashscope.credential.xml"
VALIDATION_ROOT = PROJECT_ROOT / ".local/runtime/validation"
_MODEL_ORDER = ("answer", "engine", "summary", "embedding", "rerank")
_FIXED_PROMPT = "请只回答：测试完成。"
_EMBEDDING_TEXT = "这是 CiteRAG 的公开测试句子。"
_RERANK_DOCUMENTS = ["苹果是一种水果。", "数据库保存文档。", "这是语音测试。"]
_RAG_PROVIDER_INPUT_LIMIT = 20_000
_BOUNDARY_SAMPLE_UNITS = _EMBEDDING_SINGLE_TEXT_TOKEN_LIMIT + 1024
_SAFE_PROVIDER_CATEGORIES = frozenset(
    {
        "authentication", "input_limit", "input_limit_other", "network", "permission",
        "protocol", "quota", "rate_limit", "request", "timeout", "upstream",
    }
)


def _embedding_boundary_sample() -> str:
    """A fixed token-heavy probe, not a local claim about the model's token count.

    The documented limit is 8192 tokens per string. The model tokenizer is not
    published, so only an explicit provider input-limit response can pass M0.
    """

    return " ".join(f"{index:04d}" for index in range(_BOUNDARY_SAMPLE_UNITS))


class _BoundaryProbeFailure(RuntimeError):
    def __init__(
        self,
        outcome: str,
        *,
        provider_category: str | None = None,
        status: int | None = None,
        request_id_present: bool | None = None,
        observed_tokens: int | None = None,
        diagnostic: ProviderDiagnostic | None = None,
    ) -> None:
        self.outcome = outcome
        self.provider_category = (
            provider_category if provider_category in _SAFE_PROVIDER_CATEGORIES else "other"
        )
        self.status = status if status is not None and 100 <= status <= 599 else None
        self.request_id_present = request_id_present
        self.observed_tokens = (
            observed_tokens if observed_tokens is not None and observed_tokens >= 0 else None
        )
        self.diagnostic = diagnostic
        super().__init__(outcome)


def _boundary_diagnostic(error: _BoundaryProbeFailure, *, used: int, limit: int) -> str:
    details = [f"结果={error.outcome}", "步骤=embedding_boundary"]
    if error.provider_category != "other":
        details.append(f"供应商类别={error.provider_category}")
    if error.status is not None:
        details.append(f"HTTP状态={error.status}")
    if error.request_id_present is not None:
        details.append(f"请求ID存在={str(error.request_id_present).lower()}")
    if error.observed_tokens is not None:
        details.append(f"供应商计量Token={error.observed_tokens}")
    if error.diagnostic is not None:
        details.extend((
            f"响应结构={error.diagnostic.response_shape}",
            f"错误码类别={error.diagnostic.code_class}",
            f"错误提示类别={error.diagnostic.message_class}",
        ))
        if error.diagnostic.input_range is not None:
            lower, upper = error.diagnostic.input_range
            details.append(f"输入范围={lower}..{upper}")
    details.append(f"reserved_requests={used}/{limit}")
    return "Embedding 边界未通过：" + "；".join(details) + "。"


@dataclass
class _ProbeEvidence:
    stage: str = "setup"
    request_ids: list[str] = field(default_factory=list)
    usage: dict[str, ValidationUsage] = field(default_factory=dict)
    embedding_dimension: int | None = None
    boundary_category: str | None = None

    def record(self, model: str, request_id: str | None, usage: ProviderUsage) -> None:
        if not request_id or request_id in self.request_ids:
            raise ValueError("provider response has no unique request ID")
        self.request_ids.append(request_id)
        self.usage[model] = ValidationUsage(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )


async def _verify_models(
    client: DashScopeClient, config: DashScopeConfig, evidence: _ProbeEvidence
) -> None:
    for role in ("answer", "engine", "summary"):
        evidence.stage = role
        model = config.models[role]
        result = await client.complete(model, [Message("user", _FIXED_PROMPT)], max_tokens=32)
        if result.model != model or not result.content.strip():
            raise ValueError("completion response is incompatible with configured model")
        evidence.record(model, result.request_id, result.usage)

    evidence.stage = "embedding"
    embedding = await client.embed([_EMBEDDING_TEXT])
    if (
        embedding.model != config.models["embedding"]
        or len(embedding.vectors) != 1
        or len(embedding.vectors[0]) != config.embedding_dimension
    ):
        raise ValueError("embedding dimensions or model are incompatible")
    evidence.embedding_dimension = len(embedding.vectors[0])
    evidence.record(embedding.model, embedding.request_id, embedding.usage)

    evidence.stage = "rerank"
    rerank = await client.rerank(
        "哪句话描述水果？", _RERANK_DOCUMENTS, top_n=len(_RERANK_DOCUMENTS)
    )
    if rerank.model != config.models["rerank"] or not rerank.items:
        raise ValueError("rerank response is incompatible with configured model")
    evidence.record(rerank.model, rerank.request_id, rerank.usage)


async def _verify_embedding_boundary(
    client: DashScopeClient, evidence: _ProbeEvidence
) -> None:
    evidence.stage = "embedding_boundary"
    try:
        accepted = await client.embed([_embedding_boundary_sample()])
    except ProviderError as error:
        if error.category != "input_limit" or error.status != 400:
            raise _BoundaryProbeFailure(
                "unexpected_error",
                provider_category=error.category,
                status=error.status,
                request_id_present=bool(error.request_id),
                diagnostic=error.diagnostic,
            ) from None
        if not error.request_id:
            raise _BoundaryProbeFailure(
                "missing_request_id", provider_category=error.category,
                status=error.status, request_id_present=False,
            ) from None
        if error.request_id in evidence.request_ids:
            raise _BoundaryProbeFailure("duplicate_request_id", request_id_present=True) from None
        evidence.request_ids.append(error.request_id)
        evidence.boundary_category = "input_limit"
    else:
        raise _BoundaryProbeFailure(
            "accepted" if accepted.request_id else "missing_request_id",
            request_id_present=bool(accepted.request_id),
            observed_tokens=accepted.usage.total_tokens,
        )


def _has_five_model_evidence(
    report: ValidationReport | None, config: DashScopeConfig, *, allow_boundary: bool
) -> bool:
    if report is None:
        return False
    models = [config.models[role] for role in _MODEL_ORDER]
    expected_ids = 6 if report.boundary_category == "input_limit" else 5
    return (
        report.kind == "models"
        and report.outcome == "available"
        and report.provider == "dashscope"
        and report.region == config.region
        and report.model_ids == config.models
        and report.embedding_dimension == config.embedding_dimension
        and report.failure_category is None
        and (allow_boundary or report.boundary_category is None)
        and report.boundary_category in (None, "input_limit")
        and len(set(models)) == 5
        and len(report.request_ids) == expected_ids
        and len(set(report.request_ids)) == expected_ids
        and set(report.usage) == set(models)
        and all(
            any(
                number is not None
                for number in (
                    report.usage[model].prompt_tokens,
                    report.usage[model].completion_tokens,
                    report.usage[model].total_tokens,
                )
            )
            for model in models
        )
    )


def _report(
    config: DashScopeConfig,
    evidence: _ProbeEvidence,
    *,
    outcome: str,
    elapsed_ms: int,
    failure_category: str | None = None,
) -> ValidationReport:
    return ValidationReport(
        kind="models",
        fingerprint=model_fingerprint(config, config.credential_ciphertext_sha256),
        outcome=outcome,
        verified_at=datetime.now(UTC),
        provider="dashscope",
        region=config.region,
        model_ids=dict(config.models),
        embedding_dimension=evidence.embedding_dimension,
        request_ids=tuple(evidence.request_ids),
        elapsed_ms=elapsed_ms,
        usage=evidence.usage,
        failure_category=failure_category,
        boundary_category=evidence.boundary_category,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    group = parser.add_subparsers(dest="group", required=True)
    models = group.add_parser("models", help="模型配置和接入验证")
    action = models.add_subparsers(dest="action", required=True)
    verify = action.add_parser("verify", help="默认只预检，不发送供应商请求")
    verify.add_argument("--real", action="store_true", help="执行真实供应商请求")
    verify.add_argument("--max-requests", type=int, choices=(5, 6), default=5)
    verify.add_argument("--confirm", help="非交互执行时必须明确提供 RUN")
    boundary = action.add_parser("boundary", help="单独验证 Embedding 单条 Token 上限")
    boundary.add_argument("--real", action="store_true", help="最多发送一次真实 Embedding 请求")
    boundary.add_argument("--confirm", help="非交互执行时必须明确提供 RUN")
    rag = group.add_parser("rag", help="知识引擎隔离与来源验证")
    rag_action = rag.add_subparsers(dest="action", required=True)
    rag_verify = rag_action.add_parser("verify", help="默认只预检，不写入知识库")
    rag_verify.add_argument("--real", action="store_true", help="执行真实双知识库验证")
    rag_verify.add_argument(
        "--max-provider-requests", type=_bounded_integer(40), default=40
    )
    rag_verify.add_argument("--max-input-chars", type=_bounded_integer(4000), default=4000)
    rag_verify.add_argument("--max-output-tokens", type=_bounded_integer(512), default=512)
    rag_verify.add_argument("--confirm", help="非交互执行时必须明确提供 RUN")
    return parser


def _bounded_integer(maximum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("expected a positive integer") from None
        if not 1 <= number <= maximum:
            raise argparse.ArgumentTypeError(f"allowed range is 1..{maximum}")
        return number

    return parse


def _rag_main(
    args: argparse.Namespace, input_fn: Callable[[str], str], config: DashScopeConfig
) -> int:
    from app.rag.verify import RagVerificationError

    if not args.real:
        print("双知识库离线预检通过；未发送供应商请求，未写入引擎数据库。")
        return 0
    print(
        "双知识库真实验证：两份固定合成资料，最多 "
        f"{args.max_provider_requests} 次模型请求、"
        f"测试资料合计 {args.max_input_chars} 字符、每次供应商输入最多 "
        f"{_RAG_PROVIDER_INPUT_LIMIT} 字符、每次 LLM 最多 "
        f"{args.max_output_tokens} 输出 token；可能产生供应商费用。"
    )
    confirmation = args.confirm
    if confirmation is None and (sys.stdin.isatty() or input_fn is not input):
        try:
            confirmation = input_fn("输入 RUN 才会执行双库验证：")
        except (EOFError, KeyboardInterrupt):
            confirmation = None
    if confirmation != "RUN":
        print("未确认真实调用；没有发送供应商请求。", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_run_rag(args, config))
    except RagVerificationError as error:
        print(
            f"双库验证未完成：步骤={error.stage}；类别={error.category}。",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print("双库验证未完成：本地运行或报告写入失败。", file=sys.stderr)
        return 1


async def _run_rag(args: argparse.Namespace, config: DashScopeConfig) -> int:
    from app.config import Settings
    from app.rag.database import (
        RagDatabaseError,
        load_rag_database_settings,
        probe_rag_database,
    )
    from app.rag.runtime import RagRuntime
    from app.rag.sdk import LIGHTRAG_COMMIT
    from app.rag.verify import (
        RagProviderEvidence,
        RagStorageInspector,
        RagVerificationError,
        RagVerificationOwner,
        make_cases,
        rag_configuration_fingerprint,
        verify_two_workspaces,
        write_rag_diagnostic,
    )
    from app.validation import StorageRowCount

    store = ValidationStore(VALIDATION_ROOT)
    current_models = store.read(
        "models", model_fingerprint(config, config.credential_ciphertext_sha256)
    )
    if current_models is None or current_models.outcome != "available":
        raise RagVerificationError("preflight", "models_unverified")
    settings = Settings.from_env()
    try:
        database = load_rag_database_settings(settings.rag_database_record)
    except (RagDatabaseError, FileNotFoundError, OSError):
        raise RagVerificationError("preflight", "engine_database_unconfigured") from None
    cases = make_cases(settings.rag_workspace_root)
    if sum(len(case.text) for case in cases) > args.max_input_chars:
        raise RagVerificationError("preflight", "input_limit")

    budget = RequestBudget(args.max_provider_requests)
    evidence = RagProviderEvidence()
    started = time.monotonic()
    store.invalidate("rag")
    try:
        async with RagVerificationOwner(database) as owner:
            probe = await probe_rag_database(database)
            inspector = RagStorageInspector(database)

            async def open_runtime() -> RagRuntime:
                runtime = RagRuntime(
                    settings,
                    request_budget=budget,
                    on_provider_result=evidence.record,
                    max_output_tokens=args.max_output_tokens,
                    max_provider_input_chars=_RAG_PROVIDER_INPUT_LIMIT,
                )
                await runtime.start(owner.assert_owned)
                if runtime.probe is None:
                    await runtime.close()
                    raise RagVerificationError("database", "engine_probe_failed")
                return runtime

            result = await verify_two_workspaces(
                cases,
                open_runtime=open_runtime,
                inspector=inspector,
                observed_reranks=lambda: evidence.rerank_count,
            )
        if not evidence.request_ids or len(evidence.request_ids) != budget.used:
            raise RagVerificationError("provider", "request_evidence_incomplete")
        store.write(
            ValidationReport(
                kind="rag",
                fingerprint=rag_configuration_fingerprint(config, database),
                outcome="available",
                verified_at=datetime.now(UTC),
                provider="dashscope",
                region=config.region,
                model_ids=dict(config.models),
                embedding_dimension=config.embedding_dimension,
                request_ids=tuple(evidence.request_ids),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                usage=evidence.usage,
                image_digest=database.image_digest,
                lightrag_commit=LIGHTRAG_COMMIT,
                storage_names=(
                    "PGKVStorage", "PGVectorStorage", "PGTableGraphStorage",
                    "PGDocStatusStorage",
                ),
                storage_counts=tuple(
                    StorageRowCount(
                        table=count.table,
                        workspace=count.workspace,
                        row_count=count.row_count,
                    )
                    for count in result.storage_counts
                ),
                postgresql_major=probe.postgresql_major,
                vector_version=probe.vector_version,
                source_verified=True,
                deletion_verified=True,
                restart_verified=True,
                cleanup_verified=True,
            )
        )
    except BaseException as error:
        if isinstance(error, RagVerificationError):
            safe = error
        elif isinstance(error, ProviderBudgetExceeded):
            safe = RagVerificationError("provider", "request_budget")
        elif isinstance(error, ProviderError):
            safe = RagVerificationError("provider", error.category)
        else:
            safe = RagVerificationError("runtime", "engine_failure")
        try:
            write_rag_diagnostic(
                VALIDATION_ROOT,
                cases,
                stage=safe.stage,
                category=safe.category,
                reserved_requests=budget.used,
            )
        except (OSError, ValueError):
            pass
        raise safe from None
    print(
        f"真实双库验证通过：预留 {budget.used} 次模型请求；"
        "来源、隔离、删除、重开和清理均已核对，报告已保存在本地。"
    )
    return 0


def _load_config(path: Path) -> DashScopeConfig | None:
    try:
        return load_dashscope_config(path)
    except CredentialError as error:
        if "metadata update" in str(error).lower():
            print(
                "百炼配置需升级元数据；在项目根目录运行 "
                "powershell.exe -NoProfile -File .\\scripts\\configure_models.ps1 "
                "-Provider dashscope -UpdateMetadata。",
                file=sys.stderr,
            )
        else:
            print("百炼配置不可用；请检查本地凭证记录和权限。", file=sys.stderr)
    except (FileNotFoundError, OSError):
        print("百炼配置文件不可用；请先配置本地模型凭证。", file=sys.stderr)
    return None


def _boundary_main(
    args: argparse.Namespace,
    input_fn: Callable[[str], str],
    config: DashScopeConfig,
    client_factory: Callable[[DashScopeConfig, RequestBudget], DashScopeClient] | None,
) -> int:
    try:
        store = ValidationStore(VALIDATION_ROOT)
        fingerprint = model_fingerprint(config, config.credential_ciphertext_sha256)
        current = store.read("models", fingerprint)
        if not _has_five_model_evidence(current, config, allow_boundary=True):
            print("缺少当前配置的完整五模型成功报告；先重新验证五模型。", file=sys.stderr)
            return 2
        assert current is not None
    except (OSError, ValueError):
        print("五模型报告不可用；未发送供应商请求。", file=sys.stderr)
        return 2
    if not args.real:
        print("Embedding 边界离线预检通过；未发送供应商请求，边界未重新验证。")
        return 0
    print(
        f"单条 Embedding 文档上限 {_EMBEDDING_SINGLE_TEXT_TOKEN_LIMIT} Token；"
        "固定非私人样本最多发送 1 次，实际 Token 数以供应商为准，可能产生费用。"
    )
    confirmation = args.confirm
    if confirmation is None and (sys.stdin.isatty() or input_fn is not input):
        try:
            confirmation = input_fn("输入 RUN 才会发送边界请求：")
        except (EOFError, KeyboardInterrupt):
            confirmation = None
    if confirmation != "RUN":
        print("未确认真实调用；没有发送供应商请求。", file=sys.stderr)
        return 2

    five_report = current.model_copy(
        update={"boundary_category": None, "request_ids": current.request_ids[:5]}
    )
    if current.boundary_category is not None:
        try:
            store.invalidate("models")
            store.write(five_report)
        except (OSError, ValueError):
            print("旧边界报告无法安全归档；没有发送供应商请求。", file=sys.stderr)
            return 1
    evidence = _ProbeEvidence(
        request_ids=list(five_report.request_ids),
        usage=dict(five_report.usage),
        embedding_dimension=five_report.embedding_dimension,
    )
    budget = RequestBudget(1)
    factory = client_factory or (lambda configuration, bound: DashScopeClient(
        configuration, budget=bound
    ))
    started = time.monotonic()
    try:
        async def run() -> None:
            async with factory(config, budget) as client:
                await _verify_embedding_boundary(client, evidence)

        asyncio.run(run())
    except _BoundaryProbeFailure as error:
        print(_boundary_diagnostic(error, used=budget.used, limit=1), file=sys.stderr)
        print("五模型成功证据保留；输入边界未通过。", file=sys.stderr)
        return 1
    except (ProviderBudgetExceeded, OSError, ValueError, TypeError, RuntimeError):
        print("Embedding 边界未完成：本地验证失败；五模型证据保留。", file=sys.stderr)
        return 1
    try:
        store.write(_report(
            config, evidence, outcome="available",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        ))
    except (OSError, ValueError):
        print("Embedding 边界结果无法安全写入；五模型证据保留。", file=sys.stderr)
        return 1
    print("Embedding 单条输入超限已由供应商拒绝并核对；报告已更新。")
    return 0


def main(
    argv: list[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    client_factory: Callable[[DashScopeConfig, RequestBudget], DashScopeClient] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    config = _load_config(MODEL_CREDENTIAL_RECORD)
    if config is None:
        return 2

    print(f"地域：{config.region}")
    print("模型：" + "、".join(config.models[role] for role in _MODEL_ORDER))
    print(f"Embedding 目标维度：{config.embedding_dimension}")
    if args.group == "rag":
        return _rag_main(args, input_fn, config)
    if args.action == "boundary":
        return _boundary_main(args, input_fn, config, client_factory)
    if not args.real:
        print("离线预检通过；未发送供应商请求，模型能力仍未验证。")
        return 0

    print(f"真实验证最多发送 {args.max_requests} 次请求，可能产生供应商费用。")
    confirmation = args.confirm
    if confirmation is None and (sys.stdin.isatty() or input_fn is not input):
        try:
            confirmation = input_fn("输入 RUN 才会发送请求：")
        except (EOFError, KeyboardInterrupt):
            confirmation = None
    if confirmation != "RUN":
        print("未确认真实调用；没有发送供应商请求。", file=sys.stderr)
        return 2

    try:
        store = ValidationStore(VALIDATION_ROOT)
        store.invalidate("models")
    except (OSError, ValueError):
        print("旧验证报告无法安全归档；没有发送供应商请求。", file=sys.stderr)
        return 1

    evidence = _ProbeEvidence()
    budget = RequestBudget(args.max_requests)
    factory = client_factory or (lambda current, bound: DashScopeClient(current, budget=bound))
    started = time.monotonic()
    try:
        async def run() -> None:
            async with factory(config, budget) as client:
                await _verify_models(client, config, evidence)
                if args.max_requests == 6:
                    evidence.stage = "five_model_report"
                    store.write(_report(
                        config, evidence, outcome="available",
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    ))
                    await _verify_embedding_boundary(client, evidence)

        asyncio.run(run())
    except _BoundaryProbeFailure as error:
        print(
            _boundary_diagnostic(error, used=budget.used, limit=args.max_requests),
            file=sys.stderr,
        )
        print("五模型成功证据已保留；输入边界未通过。", file=sys.stderr)
        return 1
    except ProviderError as error:
        if error.request_id and error.request_id not in evidence.request_ids:
            evidence.request_ids.append(error.request_id)
        try:
            store.write(
                _report(
                    config,
                    evidence,
                    outcome="unavailable",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    failure_category=error.category,
                )
            )
        except (OSError, ValueError):
            print("供应商失败，且验证报告写入失败。", file=sys.stderr)
            return 1
        print(
            f"供应商验证失败：{error.category}；步骤={evidence.stage}；"
            f"reserved_requests={budget.used}/{args.max_requests}。",
            file=sys.stderr,
        )
        return 1
    except (ProviderBudgetExceeded, OSError, ValueError, TypeError, RuntimeError):
        print(
            f"验证未完成；步骤={evidence.stage}；"
            f"reserved_requests={budget.used}/{args.max_requests}；"
            "旧结果已失效，没有生成可用报告。",
            file=sys.stderr,
        )
        return 1

    try:
        store.write(
            _report(
                config,
                evidence,
                outcome="available",
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        )
    except (OSError, ValueError):
        print("验证结果无法安全写入；能力仍未标记为可用。", file=sys.stderr)
        return 1
    print(f"模型真实验证通过：{budget.used} 次请求，报告已保存在本地。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
