/*
 * KGSL's legacy GPU-memory ioctls answered with the GPUOBJ family, for kernels whose SELinux
 * policy refuses the legacy ones to apps (issue #245).
 *
 * Meta's Horizon OS (Quest 3 / 3S) lets an app domain query /dev/kgsl-3d0 but denies
 *     IOCTL_KGSL_GPUMEM_ALLOC_ID (0x934) and IOCTL_KGSL_GPUMEM_FREE_ID with EACCES,
 * while IOCTL_KGSL_GPUOBJ_ALLOC / _INFO / _FREE are allowed. Turnip's KGSL backend allocates every
 * non-shareable buffer object with GPUMEM_ALLOC_ID, so its first allocation fails, Turnip reports
 * it as VK_ERROR_OUT_OF_DEVICE_MEMORY and vkCreateDevice fails. Both Turnips in a session hit it:
 * the glibc one the guest's gamescope, Steam and Proton load, and the bionic one the app's own
 * Wayland compositor loads.
 *
 * The two families allocate the same kind of object from the same id space. This header turns one
 * into the other:
 *
 *     GPUMEM_ALLOC_ID {flags, size}  ->  GPUOBJ_ALLOC {flags, size}, then GPUOBJ_INFO {id}
 *                                        for the GPU address the legacy call returns.
 *     GPUMEM_FREE_ID  {id}           ->  GPUOBJ_FREE {id}
 *
 * The kernel is always asked first. Only an EACCES switches a process over, and it stays switched:
 * the denial is policy, not a transient, and every further attempt would be one more avc line in
 * the log. A device that allows the legacy ioctls never takes the translated path.
 *
 * Header-only, with its own copies of the uapi structs (layout: msm_kgsl.h), so it builds in the
 * glibc preload (tools/linuxfs/preload/kgslalloc.c) and the bionic hook (kgsl_gpuobj_hook.c)
 * alike. The caller supplies the ioctl to use; nothing here calls the C library's.
 *
 * BL_KGSL_GPUOBJ=0 turns it off; BL_KGSL_GPUOBJ=1 translates without asking the kernel first.
 */
#ifndef DROIDDECK_KGSL_GPUOBJ_COMPAT_H
#define DROIDDECK_KGSL_GPUOBJ_COMPAT_H

#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>

#ifndef DD_KGSL_NOTE
#define DD_KGSL_NOTE(msg) ((void)0) /* the includer may log the one-time switch-over */
#endif

#define DD_KGSL_IOC_TYPE 0x09

struct dd_kgsl_gpumem_alloc_id {
    unsigned int id;
    unsigned int flags;
    size_t size;
    size_t mmapsize;
    unsigned long gpuaddr;
    unsigned long pad[2];
};

struct dd_kgsl_gpumem_free_id {
    unsigned int id;
    unsigned int pad;
};

struct dd_kgsl_gpuobj_alloc {
    uint64_t size;
    uint64_t flags;
    uint64_t va_len;
    uint64_t mmapsize;
    unsigned int id;
    unsigned int metadata_len;
    uint64_t metadata;
};

struct dd_kgsl_gpuobj_free {
    uint64_t flags;
    uint64_t priv;
    unsigned int id;
    unsigned int type;
    unsigned int len;
};

struct dd_kgsl_gpuobj_info {
    uint64_t gpuaddr;
    uint64_t flags;
    uint64_t size;
    uint64_t va_len;
    uint64_t va_addr;
    unsigned int id;
};

/* Request numbers are 32 bits; bionic's ioctl takes them as int, glibc's as unsigned long.
 * Everything here compares and passes them as unsigned int. */
#define DD_IOCTL_GPUMEM_ALLOC_ID \
    ((unsigned int)_IOWR(DD_KGSL_IOC_TYPE, 0x34, struct dd_kgsl_gpumem_alloc_id))
#define DD_IOCTL_GPUMEM_FREE_ID \
    ((unsigned int)_IOWR(DD_KGSL_IOC_TYPE, 0x35, struct dd_kgsl_gpumem_free_id))
#define DD_IOCTL_GPUOBJ_ALLOC \
    ((unsigned int)_IOWR(DD_KGSL_IOC_TYPE, 0x45, struct dd_kgsl_gpuobj_alloc))
#define DD_IOCTL_GPUOBJ_FREE \
    ((unsigned int)_IOW(DD_KGSL_IOC_TYPE, 0x46, struct dd_kgsl_gpuobj_free))
