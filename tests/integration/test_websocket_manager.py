"""Tests for websocket connection management and event fanout."""

from __future__ import annotations

import asyncio

from backend.main import ConnectionManager


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        self.messages.append(payload)


def test_connection_establishment_and_broadcast():
    manager = ConnectionManager()
    ws_a = FakeWebSocket()
    ws_b = FakeWebSocket()

    async def _run():
        await manager.connect("session-1", ws_a)
        await manager.connect("session-1", ws_b)
        await manager.send_event("session-1", {"event": "agent_start"})
        await asyncio.sleep(0.08)

    asyncio.run(_run())

    assert ws_a.accepted is True
    assert ws_b.accepted is True
    assert ws_a.messages[0]["event"] == "agent_start"
    assert ws_b.messages[0]["event"] == "agent_start"


def test_replay_buffer_for_reconnections():
    manager = ConnectionManager()
    ws = FakeWebSocket()

    async def _run():
        await manager.send_event("session-2", {"event": "concept_extracted"})
        await asyncio.sleep(0.08)
        await manager.connect("session-2", ws)

    asyncio.run(_run())

    assert ws.messages
    assert ws.messages[0]["event"] == "concept_extracted"


def test_event_batch_compaction_for_large_payloads():
    manager = ConnectionManager(compact_threshold_bytes=1)
    ws = FakeWebSocket()

    async def _run():
        await manager.connect("session-3", ws)
        await manager.send_event("session-3", {"event": "concept_extracted", "data": {"concept": "A"}})
        await manager.send_event("session-3", {"event": "concept_extracted", "data": {"concept": "B"}})
        await asyncio.sleep(0.08)

    asyncio.run(_run())

    assert ws.messages
    assert ws.messages[0]["event"] == "event_batch_compact"
    assert ws.messages[0]["data"]["count"] == 2
