from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from services.loom_account.server import AccountConfig, AccountError
from services.loom_account.agent_ops import AgentOpsApplication as AccountApplication, AgentOpsStore as AccountStore, AgentOpsServer as LoomAccountServer


def _store(tmp_path):
    os.environ["LOOM_TELEMETRY_SECRET"] = "test-secret"
    store = AccountStore(AccountConfig(db_path=tmp_path / "accounts.db"))
    user = store.register_verified("owner@example.com", "test-hash")
    with store._connect() as db:
        db.execute("UPDATE users SET role='owner' WHERE id=?", (user["id"],))
    return store, {**user, "role": "owner"}


def test_agent_telemetry_lifecycle_and_usage_delta(tmp_path):
    store, user = _store(tmp_path)
    uid = user["id"]
    base = int(time.time()) - 120
    device = {
        "event": "connected", "user_id": uid, "device_id": "device-1", "name": "Yuchen-PC",
        "platform": "win32", "app_version": "0.1.18", "host_version": "1.0.6",
        "host_mode": "background", "host_protocol": 8, "bootstrap_protocol": 1, "at": base,
    }
    assert store.telemetry_device(device) == {"ok": True}
    devices = store.admin_devices()
    assert len(devices) == 1
    assert devices[0]["device_id"] == "device-1"
    assert devices[0]["email"] == "owner@example.com"
    store.telemetry_agent_event({"event":"thread.started","user_id":uid,"device_id":"device-1","thread_id":"thread-a","model":"gpt-test","provider":"openai","at":base+10})
    store.telemetry_agent_event({"event":"turn.started","user_id":uid,"device_id":"device-1","thread_id":"thread-a","turn_id":"turn-1","at":base+20})
    tool={"event":"tool.completed","user_id":uid,"device_id":"device-1","thread_id":"thread-a","turn_id":"turn-1","call_id":"call-1","event_key":"tool:item-1","tool_name":"terminal","at":base+30}
    store.telemetry_agent_event(tool); store.telemetry_agent_event(tool)
    store.telemetry_agent_event({"event":"approval.requested","user_id":uid,"device_id":"device-1","thread_id":"thread-a","turn_id":"turn-1","call_id":"call-2","event_key":"approval:turn-1:call-2","tool_name":"computer_use","at":base+35})
    store.telemetry_agent_event({"event":"turn.completed","user_id":uid,"device_id":"device-1","thread_id":"thread-a","turn_id":"turn-1","status":"completed","usage":{"inputTokens":100,"outputTokens":50,"totalTokens":150},"at":base+40})
    store.telemetry_agent_event({"event":"turn.started","user_id":uid,"device_id":"device-1","thread_id":"thread-a","turn_id":"turn-2","at":base+50})
    store.telemetry_agent_event({"event":"turn.completed","user_id":uid,"device_id":"device-1","thread_id":"thread-a","turn_id":"turn-2","status":"completed","usage":{"inputTokens":130,"outputTokens":70,"totalTokens":200},"at":base+60})
    runs={run["turn_id"]:run for run in store.admin_runs()}
    assert runs["turn-1"]["tool_count"]==1 and runs["turn-1"]["approval_count"]==1 and runs["turn-1"]["total_tokens"]==150
    assert runs["turn-2"]["input_tokens"]==30 and runs["turn-2"]["output_tokens"]==20 and runs["turn-2"]["total_tokens"]==50
    tools=store.admin_tools(); assert tools["tool_calls"]==1 and tools["approvals"]==1 and tools["tools"][0]["tool_name"]=="terminal"


def test_admin_interrupt_command_round_trip(tmp_path):
    store,user=_store(tmp_path); uid=user["id"]
    store.telemetry_agent_event({"event":"turn.started","user_id":uid,"device_id":"device-1","thread_id":"thread-a","turn_id":"turn-live","model":"model-a","provider":"provider-a"})
    run=store.admin_runs()[0]; command=store.admin_queue_interrupt(user,run["id"]); assert command["kind"]=="turn.interrupt"
    polled=store.telemetry_poll_commands({"user_id":uid,"device_id":"device-1"}); assert len(polled["commands"])==1 and polled["commands"][0]["payload"]["turnId"]=="turn-live"
    command_id=polled["commands"][0]["id"]; assert store.telemetry_complete_command({"command_id":command_id,"ok":True})["changed"] is True
    assert store.telemetry_poll_commands({"user_id":uid,"device_id":"device-1"})["commands"]==[]
    assert store.admin_audit()[0]["action"]=="agent.run.interrupt"


def test_internal_telemetry_secret_fails_closed(tmp_path):
    store,user=_store(tmp_path); app=AccountApplication(store); body={"event":"connected","user_id":user["id"],"device_id":"device-1"}
    with pytest.raises(AccountError) as missing: app.telemetry_device(body,"wrong-secret")
    assert missing.value.code=="TELEMETRY_UNAUTHORIZED"
    assert app.telemetry_device(body,"test-secret")=={"ok":True}


