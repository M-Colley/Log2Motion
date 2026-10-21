# Log2Motion: biomechanical motion synthesis from touch logs

![Log2Motion](teaser.png)

Log2Motion replays normalized touch logs with the bundled muscle-control
policies on **MyoSuite 3.0.0** and native MuJoCo. The migration preserves the
bundled scene, 74-value observation order, 63 muscle actions, and policy action
sigmoid while updating the Python and simulation dependencies.

## Install

Use Python **3.12–3.15** and [uv 0.13.2 or newer](https://docs.astral.sh/uv/).
Python 3.14 uses stable packages and is the default choice for this setup.
Run from this repository:

```powershell
uv sync --locked --python 3.14
uv run python log2motion_visualization.py --actions-file examples/taps.json
```

With pip instead of uv, create a Python 3.14 virtual environment and run
`python -m pip install -r requirements.txt`. Both Conda environment files use
the same exported requirements. `uv.lock` locks the complete dependency tree.
Linux uses CPU PyTorch for inference.

Python 3.15 needs additional package sources:

```powershell
uv sync --locked --python 3.15
```

Its lock uses official PyTorch CPU wheels, an official h5py 3.17 development
wheel, and the exact MyoSuite source commit from
[upstream PR #557](https://github.com/MyoHub/myosuite/pull/557). That patch
makes unused ONNX Runtime optional on 3.15; ONNX-dependent MyoSuite features
remain unavailable until that package supports the interpreter. OSQP and
Zstandard build from source on 3.15, requiring Visual Studio C++ Build Tools
on Windows or a C/C++ compiler on Linux. Python 3.12–3.14 use released MyoSuite
and h5py packages. Update an older pip-installed uv with
`python -m pip install --upgrade uv`.

## Replay and video

```powershell
uv run python log2motion_visualization.py --actions-file examples/taps.json
uv run python log2motion_visualization.py --actions-file examples/taps.json --video runtime/demo.mp4
```

Coordinates are normalized **[y, x]** in `[0, 1]`. Equal touch/lift positions
produce taps; sufficiently separated positions produce swipes. Each gesture
returns one trajectory, including its release movement, with progress and JSON
output between `---DATA_START---` / `---DATA_END---`. Results include success,
termination, timeout, and phase metadata. A completion marker ends the replay
without adding a gesture.

`--no-rest` skips the release movement for legacy replay. `--visualization`
opens the native viewer; on macOS, use the environment's `mjpython` executable.
Video uses approximately 30 FPS; `--video-size 640 360` reduces export resolution.

Android interaction is optional. Install `uv sync --locked --extra android`,
then construct `Log2MotionEnv` with `use_android=True` and your Android SDK/AVD
configuration. A separately installed and configured emulator is required.

## Docker

The container runs the core command-line simulator under a virtual display.
For the Compose video-export example, prepare a touch log:

```powershell
New-Item -ItemType Directory -Force runtime
Copy-Item examples/taps.json runtime/actions.json
docker compose up --build --abort-on-container-exit
```

The one-shot run reads `runtime/actions.json` and writes `runtime/replay.mp4`.
Use `docker compose down` to remove the stopped Compose container.

## Development

```powershell
uv run pytest -q
uv run ruff check .
uv pip check
```

Linux rendering tests need a display, for example
`xvfb-run -a uv run pytest -q`. CI covers the core simulator and command-line
replay on Windows/Linux with Python 3.12–3.15, plus a container smoke test.
To update the dependency export after editing `pyproject.toml`:

```powershell
uv lock
uv export --locked --no-dev --no-hashes --no-emit-project --emit-index-url --output-file requirements.txt
```

See [validation notes](docs/validation.md) for core regression coverage,
spatial rollout results, upstream fixes, and remaining scientific limits.
Successful rollouts and preserved policy spaces do not establish numerical
or scientific equivalence across MuJoCo versions; validate movement and effort
measures for your study before comparing them.

## Credits and license

Log2Motion was developed by Michał Patryk Miazga, Hannah Bussmann,
Antti Oulasvirta, and Patrick Ebel. Existing source and model attribution
notices are retained. See [LICENSE.txt](LICENSE.txt); bundled third-party
models retain their accompanying licenses.
