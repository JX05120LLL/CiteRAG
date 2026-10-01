"""Decorates generation, not routing/retrieval. The first model request can finish directly."""

import json

from langgraph.types import Command

from app.agent.errors import AgentPaused
from app.agent.graph import build_graph, initial_state


class AgentAnswerer:
    def __init__(self, runner, run_id, generation, base):
        self.runner, self.run_id, self.generation, self.base = runner, run_id, generation, base
        self.citations = []
        self.requires_support_verification = getattr(base, "requires_support_verification", False)

    def __getattr__(self, name):
        if name in {"stream_general", "stream_with_context"}:
            raise AttributeError(name)  # Tool decisions remain buffered until final source checks.
        return getattr(self.base, name)

    def prepare_sources(self, citations):
        self.citations = citations

    async def general_answer(self, question, context):
        return await self.generate(question, [], context, True)

    async def answer_with_context(self, question, evidence, context):
        return await self.generate(question, evidence, context, False)

    async def answer(self, question, evidence):
        return await self.generate(question, evidence, {}, False)

    async def generate(self, question, evidence, context, general):
        prepared = {
            "question": question,
            "evidence": evidence,
            "context": context,
            "general": general,
            "citations": self.citations,
        }
        await self.runner.prepare_generation(self.run_id, self.generation, prepared)
        return await drive(self.runner, self.run_id, self.generation, self.base)


async def drive(runner, run_id, generation, base, *, resume=None, continuing=False):
    from app.agent.hooks import RunHooks

    graph = build_graph(RunHooks(runner, run_id, generation, base), runner.saver)
    config = {"configurable": {"thread_id": str(run_id)}, "recursion_limit": 40}
    value = (
        Command(resume=resume)
        if resume is not None
        else None
        if continuing
        else initial_state(str(run_id))
    )
    result = await graph.ainvoke(value, config)
    if result.get("__interrupt__"):
        await runner.pause(run_id, generation, result["__interrupt__"][0].value)
        raise AgentPaused
    return json.dumps(result["answer"], ensure_ascii=False, allow_nan=False)
