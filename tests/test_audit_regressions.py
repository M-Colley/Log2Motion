"""Reproductions found in the second integration bug audit."""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integration import history, reports, server
from integration.preparation import reduce_data_for_figma


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DATA_DIR", tmp_path / "history")


def figma_export():
    return {"nodes": [{"id": "button", "name": "Button", "normalizedCenter": [0.5, 0.5]}]}


def simulation_result():
    return {"results": {"episode_0": [
        {"time": 0.01, "IFtip_pos": [0.1, 0.2, 0.3], "muscle_cost": 0.1,
         "contact_information": 1}
    ]}}


def test_deleted_session_ids_are_never_reassigned_to_new_sessions():
    history.add_session("First")
    history.add_session("Deleted")
    history.delete_session("2")
    replacement = history.add_session("Replacement")["activeSessionId"]
    assert replacement == "3"
    history.delete_session("1")
    history.delete_session("3")
    assert history.add_session("After deleting everything")["activeSessionId"] == "4"


def test_delayed_results_cannot_be_recorded_in_a_recreated_session():
    history.add_session("Original")
    history.delete_session("1")
    history.add_session("Replacement")
    with pytest.raises(ValueError, match="no longer exists"):
        history.record_simulation_run(figma_export(), simulation_result(), session_id="1")
    assert history.get_formatted_history_for_ui() == []


def test_session_high_water_mark_is_migrated_from_existing_overview():
    files = history.get_session_filenames("7")
    history.save_json_file(history.SESSION_OVERVIEW_FILE, {
        "count": 1, "activeSessionId": "7", "sessions": [
            {"id": "7", "name": "Legacy", "total_run_counter": 0, "files": files}
        ]})
    history.delete_session("7")
    assert history.add_session()["activeSessionId"] == "8"


def test_bridge_echoes_resolved_session_and_chart_identity():
    history.add_session()
    history.record_simulation_run(figma_export(), simulation_result())

    async def scenario():
        bridge = server.BridgeServer()
        messages = []

        async def send(payload):
            messages.append(payload)

        await bridge.dispatch({"type": "GET_HISTORY_DATA"}, send)
        await bridge.dispatch({"type": "SAVE_SIMULATION_CHARTS", "session_id": "1",
                               "run_id": "1", "charts": {}}, send)
        await bridge.dispatch({"type": "GET_RUN_DIAGRAMS", "sessionId": "1",
                               "runId": "1", "runIndex": 0}, send)
        await bridge.dispatch({"type": "COMPARE_RUNS", "run_a_id": "1", "run_b_id": "1"}, send)
        assert messages[0]["session_id"] == "1"
        assert messages[1]["session_id"] == "1"
        assert messages[1]["run_id"] == "1"
        assert messages[2]["sessionId"] == "1"
        assert messages[3]["session_id"] == "1"
        assert messages[3]["run_a_id"] == messages[3]["run_b_id"] == "1"

    asyncio.run(scenario())


def test_downsampled_charts_preserve_actual_timestamps_and_duration():
    points = [dict(simulation_result()["results"]["episode_0"][0], time=time)
              for time in (0.006, 0.012, 0.018, 0.024, 0.030, 0.036)]
    chart = reduce_data_for_figma(points, max_points=4)["chart_data"]
    assert chart["times"] == [0.006, 0.018, 0.024, 0.036]
    assert chart["duration"] == 0.036
    assert chart["times"][1] != pytest.approx(chart["start_time"] + chart["dt"])


def test_simulation_uses_requesting_clients_session_instead_of_global_active_session():
    history.add_session("First client")
    history.add_session("Second client")

    async def scenario():
        messages = []

        async def runner(*args):
            return simulation_result()

        async def send(payload):
            messages.append(payload)

        bridge = server.BridgeServer(runner)
        await bridge.dispatch({"type": "START_SIM", "session_id": "1", "data": figma_export()}, send)
        assert messages[0]["data"]["session_id"] == "1"
        assert len(history.get_formatted_history_for_ui("1")) == 1
        assert history.get_formatted_history_for_ui("2") == []
        assert history.get_session_overview()["activeSessionId"] == "2"
        with pytest.raises(ValueError, match="no longer exists"):
            await bridge.dispatch({"type": "START_SIM", "session_id": "999", "data": figma_export()}, send)

    asyncio.run(scenario())


