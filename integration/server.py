"""Local Figma bridge. Start with ``python -m integration.server``."""

import asyncio
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from websockets.asyncio.server import serve

from . import history
from .preparation import reduce_data_for_figma
from .transformation import transform_figma_data

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = Path(os.environ.get("LOG2MOTION_ASSET_DIR", Path(__file__).parent / "assets"))
MAX_PAYLOAD_SIZE = 20 * 1024 * 1024
MAX_OUTPUT_SIZE = 64 * 1024 * 1024
SIMULATION_TIMEOUT = float(os.environ.get("LOG2MOTION_SIMULATION_TIMEOUT", "1800"))
ICONS = {
    "edit": "edit_icon.svg", "route": "route_icon.svg", "back": "back_icon.svg",
    "history": "history_icon.svg", "close": "close_icon.svg",
    "closed_menu": "closed_menu_icon.svg", "open_menu": "open_menu_icon.svg",
    "closed_card": "closed_card_icon.svg", "open_card": "open_card_icon.svg",
    "hover_closed_card": "closed_hover_icon.svg", "download": "download_icon_black.svg",
    "show_info": "show_info_icon.svg", "hide_info": "hide_info_icon.svg",
}


def encode_file(path):
    return base64.b64encode(path.read_bytes()).decode("ascii")


def parse_simulation_output(stdout, returncode=0, stderr=""):
    matches = re.findall(r"---DATA_START---\s*\n(.*?)\n---DATA_END---", stdout, re.DOTALL)
    result = None
    if matches:
        try:
            result = json.loads(matches[-1])
        except json.JSONDecodeError as exc:
            if not returncode:
                raise ValueError("Simulation returned invalid JSON.") from exc
    if isinstance(result, dict) and result.get("status") == "error":
        detail = result.get("message") or result.get("error") or stderr.strip()[-4000:] or "No error details were returned."
        raise ValueError(f"Simulation failed (exit code {returncode}): {detail}")
    if returncode:
        detail = stderr.strip()[-4000:] or "No error details were returned."
        raise ValueError(f"Simulation exited with code {returncode}: {detail}")
    if not matches:
        raise ValueError(f"Simulation returned no result payload. {stderr[-4000:]}")
    if not isinstance(result, dict) or not isinstance(result.get("results"), dict):
        raise ValueError("Simulation results must contain a results object.")
    if result.get("status", "success") != "success" or not result["results"]:
        raise ValueError("Simulation did not return any successful action trajectories.")
    return result


