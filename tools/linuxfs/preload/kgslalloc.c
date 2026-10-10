/*
 * KGSL GPU-memory allocation on kernels that deny the legacy ioctls to apps (issue #245).
 *
 * Horizon OS (Quest 3 / 3S) answers IOCTL_KGSL_GPUMEM_ALLOC_ID and _FREE_ID with EACCES from an
 * app's SELinux domain and allows the GPUOBJ family instead. Turnip allocates every plain buffer
 * object with GPUMEM_ALLOC_ID, so gamescope, Steam and every Proton process died in vkCreateDevice
 * ("BO init (VK_ERROR_OUT_OF_DEVICE_MEMORY)"). The translation itself lives in
 * app/src/main/cpp/kgsl_gpuobj_compat.h, shared with the hook the app's own Turnip gets.
 *
 * This runs from ioctl() in netif.c, so it covers any Turnip in the session, whatever else is
 * preloaded after this library (libfakeinput.so used to end the chain with a raw syscall).
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define DD_KGSL_NOTE(msg) kgsl_note(msg)
static void kgsl_note(const char *msg);

#include "../../../app/src/main/cpp/kgsl_gpuobj_compat.h"

/* Once per process, on stderr where the session log picks it up. */
static void kgsl_note(const char *msg) {
    char line[160];
    int length = snprintf(line, sizeof(line), "[blsession] %s\n", msg);
    if (length > 0) {
        ssize_t ignored = write(2, line, (size_t)length);
        (void)ignored;
    }
}

/* True when the call was answered here, with its result in *rc (and errno set on failure). */
__attribute__((visibility("hidden"))) int bl_kgsl_gpuobj(int (*real)(int, unsigned long, void *),
                                                         int fd, unsigned long request, void *arg,
                                                         int *rc) {
    return dd_kgsl_gpuobj_compat(real, fd, request, arg, rc);
}
