"""Console history, WebSocket stream and chat (FEAT-11)."""
from support import *  # noqa: F401,F403


def test_console_history_endpoint_returns_messages():
    app.state.console_manager.publish("hello from history endpoint")
    response = client.get("/api/console", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert "lines" in payload
    assert "hello from history endpoint" in payload["lines"]


def test_websocket_console_streams_messages():
    app.state.console_manager.history.clear()
    with client.websocket_connect(
        "/ws/console",
        headers={"Authorization": "Basic YWRtaW46czNjcmV0LXBhc3M="},
    ) as websocket:
        app.state.console_manager.publish("hello from websocket")
        message = websocket.receive_text()
        assert message == "hello from websocket"


def test_console_since_returns_only_newer_lines():
    manager = app.state.console_manager
    manager.publish("first line")
    mark = client.get("/api/console", auth=AUTH).json()["last"]
    manager.publish("second line")
    manager.publish("third line")
    newer = client.get(f"/api/console?since={mark}", auth=AUTH).json()
    assert newer["lines"] == ["second line", "third line"] and newer["last"] == mark + 2
    assert client.get(f"/api/console?since={newer['last']}", auth=AUTH).json()["lines"] == []
    assert client.get("/api/console?since=0", auth=AUTH).json()["lines"][-1] == "third line"


def test_chat_uses_tellraw_json_and_validates(fake_server):
    assert client.post("/api/server/chat", auth=AUTH, json={"message": "hi"}).status_code == 409
    _start()
    tricky = r'hi "all" \ ok'  # a quote and a backslash must survive the tellraw JSON
    assert client.post("/api/server/chat", auth=AUTH, json={"message": tricky}).status_code == 200
    command = fake_server[0].sent[-1]
    assert command.startswith("tellraw @a ")
    assert json.loads(command[len("tellraw @a "):])[-1] == {"text": tricky}
    assert json.loads(command[len("tellraw @a "):])[2] == {"text": "admin: ", "color": "aqua"}
    for bad in ("line\nbreak", "", "x" * 201):
        assert client.post("/api/server/chat", auth=AUTH, json={"message": bad}).status_code == 400
    assert any(line == f"[chat] admin: {tricky}" for line in app.state.console_manager.history)


def test_console_page_has_filters_and_overview_has_chat():
    console = client.get("/dashboard/console").text
    assert 'id="console-filter"' in console and 'id="console-search"' in console
    assert 'id="chat-form"' in client.get("/dashboard/overview").text
