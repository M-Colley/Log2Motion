"""Figma contract regression coverage, independent of MuJoCo/Android."""

import asyncio
import base64
import json
from pathlib import Path
from io import BytesIO

import pytest
from PIL import Image
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integration import history, reports, server
from integration.preparation import reduce_data_for_figma
from integration.transformation import transform_figma_data


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DATA_DIR", tmp_path / "history")


def export(y=0.2, x=0.3):
    return {"nodes": [{"id": "button", "name": "Button <A> & B", "type": "RECTANGLE",
                       "normalizedCenter": [y, x], "x": 10, "y": 20, "width": 20, "height": 40}]}


def results(count=4):
    return {"status": "success", "results": {"0": [
        {"time": index * 0.01, "IFtip_pos": [index * 0.001, 0.2, 0.3],
         "muscle_cost": 0.1, "contact_information": index == 2}
        for index in range(count)]}}


def test_exports_distinct_positions_and_repeated_clicks():
    data = export()
    data["nodes"] += [dict(data["nodes"][0], id="other", normalizedCenter=[0.8, 0.9]), data["nodes"][0]]
    assert [action["yx_touch"] for action in transform_figma_data(data)] == [(0.2, 0.3), (0.8, 0.9), (0.2, 0.3)]


def test_normalizes_legacy_export_with_nested_ancestor_geometry():
    data = {"nodes": [{"id": "button", "type": "RECTANGLE", "parentId": "group", "x": 10, "y": 20,
                       "width": 20, "height": 40}], "ancestors": [
        {"id": "screen", "type": "FRAME", "width": 100, "height": 200, "x": 700, "y": 900},
        {"id": "group", "type": "GROUP", "parentId": "screen", "x": 10, "y": 20}]}
    assert transform_figma_data(data)[0]["yx_touch"] == (0.3, 0.3)


@pytest.mark.parametrize("data", [{}, {"nodes": []}, {"nodes": [{"id": "old-plugin"}]}, export(float("nan")), export(1.2)])
def test_rejects_invalid_exports_instead_of_center_taps(data):
    with pytest.raises(ValueError):
        transform_figma_data(data)


@pytest.mark.parametrize("count,limit", [(1, 300), (299, 300), (301, 300), (599, 300), (10000, 300), (10, 1)])
def test_downsampling_is_bounded_and_preserves_contacts(count, limit):
    data = results(count)["results"]["0"]
    reduced = reduce_data_for_figma(data, max_points=limit)["chart_data"]
    assert len(reduced["trajectory"]) == min(count, limit)
    assert reduced["trajectory"][0] == [0, 0.2, 0.3]
    if limit > 1:
        assert reduced["trajectory"][-1] == [round((count - 1) * 0.001, 4), 0.2, 0.3]
    if count > 2:
        assert reduced["contact_indices"]


def test_chart_contacts_match_button_contact_metrics_not_other_screen_touches():
    samples = results(3)["results"]["0"]
    for point, contact in zip(samples, [0, 1, 2]):
        point["contact_information"] = contact
    assert reduce_data_for_figma(samples)["chart_data"]["contact_indices"] == [1]
    assert history.extract_summary({"results": {"0": samples}})["total_contacts"] == 1


@pytest.mark.parametrize("outcomes,status,successful,failed,timed_out,unknown", [
    ([{"is_success": True, "truncated": False}], "success", 1, 0, 0, 0),
    ([{"is_success": False, "truncated": True}], "failed", 0, 1, 1, 0),
    ([{"is_success": False, "truncated": False}], "failed", 0, 1, 0, 0),
    ([{"is_success": True}, {"is_success": False, "truncated": True}], "partial_failure", 1, 1, 1, 0),
    ([None], "unknown", 0, 0, 0, 1),
])
def test_summary_distinguishes_process_completion_from_gesture_outcomes(outcomes, status, successful, failed, timed_out, unknown):
    simulation = {"status": "success", "results": {str(index): results()["results"]["0"]
                  for index in range(len(outcomes))},
                  "episodes": {str(index): outcome for index, outcome in enumerate(outcomes)}}
    summary = history.extract_summary(simulation)
    assert summary["outcome_summary"] == {"status": status, "total": len(outcomes),
        "successful": successful, "failed": failed, "timed_out": timed_out, "unknown": unknown}
    for index, outcome in enumerate(outcomes):
        assert summary["episodes"][str(index)]["outcome"] == outcome


