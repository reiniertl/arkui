# Running scene extraction on the device — options, as found on 2026-10-30

To revisit later. Nothing here is implemented; it records what was measured
and checked, and what is still open.

## Goal

Run the scene extraction (the one-pass selection over the display's render
tree, and the motif matcher) on the device itself, and measure how long
scene processing takes there — instead of pulling text dumps to a laptop.

## What it costs today

Measured on the capture of 2026-10-28 and the pipeline of 2026-10-30.

| Step | Cost |
|---|---|
| A capture sample that reads only the render tree (`hidumper -s RenderService -a RSTree`) | 285 ms median, 410 ms p90 |
| The client dump (`-a client`), read only for a sample the capture judges new | about 190 ms more |
| A screenshot | about 370 ms (653 ms median for a sample with one, 285 without) |
| The Python pass on the laptop, per dump | 104 ms median, 132 ms p90; 508 nodes in a dump, 305 parsed, 234 kept |

The dumps are the bottleneck: they are text that RenderService builds and the
laptop parses back. Any route that still reads them carries that cost.

## The device

Read-only checks over `hdc` on 2026-10-30.

- HongMeng kernel 1.12 (HarmonyOS NEXT), not Linux; OpenHarmony-7.0.0.32;
  aarch64; developer mode on.
- `hdc shell` is root, in the SELinux domain `u:r:su:s0`; SELinux is
  enforcing.
- `/` is erofs, read-only; `/data` is writable and mounted without `noexec`.
- `render_service`: started by init from `/system/etc/init/graphic.cfg`
  (read-only), runs as user `graphics` in the domain
  `u:r:render_service:s0`, maps 15 `librender*` libraries, and is marked
  critical — `"critical": [1, 5, 60]`: five crashes in sixty seconds
  escalate, normally to a reboot.
- `/proc/<pid>` has the Linux entries (`maps`, `mem`, `map_files`, ...) and
  HarmonyOS ones such as `xpm_region` — the code-signing enforcement for
  executable memory.
- Present tools: `hidumper`, `hiperf`, `hitrace`, `hiprofiler`, `uitest`.

## Toolchains

- The laptop has a standalone `hdc` (`C:\hdc`) and no OpenHarmony SDK or
  compiler. Installing dev tools on the laptop is acceptable.
- The VM has the LLVM 18–20 libraries but no `clang` driver and no linker;
  it has network access.
- The public OpenHarmony NDK (LLVM clang and the aarch64 OHOS musl sysroot)
  is what a native build needs; it is not installed anywhere yet.

## Options

| Option | Internal source needed | Phone must run our code | Speed | Status |
|---|---|---|---|---|
| A native tool on the device reading `hidumper` | no | yes | too slow: it keeps today's dump cost | rejected as the design; only useful to time the pass alone |
| `uitest dumpLayout` | no | no | too slow (human, 2026-10-30) | rejected |
| An accessibility extension (public ArkTS API) | no | an app | unmeasured | open: event-driven, component type and rectangle per element; coverage limited to what accessibility exposes |
| A client calling RenderService's dump directly | no | yes | unmeasured | open: still text; worth it only if most of the 285 ms is `hidumper`'s own overhead |
| Inside `render_service` (a library in the process) | yes: headers matching Huawei's build | yes, plus policy | the only zero-copy route | needs the other environment |

Notes on the last row: the configuration cannot be edited in place (erofs),
SELinux is enforcing, and a crash loop in a critical service reboots the
phone; headers that do not match the binary's object layouts would read
wrong data. It is the target design, and the step for the other
environment.

## Open questions

1. Can the device run a program we compile ourselves from
   `/data/local/tmp`, or does code signing refuse it? Possibly solvable in
   developer mode; to be verified with the NDK's ordinary hello-world.
2. Where do `hidumper`'s 285 ms go — its own process and service hop, or
   RenderService building the text? One `hitrace` capture of a dump answers
   it, and decides whether a direct client is worth building.
3. Does `frameNodeTag` reach `RSRenderNode` on this build? In-process, the
   component identity must travel with the render node; if it does, the
   client-dump join and its stale tags disappear, the system UI included.
4. What does an accessibility extension cost per scene, and how much of the
   display does its element tree cover?

## What carries over from this repository

- The Python pass (`tools/cluster/rs_scene.py`) is the oracle: any on-device
  version is tested by running both on the same render tree and requiring
  the 63 values of the motif vector to match exactly — the relationship
  `tools/checks/scene_pass.py` already has with its own oracle.
- The confirmed cases (the keyboard of 20261022_110104/s000308, the one-page
  Swipers of s000552 and s000001, the launcher's window order, the seven
  videos) become regression tests on the device.
- In-process, the pass's own cost is what matters; time it against the
  frame budget (8.3 ms at 120 Hz) and keep it off the render thread.
