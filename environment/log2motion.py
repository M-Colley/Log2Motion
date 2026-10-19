"""Replay normalized touch logs with the bundled PPO muscle policies."""
from __future__ import annotations

from pathlib import Path
from numbers import Real
import time

import numpy as np
from stable_baselines3 import PPO

_SWIPE_DISTANCE_THRESHOLD = 0.04
_HOME_BUTTON = (0.5, 0.97)
_RETURN_BUTTON = (0.2, 0.97)
_ACTION_TYPES = {"DUAL_POINT", "PRESS_BACK", "PRESS_HOME", "STATUS_TASK_COMPLETE"}


def validate_actions(actions):
    """Validate the JSON touch-log format shared with the Figma bridge."""
    if not isinstance(actions, list) or not actions:
        raise ValueError("Actions must be a non-empty JSON list")
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ValueError(f"Action {index} must be an object")
        kind = str(action.get("action_type", "")).removeprefix("ActionType.")
        if kind not in _ACTION_TYPES:
            raise ValueError(f"Action {index}: unsupported action_type {kind!r}")
        if kind == "DUAL_POINT":
            for key in ("yx_touch", "yx_lift"):
                values = action.get(key)
                if not isinstance(values, (list, tuple)) or len(values) != 2 or not all(
                    isinstance(value, Real) and not isinstance(value, bool) for value in values
                ):
                    raise ValueError(f"Action {index}: {key} must contain two numeric coordinates")
                try:
                    point = np.asarray(values, dtype=float)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Action {index}: {key} must be [y, x]") from exc
                if point.shape != (2,) or not np.isfinite(point).all() or np.any((point < 0) | (point > 1)):
                    raise ValueError(f"Action {index}: {key} must contain two coordinates in [0, 1]")
    return actions


