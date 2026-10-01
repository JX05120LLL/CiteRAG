# Agent 可选依赖许可

依赖由包管理器安装，没有复制上游 Agent 演示或另一套 RAG 实现。精确版本与传递依赖见 `backend/uv.lock`；项目 Apache-2.0 不替代第三方依赖许可。

| 依赖 | 锁定版本 | 许可 |
| --- | --- | --- |
| LangGraph | 1.2.12 | MIT |
| langgraph-checkpoint-postgres | 3.1.2 | MIT |
| MCP Python SDK | 2.2.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| psycopg / psycopg-pool | 3.3.6 / 3.3.3 | LGPL-3.0-only |

以上经本轮安装包 metadata 核对；发布二进制或包含第三方包时仍需带齐对应许可并遵守其再分发义务。psycopg 是独立可选驱动，不能称整套依赖均为 MIT。

上游：[LangGraph](https://github.com/langchain-ai/langgraph)、[MCP SDK](https://github.com/modelcontextprotocol/python-sdk)、[jsonschema](https://github.com/python-jsonschema/jsonschema)、[psycopg](https://github.com/psycopg/psycopg)、[psycopg-pool](https://github.com/psycopg/psycopg/tree/master/psycopg_pool)。