#define DD_IOCTL_GPUOBJ_INFO \
    ((unsigned int)_IOWR(DD_KGSL_IOC_TYPE, 0x47, struct dd_kgsl_gpuobj_info))

/* The ioctl to call. Returns like ioctl(2): 0 or more, or -1 with errno set. */
typedef int (*dd_kgsl_ioctl_fn)(int fd, unsigned long request, void *arg);

/* Non-zero once the kernel has answered a legacy allocation ioctl with EACCES. */
static int dd_kgsl_denied;

/* 0 off, 1 ask the kernel first (default), 2 always translate. */
static inline int dd_kgsl_mode(void) {
    static int mode = -1;
    const char *value;

    if (mode >= 0) return mode;
    value = getenv("BL_KGSL_GPUOBJ");
    if (value != NULL && value[0] == '0') mode = 0;
    else if (value != NULL && value[0] == '1') mode = 2;
    else mode = 1;
    return mode;
}

static inline int dd_kgsl_gpuobj_free(dd_kgsl_ioctl_fn real, int fd, unsigned int id) {
    struct dd_kgsl_gpuobj_free request;

    memset(&request, 0, sizeof(request));
    request.id = id;
    return real(fd, (unsigned long)DD_IOCTL_GPUOBJ_FREE, &request);
}

static inline int dd_kgsl_alloc_via_gpuobj(dd_kgsl_ioctl_fn real, int fd,
                                           struct dd_kgsl_gpumem_alloc_id *legacy) {
    struct dd_kgsl_gpuobj_alloc object;
    struct dd_kgsl_gpuobj_info info;
    int saved;

    memset(&object, 0, sizeof(object));
    object.size = legacy->size;
    object.flags = legacy->flags;
    object.va_len = legacy->size;
    if (real(fd, (unsigned long)DD_IOCTL_GPUOBJ_ALLOC, &object) != 0) {
        if (errno != EINVAL) return -1;
        /* A kernel that does not take an explicit virtual length alongside plain flags. */
        memset(&object, 0, sizeof(object));
        object.size = legacy->size;
        object.flags = legacy->flags;
        if (real(fd, (unsigned long)DD_IOCTL_GPUOBJ_ALLOC, &object) != 0) return -1;
    }
    memset(&info, 0, sizeof(info));
    info.id = object.id;
    if (real(fd, (unsigned long)DD_IOCTL_GPUOBJ_INFO, &info) != 0) {
        /* Without the address the object is no use to the caller, and it would leak. */
        saved = errno;
        dd_kgsl_gpuobj_free(real, fd, object.id);
        errno = saved;
        return -1;
    }
    legacy->id = object.id;
    legacy->flags = (unsigned int)info.flags;
    legacy->size = (size_t)(info.size != 0 ? info.size : object.size);
    legacy->mmapsize = (size_t)object.mmapsize;
    legacy->gpuaddr = (unsigned long)info.gpuaddr;
    return 0;
}

/*
 * True when the call was answered here, with its result in *rc (errno set on failure). False
 * leaves it to the caller: not one of the two ioctls, or turned off.
 */
static inline int dd_kgsl_gpuobj_compat(dd_kgsl_ioctl_fn real, int fd, unsigned long request,
                                        void *arg, int *rc) {
    unsigned int number = (unsigned int)request;
    int mode, result;

    if (arg == NULL
        || (number != DD_IOCTL_GPUMEM_ALLOC_ID && number != DD_IOCTL_GPUMEM_FREE_ID)) {
        return 0;
    }
    mode = dd_kgsl_mode();
    if (mode == 0) return 0;
    if (mode == 1 && !__atomic_load_n(&dd_kgsl_denied, __ATOMIC_RELAXED)) {
        result = real(fd, request, arg);
        if (result == 0 || errno != EACCES) {
            *rc = result;
            return 1;
        }
        __atomic_store_n(&dd_kgsl_denied, 1, __ATOMIC_RELAXED);
        DD_KGSL_NOTE("KGSL denies GPUMEM_ALLOC_ID/FREE_ID here; using GPUOBJ_ALLOC/INFO/FREE");
    }
    if (number == DD_IOCTL_GPUMEM_ALLOC_ID) {
        *rc = dd_kgsl_alloc_via_gpuobj(real, fd, (struct dd_kgsl_gpumem_alloc_id *)arg);
    } else {
        *rc = dd_kgsl_gpuobj_free(real, fd, ((struct dd_kgsl_gpumem_free_id *)arg)->id);
    }
    return 1;
}

#endif /* DROIDDECK_KGSL_GPUOBJ_COMPAT_H */