class Log2Motion:
    def __init__(self, env, policy_paths_dict, show_screen=True, button_size=0.005,
                 collect_frames=False, deterministic=True, frame_size=(1280, 720)):
        self.env = env.unwrapped
        # Old archives pickle Python-version-specific training schedules.
        # Inference needs their weights and spaces, not those schedules.
        inference_objects = {"learning_rate": 0.0, "lr_schedule": lambda _: 0.0,
                             "clip_range": lambda _: 0.2}
        self.policies = {key: PPO.load(path, env=self.env, device="cpu", custom_objects=inference_objects)
                         for key, path in policy_paths_dict.items()}
        if not {"default", "swipe"}.issubset(self.policies):
            raise ValueError("Both default and swipe policies are required")
        self.policy_keys = self.policies.keys()
        self.button_size = button_size
        self.show_screen = show_screen
        self.collect_frames = collect_frames
        self.deterministic = deterministic
        self.frame_size = frame_size
        self.frame_stride = max(1, round(1 / (30 * self.env.dt)))
        self._video_elapsed = 0.0
        self._video_next = 0.0
        self.frames = {}
        self.results = {}
        self.episode_counter = 0
        self.show_marker = True
        self.episode_info = {}

    def start_render(self, show_marker=True):
        self.show_marker = show_marker
        self.env.change_button_size(self.button_size)
        self.env.reset(reset_qpos=self.env.start_qpos_data["return_qpos_button"])
        self.results, self.frames, self.episode_info = {}, {}, {}
        self.episode_counter = 0
        self._video_elapsed = 0.0
        self._video_next = 0.0
        if self.show_screen:
            self.env.mj_render()
        self.env.update_screen(self.show_screen)

    def replay(self, action_list, multiple_episode=False, resting_position=True,
               button_size=None, progress_callback=None):
        validate_actions(action_list)
        if button_size is not None:
            self.env.change_button_size(button_size, if_reset=False)
        if multiple_episode:
            self._press(_HOME_BUTTON, resting_position)
        total = len(action_list)
        for index, action in enumerate(action_list, 1):
            kind = str(action["action_type"]).removeprefix("ActionType.")
            if kind == "PRESS_HOME":
                self._press(_HOME_BUTTON, resting_position)
            elif kind == "PRESS_BACK":
                self._press(_RETURN_BUTTON, resting_position)
            elif kind == "DUAL_POINT":
                start, end = action["yx_touch"], action["yx_lift"]
                if self._is_tap_action(start, end):
                    self._press((start[1], start[0]), resting_position)
                else:
                    first_episode = self.episode_counter
                    self._select_target(start[1], start[0], self.show_marker)
                    initial = self._do_interaction(self.policies["default"])
                    self.env.change_the_conaff(0)
                    try:
                        self._select_target(end[1], end[0], False)
                        outcome = self._do_interaction(self.policies["swipe"], swipe=True)
                        outcome = dict(outcome, is_success=initial["is_success"] and outcome["is_success"])
                    finally:
                        self.env.change_the_conaff(1)
                    if resting_position:
                        self._move_to_resting_position_with_time_interrupt(self.policies["default"], swipe=True)
                    self._group_episodes(first_episode, outcome)
            elif kind == "STATUS_TASK_COMPLETE" and resting_position:
                self._move_to_resting_position_with_time_interrupt(self.policies["default"])
            if progress_callback is not None:
                progress_callback(index, total)
            if kind == "STATUS_TASK_COMPLETE":
                break
        return self.results

    def _press(self, target, move_rest):
        first_episode = self.episode_counter
        self._select_target(*target, show_marker=self.show_marker)
        outcome = self._do_interaction(self.policies["default"])
        if outcome["is_success"]:
            self._perform_touch(target)
        if move_rest:
            self._move_to_resting_position_with_time_interrupt(self.policies["default"])
        self._group_episodes(first_episode, outcome)

    def _group_episodes(self, first_episode, outcome):
        """Keep one result per gesture, including its release/rest trajectory."""
        combined, frames, phases = [], [], []
        elapsed = 0.0
        for number in range(first_episode, self.episode_counter):
            key = f"episode_{number}"
            samples = self.results.pop(key)
            combined.extend(dict(sample, time=elapsed + sample["time"]) for sample in samples)
            duration = samples[-1]["time"] if samples else 0.0
            elapsed += duration
            phases.append(dict(self.episode_info.pop(key), duration=duration))
            if key in self.frames:
                frames.extend(self.frames.pop(key)["frames"])
        key = f"episode_{first_episode}"
        self.results[key] = combined
        self.episode_info[key] = dict(outcome, phases=phases)
        if self.collect_frames:
            self.frames[key] = {"frames": frames, "time": elapsed}
        self.episode_counter = first_episode + 1

    def _perform_touch(self, pos):
        if self.env.use_android:
            height, width = self.env.screen_resolution
            x, y = int(pos[0] * (width - 1)), int(pos[1] * (height - 1))
            self.env.call_screen([[x, y, True, 0]])
            self.env.call_screen([[x, y, False, 0]])
            time.sleep(0.5)
            self.env.update_screen(self.show_screen)

    def _select_target(self, norm_x, norm_y, show_marker=True):
        target = self.env.find_closest_button(self.env.grid, norm_x, norm_y)
        self.env.touch_action(target, show_marker=show_marker)

    def _capture(self):
        self.env.update_screen(self.show_screen)
        return self.env.sim.renderer.render_offscreen(
            width=self.frame_size[0], height=self.frame_size[1], camera_id="smartphone_view")

    @staticmethod
    def _sample(obs):
        return {
            "IFtip_pos": np.asarray(obs["IFtip_pos"]).reshape(3).tolist(),
            "time": float(np.asarray(obs["time"]).item()),
            "muscle_cost": float(np.asarray(obs["muscle_cost"]).item()),
            "contact_information": int(np.asarray(obs["contact_information"]).item()),
        }

    def _run(self, policy, max_duration=None, swipe=False):
        observation, _ = self.env.reset()
        samples, frames = [], []
        terminated = truncated = False
        info = {}
        last_xy = None
        try:
            for iteration in range(self.env.max_timestep):
                if self.show_screen:
                    self.env.mj_render()
                action, _ = policy.predict(observation, deterministic=self.deterministic)
                observation, _, terminated, truncated, info = self.env.step(action)
                sample = self._sample(info["obs_dict"])
                if max_duration is not None and sample["contact_information"] == 1:
                    # The resting target is above the phone: reaching that
                    # virtual target is not an on-screen touch in telemetry.
                    sample["contact_information"] = 0
                samples.append(sample)
                if swipe and self.env.use_android:
                    norm_x, norm_y = self.env.normalize_iftip_position(sample["IFtip_pos"])
                    height, width = self.env.screen_resolution
                    last_xy = (int(norm_x * (width - 1)), int(norm_y * (height - 1)))
                    self.env.call_screen([[*last_xy, True, 0]])
                if self.collect_frames:
                    # Keep one continuous video clock across tap/drag/release
                    # phases, without duplicating frames at phase boundaries.
                    self._video_elapsed += self.env.dt
                    if self._video_elapsed + 1e-12 >= self._video_next:
                        frames.append(self._capture())
                        self._video_next += self.frame_stride * self.env.dt
                if max_duration is not None:
                    if self.env.time >= max_duration or truncated:
                        break
                    # Rest movements run to a fixed duration even if the rest
                    # target is touched; outside-of-phone ends immediately.
                    if terminated and self.env.outside_of_phone:
                        break
                elif terminated or truncated:
                    break
        finally:
            if last_xy is not None:
                self.env.call_screen([[*last_xy, False, 0]])
        self.episode_counter += 1
        key = f"episode_{self.episode_counter - 1}"
        # Failed and timed-out interactions still contain useful trajectory data.
        self.results[key] = samples
        self.episode_info[key] = {"is_success": bool(info.get("is_success", False)),
                                  "terminated": terminated, "truncated": truncated}
        if self.collect_frames:
            self.frames[key] = {"frames": frames, "time": self.env.time}
        return self.episode_info[key]

    def _do_interaction(self, policy, swipe=False):
        self.env.update_screen(self.show_screen)
        return self._run(policy, swipe=swipe)

    def _move_to_resting_position_with_time_interrupt(self, policy, random_threshold=0.35, swipe=False):
        target = "resting_position2" if swipe else "resting_position"
        self.env.touch_action(target, show_marker=False)
        return self._run(policy, max_duration=random_threshold)

    def save_video_from_episodes(self, output_path="output_video.mp4"):
        import imageio.v2 as imageio
        frames = [frame for episode in self.frames.values() for frame in episode["frames"]]
        if not frames:
            raise ValueError("No recorded frames; enable collect_frames before replay")
        imageio.mimwrite(str(output_path), frames, fps=1 / (self.env.dt * self.frame_stride), codec="libx264",
                        pixelformat="yuv420p", macro_block_size=2)
        return Path(output_path)

    @staticmethod
    def _is_tap_action(normalized_start_yx, normalized_end_yx):
        return np.linalg.norm(np.asarray(normalized_start_yx) - normalized_end_yx) <= _SWIPE_DISTANCE_THRESHOLD
