# Reviewed Read-Only MCP Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 只有经维护者审核、部署可控且版本可核实的只读 MCP 工具，对审核许可的公开参数免逐次审批；其余调用使用现有审批流程。

**Architecture:** 保留 `ToolGateway` 作为唯一建单、审批和审计入口。MCP 注册解析严格校验每工具审核记录及有限参数范围；网关在参数规范化后计算本次是否需审批，Agent 延迟执行继续绑定策略摘要。复用 MCP 现有固定 URL、版本、完整 descriptor 和输入 schema 校验，不加数据库表或新执行通道。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、JSON Schema、MCP SDK 2.2.0、pytest、隔离 PostgreSQL；现有 React 客户端保持 API 兼容。

**Spec:** `docs/development/CITERAG-MCP-READONLY-TRUST-DESIGN.md`，批准版本 `f71bd9ecc700f67da8ee237d6f32146b68034d82`。

## Global Constraints

- 仅 CiteRAG；不调用真实天气、模型或第三方 MCP，不增加 OAuth/凭据，不访问或迁移真实业务库，不合并 `main` 或部署。
- 首版免审批仅限维护者控制部署且版本可核实、无凭据、免费、只读的服务；来源无法核实的第三方逐次审批。
- 任意自由文本、知识库正文、文件内容、凭据或可能包含私有信息的参数逐次审批；schema 不合法的参数直接拒绝；写和付费调用继续审批。
- 缺审核记录、旧 `approval_required=false` 或自报 `readOnly` 都不产生豁免；目标、版本、descriptor、参数范围、权限、去向、收费或审核记录变更使豁免失效。
- 从现有 `feat/tool-gateway-mcp-acceptance` 的 `8e2a6f3` 创建 `feat/mcp-reviewed-read-policy` 隔离工作树，并纳入已批准设计及本计划；保留原工作区未跟踪文件。仅标准分支命名。

## File Map

- `backend/app/tools/mcp.py`：审核记录解析、与固定服务/descriptor 绑定、有限公开参数约束校验；保持已有传输和契约校验。
- `backend/app/tools/gateway.py`：`ToolDefinition` 的可选审核范围、策略摘要和调用级审批判定；手动及 Agent 共用建单逻辑。
- `backend/tests/test_agent_mcp.py`：注册默认值、审核记录与真实本地合成 MCP 契约测试。
- `backend/tests/test_tool_gateway.py`：手动调用、审计/幂等、范围变更和旧待审批回归。
- `backend/tests/test_agent_api.py` 或 `backend/tests/test_agent_persistence.py`：Agent 延迟执行时的有/无豁免与策略变更回归，复用现有合成模型夹具。
- `backend/TOOLS.md`、`backend/app/tools/local_mcp.py`：登记格式和本地模板；模板继续要求审批，不附带虚构审核结论。

## Review Focus

- 审核对象字段缺失、未知、类型错误或与 URL/版本/descriptor 不匹配：不得免审批；有意填写却损坏的对象应拒绝加载，避免误以为已受信任。任务 1 覆盖。
- 任意文本、嵌套对象/数组、额外键、布尔值冒充数值、非有限数值：不得进入公开参数豁免。任务 1 覆盖。
- `approval_required=false` 的旧登记、自报 `readOnly`、缺审核记录：仍应进入审批，不能静默外发。任务 1、2 覆盖。
- 同一工具的公开参数与超出审核范围但符合工具 schema 的参数：分别自动执行和等待审批；不符合工具 schema 的参数直接 422。任务 2 覆盖。
- Agent 准备后审核策略改变、服务版本/descriptor 变化：旧执行不得绕过新策略或发出 `call_tool`。任务 3 覆盖。

---

### Task 1: Validate a Reviewed Public-Argument Grant

**Files:** `backend/app/tools/mcp.py`, `backend/app/tools/gateway.py`, `backend/tests/test_agent_mcp.py`。

**Interfaces:** 在 `ToolDefinition` 增加可选 `unattended_read_review: dict | None`，并把其规范化摘要纳入 `policy_hash`；`ReviewedMCPTool` 接受同名可选对象。`MCPAdapter.definition()` 只在审核对象有效且工具未显式要求审批时携带它；显式 `approval_required=true` 始终要求审批，旧 `false` 或省略字段却没有有效审核对象也要求审批。审核对象字段固定为 `reviewed_by`、`reviewed_at`、`evidence_ref`、`source`、`deployment_id`、`behavior`、`data_destination`、`allowed_data`、`allowed_arguments_schema`、`permissions`、`cost`、`url`、`server_version`、`descriptor_sha256`；首版 `allowed_data="public"`、`permissions=[]`、`cost="free"`。参数子 schema 仅允许顶层对象的有限枚举、双端有界数值、固定公开标识及 `additionalProperties=false`，不可包含自由文本或嵌套结构。

