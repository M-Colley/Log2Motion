"""Phone texture updates for MyoSuite 3's native MuJoCo renderer."""
import mujoco

from myosuite.viz.mj_renderer import MJRenderer


class CustomMJRenderer(MJRenderer):
    def render_offscreen(self, *args, camera_id=-1, **kwargs):
        # MyoSuite 3.0.0 only applies configured free-camera settings to its
        # window. Pass the equivalent explicit camera to its offscreen path.
        if camera_id is None or (isinstance(camera_id, int) and
                                 (camera_id < 0 or camera_id >= self._mj_model.ncam)):
            camera = mujoco.MjvCamera()
            mujoco.mjv_defaultFreeCamera(self._mj_model, camera)
            self._update_camera_properties(camera)
            camera_id = camera
        return super().render_offscreen(*args, camera_id=camera_id, **kwargs)

    def upd_texture(self, texture_id, data=None, window=True, model=None):
        if self._window is not None:
            self._window.update_texture(texture_id)
            self.refresh_window()
        # MuJoCo's Renderer exposes no public texture upload method. Recreate
        # offscreen contexts lazily to pick up the edited model texture.
        for renderer in self._renderers.values():
            renderer.close()
        self._renderers.clear()
        self._renderer = None
