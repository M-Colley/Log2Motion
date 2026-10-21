"""Real model and checkpoint regressions for the MyoSuite 3 migration."""
from pathlib import Path
from types import SimpleNamespace
import json

import mujoco
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


def test_explicit_posture_reset_clears_muscles_but_interaction_reset_preserves_them(env):
    for _ in range(5):
        env.step(np.zeros(63))
    activation = env.data.act.copy()
    assert np.max(activation) > 0.5
    env.reset()
    np.testing.assert_array_equal(env.data.act, activation)
    env.reset(reset_qpos=env.start_qpos_data["return_qpos_button"])
    assert np.count_nonzero(env.data.act) == 0


def test_observation_reads_do_not_advance_contact_history(env, monkeypatch):
    monkeypatch.setattr(env, "_get_contact_information", lambda _: 0)
    monkeypatch.setattr(env, "_is_point_in_range_screen", lambda **_: 0)
    env.last_contact = True
    env.contact_counter = 0
    env._contact_state_time = None
    first = env.get_obs_dict(env.sim)
    for _ in range(24):
        observed = env.get_obs_dict(env.sim)
        np.testing.assert_array_equal(observed["contact_information"], first["contact_information"])
    assert env.last_contact
    assert env.contact_counter == 1
    monkeypatch.setattr(env, "_is_point_in_range_screen", lambda **_: 1)
    assert env.get_obs_dict(env.sim)["contact_information"].item() == 0
    monkeypatch.setattr(env, "_is_point_in_range_screen", lambda **_: 0)
    for _ in range(23):
        env.step(np.zeros(63))
    assert not env.last_contact


@pytest.mark.parametrize("config, message", [
    ({"frame_skip": 1.5}, "frame_skip"), ({"frame_skip": 0}, "frame_skip"),
    ({"MAX_TIME": -1}, "MAX_TIME"), ({"MAX_TIME": np.nan}, "MAX_TIME"),
    ({"max_timestep": 0}, "max_timestep"), ({"max_timestep": 2.5}, "max_timestep"),
    ({"hold_threshold": 0}, "hold_threshold"), ({"hold_threshold": True}, "hold_threshold"),
])
def test_invalid_timing_and_hold_configuration_fails_before_simulation(config, message):
    with pytest.raises(ValueError, match=message):
        Log2MotionEnv(config)


def test_default_step_limit_uses_actual_model_timestep(tmp_path):
    source = ROOT / "environment/scene/log2motion_scene.xml"
    spec = mujoco.MjSpec.from_file(str(source))
    spec.option.timestep = 0.001
    spec.compiler.meshdir = str(source.parent / "simhive/myo_sim") + "/"
    spec.compiler.texturedir = spec.compiler.meshdir
    model_path = tmp_path / "one_millisecond_scene.xml"
    model_path.write_text(spec.to_xml(), encoding="utf-8")
    env = Log2MotionEnv({"model_path": str(model_path), "MAX_TIME": 0.003})
    try:
        assert env.max_timestep == 3
        assert not env.step(np.zeros(63))[3]
        assert not env.step(np.zeros(63))[3]
        assert env.step(np.zeros(63))[3]
        assert env.time == pytest.approx(0.003)
    finally:
        env.close()


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


def test_task_complete_sentinel_stops_without_adding_a_rest_episode():
    replay = object.__new__(Log2Motion)
    replay.results = {}
    calls = []

    def press(target, move_rest):
        calls.append((target, move_rest))
        replay.results["episode_0"] = [{"time": 0.002}]

    def unexpected_rest(*args, **kwargs):
        pytest.fail("Completion sentinel must not add another simulated movement")

    replay._press = press
    replay._move_to_resting_position_with_time_interrupt = unexpected_rest
    replay.policies = {"default": None}
    tap = {"action_type": "DUAL_POINT", "yx_touch": [0.5, 0.5], "yx_lift": [0.5, 0.5]}
    result = replay.replay([tap, {"action_type": "STATUS_TASK_COMPLETE"}, tap])
    assert len(result) == 1
    assert calls == [((0.5, 0.5), True)]


def test_renderer_cleanup_failure_still_closes_android_once(env, monkeypatch):
    closed = []
    env.and_instance = SimpleNamespace(close=lambda: closed.append(True))

    def broken_renderer_close(self):
        raise RuntimeError("renderer cleanup failed")

    with monkeypatch.context() as patch:
        patch.setattr(MyoGymnasiumEnv, "close", broken_renderer_close)
        with pytest.raises(RuntimeError, match="renderer cleanup"):
            env.close()
    env.close()
    assert closed == [True]


def test_failed_model_load_does_not_launch_android(tmp_path, monkeypatch):
    calls = []
    android = SimpleNamespace(launch=lambda: calls.append("launch"), close=lambda: calls.append("close"))
    monkeypatch.setattr(Log2MotionEnv, "_android_setup", lambda *args, **kwargs: android)
    android_config = {"avd_name": "test", "android_avd_home": "unused", "android_sdk_root": "unused",
                      "emulator_path": "unused", "adb_path": "unused"}
    with pytest.raises((ValueError, FileNotFoundError)):
        Log2MotionEnv({"model_path": str(tmp_path / "missing.xml"), "use_android": True,
                       "android_config": android_config})
    assert calls == []