- [ ] **Step 1: Write failing registry tests.** 更新旧默认值断言为需审批；新增无审核但 `false`、自报 `readOnly`、完整有效审核、缺字段/未知字段、错误类型、错误 URL/版本/descriptor 摘要、非空权限/收费、自由文本/嵌套/额外键、布尔值冒充数值和非有限数值用例。有效审核用合成 `add(a,b)` 数值工具，不填真实凭据。
- [ ] **Step 2: Run red tests.** 在 `backend` 执行 `uv run --locked --extra agent --extra mcp --no-env-file pytest -q tests/test_agent_mcp.py`；预期新断言失败，记录失败用例，已有测试不得被无关修改掩盖。
- [ ] **Step 3: Implement minimal parsing.** 校验对象必须精确匹配上述字段集，审核目标须等于登记 URL/版本和规范化完整 descriptor 的 SHA-256；权限必须为空、收费状态必须为免费、参数范围必须受限。无审核记录时生成需审批定义；坏审核对象拒绝注册；单独的旧 `false` 不授予信任。采用规范化 JSON 摘要，不记录秘密。
- [ ] **Step 4: Run green tests and commit.** 同一 pytest 命令全部通过后，仅提交本任务的实现与测试。核对 `git diff --check`。

### Task 2: Decide Approval Per Validated Invocation

**Files:** `backend/app/tools/gateway.py`, `backend/tests/test_tool_gateway.py`, `backend/tests/test_agent_mcp.py`。

**Interfaces:** `ToolDefinition.requires_approval(arguments: dict) -> bool` 使用任务 1 的审核范围；本地/天气沿用 `approval_required`。MCP 默认需审批，只有 `effect=read_only` 且审核记录与本次规范化参数匹配才返回 `False`。`catalog.approval_required` 对条件豁免保守显示 `true`，调用实际结果仍以 `ToolCall.status` 为准，不更改现有 API 字段。

- [ ] **Step 1: Write failing gateway tests.** 合成工具同一 schema 的公开参数 `succeeded` 且仅一次调用、超范围但 schema 合法的参数 `pending_approval` 且审批前零次调用、schema 非法返回 422；核对 `tool_calls` 审计、同 request ID 幂等、旧待审批 NULL 绑定仍失败关闭。本地计算器和天气现有审批语义保持。
- [ ] **Step 2: Run red tests.** 连接已核实属于本任务的隔离 PostgreSQL，设置 `CITERAG_TEST_DATABASE_URL` 指向该隔离库后，从 `backend` 执行 `uv run --locked --extra agent --extra mcp --no-env-file pytest -q tests/test_tool_gateway.py`；新用例失败且不可因缺测试库变量被跳过。
- [ ] **Step 3: Implement one decision path.** `invoke()` 在 `spec.validate()` 后对规范化参数调用 `requires_approval()`，该结果同时决定 `ToolCall.status` 和立即执行/等待分支；Agent 的 `defer_execution=True` 保持现有单会话串行和延迟执行。审核对象及限制进入 `policy_hash`，MCP `version` 继续由含该摘要的策略生成；不改数据库 schema。
- [ ] **Step 4: Run green tests and commit.** 同一隔离库测试通过，再跑 `test_agent_mcp.py`；检查无未经批准的 MCP 出站调用，并提交本任务文件。

### Task 3: Agent, Transport, Docs, and Whole-Branch Acceptance

**Files:** `backend/tests/test_agent_api.py` 或 `backend/tests/test_agent_persistence.py`, `backend/tests/test_agent_mcp.py`, `backend/TOOLS.md`, `backend/app/tools/local_mcp.py`（仅在模板需要明确默认值时修改）。

**Interfaces:** 不新增 API、数据库或 MCP 认证接口；Agent 通过现有 `ToolGateway.invoke(..., defer_execution=True)`、`policy_hash`、版本和参数摘要执行或等待。

- [ ] **Step 1: Write failing integration tests.** 复用合成模型、合成 MCP：公开参数的 Agent 不停在审批且只调用一次；自由文本/超范围等待审批；准备后审核记录、URL、版本或 descriptor 变化均阻止旧调用；服务 descriptor 变化时不发出 `call_tool`；持久审计和幂等仍成立。
- [ ] **Step 2: Run red tests.** 在已核实的隔离库运行新增 Agent 用例与 `tests/test_agent_mcp.py`；预期新增策略变更用例先失败。若 Task 2 已使部分用例自然通过，保留其回归价值，不人为改坏实现。
- [ ] **Step 3: Complete only the missing glue and docs.** 修正 Agent 对条件审批的状态分支（如新用例证实需要）；更新 `backend/TOOLS.md` 解释登记字段、审核证据、旧 `false` 行为与审核失效；本地 CLI 模板继续 `approval_required=true`，不编造已审核记录。
- [ ] **Step 4: Verify and review.** 隔离库执行相关与完整后端 CI 选择，执行 `ruff check app migrations tests/test_tool_gateway.py tests/test_agent_mcp.py tests/test_agent_api.py tests/test_agent_persistence.py`、`compileall`、`git diff --check`；前端现有测试/类型检查/构建按 CI 跑。独立代码审阅重点核对零未授权出站、旧数据失效、参数子 schema 绕过、日志不泄密和只提交允许文件；修复确认缺陷后再提交。
- [ ] **Step 5: Push and accept.** 在 `feat/mcp-reviewed-read-policy` 提交并推送，核对精确 SHA 的 GitHub CI 全绿；若失败，诊断、补回归、修复并重跑。记录通过/失败/未运行及残余风险，不创建 PR、合并或部署。

## Execution Handoff

用户已批准上述设计稿并要求实施。本计划须先由用户完整审阅；推荐 **Native**：按任务顺序在隔离工作树中测试先行实施，最后安排一次独立整枝审阅，因为网关/Agent/MCP 变更共享同一策略接口。用户确认计划与执行方式后，再进入代码修改。
