"""Exercise the public CLI, bundled policy archives, and encoded video."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TAP = {"action_type": "ActionType.DUAL_POINT", "yx_touch": [0.5, 0.5],
       "yx_lift": [0.5, 0.5], "time": []}


def parse_simulation_output(stdout):
    """Read the final framed JSON object emitted by the standalone CLI."""
    start_marker, end_marker = "---DATA_START---", "---DATA_END---"
    start = stdout.rfind(start_marker)
    assert start >= 0, stdout
    end = stdout.find(end_marker, start)
    assert end >= 0, stdout
    payload = json.loads(stdout[start + len(start_marker):end].strip())
    assert isinstance(payload, dict)
    return payload


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
    assert completed.returncode == 0, completed.stderr or completed.stdout
    output = parse_simulation_output(completed.stdout)
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
    output = parse_simulation_output(completed.stdout)
    assert output["status"] == "error"
    assert output["message"]