def test_rejected_delayed_charts_echo_identity_and_do_not_poison_new_session():
    history.add_session("Original")
    history.delete_session("1")
    replacement = history.add_session("Replacement")["activeSessionId"]
    history.record_simulation_run(figma_export(), simulation_result())

    async def scenario():
        bridge = server.BridgeServer()
        async with serve(bridge.handler, "127.0.0.1", 0) as websocket_server:
            port = websocket_server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                await client.send(json.dumps({"type": "SAVE_SIMULATION_CHARTS", "session_id": "1",
                                              "run_id": "1", "charts": {"trajectory": "old"}}))
                response = json.loads(await client.recv())
                assert response["type"] == "ERROR"
                assert response["request_type"] == "SAVE_SIMULATION_CHARTS"
                assert response["session_id"] == response["run_id"] == "1"
                assert response["retryable"] is False
        assert history.get_formatted_history_for_ui(replacement)[0]["chart_images"] == {}

    asyncio.run(scenario())


def test_chart_storage_failure_is_retryable_and_a_later_save_succeeds(monkeypatch):
    history.add_session()
    history.record_simulation_run(figma_export(), simulation_result())
    save = history.save_charts_to_history
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("No space left on device")
        return save(*args, **kwargs)

    monkeypatch.setattr(history, "save_charts_to_history", fail_once)

    async def scenario():
        bridge = server.BridgeServer()
        async with serve(bridge.handler, "127.0.0.1", 0) as websocket_server:
            port = websocket_server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                request = json.dumps({"type": "SAVE_SIMULATION_CHARTS", "session_id": "1",
                                      "run_id": "1", "charts": {"trajectory": "saved"}})
                await client.send(request)
                response = json.loads(await client.recv())
                assert response["type"] == "ERROR"
                assert response["retryable"] is True
                assert response["session_id"] == response["run_id"] == "1"
                await client.send(request)
                response = json.loads(await client.recv())
                assert response["type"] == "CHARTS_SAVED"
        assert history.get_formatted_history_for_ui()[0]["chart_images"] == {"trajectory": "saved"}

    asyncio.run(scenario())


def test_disconnecting_client_cancels_its_run_and_releases_simulation_lock():
    history.add_session()

    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()
        calls = 0
        running_task = None

        async def runner(episodes, visualization, progress):
            nonlocal calls, running_task
            calls += 1
            if calls == 1:
                running_task = asyncio.current_task()
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            return simulation_result()

        bridge = server.BridgeServer(runner)
        async with serve(bridge.handler, "127.0.0.1", 0) as websocket_server:
            port = websocket_server.sockets[0].getsockname()[1]
            try:
                async with connect(f"ws://127.0.0.1:{port}") as client:
                    await client.send(json.dumps({"type": "START_SIM", "data": figma_export()}))
                    await asyncio.wait_for(started.wait(), 2)
                await asyncio.wait_for(cancelled.wait(), 2)
                async with connect(f"ws://127.0.0.1:{port}") as client:
                    await client.send(json.dumps({"type": "START_SIM", "data": figma_export()}))
                    result = json.loads(await asyncio.wait_for(client.recv(), 2))
                    assert result["type"] == "RESULT"
                assert calls == 2
                assert len(history.get_formatted_history_for_ui()) == 1
            finally:
                # Keep the unfixed reproduction bounded rather than hanging on shutdown.
                if not cancelled.is_set() and running_task is not None:
                    running_task.cancel()
                    await asyncio.gather(running_task, return_exceptions=True)

    asyncio.run(scenario())


def test_disconnecting_client_reaps_real_subprocess(tmp_path, monkeypatch):
    script = tmp_path / "log2motion_visualization.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", tmp_path)
    history.add_session()

    async def scenario():
        started = asyncio.Event()
        processes = []
        spawn = asyncio.create_subprocess_exec

        async def track_process(*args, **kwargs):
            process = await spawn(*args, **kwargs)
            processes.append(process)
            started.set()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", track_process)
        bridge = server.BridgeServer()
        async with serve(bridge.handler, "127.0.0.1", 0) as websocket_server:
            port = websocket_server.sockets[0].getsockname()[1]
            try:
                async with connect(f"ws://127.0.0.1:{port}") as client:
                    await client.send(json.dumps({"type": "START_SIM", "data": figma_export()}))
                    await asyncio.wait_for(started.wait(), 5)
                await asyncio.wait_for(processes[0].wait(), 5)
                assert processes[0].returncode is not None
            finally:
                for process in processes:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()

    asyncio.run(scenario())


@pytest.mark.parametrize("report_kind", ["single", "comparison"])
def test_reports_paginate_long_selection_paths(report_kind):
    run = {"id": "1", "name": "Long path", "selected_elements": [
        {"id": str(index), "name": f"Button {index}: " + "Long but valid Figma element name " * 3}
        for index in range(90)], "metrics": {}}
    if report_kind == "single":
        pdf = reports.generate_single_run_pdf(run)
    else:
        pdf = reports.generate_compare_runs_pdf(run, run, history.compare_runs(run, run))
    assert pdf.startswith(b"%PDF-")
