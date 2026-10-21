"""Policy-compatible muscle commands on MyoSuite 3's native CPU backend."""
import mujoco
import numpy as np
from myosuite.core.muscle_conditions import CumulativeFatigue

from .env_base_custom import MujocoEnvCustom


class BaseV0Custom(MujocoEnvCustom):
    def _setup(self, obs_keys, weighted_reward_keys, frame_skip=10,
               muscle_condition="", fatigue_reset_vec=None,
               fatigue_reset_random=False, **kwargs):
        if muscle_condition not in ("", "sarcopenia", "fatigue", "reafferentation"):
            raise ValueError(f"Unsupported muscle condition: {muscle_condition}")
        self.muscle_condition = muscle_condition
        self.fatigue_reset_vec = fatigue_reset_vec
        self.fatigue_reset_random = fatigue_reset_random
        if muscle_condition == "sarcopenia":
            self.model.actuator_gainprm[:, 2] *= 0.5
        elif muscle_condition == "fatigue":
            self.muscle_fatigue = CumulativeFatigue(self.model, frame_skip, seed=self.get_input_seed())
        elif muscle_condition == "reafferentation":
            self.EPLpos = self.model.actuator("EPL").id
            self.EIPpos = self.model.actuator("EIP").id
        super()._setup(obs_keys, weighted_reward_keys, frame_skip=frame_skip, **kwargs)
        self.viewer_setup()

    def step(self, action, **kwargs):
        controls = np.asarray(action, dtype=np.float64).copy()
        muscles = self.model.actuator_dyntype == mujoco.mjtDyn.mjDYN_MUSCLE
        if self.model.na and self.normalize_act:
            # This exact sigmoid is part of the bundled PPO policy contract.
            controls[muscles] = 1 / (1 + np.exp(-5 * (controls[muscles] - 0.5)))
        if self.muscle_condition == "fatigue":
            controls[muscles], _, _ = self.muscle_fatigue.compute_act(controls[muscles])
        elif self.muscle_condition == "reafferentation":
            controls[self.EPLpos] = controls[self.EIPpos]
            controls[self.EIPpos] = 0
        self.data.ctrl[:] = controls
        self.last_ctrl = controls.copy()
        self._step_physics()
        obs_dict = self._get_obs_dict(self._accessor)
        return self._finalize_step(obs_dict, self.get_reward_dict(obs_dict))

    def reset(self, fatigue_reset=True, **kwargs):
        if kwargs.get("seed") is not None:
            self.seed(kwargs["seed"])
        if fatigue_reset and self.muscle_condition == "fatigue":
            # MyoSuite 3.0.0 aliases float64 reset vectors and mutates them
            # while stepping fatigue. Keep the caller's fixed reset state.
            reset_vector = None if self.fatigue_reset_vec is None else np.array(
                self.fatigue_reset_vec, dtype=float, copy=True)
            self.muscle_fatigue.reset(fatigue_reset_vec=reset_vector,
                                     fatigue_reset_random=self.fatigue_reset_random,
                                     np_random=self.np_random)
        return super().reset(**kwargs)

    def set_fatigue_reset_random(self, fatigue_reset_random):
        self.fatigue_reset_random = fatigue_reset_random
