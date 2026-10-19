# Log2Motion: biomechanical motion synthesis from touch logs

![Log2Motion](teaser.png)

Log2Motion runs the bundled muscle-control policies on **MyoSuite 3.0.0** and
native MuJoCo, with a local WebSocket bridge for Louisa Mathes's Figma plugin.
The integration is adapted from the supplied `ba_louisa_mathes_figma_log2motion-v1.0.0`
project. Its downloaded files are unchanged; the maintained plugin and bridge
are now in this repository.

## Install

Use Python **3.12 or 3.13**, Node.js **24 or newer**, and [uv](https://docs.astral.sh/uv/).
Run from this repository:

```powershell
uv sync --locked
uv run python -m integration.server
```

The server listens at `ws://localhost:8080`. Histories are stored in
`runtime/figma`, independently of the source files. See [integration setup](integration/README.md)
for configuration and optional tutorial assets from the downloaded project.

Build and import the updated plugin in Figma Desktop:

```powershell
cd integration/figma-plugin
npm ci
npm run build
```

In Figma, choose **Plugins → Development → Import plugin from manifest…** and
select this repository's `integration/figma-plugin/manifest.json`. Select
targets inside phone screen frames, arrange the click path, and run the simulation.
The original plugin does not export enough geometry for accurate coordinates;
import this updated plugin to use the repaired integration. Chart.js is bundled locally.

With pip instead of uv, create a Python 3.12 virtual environment and run
`python -m pip install -r requirements.txt`. Both Conda environment files use
the same exported requirements. `uv.lock` locks the complete dependency tree;
Gymnasium and WebSockets follow MyoSuite's compatible version limits. Linux
uses CPU PyTorch to avoid installing CUDA for inference.

## Docker and live visualization

Install Docker Desktop, then run:

```powershell
docker compose up --build -d
```

Build the plugin as above. The bridge uses port 8080, and the live simulation
viewer is at [localhost:6080/vnc.html](http://localhost:6080/vnc.html).
Choose visualization in the plugin to open the simulated scene in that viewer.
Persistent histories are mounted from `runtime/figma`. Stop with `docker compose down`.
The downloaded project's prebuilt image and launcher executables still contain
the old stack; use this repository's Compose setup for MyoSuite 3.

## Run touch logs directly

```powershell
uv run python log2motion_visualization.py --actions-file examples/taps.json
uv run python log2motion_visualization.py --actions-file examples/taps.json --video runtime/demo.mp4
```

Coordinates are normalized **[y, x]** in `[0, 1]`. Equal touch/lift positions
produce taps; separated positions produce swipes. Each gesture returns one
trajectory, including its release movement, with progress and JSON output
between `---DATA_START---` / `---DATA_END---`. `--no-rest` skips the release
movement for legacy replay. `--visualization` opens the native viewer; on macOS,
run the script using the environment's `mjpython` executable.
Video uses approximately 30 FPS; `--video-size 640 360` reduces export resolution.

The bundled scene, 74-value observation order, 63 muscle actions, and policy
action sigmoid are preserved. Successful policy execution is checked in tests;
scientific equivalence of trajectories and effort estimates across MuJoCo
versions requires separate validation for your study.

Android is optional and is not needed for the Figma workflow. Install it with
`uv sync --locked --extra android`, then construct `Log2MotionEnv` with
`use_android=True` and your Android SDK/AVD configuration. Android emulator
integration requires a separately installed and configured emulator.

## Development

```powershell
uv run pytest -q
uv run ruff check .
uv pip check
cd integration/figma-plugin
npm run build
npm run lint
npm test
```

Linux rendering tests need a display, for example `xvfb-run -a uv run pytest -q`.
CI covers Windows/Linux and Python 3.12/3.13 plus the plugin build and tests.
To update the dependency export after editing `pyproject.toml`:

```powershell
uv lock
uv export --locked --no-dev --no-hashes --no-emit-project --emit-index-url --output-file requirements.txt
```

See [validation notes](docs/validation.md) for tested behavior and upstream findings.

## Credits and license

Log2Motion was developed by Michał Patryk Miazga, Hannah Bussmann,
Antti Oulasvirta, and Patrick Ebel. The Figma integration is adapted from
Louisa Mathes's Ulm University bachelor's thesis project. Existing model and
source attribution notices are retained. See [LICENSE.txt](LICENSE.txt).