def test_android_launch_failure_closes_partially_created_simulator(monkeypatch):
    closed = []

    def fail_launch():
        raise RuntimeError("emulator launch failed")

    android = SimpleNamespace(launch=fail_launch, close=lambda: closed.append(True))
    monkeypatch.setattr(Log2MotionEnv, "_android_setup", lambda *args, **kwargs: android)
    with pytest.raises(RuntimeError, match="emulator launch failed"):
        Log2MotionEnv({"use_android": True, "android_config": {"avd_name": "test"}})
    assert closed == [True]


@pytest.mark.parametrize("primary_error", [None, "primary replay failure"])
def test_cli_frames_cleanup_failures_and_preserves_primary_error(tmp_path, monkeypatch, capsys, primary_error):
    import environment.log2motion as replay_module
    import environment.log2motion_env as env_module
    from log2motion_visualization import main

    def close():
        raise RuntimeError("renderer cleanup failed")

    def replay(*args, **kwargs):
        if primary_error:
            raise ValueError(primary_error)
        return {"episode_0": [{"time": 0.002}]}

    fake_replay = SimpleNamespace(start_render=lambda: None, replay=replay, episode_info={})
    monkeypatch.setattr(env_module, "Log2MotionEnv", lambda **kwargs: SimpleNamespace(close=close))
    monkeypatch.setattr(replay_module, "Log2Motion", lambda *args, **kwargs: fake_replay)
    actions = tmp_path / "actions.json"
    actions.write_text(json.dumps([{"action_type": "PRESS_HOME"}]), encoding="utf-8")
    assert main(["--actions-file", str(actions)]) == 1
    stdout = capsys.readouterr().out
    output = json.loads(stdout.split("---DATA_START---")[1].split("---DATA_END---")[0])
    assert output["status"] == "error"
    assert output["message"] == (primary_error or "renderer cleanup failed")
    if primary_error:
        assert output["cleanup_error"] == "renderer cleanup failed"


def test_cli_frames_nonfinite_result_serialization_error(tmp_path, monkeypatch, capsys):
    import environment.log2motion as replay_module
    import environment.log2motion_env as env_module
    from log2motion_visualization import main

    fake_replay = SimpleNamespace(start_render=lambda: None, episode_info={},
        replay=lambda *args, **kwargs: {"episode_0": [{"time": float("nan")}]})
    monkeypatch.setattr(env_module, "Log2MotionEnv", lambda **kwargs: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(replay_module, "Log2Motion", lambda *args, **kwargs: fake_replay)
    actions = tmp_path / "actions.json"
    actions.write_text(json.dumps([{"action_type": "PRESS_HOME"}]), encoding="utf-8")
    assert main(["--actions-file", str(actions)]) == 1
    stdout = capsys.readouterr().out
    output = json.loads(stdout.split("---DATA_START---")[1].split("---DATA_END---")[0])
    assert output["status"] == "error"
    assert "JSON" in output["message"]


@pytest.mark.parametrize("contacts, expected", [
    ([0, 0], []),
    ([0, 1, 0], [[99, 49, True, 0], [99, 49, False, 0]]),
    ([2], [[99, 49, True, 0], [99, 49, False, 0]]),
])
def test_android_swipe_sends_touches_only_while_finger_contacts_screen(contacts, expected):
    touches = []
    sequence = iter(enumerate(contacts))

    def step(action):
        number, contact = next(sequence)
        obs = {"IFtip_pos": np.zeros(3), "time": np.array([(number + 1) * 0.002]),
               "muscle_cost": np.array([0.0]), "contact_information": np.array([contact])}
        return np.zeros(74), 0.0, number == len(contacts) - 1, False, {"obs_dict": obs}

    replay = object.__new__(Log2Motion)
    replay.env = SimpleNamespace(
        reset=lambda: (np.zeros(74), {}), step=step, max_timestep=len(contacts), use_android=True,
        screen_resolution=(100, 200), normalize_iftip_position=lambda _: (0.5, 0.5),
        call_screen=lambda batch: touches.extend(batch))
    replay.collect_frames = False
    replay.show_screen = False
    replay.deterministic = True
    replay.episode_counter = 0
    replay.results, replay.frames, replay.episode_info = {}, {}, {}
    policy = SimpleNamespace(predict=lambda *args, **kwargs: (np.zeros(63), None))
    replay._run(policy, swipe=True)
    assert touches == expected


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


def test_fixed_fatigue_resets_do_not_mutate_or_alias_the_reset_vector():
    env = Log2MotionEnv({"muscle_condition": "fatigue"})
    try:
        initial = np.full(env.model.na, 0.25, dtype=np.float64)
        reset_vector = initial.copy()
        reset_vector.flags.writeable = False
        env.fatigue_reset_vec = reset_vector
        for _ in range(3):
            env.reset()
            np.testing.assert_array_equal(env.muscle_fatigue.MF, initial)
            assert not np.shares_memory(env.muscle_fatigue.MF, reset_vector)
            for _ in range(5):
                env.step(np.zeros(63))
            assert np.any(env.muscle_fatigue.MF != initial)
            np.testing.assert_array_equal(reset_vector, initial)
    finally:
        env.close()
