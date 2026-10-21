"""Verify core policy replay, progress output, and decoded MP4 export."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def framed_result(stdout):
    start = stdout.rfind("---DATA_START---")
    if start < 0:
        raise AssertionError(f"CLI returned no JSON frame: {stdout[-4000:]}")
    end = stdout.find("---DATA_END---", start)
    if end < 0:
        raise AssertionError("CLI did not finish its JSON frame")
    return json.loads(stdout[start + len("---DATA_START---"):end].strip())


def main():
    import imageio.v2 as imageio

    with tempfile.TemporaryDirectory(prefix="log2motion-cli-smoke-") as directory:
        directory = Path(directory)
        source, video = directory / "actions.json", directory / "replay.mp4"
        tap = {"action_type": "DUAL_POINT", "yx_touch": [0.5, 0.5], "yx_lift": [0.5, 0.5]}
        source.write_text(json.dumps([tap, tap]), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(ROOT / "log2motion_visualization.py"),
             "--actions-file", str(source), "--video", str(video), "--video-size", "320", "180"],
            cwd=directory, capture_output=True, text=True, timeout=180,
        )
        if completed.returncode:
            raise AssertionError(f"CLI exited with {completed.returncode}:\n"
                                 f"{completed.stdout[-4000:]}\n{completed.stderr[-4000:]}")
        result = framed_result(completed.stdout)
        assert result["status"] == "success", result
        progress = re.findall(r"(?m)^---PROGRESS:(\d+)/(\d+)---$", completed.stdout)
        assert progress == [("1", "2"), ("2", "2")], progress
        assert list(result["results"]) == ["episode_0", "episode_1"]
        duration = 0.0
        for key, samples in result["results"].items():
            assert result["episodes"][key]["is_success"], result["episodes"][key]
            assert len(samples) > 1
            times = [point["time"] for point in samples]
            assert times[0] > 0 and all(a < b for a, b in zip(times, times[1:]))
            assert all(math.isfinite(point["muscle_cost"]) for point in samples)
            assert all(len(point["IFtip_pos"]) == 3 and
                       all(math.isfinite(value) for value in point["IFtip_pos"]) for point in samples)
            assert any(point["contact_information"] == 1 for point in samples)
            duration += times[-1]
        assert video.stat().st_size > 1000
        with imageio.get_reader(video) as reader:
            assert reader.get_data(0).shape == (180, 320, 3)
            frame_count = reader.count_frames()
            fps = reader.get_meta_data()["fps"]
            assert frame_count > 1 and fps > 0
            assert abs(frame_count / fps - duration) <= 1 / fps + 0.004
    print("Core CLI smoke passed: two policy taps, progress, finite trajectories, decoded MP4")


if __name__ == "__main__":
    main()
