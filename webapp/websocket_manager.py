from __future__ import annotations

import asyncio
from collections import deque
from typing import Deque


class WebSocketManager:
    def __init__(self, max_history: int = 2000, max_queue: int = 250) -> None:
        self.max_history = max_history
        self.max_queue = max_queue
        self.history: Deque[str] = deque(maxlen=max_history)
        # Sequence number of the newest line (1-based, never reset): the polling fallback asks for
        # lines after the last number it saw (FEAT-11).
        self.last_seq = 0
        self._clients: dict[object, asyncio.Queue[str]] = {}
        self._tasks: dict[object, asyncio.Task[None]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, websocket: object) -> None:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self.max_queue)
        self._clients[websocket] = queue
        self._loop = asyncio.get_running_loop()

        for line in list(self.history)[-500:]:
            await websocket.send_text(line)

        async def sender() -> None:
            while True:
                message = await queue.get()
                if message is None:
                    return
                await websocket.send_text(message)

        self._tasks[websocket] = asyncio.create_task(sender())

    def lines_since(self, seq: int) -> list[str]:
        """Lines published after sequence number `seq` (all kept lines if it's older than the history)."""
        newer = self.last_seq - max(0, seq)
        if newer <= 0:
            return []
        lines = list(self.history)
        return lines[-newer:] if newer < len(lines) else lines

    async def disconnect(self, websocket: object) -> None:
        queue = self._clients.pop(websocket, None)
        task = self._tasks.pop(websocket, None)
        if queue is not None:
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def publish(self, message: str) -> None:
        if not message:
            return
        line = message.rstrip("\n")
        if not line:
            return
        self.history.append(line)
        self.last_seq += 1

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._loop

        for websocket, queue in list(self._clients.items()):
            try:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                if loop is not None and loop.is_running():
                    loop.call_soon_threadsafe(queue.put_nowait, line)
                else:
                    queue.put_nowait(line)
            except RuntimeError:
                pass
            except asyncio.QueueFull:
                pass
