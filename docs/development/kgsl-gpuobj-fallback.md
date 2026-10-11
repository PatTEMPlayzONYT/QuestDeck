# KGSL GPUOBJ fallback (Quest 3 / 3S, issue #245)

Horizon OS's SELinux policy lets an app domain query `/dev/kgsl-3d0` but answers
`IOCTL_KGSL_GPUMEM_ALLOC_ID` (0x934) and `IOCTL_KGSL_GPUMEM_FREE_ID` with `EACCES`. The
`IOCTL_KGSL_GPUOBJ_ALLOC` / `_INFO` / `_FREE` family is allowed. Turnip allocates every plain buffer
object with `GPUMEM_ALLOC_ID`, so its first allocation failed and Turnip reported it as
`VK_ERROR_OUT_OF_DEVICE_MEMORY`, ending in `vkCreateDevice failed (VkResult: -2)`.

Two Turnips run in a session and both hit it, so both are covered:

| Turnip | Where it runs | Fix |
| --- | --- | --- |
| glibc, in the guest (gamescope, Steam, Proton) | `libblsession.so`, first in `/etc/ld.so.preload` | `tools/linuxfs/preload/kgslalloc.c`, called from `ioctl()` in `netif.c` |
| bionic, in the app (Wayland compositor) | adrenotools driver namespace | `libkgsl_gpuobj_hook.so`, enabled by `ADRENOTOOLS_DRIVER_KGSL_GPUOBJ` in `vk_loader.c` |

The translation is shared: `app/src/main/cpp/kgsl_gpuobj_compat.h`.

`GPUMEM_ALLOC_ID` becomes `GPUOBJ_ALLOC` plus `GPUOBJ_INFO` (for the GPU address); `GPUMEM_FREE_ID`
becomes `GPUOBJ_FREE`. The kernel is asked first; only an `EACCES` switches a process over, and it
stays switched. A device that allows the legacy ioctls never takes the translated path.
`BL_KGSL_GPUOBJ=0` turns it off, `=1` translates without asking first.

## libfakeinput

`libfakeinput.so`'s `ioctl` used to end with `syscall(SYS_ioctl, ...)` for descriptors it does not
own. Proton builds `LD_PRELOAD` as `libblsession.so:libfakeinput.so:<more>`, so everything listed
after it was skipped. It now forwards through `dlsym(RTLD_NEXT, "ioctl")` like the other hooks.

## Not verified on a device

Tested in CI-style unit tests (`tools/tests/test_kgsl_gpuobj.py`): the translation against a mock
kernel, the real `libblsession.so` under a seccomp filter standing in for the SELinux denial, and
`libfakeinput.so` forwarding. Not tested: a Quest, the bionic hook under the NDK, a 32-bit build,
`GPUOBJ_ALLOC`'s handling of `va_len` on Meta's kernel (a retry without it is included), large
allocations, sign-in, or performance. Quest/Horizon OS remains an unsupported target.

The proper upstream fix is in Mesa: fall back to `GPUOBJ_ALLOC` when `GPUMEM_ALLOC_ID` returns
`EACCES`.
