The Python modules and plugin UI were adapted from the supplied
`ba_louisa_mathes_figma_log2motion-v1.0.0` project. The downloaded project is
unchanged. Generated session histories, personal simulation results, font
files, and large tutorial/animation videos are not copied into this repository.

From the repository root, start the local bridge with:

```powershell
uv run python -m integration.server
```

Build the updated plugin, then import `integration/figma-plugin/manifest.json`
through Figma's development plugins menu:

```powershell
cd integration/figma-plugin
npm ci
npm run build
```

Select elements inside phone screen frames. This version exports transformed
coordinates relative to the outermost containing frame, including repeated
clicks and nested elements. Older exports lacking frame geometry are rejected
with an actionable message; they previously became identical center taps.
Chart.js is bundled into the generated HTML, so chart rendering does not depend
on a CDN. Optional web fonts retain the original UI configuration.

The WebSocket bridge listens at `ws://localhost:8080`. It runs the simulator
with the same Python interpreter and passes actions in a temporary JSON file.
Set `LOG2MOTION_PYTHON` only when the simulation must run in another environment.
Set `LOG2MOTION_DATA_DIR` to relocate session storage; the default is
`runtime/figma` in the repository. Set `LOG2MOTION_SIMULATION_TIMEOUT` to change
the default 1800 second subprocess limit. `LOG2MOTION_HOST` and
`LOG2MOTION_PORT` change the listening address; changing the port also requires
updating the plugin URL and development network permissions.

The small SVG controls are included. To retain the original intro animation
and tutorial, set `LOG2MOTION_ASSET_DIR` to the original project's
`Interface/images` directory before starting the server. PDF reports work with
built-in fonts. Set `LOG2MOTION_FONT_DIR` to the original `Interface/fonts`
directory to use its Anton and Inter fonts.

The bridge preserves the original `START_SIM`, `SIM_PROGRESS`, `RESULT`,
session, history, chart, comparison and PDF message shapes. Simulator output
uses `---DATA_START---` / `---DATA_END---` JSON markers. A nonzero subprocess
exit is always reported as `SIM_ERROR`, even if output contains partial data.
Each selected tap produces one result trajectory, including the release and
rest movement needed before the next tap. The simulator groups those movements
within the selected tap's episode so repeated taps remain separate interactions.
Each returned episode preserves the simulator's `outcome` metadata, including
success, termination, truncation and phase details. The result and saved history
also include an `outcome_summary`. Failed or timed-out gestures retain their
charts and receive a visible warning; a completed subprocess does not imply
that every gesture succeeded. Reports record these outcomes as well.

Regression tests:

```powershell
uv run python -m pytest tests/test_figma_integration.py
cd integration/figma-plugin
npm run lint
npm test
```
