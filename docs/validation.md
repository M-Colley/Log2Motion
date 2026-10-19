# Migration validation — 19–20 October 2026

The initial migration checks below are supplemented by the
[additional bug audit](additional-bug-audit.md): the expanded suite has 126 Python
tests and 36 plugin tests, including further simulator, bridge, UI, and upstream
regressions. That report records the subsequent validation and remaining limits.

## Runtime

The reproducible CPU stack is in `uv.lock` and the exported `requirements.txt`:
MyoSuite 3.0.0, MuJoCo 3.16.0, myo-sim 0.2.3, Stable-Baselines3 2.9.0,
PyTorch 2.14.1, NumPy 2.5.4, SciPy 1.18.1, Pillow 13.0.0,
Gymnasium 1.2.3, WebSockets 16.1.1, and ReportLab 5.0.1.
Python 3.14 uses ONNX Runtime 1.31.0. Python 3.15 uses official CPU PyTorch
wheels, h5py 3.17.0.dev0 from the official scientific-Python nightly feed,
and MyoSuite commit `42cfe809b458c525dabe47945fd077c775d75183` from
[upstream PR #557](https://github.com/MyoHub/myosuite/pull/557). That patch
preserves the existing ONNX dependencies on older Python versions while
allowing native CPU rollouts on 3.15 without the unavailable ONNX binary.
OSQP 1.1.3 and Zstandard 0.25.0 build successfully from their official sdists
using MSVC on Windows 3.15. Real QP solving, compression, HDF5 I/O, and PPO
checkpoint loading are checked after the builds.
MyoSuite constrains Gymnasium below 1.3; its Viser dependency constrains
WebSockets below 17. Linux uses the matching CPU PyTorch build.

The plugin uses TypeScript 6.0.3, ESLint 10.13.0, typescript-eslint 8.72.0,
Figma typings 1.142.0, esbuild 0.28.2, and Chart.js 4.5.1.
TypeScript 7 is outside the supported typescript-eslint peer range, so the
newest compatible TypeScript 6 release is locked instead.

## Automated checks

- Python 3.12 and 3.13 each pass all 90 simulator, CLI, and bridge tests on
  Windows: 37 simulation, 48 integration, and five real end-to-end checks.
  JUnit results are recorded under `runtime/pytest312-results.xml` and the
  task scratch directory on D:. Gymnasium emits one legacy `seed()` warning;
  the modern `reset(seed=...)` contract passes its checker.
- Python 3.14.6 and the current security patch 3.14.8 also pass all 90 tests
  and dependency compatibility checks on Windows.
- Python 3.15.0 passes all 90 tests in 63 seconds, with a clean dependency
  check. Its HDF5 read/write round trip, OSQP optimization solve, native
  compression, trained PPO loading, real rendering, and video export pass.
- The final suite adds two grouped-timeout regressions: 92 tests total
  (38 simulation, 49 bridge/history/PDF, five end-to-end). Python 3.12 passes
  all 92 locally. Hosted Windows CI runs 90 because its two OpenGL checks
  are covered by local Windows and the complete Linux matrix instead.
- All five bundled policy archives load, predict 63 controls, and step the
  model with the original 74-value observation contract.
- Gymnasium's environment checker, seeded reset/noise reproducibility,
  activation reset, success/timeout flags, muscle conditions, hold actions,
  normalized coordinate bounds, and both scene XML files are tested.
- Runtime target movement/resizing is checked against actual MuJoCo fingertip
  collision contacts, including refreshed broadphase bounds.
- Real repeated taps and a swipe exercise policy inference, physical target
  selection, release movements, gesture grouping, and monotonic sample times.
- The [spatial rollout report](rollout-validation.json) records nine interior
  grid taps, two repeated center taps, and a swipe at seed 42. All 12 gestures
  succeed, with finite values, increasing times, and positive MuJoCo fingertip
  contact force. Two bottom-row taps also make incorrect contacts before
  succeeding. Sensor, force, proximity, and penetration evidence are recorded
  separately; this is a single-seed simulation check.
- Named/free offscreen camera rendering and Android texture resizing are
  tested with real MuJoCo arrays. MP4 export is decoded and checked for frame
  dimensions and playback duration against simulated time.
- A real socket and real simulation subprocess exercise progress, repeated
  clicks, results, history, and PDF export. Other bridge regressions cover
  malformed requests, timeouts, stderr pressure, session isolation, occurrence
  comparisons, chart limits/contact classification, and outcome reporting.
- Ten plugin tests cover transformed coordinates, nested/rotated frames,
  repeats, selection changes during pending lookups, safe vector cleanup,
  bundled HTML syntax, and outcome messages.
  Type checking, build, ESLint, and `npm audit` pass with no reported npm
  vulnerabilities.
- Ruff, dependency compatibility, lock freshness, and whitespace checks pass.
  CI runs Windows/Linux on Python 3.12–3.15, plugin checks, and a real Docker
  bridge smoke test.

## Upstream defect

[MyoSuite PR #556](https://github.com/MyoHub/myosuite/pull/556) fixes ignored
free-camera settings in offscreen RGB/depth/segmentation rendering. The
regression produces identical frames before the patch and different frames
after it. Eight renderer tests and all pre-commit checks pass. Broader upstream
tests produced 312 passes, two skips, four expected failures, and four physics
parity failures that reproduce identically on unmodified upstream `dev`.
These existing failures are disclosed in the PR; their expected values were
not altered. Log2Motion contains a small local camera workaround until an
upstream release includes the fix.
All upstream PR #556 CI checks also pass, including Ubuntu, Windows, macOS,
Python 3.14, docs, lint, and CLA verification.

## Limits

Actual interaction inside Figma Desktop has not been exercised. Build, mocked
Figma API tests, browser-script syntax, and the live WebSocket-to-MuJoCo path
are covered. Import the rebuilt manifest from this repository for manual
acceptance; the downloaded plugin omits required screen geometry.

A real Android emulator has not been exercised. It remains an optional extra;
the Figma workflow has no Android dependency. The alternate tilted scene was
checked for loading and finite stepping, while policy success and coordinate
calibration are validated on the default flat-phone scene.

The local Docker dependency build encountered an almost-full Windows system
drive, then Docker Desktop's filesystem became read-only. Task-created audit
clones were moved to D: and disk space recovered. Docker Desktop restart is
blocked by its stale `sailor-ingest.sock` runtime socket; automatic approval
review rejected its cleanup, and moving it failed. Container validation is
delegated to the repository's Linux CI job. No Docker images or volumes were
deleted as a recovery measure.
The Linux CI job successfully built the image and passed its real two-tap,
progress, history, and PDF smoke test. Hosted Windows CI lacks an OpenGL driver,
so its two rendering tests run on Linux CI and local Windows desktop sessions.

Preserving policy spaces and demonstrating successful rollouts does not
establish numerical or scientific equivalence with MyoSuite 2.4/MuJoCo 3.1.2.
The bundled model assets remain in place; MyoSuite 3's updated default arm
models are not substituted underneath the pretrained policies. Validate the
movement/effort measures for a study before comparing them across versions.