def test_gateway_telemetry_strips_user_content():
    import ast
    from types import SimpleNamespace
    source=Path("services/loom_web_gateway/app.py").read_text(); module=ast.parse(source)
    function=next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=="_notification_telemetry")
    isolated=ast.Module(body=[function],type_ignores=[]); ast.fix_missing_locations(isolated)
    namespace={"Any":object,"DevicePeer":object,"_device_id":lambda value:str(value or "").strip()}; exec(compile(isolated,"<notification_telemetry>","exec"),namespace); mapper=namespace["_notification_telemetry"]
    peer=SimpleNamespace(user_id=7,device={"id":"device-7"})
    completed=mapper(peer,{"method":"turn/completed","params":{"threadId":"thread-secret","turn":{"id":"turn-secret","status":"failed","finalText":"PRIVATE RESPONSE TEXT","error":"PRIVATE ERROR DETAILS","usage":{"inputTokens":10,"outputTokens":4,"totalTokens":14}}}})
    assert completed is not None; serialized=repr(completed); assert "PRIVATE RESPONSE TEXT" not in serialized and "PRIVATE ERROR DETAILS" not in serialized and "finalText" not in completed and "error" not in completed and completed["error_present"] is True
    tool=mapper(peer,{"method":"item/completed","params":{"item":{"id":"tool:item-1","type":"tool_call","threadId":"thread-secret","turnId":"turn-secret","callId":"call-1","toolName":"terminal","arguments":{"command":"PRIVATE COMMAND"},"output":"PRIVATE TOOL OUTPUT"}}})
    assert tool is not None and tool["tool_name"]=="terminal"; serialized=repr(tool); assert "PRIVATE COMMAND" not in serialized and "PRIVATE TOOL OUTPUT" not in serialized


def test_agent_ops_http_routes(tmp_path, monkeypatch):
    import json, threading, urllib.error, urllib.request
    monkeypatch.setenv("LOOM_TELEMETRY_SECRET","route-secret")
    store=AccountStore(AccountConfig(db_path=tmp_path/"http-accounts.db")); user=store.register_verified("route-owner@example.com","test-hash")
    with store._connect() as db: db.execute("UPDATE users SET role='owner' WHERE id=?",(user["id"],))
    token=store.create_session(user["id"])["access_token"]; server=LoomAccountServer(("127.0.0.1",0),AccountApplication(store)); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start(); host,port=server.server_address[:2]; base=f"http://{host}:{port}"
    def call(path,*,method="GET",body=None,auth=False,secret=""):
        data=None if body is None else json.dumps(body).encode(); request=urllib.request.Request(base+path,data=data,method=method)
        if data is not None: request.add_header("Content-Type","application/json")
        if auth: request.add_header("Authorization","Bearer "+token)
        if secret: request.add_header("X-Loom-Telemetry-Secret",secret)
        try:
            with urllib.request.urlopen(request,timeout=5) as response: return response.status,json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error: return error.code,json.loads(error.read() or b"{}")
    try:
        body={"event":"connected","user_id":user["id"],"device_id":"route-device"}; status,payload=call("/v1/telemetry/device",method="POST",body=body,secret="wrong"); assert status==401 and payload["error"]["code"]=="TELEMETRY_UNAUTHORIZED"
        assert call("/v1/telemetry/device",method="POST",body=body,secret="route-secret")[0]==200
        assert call("/v1/telemetry/agent-event",method="POST",secret="route-secret",body={"event":"turn.started","user_id":user["id"],"device_id":"route-device","thread_id":"route-thread","turn_id":"route-turn","model":"route-model"})[0]==200
        status,overview=call("/v1/admin/agent-overview",auth=True); assert status==200 and overview["known_devices"]==1 and overview["active_runs"]==1
        status,runs=call("/v1/admin/runs",auth=True); assert status==200 and runs["runs"][0]["turn_id"]=="route-turn"; run_id=runs["runs"][0]["id"]
        status,queued=call("/v1/admin/runs/interrupt",method="POST",body={"run_id":run_id},auth=True); assert status==200 and queued["command"]["kind"]=="turn.interrupt"
        status,commands=call("/v1/telemetry/commands/poll",method="POST",secret="route-secret",body={"user_id":user["id"],"device_id":"route-device"}); assert status==200 and commands["commands"][0]["payload"]["turnId"]=="route-turn"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_gateway_heartbeat_is_owned_by_device_socket():
    source=Path("services/loom_web_gateway/app.py").read_text(encoding="utf-8")
    browser=source.split('async def browser_socket',1)[1].split('@app.websocket("/api/ws/device")',1)[0]
    device=source.split('async def device_socket',1)[1].split('def _static_headers',1)[0]
    assert 'heartbeat' not in browser or '_record_device(peer' not in browser
    assert 'heartbeat' in device and '_record_device(peer' in device
