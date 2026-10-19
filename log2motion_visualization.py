"""Command-line simulation protocol used by the Log2Motion Figma bridge."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def _boolean(value):
    if isinstance(value, bool):
        return value
    if value.lower() in ("1", "true", "yes", "on"):
        return True
    if value.lower() in ("0", "false", "no", "off"):
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actions-file", type=Path, required=True, help="JSON list of normalized touch actions")
    parser.add_argument("--visualization", nargs="?", const=True, default=False, type=_boolean)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--button-size", type=float, default=0.005)
    parser.add_argument("--video", type=Path, help="optional MP4 of the simulated scene")
    parser.add_argument("--video-size", nargs=2, type=int, default=(1280, 720),
                        metavar=("WIDTH", "HEIGHT"), help="positive even MP4 dimensions (default: 1280 720)")
    parser.add_argument("--no-rest", action="store_true", help="skip movements to the resting target")
    args = parser.parse_args(argv)
    if any(value <= 0 or value % 2 for value in args.video_size):
        parser.error("--video-size requires positive even width and height")
    env = None
    try:
        from environment.log2motion import Log2Motion, validate_actions
        from environment.log2motion_env import Log2MotionEnv
        actions = validate_actions(json.loads(args.actions_file.read_text(encoding="utf-8-sig")))
        root = Path(__file__).resolve().parent
        env = Log2MotionEnv(seed=args.seed)
        replay = Log2Motion(env, {"swipe": root / "policies/swipe.zip",
                                 "default": root / "policies/normal.zip"},
                            show_screen=args.visualization, button_size=args.button_size,
                            collect_frames=args.video is not None, frame_size=tuple(args.video_size))
        replay.start_render()
        results = replay.replay(actions, resting_position=not args.no_rest,
                                progress_callback=lambda n, total: print(f"---PROGRESS:{n}/{total}---", flush=True))
        if args.video is not None:
            args.video.parent.mkdir(parents=True, exist_ok=True)
            replay.save_video_from_episodes(args.video)
        output = {"status": "success", "results": results, "episodes": replay.episode_info}
        code = 0
    except Exception as exc:
        output = {"status": "error", "message": str(exc), "error": str(exc)}
        code = 1
    finally:
        if env is not None:
            env.close()
    print("---DATA_START---", flush=True)
    print(json.dumps(output, allow_nan=False), flush=True)
    print("---DATA_END---", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
