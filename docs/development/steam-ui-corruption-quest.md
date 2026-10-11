# Steam UI shows coloured stripes (Quest 3 / 3S, Adreno 740)

Steam starts, audio plays, the DroidDeck chrome is fine, but the Steam window is covered in
pink/green/purple lines. That is not a launch failure and not the KGSL allocation bug (#245).

## Working hypothesis

A UBWC (Qualcomm compressed framebuffer) layout mismatch on the frames gamescope/Steam hand the
compositor. Evidence and what is still open are in the QuestDeck investigation notes; the short
version:

- The same symptom is reported on other Adreno 740 devices with no KGSL patch (upstream #428, the
  "OneUI UBWC bug"), and on an Adreno 840 (The412Banner/DroidDeck #53, frames reported as
  `qcom_compressed`).
- Upstream's fix for the Steam UI on Adreno 740 is `FD_DEV_FEATURES=enable_tp_ubwc_flag_hint=1`
  applied to the whole session (the game-environment page only reaches Proton games), or the
  "Banners-Turnip - 8 Gen 2 on One UI" driver pair chosen by hand.
- `TU_DEBUG=noubwc` does **not** make gamescope hand over linear frames (#53: still
  `qcom_compressed`), and hiding the modifier in Turnip aborts gamescope.
- shukumei7 (#245) saw the same garble on a Quest 3S while a clean gamescope screenshot proved the
  corruption was downstream of Steam, and that it went away when frames were routed through a
  SurfaceControl layer (zero-copy) instead of the compositor's own draw.

## The switch this branch adds

`DROIDDECK_WAYLAND_UBWC=0` was documented (WaylandCompositor.java, compositor.c) but
`nativeSetUbwc()` had no caller, so the compositor always advertised `qcom_compressed`. It is now
read from `droiddeck-env` before the compositor starts. `0` advertises LINEAR only; `1` or no line
keeps the default (compressed buffers on). Nothing changes unless the file says so.

## Isolation sequence (each step reversible; do one at a time, reproduce, collect logs)

`droiddeck-env` lives in the app's own files folder, e.g.
`/sdcard/Android/data/com.droiddeck.launcher/files/droiddeck-env` (package of CI builds). One
`KEY=VALUE` per line, read at session start; delete the line to undo.

1. **Baseline:** no file. Collect logs (below). Note the `dmabuf` and `[vulkan] ... presenting GPU
   frames` lines.
2. **`FD_DEV_FEATURES=enable_tp_ubwc_flag_hint=1`** (upstream's fix). Also try the One UI driver
   pair from the GPU drivers tab.
3. **`DROIDDECK_WAYLAND_UBWC=0`** (needs this branch). Expect `qcom_compressed not advertised` in
   the dmabuf line and `linear` in the presenting line. Clean picture => UBWC path. Same
   corruption => not UBWC; go to step 4.
4. **Zero-copy** (HDR route, where the panel offers HDR10): clean => the compositor's own import and
   draw path is at fault.

## Log lines to read (session folder, "Share logs")

`wayland.log` / `session.log`: the `dmabuf formats:` line (is `qcom_compressed` listed?), the
`present: driver ...` lines (missing `VK_EXT_image_drm_format_modifier` means import fails), and
`"Steam Big Picture Mode" (gamescope) is presenting GPU frames through Wayland: WxH, format ...,
<modifier> (...)`. Also `device.txt` (kernel, GPU chip id) and `app.log`.
