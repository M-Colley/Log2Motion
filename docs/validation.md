# Core migration validation — 21 October 2026

This document covers the standalone Log2Motion simulator, command-line replay,
video export, and relevant upstream MyoSuite fixes.

## Runtime

`uv.lock` and the exported `requirements.txt` define the CPU stack:
MyoSuite 3.0.0, MuJoCo 3.16.0, myo-sim 0.2.3, Stable-Baselines3 2.9.0,
PyTorch 2.14.1, NumPy 2.5.4, SciPy 1.18.1, Pillow 13.0.0,
Gymnasium 1.2.3, ImageIO 2.38.1, and ImageIO-FFmpeg 0.6.0.

Python 3.12–3.14 use released MyoSuite and h5py packages. Python 3.15 uses
MyoSuite commit `42cfe809b458c525dabe47945fd077c775d75183` from
[upstream PR #557](https://github.com/MyoHub/myosuite/pull/557), official CPU
PyTorch wheels, and h5py 3.17.0.dev0 from the official scientific-Python nightly
feed. The MyoSuite patch preserves ONNX dependencies on older interpreters and
allows native CPU rollouts on 3.15 without the unavailable ONNX Runtime binary.
It does not establish Python 3.15 support for ONNX-dependent features.

OSQP 1.1.3 and Zstandard 0.25.0 build from their official source distributions
on Python 3.15. Native Windows checks include an OSQP solve, compression,
HDF5 read/write, and trained PPO loading. These builds require MSVC on Windows
or a C/C++ compiler on Linux. Linux uses CPU PyTorch. Gymnasium remains below
1.3 to satisfy MyoSuite's compatible version range.

## Core checks

The core suite contains **64 cases**: 60 simulator regressions and four
standalone command-line/video cases. Collection is restricted to
`tests/test_simulation.py` and `tests/test_end_to_end.py`.

Native Windows Python 3.12 passes all 64 cases. The standalone smoke test also
passes with two real policy taps, progress updates, finite telemetry, and a
decoded 320 by 180 MP4 whose duration matches simulated time.

The regression coverage includes:

- Gymnasium's environment checker, float32 observations, and the original
  74-value observation / 63-control policy contract. All five bundled policy
  archives load, predict controls, and step the real model.
- Seeded noise and reset reproducibility. Fresh posture resets clear muscle
  activation, while continuous gesture phases preserve activation.
- Contact-state reads that remain stable within one physics timestep,
  success/timeout metadata, muscle conditions, hold actions, coordinate
  bounds, validated frame skips and episode limits, and both scene XML files.
- Real repeated taps and a swipe, including target selection, release phases,
  gesture grouping, completion markers, and increasing sample times.
- Runtime target movement/resizing checked against actual MuJoCo fingertip
  contacts after updating compiled collision bounds.
- Fixed fatigue reset arrays, including read-only inputs, remaining unchanged
  across stepping and repeated resets.
- Free/named offscreen cameras and native texture arrays. MP4 output is decoded
  and checked for frame dimensions and duration against simulated time.
- Standalone CLI execution outside the repository directory, valid framed
  results, invalid input, cleanup errors, and serialization failures.
- Android startup/teardown and contact-gated touch events using controlled
  stand-ins. No real Android emulator was used.

The [spatial rollout report](rollout-validation.json) records nine interior
screen-grid taps, two repeated center taps, and a swipe at seed 42. All 12
gestures succeed with finite telemetry, increasing times, and positive MuJoCo
fingertip contact force. Two bottom-row taps also make incorrect contacts
before succeeding. The artifact separates sensor, force, proximity, and
penetration evidence; it is a single-seed simulation check.

CI runs the core tests on Windows/Linux with Python 3.12–3.15. Hosted Windows
excludes rendering cases that require a graphics driver; native Windows and
Linux cover them. A separate container smoke test runs two real policy taps,
checks progress and finite telemetry, and decodes the exported video.

## Upstream findings

| Pull request | Confirmed defect and fix | Application impact |
| --- | --- | --- |
| [MyoSuite #556](https://github.com/MyoHub/myosuite/pull/556) | Configured free-camera settings were ignored in offscreen RGB/depth/segmentation rendering. The fix applies the configured camera. | A local renderer workaround keeps the selected camera effective. |
| [MyoSuite #557](https://github.com/MyoHub/myosuite/pull/557) | Mandatory ONNX Runtime prevented native Python 3.15 installation despite CPU rollouts not using it. The patch gates that dependency and adds an explicit ONNX extra with useful import errors. | The Python 3.15 dependency source is pinned to the tested patch. |
| [MyoSuite #558](https://github.com/MyoHub/myosuite/pull/558) | A fixed NumPy fatigue reset vector became mutable internal state, causing drift between episodes or errors for read-only arrays. The fix copies the input. | Log2Motion copies the reset vector at its own boundary while awaiting an upstream release. |
| [MyoSuite #559](https://github.com/MyoHub/myosuite/pull/559) | Runtime mesh edits left cached renderers stale or closed. The fix invalidates every cached offscreen context. | Current target edits use primitive geometry, so no local mesh-cache workaround is required. |

Each fix has a reproduction that fails before the patch and passes afterward.
For #558, 108 core/wrapper tests pass and one skips; the three new cases also
pass on native Python 3.15. For #559, 45 renderer/geometry tests pass, including
two real-image regressions. Pre-commit hooks pass for both patches.

The broader required upstream suite produced 312 passes, two skips, four
expected failures, and four existing Windows physics-parity failures also
present on unmodified upstream. Those failures are disclosed in the PRs;
expected physics values and tolerances were not changed to hide them.
The linked PRs provide current review and CI status.

A separate native MuJoCo 3.16 check confirmed that `mj_setConst()` refreshes
primitive bounding boxes and radii after a size edit, as documented in the
[3.16 release notes, Engine item 7](https://github.com/google-deepmind/mujoco/releases/tag/3.16.0).
Updating compiled fields is the caller's responsibility. Log2Motion's geometry
refresh is not evidence of a new MuJoCo defect. For the static smartphone body,
resizing a target leaves mass, inertia, and inertial pose unchanged.

## Limits

Preserved policy spaces and successful rollouts do not establish numerical or
scientific equivalence with MyoSuite 2.4 / MuJoCo 3.1.2. Bundled model assets
remain in place; MyoSuite 3's updated default arm models are not substituted
under the pretrained policies. Validate movement and effort measures for a
study before comparing them across simulation versions.

The spatial rollout check uses one seed and interior targets. It does not
establish reachability or success rates for arbitrary layouts and coordinates.
The alternate tilted scene loads and steps with finite values; policy success
and coordinate calibration are checked on the default flat-phone scene.

A real Android emulator remains outside completed validation. Optional GPU
backends and ONNX inference on Python 3.15 are outside the tested CPU replay
scope. Container validation uses Linux CI; Docker Desktop's local runtime
remained unavailable during the earlier validation session.