def test_bridge_retains_timed_out_gesture_outcome_charts_and_history():
    history.add_session()
    simulation = results()
    simulation["results"]["1"] = results()["results"]["0"]
    success = {"is_success": True, "terminated": True, "truncated": False,
               "phases": [{"is_success": True, "duration": 0.35}]}
    timeout = {"is_success": False, "terminated": False, "truncated": True,
               "phases": [{"is_success": False, "truncated": True, "duration": 2.0}]}
    simulation["episodes"] = {"0": success, "1": timeout}
    data = export()
    data["nodes"].append(dict(data["nodes"][0]))
    async def exercise():
        messages = []
        async def runner(episodes, visualization, progress):
            assert len(episodes) == 2
            return simulation
        async def send(message):
            messages.append(message)
        await server.BridgeServer(runner).dispatch({"type": "START_SIM", "data": data}, send)
        return messages
    messages = asyncio.run(exercise())
    assert len(messages) == 1 and messages[0]["type"] == "RESULT"
    result = messages[0]["data"]
    assert result["episodes"][0]["outcome"] == success
    assert result["episodes"][1]["outcome"] == timeout
    assert result["episodes"][1]["data"]["trajectory"]
    assert result["outcome_summary"]["status"] == "partial_failure"
    assert result["outcome_summary"]["timed_out"] == 1
    run = history.get_formatted_history_for_ui()[0]
    assert run["metrics"]["episodes"]["1"]["outcome"] == timeout
    assert run["metrics"]["outcome_summary"] == result["outcome_summary"]
    assert "1 failed (1 timed out)" in reports.outcome_description(run["metrics"])
    assert reports.generate_single_run_pdf(run).startswith(b"%PDF-")


def test_missing_legacy_outcomes_are_unknown_and_failed_run_has_lower_success_rate():
    assert history.extract_summary(results())["outcome_summary"]["status"] == "unknown"
    assert reports.outcome_success_rate({"outcome_summary": {"total": 1, "successful": 0, "unknown": 0}}) == 0
    assert reports.outcome_success_rate({"outcome_summary": {"total": 1, "successful": 1, "unknown": 0}}) == 1
    assert reports.outcome_success_rate({}) is None


def test_downsampling_does_not_promote_other_screen_contact_to_button_contact():
    samples = results(1000)["results"]["0"]
    for point in samples:
        point["contact_information"] = 2
    assert reduce_data_for_figma(samples, max_points=300)["chart_data"]["contact_indices"] == []


def test_history_keeps_unique_ids_after_retention_limit_and_saves_correct_charts():
    history.add_session("Session A")
    for index in range(12):
        assert history.record_simulation_run(export(), results()) == str(index + 1)
    runs = history.get_formatted_history_for_ui()
    assert [run["id"] for run in runs] == [str(index) for index in range(3, 13)]
    history.save_charts_to_history("1", "4", {"trajectory": "fixture"})
    runs = history.get_formatted_history_for_ui("1")
    assert next(run for run in runs if run["id"] == "4")["chart_images"] == {"trajectory": "fixture"}
    assert runs[-1]["chart_images"] == {}
    with pytest.raises(ValueError, match="no longer"):
        history.save_charts_to_history("1", "1", {})


def test_repeated_clicks_remain_in_saved_history():
    history.add_session()
    data = export()
    data["nodes"].append(dict(data["nodes"][0]))
    history.record_simulation_run(data, results())
    assert len(history.get_formatted_history_for_ui()[0]["selected_elements"]) == 2


def run_with_clicks(*node_ids):
    return {"selected_elements": [{"id": node_id, "name": node_id, "number": index}
                                  for index, node_id in enumerate(node_ids, 1)]}


def test_comparison_reports_removed_repeated_click_without_false_reorder():
    diff = history.compare_runs(run_with_clicks("A", "B", "A"), run_with_clicks("A", "B"))
    assert diff["removed_elements"] == [{"id": "A", "name": "A", "number": 3, "occurrence": 2}]
    assert diff["added_elements"] == []
    assert diff["reordered_elements"] == []
    assert diff["order_changed"] is False


def test_comparison_reports_added_and_reordered_repeated_click_occurrences():
    diff = history.compare_runs(run_with_clicks("A", "B"), run_with_clicks("A", "B", "A"))
    assert diff["added_elements"] == [{"id": "A", "name": "A", "number": 3, "occurrence": 2}]
    assert diff["removed_elements"] == []
    assert diff["order_changed"] is False
    diff = history.compare_runs(run_with_clicks("A", "B", "A"), run_with_clicks("A", "A", "B"))
    assert {(item["id"], item["occurrence"], item["from_number"], item["to_number"])
            for item in diff["reordered_elements"]} == {("A", 2, 3, 2), ("B", 1, 2, 3)}
    assert diff["removed_elements"] == diff["added_elements"] == []


