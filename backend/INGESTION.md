# 资料入库、失败处理与固定问答回归

## 支持范围

| 格式 | 当前范围与限制 |
| --- | --- |
| TXT / Markdown | UTF-8（可带 BOM），保留可核对的行号与正文偏移；不执行 Markdown 中的代码或链接。 |
| PDF | 最多100页的文字型 PDF；扫描图片、混合扫描、加密文件和不能可靠抽取的结构明确拒绝，不包含 OCR。 |
| DOCX | 普通段落、简单表格；拒绝宏、DTD/实体声明、外部关系、嵌套/合并等不支持结构和非空页眉页脚。 |

每批最多5份，每份20 MiB；解析正文最多5,000,000字符、50,000块。解析在受限子进程执行，20秒超时会终止并回收进程。DOCX另限制ZIP条目数量和解压总量。达到资源上限报告 `parse_limit`，损坏/CRC/乱码为 `invalid_document`，不支持结构为 `unsupported_document`。

普通合法的UTF-16编码DOCX内部XML仍可解析；DTD/实体声明无论UTF-8或UTF-16都拒绝。TXT/MD本身仍要求UTF-8，不因此新增UTF-16文本文件支持。

## 长文本与向量化

入库仍用固定LightRAG版本和原文字符跨度分块：正常块最多1200个现有分块器token、重叠100。额外检查 `ceil(UTF-8字节数 × 1.1) ≤ 8192` 的本地保守预算，压缩率高的长正文会按原字符边界继续拆分，再以每批最多10条传给向量化模型。

- 不截断正文、不用多个向量平均来伪装原文本向量；分块token计数仍来自原分块器，预算单位不冒充实际token数。
- 新上传的高压缩长文本可能增加分块数及后续模型工作量；原有10,000片段容量检查继续执行。旧原文、索引、模型和1024维配置不自动改动或迁移。
- 沿用跳过纯空白块的既有规则；受管原文文件完整保留，不能把分块拼接描述成对所有空白输入都逐字保全。
- 无法按原语义拆分的实体、关系或查询输入超过本地预算时，在发送前明确失败。直接供应商客户端的显式边界探测不受此生产桥守卫替代。

**本地预算不是Qwen精确token计数，也不能证明真实供应商边界通过。** [同步API](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)写明text-embedding-v4单条8192 token、最多10条；[向量化总览](https://help.aliyun.com/zh/model-studio/embedding)的北京表与相关说明存在不同上限口径。本项目保留现有保守策略，不据此扩大输入或宣称M0边界已验收。真实边界探测须另行授权并保留供应商响应证据。

## 状态与失败原因

上传受理只表示原文件和任务已记录。解析、索引、核验依次完成后，才能显示“入库核验通过”。索引中的等待时间不等于失败；不能用虚构百分比或直接把状态改成功解决等待。

| 错误代码 | 含义与处理 |
| --- | --- |
| `parse_timeout` / `parse_limit` | 解析超时或资源超限；拆分/简化文件后重新上传。 |
| `unsupported_document` | 结构不在支持范围；转成普通段落/简单表格或UTF-8文字资料。 |
| `embedding_input_limit` | 生产向量化输入超过本地预算；拆分/简化资料，已修改索引须核对或受管重建。 |
| `engine_unavailable` | 索引服务暂不可用，不能据此断言从未写入；检查模型与服务状态及任务详情。 |
| `engine_incomplete` | 引擎未完成本次处理，未通过核验；检查任务后显式重试或重建。 |
| `rebuild_required` / `verification_failed` | 原文、索引或来源不能可靠对应；库保持待修复，不用旧证据回答。 |

本地预算失败只携带固定安全标记，即使上游SDK吞异常后只记录FAILED，任务仍保留 `embedding_input_limit`。不解析SDK错误正文推断原因，不输出原文、供应商响应或凭证。

任务详情的“引擎写入”决定恢复方式：发生或可能发生修改时保守保持待修复；重试/重建必须重新核验。状态未知不能当成成功。现有单写入器、重启恢复、库维护门禁和删除/替换语义保持不变；本轮未新增强制结束整个索引任务的总时限。

## 可重复的公开合成资料

[knowledge_quality.json](evaluation/knowledge_quality.json)含设备维护、社区活动、阳台种植三个主题、六份资料和固定问题。所有名称/数值均为公开合成内容，不能充当真实业务验收。

从仓库根目录在PowerShell中导出为可上传TXT；只允许新目录，拒绝覆盖既有文件：

```powershell
$cases = Get-Content -Raw -LiteralPath .\backend\evaluation\knowledge_quality.json | ConvertFrom-Json
$caseTarget = Join-Path (Get-Location) '.local\manual-knowledge-quality'
if (Test-Path -LiteralPath $caseTarget) { throw '目录已存在，请使用新的验收目录名。' }
New-Item -ItemType Directory -Path $caseTarget | Out-Null
foreach ($corpus in $cases.corpora) {
    $corpusTarget = Join-Path $caseTarget $corpus.id
    New-Item -ItemType Directory -Path $corpusTarget | Out-Null
    foreach ($source in $corpus.documents) {
        Set-Content -LiteralPath (Join-Path $corpusTarget $source.filename) -Value $source.text -Encoding utf8 -NoNewline
    }
}
```

真实入库和模型提问可能计费，导出文件本身不调用模型。获得相应授权后，三个主题分别新建知识库并导入各自两份文件，按JSON内 `doc_code` 确认属性，再测试：

| 问题类别 | 预期 |
| --- | --- |
| `exact_question` | 只定位目标编号，不混入多一位的近似编号；引用对应原文。 |
| `summary_question` | 数字、否定及适用条件正确，资料结论有当前库来源。 |
| `followup_question` | 结合本聊天理解“它”，重新查询当前库，不能沿用历史错误数字。 |
| `unsupported_question` | 明确证据不足，不悄悄改用常识补齐资料事实。 |
| `false_answer` | 作为负例核对：不能把近似编号的数字移到目标资料。 |
| `conflicting_quote` | 如需验证，可另建明确标注的合成冲突文件；应说明冲突，不自行猜版本或取一条作确定结论。 |
| 普通聊天同样的问题 | 不查询任何库，不伪造库来源或当前资料事实。 |

## 自动回归与证据边界

后端在 `backend/` 执行：

```powershell
uv run --locked --no-sync --no-env-file pytest -q tests/test_ingestion_parsing.py tests/test_embedding_boundaries.py tests/test_answer_quality_regression.py
```

这些文件已登记到公开CI测试清单。解析测试使用真实受限子进程和合成文件；Embedding测试使用合成tokenizer/供应商替身；问答测试执行实际AnswerService与原文/引用门禁，但数据库行、模型路由、检索和事实判定均为明确替身。后者验证协议和保护措施，**不等于真实模型能正确判断所有问题、事实或冲突**。

真实迁移/SQL的入库失败、重试、维护和保存测试须使用独立可丢弃PostgreSQL，绝不能将 `CITERAG_TEST_DATABASE_URL` 指向实际业务库。供应商探测、真实资料质量和真人语音另行验收，不能混入上述通过项。
