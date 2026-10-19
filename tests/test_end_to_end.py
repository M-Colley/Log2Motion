"""Exercise the actual CLI, policy files and WebSocket wire protocol."""
import asyncio
import base64
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integration import history
from integration.server import BridgeServer, parse_simulation_output

ROOT = Path(__file__).resolve().parents[1]
TAP = {"action_type": "ActionType.DUAL_POINT", "yx_touch": [0.5, 0.5],
       "yx_lift": [0.5, 0.5], "time": []}


def run_cli(tmp_path, actions, *extra):
    source = tmp_path / "actions with spaces.json"
    source.write_text(json.dumps(actions), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(ROOT / "log2motion_visualization.py"),
         "--actions-file", str(source), "--no-rest", *extra],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )


@pytest.mark.integration
@pytest.mark.rendering
def test_cli_runs_outside_repo_and_exports_playable_video(tmp_path):
    import imageio.v2 as imageio

    video = tmp_path / "exported video.mp4"
    completed = run_cli(tmp_path, [TAP], "--video", str(video), "--video-size", "320", "180")
    output = parse_simulation_output(completed.stdout, completed.returncode, completed.stderr)
    assert list(output["results"]) == ["episode_0"]
    assert output["episodes"]["episode_0"]["is_success"]
    assert "---PROGRESS:1/1---" in completed.stdout
    assert video.stat().st_size > 1000
    with imageio.get_reader(video) as reader:
        assert reader.get_data(0).shape == (180, 320, 3)
        frame_count = reader.count_frames()
        fps = reader.get_meta_data()["fps"]
        assert frame_count > 1
        duration = output["results"]["episode_0"][-1]["time"]
        assert abs(frame_count / fps - duration) <= 1 / fps + 0.002


@pytest.mark.parametrize("actions", [[], [{}], [dict(TAP, yx_touch=[float("nan"), 0])]])
def test_cli_input_errors_are_framed_and_nonzero(tmp_path, actions):
    completed = run_cli(tmp_path, actions)
    assert completed.returncode == 1
    assert '"status": "error"' in completed.stdout
    assert "---DATA_END---" in completed.stdout


@pytest.mark.integration
def test_real_websocket_simulation_history_and_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DATA_DIR", tmp_path)

    async def scenario():
        bridge = BridgeServer()
        async with serve(bridge.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", max_size=20 * 1024 * 1024) as socket:
                async def request(payload):
                    await socket.send(json.dumps(payload))
                    return json.loads(await asyncio.wait_for(socket.recv(), timeout=120))

                session = await request({"type": "ADD_SESSION", "name": "End to end"})
                assert session["type"] == "SESSION_ADDED"
                session_id = session["session_id"]
                # A malformed request must leave the connection usable.
                assert (await request({"type": "START_SIM", "data": {"nodes": []}}))["type"] == "SIM_ERROR"

                node = {"id": "button:1", "name": "Tap", "type": "RECTANGLE",
                        "normalizedCenter": [0.5, 0.5], "x": 100, "y": 100,
                        "width": 40, "height": 40}
                await socket.send(json.dumps({"type": "START_SIM", "visualization": False,
                                              "data": {"nodes": [node, node]}}))
                progress = []
                while True:
                    response = json.loads(await asyncio.wait_for(socket.recv(), timeout=120))
                    if response["type"] == "SIM_PROGRESS":
                        progress.append(response["current"])
                    else:
                        break
                assert response["type"] == "RESULT", response
                assert progress == [1, 2]
                result = response["data"]
                assert len(result["episodes"]) == 2
                for episode in result["episodes"]:
                    data = episode["data"]
                    assert 1 < len(data["trajectory"]) <= 300
                    assert np.isfinite(data["trajectory"]).all()
                    assert np.isfinite(data["muscle_costs"]).all()
                    assert data["dt"] > 0
                    assert data["contact_indices"]

                saved = await request({"type": "GET_HISTORY_DATA", "session_id": session_id})
                assert saved["type"] == "HISTORY_DATA"
                assert len(saved["data"]) == 1
                pdf = await request({"type": "DOWNLOAD_SINGLE_RUN_PDF", "sessionId": session_id,
                                     "runId": result["run_id"]})
                assert pdf["type"] == "SINGLE_RUN_PDF", pdf
                assert base64.b64decode(pdf["pdf_base64"]).startswith(b"%PDF-")

    asyncio.run(scenario())
