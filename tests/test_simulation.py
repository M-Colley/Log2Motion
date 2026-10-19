"""Real model and checkpoint regressions for the MyoSuite 3 migration."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from gymnasium.utils.env_checker import check_env
from myosuite.envs.gymnasium_env import MyoGymnasiumEnv
from stable_baselines3 import PPO

from environment.log2motion import Log2Motion, validate_actions
from environment.log2motion_env import Log2MotionEnv

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env():
    instance = Log2MotionEnv()
    yield instance
    instance.close()


def test_modern_api_and_exact_checkpoint_contract(env):
    assert isinstance(env, MyoGymnasiumEnv)
    check_env(env, skip_render_check=True)
    obs, info = env.reset(seed=42)
    assert obs.shape == (74,)
    assert obs.dtype == np.float32
    assert env.action_space.shape == (63,)
    expected = np.concatenate([np.atleast_1d(env.obs_dict[key]).ravel() for key in
                               ["qvel", "act", "distance_to_target", "IFtip_pos_norm",
                                "contact_information", "current_target_position", "action_type", "button_size"]])
    np.testing.assert_array_equal(obs, expected.astype(np.float32))
    assert info == {}


def test_seeded_reset_replays_noise_and_clears_previous_activation(env):
    action = np.linspace(-0.2, 0.2, 63)
    trajectories = []
    for _ in range(2):
        initial, _ = env.reset(seed=7)
        assert np.count_nonzero(env.data.act) == 0
        steps = [env.step(action)[0] for _ in range(8)]
        trajectories.append((initial, np.stack(steps), env.data.qpos.copy()))
    for first, second in zip(*trajectories):
        np.testing.assert_array_equal(first, second)


def test_reset_accepts_numpy_state_and_requested_velocity(env):
    qpos = env.data.qpos.copy()
    qvel = np.linspace(-0.01, 0.01, env.model.nv)
    env.reset(reset_qpos=qpos, reset_qvel=qvel)
    np.testing.assert_array_equal(env.data.qpos, qpos)
    np.testing.assert_array_equal(env.data.qvel, qvel)


@pytest.mark.parametrize("action", [np.zeros(62), np.full(63, np.nan), np.full(63, np.inf)])
def test_invalid_policy_actions_fail_before_physics(env, action):
    before = env.data.qpos.copy()
    with pytest.raises(ValueError, match="finite action"):
        env.step(action)
    np.testing.assert_array_equal(env.data.qpos, before)


def test_time_limit_is_truncation_and_success_is_in_info():
    env = Log2MotionEnv({"MAX_TIME": 0.004})
    try:
        _, _, terminated, truncated, _ = env.step(np.zeros(63))
        assert not terminated and not truncated
        _, _, terminated, truncated, info = env.step(np.zeros(63))
        assert not terminated and truncated
        assert info["is_success"] is False
    finally:
        env.close()


def test_hold_requires_consecutive_contacts():
    env = Log2MotionEnv({"hold_threshold": 3, "constant_noise_level": 0,
                         "sigma_signal_dependent": 0})
    try:
        env.target["position"] = env.data.site("IFtip").xpos.copy()
        assert not env.step(np.zeros(63))[2]
        assert not env.step(np.zeros(63))[2]
        _, _, terminated, truncated, info = env.step(np.zeros(63))
        assert terminated and not truncated
        assert info["is_success"] is True
    finally:
        env.close()


def test_swipe_affinity_and_initial_grid_target(env):
    env.grid = None
    env.touch_action("resting_position", show_marker=False)
    assert env.target["unique_button_name"] == "resting_position"
    env.change_the_conaff(0)
    assert env.swipe
    env.change_the_conaff(1)
    assert not env.swipe
    assert env.model.geom_conaffinity[env.model.geom("smartphone_geom").id] == 1


def test_screen_targets_exclude_elevated_start_and_rest_markers(env):
    assert env.find_closest_button(env.grid, 0.5, 0.5) not in {
        "start_button", "resting_position", "resting_position2"}
    x, y, z = env.target["smartphone_body_pos"]
    radius_x, radius_y, _ = env.target["smartphone_screen_geo_size"]
    np.testing.assert_allclose(env.normalize_iftip_position([x, y, z]), (0.5, 0.5))
    np.testing.assert_allclose(env.normalize_iftip_position([x + 2 * radius_x, y, z]), (0, 0.5))
    np.testing.assert_allclose(env.normalize_iftip_position([x - 2 * radius_x, y, z]), (1, 0.5))


def test_resized_moved_button_keeps_real_collision_bounds_current(env):
    target = env.model.geom("touch_area_1_geom").id
    body = int(env.model.geom_bodyid[target])
    env.change_button_size(0.02, if_reset=False)
    local_tip = env.data.site("IFtip").xpos - env.data.xpos[body]
    env.move_button_xyz(*local_tip)
    # This returns zero with mj_forward alone: stale BVH bounds cull the
    # overlapping cylinder after a runtime move/resize under MuJoCo 3.16.
    contacts = env.data.contact.geom
    assert any(target in pair for pair in contacts)


def test_alternate_scene_loads_with_portable_asset_paths():
    env = Log2MotionEnv({"model_path": str(ROOT / "environment/scene/log2motion_2scene.xml")})
    try:
        observation, _ = env.reset(seed=42)
        assert observation.shape == (74,)
        observation, *_ = env.step(np.zeros(63))
        assert np.isfinite(observation).all()
    finally:
        env.close()


@pytest.mark.parametrize("radius", [0, -0.01, np.nan, np.inf])
def test_invalid_button_size(env, radius):
    with pytest.raises(ValueError, match="positive finite"):
        env.change_button_size(radius)


@pytest.mark.parametrize("actions", [[], {}, [None], [{"action_type": "unknown"}],
    [{"action_type": "DUAL_POINT", "yx_touch": [0.5, "0.5"], "yx_lift": [0.5, 0.5]}],
    [{"action_type": "DUAL_POINT", "yx_touch": [True, 0.5], "yx_lift": [0.5, 0.5]}],
    [{"action_type": "DUAL_POINT", "yx_touch": [0.5, np.nan], "yx_lift": [0.5, 0.5]}],
    [{"action_type": "DUAL_POINT", "yx_touch": [0.5, 1.01], "yx_lift": [0.5, 0.5]}]])
def test_invalid_touch_logs(actions):
    with pytest.raises(ValueError):
        validate_actions(actions)


def test_boundaries_and_tap_swipe_classification():
    validate_actions([{"action_type": "DUAL_POINT", "yx_touch": [0, 0], "yx_lift": [1, 1]}])
    assert Log2Motion._is_tap_action([0, 0], [0, 0.04])
    assert not Log2Motion._is_tap_action([0, 0], [0, 0.041])


@pytest.mark.parametrize("archive", sorted((ROOT / "policies").glob("*.zip")), ids=lambda p: p.stem)
def test_every_bundled_checkpoint_loads_and_controls_model(env, archive):
    policy = PPO.load(archive, env=env, device="cpu", custom_objects={
        "learning_rate": 0.0, "lr_schedule": lambda _: 0.0, "clip_range": lambda _: 0.2})
    observation, _ = env.reset(seed=42)
    for _ in range(3):
        action, _ = policy.predict(observation, deterministic=True)
        assert action.shape == (63,)
        observation, _, terminated, truncated, info = env.step(action)
        assert np.isfinite(observation).all()
        assert np.isfinite(info["obs_dict"]["muscle_cost"]).all()


@pytest.mark.rendering
def test_offscreen_free_camera_changes_image_and_named_camera_renders(env):
    renderer = env.sim.renderer
    renderer.set_free_camera_settings(distance=0.5, azimuth=0)
    first = renderer.render_offscreen(width=160, height=120)
    renderer.set_free_camera_settings(distance=3, azimuth=180)
    second = renderer.render_offscreen(width=160, height=120)
    named = renderer.render_offscreen(width=160, height=120, camera_id="smartphone_view")
    assert first.shape == second.shape == named.shape == (120, 160, 3)
    assert not np.array_equal(first, second)
    assert np.std(named) > 0


def test_android_texture_resizes_screenshot_to_native_model(env, monkeypatch):
    calls = []
    env.and_instance = SimpleNamespace(
        get_screenshot=lambda: np.full((32, 16, 3), 127, dtype=np.uint8), close=lambda: None)
    env.use_android = True
    monkeypatch.setattr(env.sim.renderer, "upd_texture", lambda *args: calls.append(args))
    env.update_screen(window=False)
    texture = env.model.texture("smartphone_texture").id
    size = int(env.model.tex_width[texture] * env.model.tex_height[texture] * env.model.tex_nchannel[texture])
    address = env.model.tex_adr[texture]
    assert np.all(env.model.tex_data[address:address + size] == 127)
    assert len(calls) == 1


@pytest.mark.integration
@pytest.mark.parametrize("gesture", ["repeat", "swipe"])
def test_default_replay_groups_release_phases_into_one_episode_per_gesture(env, gesture):
    replay = Log2Motion(env, {"default": ROOT / "policies/normal.zip",
                             "swipe": ROOT / "policies/swipe.zip"}, show_screen=False)
    replay.start_render()
    tap = {"action_type": "DUAL_POINT", "yx_touch": [0.5, 0.5], "yx_lift": [0.5, 0.5]}
    actions = [tap, tap] if gesture == "repeat" else [dict(tap, yx_lift=[0.7, 0.5])]
    results = replay.replay(actions)
    assert len(results) == len(actions)
    for key, samples in results.items():
        metadata = replay.episode_info[key]
        assert metadata["is_success"]
        assert len(samples) > 175
        assert np.all(np.diff([sample["time"] for sample in samples]) > 0)
        assert any(sample["contact_information"] == 1 for sample in samples)
        assert samples[-1]["contact_information"] == 0
        assert len(metadata["phases"]) == (2 if gesture == "repeat" else 3)


def test_grouped_swipe_retains_initial_reach_timeout_after_successful_drag():
    replay = object.__new__(Log2Motion)
    replay.collect_frames = False
    replay.frames = {}
    replay.episode_counter = 2
    replay.results = {
        "episode_0": [{"time": 0.002}, {"time": 0.004}],
        "episode_1": [{"time": 0.002}, {"time": 0.004}],
    }
    replay.episode_info = {
        "episode_0": {"is_success": False, "terminated": False, "truncated": True},
        "episode_1": {"is_success": True, "terminated": True, "truncated": False},
    }
    outcome = dict(replay.episode_info["episode_1"], is_success=False)
    replay._group_episodes(0, outcome)
    grouped = replay.episode_info["episode_0"]
    assert grouped["is_success"] is False
    assert grouped["truncated"] is True
    assert grouped["terminated"] is True
    assert [phase["truncated"] for phase in grouped["phases"]] == [True, False]
    assert replay.episode_counter == 1
    np.testing.assert_allclose([sample["time"] for sample in replay.results["episode_0"]],
                               [0.002, 0.004, 0.006, 0.008])


@pytest.mark.parametrize("condition", ["fatigue", "sarcopenia", "reafferentation"])
def test_muscle_conditions_use_myo3_and_remain_finite(condition):
    env = Log2MotionEnv({"muscle_condition": condition})
    try:
        env.reset(seed=42)
        for _ in range(3):
            observation, *_ = env.step(np.zeros(63))
            assert np.isfinite(observation).all()
    finally:
        env.close()