def test_comparison_reports_normalized_movement_from_ancestor_transform():
    previous = {"selected_elements": export(0.2, 0.3)["nodes"]}
    current = {"selected_elements": export(0.8, 0.3)["nodes"]}
    diff = history.compare_runs(previous, current)
    assert diff["modified_elements"] == [{
        "id": "button", "name": "Button <A> & B", "number": 1, "occurrence": 1,
        "property": "Screen position", "old_v1": 0.3, "old_v2": 0.2,
        "new_v1": 0.3, "new_v2": 0.8,
    }]
    assert reports.generate_compare_runs_pdf(previous, current, diff).startswith(b"%PDF-")


def test_sessions_do_not_follow_working_directory(tmp_path, monkeypatch):
    other_directory = tmp_path / "elsewhere"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)
    overview = history.add_session("Portable")
    assert overview["activeSessionId"] == "1"
    assert history.DATA_DIR.joinpath("Session_overview.json").is_file()
    assert not other_directory.joinpath("Session_overview.json").exists()
    with pytest.raises(ValueError):
        history.get_session_filenames("../outside")
    with pytest.raises(ValueError):
        history.save_json_file("../outside.json", {})


def test_simulation_history_stays_in_original_session():
    history.add_session("First")
    history.add_session("Second")
    history.record_simulation_run(export(), results(), session_id="1")
    assert len(history.get_formatted_history_for_ui("1")) == 1
    assert history.get_formatted_history_for_ui("2") == []
    assert history.get_session_overview()["activeSessionId"] == "2"


def test_session_deletion_removes_only_its_files():
    history.add_session("First")
    history.add_session("Second")
    overview = history.delete_session("1")
    assert overview["activeSessionId"] == "2"
    assert not history.DATA_DIR.joinpath("CHANGE_HISTORY_session_1.json").exists()
    assert history.DATA_DIR.joinpath("CHANGE_HISTORY_session_2.json").is_file()


def test_report_uses_real_timestamp_and_does_not_require_external_fonts():
    history.add_session()
    history.record_simulation_run(export(), results(), timestamp="2026-10-19T12:34:56+02:00")
    run = history.get_formatted_history_for_ui()[0]
    assert reports.run_date_time(run) == ("19.10.2026", "12:34:56")
    assert reports.generate_single_run_pdf(run).startswith(b"%PDF-")
    assert reports.generate_compare_runs_pdf(run, run, history.compare_runs(run, run)).startswith(b"%PDF-")


def test_pdf_reports_support_chart_snapshots_and_modified_geometry():
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, format="PNG")
    image = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    history.add_session()
    history.record_simulation_run(export(), results())
    history.save_charts_to_history("1", "1", dict.fromkeys(("muscleCost", "trajectory", "contactEvents"), image))
    first = history.get_formatted_history_for_ui()[0]
    changed = export()
    changed["nodes"][0].update(x=20, y=30, width=25, height=50)
    history.record_simulation_run(changed, results())
    second = history.get_formatted_history_for_ui()[1]
    diff = history.compare_runs(first, second)
    assert len(diff["modified_elements"]) == 2
    assert reports.generate_single_run_pdf(first).startswith(b"%PDF-")
    assert reports.generate_compare_runs_pdf(first, second, diff).startswith(b"%PDF-")


@pytest.mark.parametrize("stdout,code", [("", 0), ('---DATA_START---\n{}\n---DATA_END---', 0),
    ('---DATA_START---\ninvalid\n---DATA_END---', 0),
    ('---DATA_START---\n{"results":{}}\n---DATA_END---', 1)])
def test_rejects_missing_or_failed_process_results(stdout, code):
    with pytest.raises(ValueError):
        server.parse_simulation_output(stdout, code, "diagnostic")


@pytest.mark.parametrize("code,stderr", [(0, ""), (1, ""), (1, "Nonfatal import warning")])
@pytest.mark.parametrize("detail_field", ["message", "error"])
def test_preserves_framed_simulation_error_message(code, stderr, detail_field):
    stdout = "---DATA_START---\n" + json.dumps({"status": "error", detail_field: "Missing policy: normal.zip"}) + "\n---DATA_END---"
    with pytest.raises(ValueError, match="Missing policy: normal.zip"):
        server.parse_simulation_output(stdout, code, stderr)


