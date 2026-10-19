"""Log2Motion adapter to the MyoSuite 3 native CPU environment API.

Keep the trained observation order and action mapping while replacing the
removed MyoSuite 2 SimScene and Robot classes with native model/data access.
"""
from types import SimpleNamespace

import gymnasium as gym
import mujoco
import numpy as np
from myosuite.envs.gymnasium_env import CpuEnvAccessor, MyoGymnasiumEnv

from .mj_renderer_custom import CustomMJRenderer


class MujocoEnvCustom(MyoGymnasiumEnv):
    def __init__(self, model_path, obsd_model_path=None, seed=None, **kwargs):
        if obsd_model_path is not None:
            raise ValueError("Log2Motion uses a single observed and simulated model")
        super().__init__(**kwargs)
        self.seed(seed)
        self._model_spec = mujoco.MjSpec.from_file(str(model_path))
        self.model = self._model_spec.compile()
        self.data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, self.data)
        self._mj_renderer_compat = CustomMJRenderer(self.model, self.data)
        # Retain the small public surface used by existing replay clients.
        self.sim = SimpleNamespace(
            model=self.model, data=self.data,
            renderer=self._mj_renderer_compat,
            forward=lambda: mujoco.mj_forward(self.model, self.data),
            init_qvel=self.data.qvel.copy(),
        )
        self.sim_obsd = self.sim

    def _setup(self, obs_keys, weighted_reward_keys, frame_skip=1,
               normalize_act=True, obs_range=(-10, 10), **kwargs):
        self.frame_skip = int(frame_skip)
        if self.frame_skip < 1:
            raise ValueError("frame_skip must be positive")
        self._ctrl_dt = float(self.model.opt.timestep) * self.frame_skip
        self.normalize_act = normalize_act
        low = -np.ones(self.model.nu) if normalize_act else self.model.actuator_ctrlrange[:, 0]
        high = np.ones(self.model.nu) if normalize_act else self.model.actuator_ctrlrange[:, 1]
        self.action_space = gym.spaces.Box(low.astype(np.float32), high.astype(np.float32))
        self.init_qpos = self.data.qpos.copy()
        self.init_qvel = self.data.qvel.copy()
        self.obs_keys = list(obs_keys)
        self.rwd_keys_wt = dict(weighted_reward_keys)
        self.obs_dict = {}
        self.rwd_dict = {}
        self._accessor = CpuEnvAccessor(self.model, self.data, self.dt)
        obs = self.get_obs()
        self.observation_space = gym.spaces.Box(
            obs_range[0], obs_range[1], shape=obs.shape, dtype=np.float32
        )

    @property
    def dt(self):
        return self._ctrl_dt

    def _get_obs_dict(self, accessor):
        return self.get_obs_dict(self.sim)

    def _obs_dict_to_vec(self, obs_dict):
        return self._obs_keys_to_vec(obs_dict)

    def get_obs(self):
        self.obs_dict = self.get_obs_dict(self.sim)
        return self._ensure_obs_gymnasium_compliant(self._obs_dict_to_vec(self.obs_dict))

    def _finalize_step(self, obs_dict, rwd_dict):
        self.obs_dict, self.rwd_dict = obs_dict, rwd_dict
        return super()._finalize_step(obs_dict, rwd_dict)

    def reset(self, *, seed=None, options=None, reset_qpos=None, reset_qvel=None, **kwargs):
        gym.Env.reset(self, seed=seed)
        if seed is not None:
            self.rng = self.np_random
            self.action_space.seed(seed)
        options = options or {}
        reset_qpos = options.get("reset_qpos", reset_qpos)
        reset_qvel = options.get("reset_qvel", reset_qvel)
        obs = self._reset(reset_qpos=reset_qpos, reset_qvel=reset_qvel, seed=seed, **kwargs)
        return obs, {}

    def _reset_physics(self, qpos, qvel):
        # Consecutive replay interactions retain muscle activations and posture.
        act = self.data.act.copy()
        mujoco.mj_resetData(self.model, self.data)
        self.data.qpos[:] = qpos
        self.data.qvel[:] = qvel
        self.data.act[:] = act
        mujoco.mj_forward(self.model, self.data)

    def viewer_setup(self, distance=1.5, azimuth=90, elevation=-30, lookat=None,
                     render_actuator=True, render_tendon=None):
        renderer = self.sim.renderer
        renderer.set_free_camera_settings(distance=distance, azimuth=azimuth,
                                          elevation=elevation, lookat=lookat)
        renderer.set_viewer_settings(render_actuator=render_actuator, render_tendon=render_tendon)

    def close(self):
        super().close()
        android = getattr(self, "and_instance", None)
        if android is not None:
            android.close()