class BridgeServer:
    def __init__(self, runner=None):
        self.runner = runner or self.run_simulation
        self.simulation_lock = asyncio.Lock()

    async def run_simulation(self, episodes, visualization, progress):
        """Use the running environment, or an explicitly configured interpreter."""
        interpreter = os.environ.get("LOG2MOTION_PYTHON", sys.executable)
        with tempfile.TemporaryDirectory(prefix="log2motion-") as directory:
            actions_file = Path(directory) / "actions.json"
            actions_file.write_text(json.dumps(episodes), encoding="utf-8")
            command = [interpreter, "-u", str(ROOT / "log2motion_visualization.py"),
                       "--actions-file", str(actions_file)]
            if visualization:
                command.append("--visualization")
            process = await asyncio.create_subprocess_exec(
                *command, cwd=ROOT, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=MAX_OUTPUT_SIZE,
            )
            stdout_lines = []
            stdout_size = 0
            stderr_tail = bytearray()

            async def read_stdout():
                nonlocal stdout_size
                async for raw_line in process.stdout:
                    stdout_size += len(raw_line)
                    if stdout_size > MAX_OUTPUT_SIZE:
                        raise ValueError("Simulation output exceeded the 64 MB limit.")
                    line = raw_line.decode("utf-8", errors="replace").replace("\r\n", "\n")
                    stdout_lines.append(line)
                    match = re.fullmatch(r"---PROGRESS:(\d+)/(\d+)---", line.strip())
                    if match:
                        await progress(int(match[1]), int(match[2]))

            async def read_stderr():
                while chunk := await process.stderr.read(65536):
                    stderr_tail.extend(chunk)
                    del stderr_tail[:-8000]

            tasks = [asyncio.create_task(read_stdout()), asyncio.create_task(read_stderr())]
            try:
                async with asyncio.timeout(SIMULATION_TIMEOUT):
                    await asyncio.gather(*tasks)
                    await process.wait()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
            return parse_simulation_output("".join(stdout_lines), process.returncode,
                                           stderr_tail.decode("utf-8", errors="replace"))

    async def dispatch(self, msg, send):
        if not isinstance(msg, dict):
            raise ValueError("Messages must be JSON objects.")
        kind = msg.get("type")
        if kind == "GET_ASSETS":
            payload = {f"svg_{key}_icon": encode_file(ASSET_DIR / filename)
                       for key, filename in ICONS.items() if (ASSET_DIR / filename).is_file()}
            video = ASSET_DIR / "gecko_grey_light.webm"
            if video.is_file():
                payload["video"] = encode_file(video)
            await send({"type": "ASSETS", **payload})
        elif kind == "GET_TUTORIAL_VIDEO":
            video = ASSET_DIR / "Plugin_Tutorial.mp4"
            if not video.is_file():
                raise ValueError("Set LOG2MOTION_ASSET_DIR to the original Interface/images directory to load the tutorial.")
            await send({"type": "TUTORIAL_VIDEO", "video": encode_file(video)})
        elif kind == "GET_SESSION_OVERVIEW":
            await send({"type": "SESSION_OVERVIEW", "session_overview": history.get_session_overview()})
        elif kind in ("SELECT_SESSION", "ADD_SESSION", "DELETE_SESSION"):
            if kind == "SELECT_SESSION":
                overview = history.set_active_session(str(msg.get("session_id")))
                response = "SESSION_CHANGED"
            elif kind == "ADD_SESSION":
                overview = history.add_session(msg.get("name"))
                response = "SESSION_ADDED"
            else:
                overview = history.delete_session(str(msg.get("session_id")))
                response = "SESSION_DELETED"
            active_id = overview["activeSessionId"]
            await send({"type": response, "session_overview": overview,
                        "history_data": history.get_formatted_history_for_ui(active_id),
                        "session_id": active_id})
        elif kind == "GET_HISTORY_DATA":
            await send({"type": "HISTORY_DATA", "data": history.get_formatted_history_for_ui(msg.get("session_id")),
                        "session_id": msg.get("session_id")})
        elif kind == "COMPARE_RUNS":
            await send({"type": "COMPARISON_RESULT", "data": history.compare_runs(
                msg.get("run_a_id"), msg.get("run_b_id"), msg.get("session_id"))})
        elif kind == "SAVE_SIMULATION_CHARTS":
            charts = msg.get("charts", {})
            if not isinstance(charts, dict):
                raise ValueError("Chart images must be an object.")
            updated = history.save_charts_to_history(msg.get("session_id"), msg.get("run_id"), charts)
            await send({"type": "CHARTS_SAVED", "history_data": updated})
        elif kind == "GET_RUN_DIAGRAMS":
            runs = history.get_formatted_history_for_ui(msg.get("sessionId"))
            run = next((run for run in runs if str(run["id"]) == str(msg.get("runId"))), {})
            await send({"type": "SINGLE_RUN_DIAGRAMS", "runId": msg.get("runId"),
                        "runIndex": msg.get("runIndex"), "images": run.get("chart_images", {})})
        elif kind in ("DOWNLOAD_SINGLE_RUN_PDF", "DOWNLOAD_COMPARE_RUNS_PDF"):
            from . import reports
            runs = history.get_formatted_history_for_ui(msg.get("sessionId") or msg.get("session_id"))
            def find_run(key, alias):
                run_id = msg.get(key, msg.get(alias))
                run = next((run for run in runs if str(run["id"]) == str(run_id)), None)
                if run is None:
                    raise ValueError("The requested run is no longer in history.")
                return run
            if kind == "DOWNLOAD_SINGLE_RUN_PDF":
                run = find_run("runId", "run_id")
                pdf = await asyncio.to_thread(reports.generate_single_run_pdf, run)
                filename = f"Single_Run_Result_{run['id']}.pdf"
                response = "SINGLE_RUN_PDF"
            else:
                run_a, run_b = find_run("runIdA", "run_id_a"), find_run("runIdB", "run_id_b")
                diff = history.compare_runs(run_a, run_b)
                pdf = await asyncio.to_thread(reports.generate_compare_runs_pdf, run_a, run_b, diff)
                filename = f"Run_Comparison_{run_a['id']}_vs_{run_b['id']}.pdf"
                response = "COMPARE_RUNS_PDF"
            await send({"type": response, "filename": filename,
                        "pdf_base64": base64.b64encode(pdf).decode("ascii")})
        elif kind == "START_SIM":
            if not isinstance(msg.get("visualization", False), bool):
                raise ValueError("visualization must be true or false.")
            data = msg.get("data")
            episodes = transform_figma_data(data)
            session_id = history.get_session_overview().get("activeSessionId")
            if not session_id:
                raise ValueError("Create or select a session before starting a simulation.")
            async def progress(current, total):
                await send({"type": "SIM_PROGRESS", "current": current, "total": total})
            async with self.simulation_lock:
                results = await self.runner(episodes, msg.get("visualization", False), progress)
                reduced = [{"episode_id": episode_id,
                            "outcome": results.get("episodes", {}).get(episode_id),
                            "data": reduce_data_for_figma(trajectory)["chart_data"]}
                           for episode_id, trajectory in results["results"].items()]
                run_id = history.record_simulation_run(data, results,
                    timestamp=datetime.now().astimezone().isoformat(timespec="seconds"), session_id=session_id)
            await send({"type": "RESULT", "data": {"episodes": reduced, "run_id": run_id,
                                                       "outcome_summary": history.summarize_outcomes(results),
                                                       "session_id": session_id}})
        else:
            raise ValueError(f"Unsupported message type: {kind!r}")

    async def handler(self, websocket):
        async def send(payload):
            await websocket.send(json.dumps(payload, allow_nan=False))
        async for message in websocket:
            kind = None
            try:
                msg = json.loads(message)
                kind = msg.get("type") if isinstance(msg, dict) else None
                await self.dispatch(msg, send)
            except (ValueError, TypeError, KeyError, OSError, TimeoutError) as exc:
                error_type = "SIM_ERROR" if kind == "START_SIM" else "PDF_ERROR" if kind in (
                    "DOWNLOAD_SINGLE_RUN_PDF", "DOWNLOAD_COMPARE_RUNS_PDF") else "ERROR"
                error = str(exc) or (f"Simulation timed out after {SIMULATION_TIMEOUT:g} seconds."
                                     if isinstance(exc, TimeoutError) else type(exc).__name__)
                await send({"type": error_type, "status": "error", "error": error, "message": error})


async def main():
    host = os.environ.get("LOG2MOTION_HOST", "127.0.0.1")
    port = int(os.environ.get("LOG2MOTION_PORT", "8080"))
    bridge = BridgeServer()
    async with serve(bridge.handler, host, port, max_size=MAX_PAYLOAD_SIZE):
        print(f"Figma bridge listening on ws://{host}:{port}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