def test_success_payload_cannot_hide_nonzero_process_exit():
    stdout = "---DATA_START---\n" + json.dumps(results()) + "\n---DATA_END---"
    with pytest.raises(ValueError, match="exited with code 1"):
        server.parse_simulation_output(stdout, 1, "")


def test_websocket_preserves_real_subprocess_framed_error(tmp_path, monkeypatch):
    script = tmp_path / "log2motion_visualization.py"
    script.write_text('''import sys
print('---DATA_START---')
print('{"status":"error","message":"Missing policy: normal.zip"}')
print('---DATA_END---', flush=True)
sys.exit(1)
''', encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", tmp_path)
    history.add_session()
    async def exercise():
        bridge = server.BridgeServer()
        async with serve(bridge.handler, "127.0.0.1", 0) as websocket_server:
            port = websocket_server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                await client.send(json.dumps({"type": "START_SIM", "data": export()}))
                response = json.loads(await client.recv())
                assert response["type"] == "SIM_ERROR"
                assert "Missing policy: normal.zip" in response["error"]
    asyncio.run(exercise())


def test_parses_crlf_process_output():
    payload = json.dumps(results())
    assert server.parse_simulation_output(f"log\r\n---DATA_START---\r\n{payload}\r\n---DATA_END---\r\n") == results()


def test_full_websocket_contract_handles_errors_and_returns_history():
    async def exercise():
        received_actions = []
        async def runner(episodes, visualization, progress):
            received_actions.extend(episodes)
            assert visualization is False
            await progress(1, len(episodes))
            return results()
        bridge = server.BridgeServer(runner)
        async with serve(bridge.handler, "127.0.0.1", 0) as websocket_server:
            port = websocket_server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                await client.send("not json")
                assert json.loads(await client.recv())["type"] == "ERROR"
                await client.send(json.dumps({"type": "ADD_SESSION", "name": "Contract"}))
                assert json.loads(await client.recv())["type"] == "SESSION_ADDED"
                await client.send(json.dumps({"type": "START_SIM", "data": {"nodes": []}}))
                assert json.loads(await client.recv())["type"] == "SIM_ERROR"
                await client.send(json.dumps({"type": "START_SIM", "data": export()}))
                assert json.loads(await client.recv()) == {"type": "SIM_PROGRESS", "current": 1, "total": 1}
                result = json.loads(await client.recv())
                assert result["type"] == "RESULT"
                assert result["data"]["run_id"] == "1"
                assert result["data"]["episodes"][0]["data"]["contact_indices"] == [2]
                await client.send(json.dumps({"type": "GET_HISTORY_DATA"}))
                assert json.loads(await client.recv())["data"][0]["metrics"]["total_contacts"] == 1
                await client.send(json.dumps({"type": "DOWNLOAD_SINGLE_RUN_PDF", "sessionId": "1", "runId": "1"}))
                pdf = json.loads(await client.recv())
                assert pdf["type"] == "SINGLE_RUN_PDF"
                assert base64.b64decode(pdf["pdf_base64"]).startswith(b"%PDF-")
        assert received_actions[0]["yx_touch"] == (0.2, 0.3)
    asyncio.run(exercise())


def test_real_subprocess_streams_progress_and_uses_json_file(tmp_path, monkeypatch):
    script = tmp_path / "log2motion_visualization.py"
    script.write_text('''import json, sys
from pathlib import Path
actions = json.loads(Path(sys.argv[sys.argv.index("--actions-file") + 1]).read_text())
assert actions[0]["yx_touch"] == [0.2, 0.3]
assert "--visualization" in sys.argv
assert "--no-rest" not in sys.argv
print("---PROGRESS:1/1---", flush=True)
print("---DATA_START---")
print(''' + repr(json.dumps(results())) + ''')
print("---DATA_END---", flush=True)
''', encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", tmp_path)
    progress_messages = []
    async def exercise():
        async def progress(current, total):
            progress_messages.append((current, total))
        return await server.BridgeServer().run_simulation(transform_figma_data(export()), True, progress)
    assert asyncio.run(exercise()) == results()
    assert progress_messages == [(1, 1)]


def test_subprocess_times_out_and_is_reaped(tmp_path, monkeypatch):
    script = tmp_path / "log2motion_visualization.py"
    script.write_text("import time; time.sleep(30)", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "SIMULATION_TIMEOUT", 0.05)
    async def progress(*args):
        pass
    with pytest.raises(TimeoutError):
        asyncio.run(server.BridgeServer().run_simulation([], False, progress))
