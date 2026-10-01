"""Windows-only checkpointer bridge; keeps API subprocess/media support on Proactor."""

import asyncio

from langgraph.checkpoint.base import BaseCheckpointSaver


class ThreadedSaver(BaseCheckpointSaver):
    def __init__(self, backend, loop):
        super().__init__(serde=backend.serde)
        self.backend, self.loop = backend, loop

    async def dispatch(self, coroutine):
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coroutine, self.loop))

    async def aget_tuple(self, config):
        return await self.dispatch(self.backend.aget_tuple(config))

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await self.dispatch(self.backend.aput(config, checkpoint, metadata, new_versions))

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await self.dispatch(self.backend.aput_writes(config, writes, task_id, task_path))

    async def adelete_thread(self, thread_id):
        return await self.dispatch(self.backend.adelete_thread(thread_id))

    async def alist(self, config, *, filter=None, before=None, limit=None):
        iterator = self.backend.alist(config, filter=filter, before=before, limit=limit)
        while True:

            async def next_item():
                try:
                    return True, await anext(iterator)
                except StopAsyncIteration:
                    return False, None

            found, item = await self.dispatch(next_item())
            if not found:
                return
            yield item

    def get_next_version(self, current, channel):
        return self.backend.get_next_version(current, channel)
