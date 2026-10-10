"""Issue #245: Horizon OS denies KGSL's legacy GPU-memory ioctls to apps.

Three layers are covered:
  * the translation itself (app/src/main/cpp/kgsl_gpuobj_compat.h) against a mock kernel;
  * libblsession.so, under a seccomp filter standing in for the SELinux denial, so the real
    preload is shown to reach the kernel with the GPUOBJ request;
  * libfakeinput.so forwarding ioctls it does not own to the preload after it (it used to end
    with a raw syscall, so a library listed after it never saw them).
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PRELOAD = ROOT / "tools/linuxfs/preload"
FAKEINPUT = ROOT / "app/src/main/cpp/fakeinput_steam.cpp"
COMPAT_DIR = ROOT / "app/src/main/cpp"

MOCK = r"""
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "kgsl_gpuobj_compat.h"

static int kernel_denies_legacy = 1;
static int fail_gpuobj_alloc_with_va_len = 0; /* EINVAL when va_len is set */
static int fail_info = 0;
static int calls_alloc_id, calls_free_id, calls_gpuobj_alloc, calls_info, calls_gpuobj_free;
static unsigned last_freed;
static unsigned next_id = 7;

static int kernel(int fd, unsigned long request, void *arg) {
  unsigned int r = (unsigned int)request;
  (void)fd;
  if (r == DD_IOCTL_GPUMEM_ALLOC_ID) {
    calls_alloc_id++;
    if (kernel_denies_legacy) { errno = EACCES; return -1; }
    struct dd_kgsl_gpumem_alloc_id *a = arg;
    a->id = 99; a->gpuaddr = 0xAAAA0000; a->mmapsize = a->size;
    return 0;
  }
  if (r == DD_IOCTL_GPUMEM_FREE_ID) {
    calls_free_id++;
    if (kernel_denies_legacy) { errno = EACCES; return -1; }
    return 0;
  }
  if (r == DD_IOCTL_GPUOBJ_ALLOC) {
    struct dd_kgsl_gpuobj_alloc *o = arg;
    calls_gpuobj_alloc++;
    if (fail_gpuobj_alloc_with_va_len && o->va_len) { errno = EINVAL; return -1; }
    o->id = next_id++;
    o->mmapsize = (o->size + 4095) & ~4095ULL;
    return 0;
  }
  if (r == DD_IOCTL_GPUOBJ_INFO) {
    struct dd_kgsl_gpuobj_info *i = arg;
    calls_info++;
    if (fail_info) { errno = EIO; return -1; }
    i->gpuaddr = 0x100000000ULL + i->id * 0x1000;
    i->flags = 0x0C000000u | (1u << 28); /* what the kernel reports back */
    i->size = 8192;
    return 0;
  }
  if (r == DD_IOCTL_GPUOBJ_FREE) {
    struct dd_kgsl_gpuobj_free *f = arg;
    calls_gpuobj_free++;
    last_freed = f->id;
    return 0;
  }
  errno = ENOTTY;
  return -1;
}

#define CHECK(cond) do { if (!(cond)) { printf("FAIL line %d: %s\n", __LINE__, #cond); return 1; } } while (0)

int main(void) {
  int rc = 12345;
  struct dd_kgsl_gpumem_alloc_id a;
  struct dd_kgsl_gpumem_free_id f;

  /* Struct sizes are what the kernel's ioctl numbers encode (64-bit). */
  if (sizeof(void *) == 8) {
    CHECK(sizeof(struct dd_kgsl_gpumem_alloc_id) == 48);
    CHECK(sizeof(struct dd_kgsl_gpuobj_alloc) == 48);
    CHECK(sizeof(struct dd_kgsl_gpuobj_info) == 48);
    CHECK(sizeof(struct dd_kgsl_gpuobj_free) == 32);
    CHECK(DD_IOCTL_GPUMEM_ALLOC_ID == 0xC0300934u); /* the 0x934 in the issue */
  }
  CHECK((DD_IOCTL_GPUMEM_ALLOC_ID & 0xFFFF) == 0x0934);
  CHECK((DD_IOCTL_GPUMEM_FREE_ID & 0xFFFF) == 0x0935);

  /* Other ioctls are not ours. */
  CHECK(dd_kgsl_gpuobj_compat(kernel, 3, 0x5401, &a, &rc) == 0);
  CHECK(dd_kgsl_gpuobj_compat(kernel, 3, DD_IOCTL_GPUMEM_ALLOC_ID, NULL, &rc) == 0);

  /* First allocation: legacy denied -> translated. */
  memset(&a, 0, sizeof(a));
  a.size = 8192; a.flags = (1u << 28) | (3u << 26);
  CHECK(dd_kgsl_gpuobj_compat(kernel, 3, DD_IOCTL_GPUMEM_ALLOC_ID, &a, &rc) == 1);
  CHECK(rc == 0);
  CHECK(calls_alloc_id == 1 && calls_gpuobj_alloc == 1 && calls_info == 1);
  CHECK(a.id == 7);
  CHECK(a.gpuaddr == 0x100000000UL + 7 * 0x1000);
  CHECK(a.size == 8192 && a.mmapsize == 8192);
  CHECK(a.flags == (0x0C000000u | (1u << 28)));

  /* Sticky: the kernel is not asked again. */
  memset(&a, 0, sizeof(a));
  a.size = 100;
  CHECK(dd_kgsl_gpuobj_compat(kernel, 3, DD_IOCTL_GPUMEM_ALLOC_ID, &a, &rc) == 1 && rc == 0);
  CHECK(calls_alloc_id == 1);
  CHECK(a.id == 8);

  /* Free by id -> GPUOBJ_FREE. */
  memset(&f, 0, sizeof(f));
  f.id = 8;
  CHECK(dd_kgsl_gpuobj_compat(kernel, 3, DD_IOCTL_GPUMEM_FREE_ID, &f, &rc) == 1 && rc == 0);
  CHECK(calls_free_id == 0 && calls_gpuobj_free == 1 && last_freed == 8);

  /* A kernel that rejects an explicit va_len: retried without it. */
  fail_gpuobj_alloc_with_va_len = 1;
  memset(&a, 0, sizeof(a));
  a.size = 4096;
  CHECK(dd_kgsl_gpuobj_compat(kernel, 3, DD_IOCTL_GPUMEM_ALLOC_ID, &a, &rc) == 1 && rc == 0);
  CHECK(a.id == 9);
  fail_gpuobj_alloc_with_va_len = 0;

  /* INFO failing frees the object and reports the error. */
  fail_info = 1;
  calls_gpuobj_free = 0;
  memset(&a, 0, sizeof(a));
  a.size = 4096;
  errno = 0;
  CHECK(dd_kgsl_gpuobj_compat(kernel, 3, DD_IOCTL_GPUMEM_ALLOC_ID, &a, &rc) == 1);
  CHECK(rc == -1 && errno == EIO);
  CHECK(calls_gpuobj_free == 1 && last_freed == 10);
  fail_info = 0;

  printf("ok\n");
  return 0;
}
"""

MOCK_ALLOWED = r"""
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "kgsl_gpuobj_compat.h"

static int legacy_calls, gpuobj_calls;
static int kernel(int fd, unsigned long request, void *arg) {
  unsigned int r = (unsigned int)request;
  (void)fd;
  if (r == DD_IOCTL_GPUMEM_ALLOC_ID) { legacy_calls++; ((struct dd_kgsl_gpumem_alloc_id *)arg)->id = 5; return 0; }
  if (r == DD_IOCTL_GPUMEM_FREE_ID) { legacy_calls++; errno = ENOENT; return -1; }
  gpuobj_calls++;
  errno = ENOTTY;
  return -1;
}

int main(void) {
  struct dd_kgsl_gpumem_alloc_id a;
  struct dd_kgsl_gpumem_free_id f = {1, 0};
  int rc = 0;
  memset(&a, 0, sizeof(a));
  a.size = 4096;
  /* A device that allows the legacy ioctls never takes the translated path... */
  if (!dd_kgsl_gpuobj_compat(kernel, 3, DD_IOCTL_GPUMEM_ALLOC_ID, &a, &rc) || rc != 0 || a.id != 5) return 1;
  /* ...and an error other than EACCES is the kernel's answer, passed through. */
  errno = 0;
  if (!dd_kgsl_gpuobj_compat(kernel, 3, DD_IOCTL_GPUMEM_FREE_ID, &f, &rc) || rc != -1 || errno != ENOENT) return 2;
  if (gpuobj_calls != 0 || legacy_calls != 2) return 3;
  printf("ok\n");
  return 0;
}
"""

# A seccomp filter standing in for the SELinux denial: the legacy ioctls get EACCES, the GPUOBJ
# ones ENOMEM. ENOMEM is not something the kernel would say to a plain fd, so seeing it proves the
# request that reached the kernel was the translated one.
SECCOMP = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <fcntl.h>
#include <unistd.h>
#include "kgsl_gpuobj_compat.h"

#if defined(__x86_64__)
#define AUDIT_ARCH_THIS AUDIT_ARCH_X86_64
#elif defined(__aarch64__)
#define AUDIT_ARCH_THIS AUDIT_ARCH_AARCH64
#endif

static void install(void) {
  struct sock_filter filter[] = {
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_THIS, 1, 0),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_ioctl, 1, 0),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[1])),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, DD_IOCTL_GPUMEM_ALLOC_ID, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EACCES),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, DD_IOCTL_GPUMEM_FREE_ID, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EACCES),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, DD_IOCTL_GPUOBJ_ALLOC, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | ENOMEM),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, DD_IOCTL_GPUOBJ_FREE, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | ENOMEM),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
  };
  struct sock_fprog prog = { sizeof(filter) / sizeof(filter[0]), filter };
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) || prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog)) {
    perror("seccomp");
    _exit(77);
  }
}

int main(void) {
  struct dd_kgsl_gpumem_alloc_id a;
  struct dd_kgsl_gpumem_free_id f = {3, 0};
  int fd = open("/dev/null", O_RDWR);
  install();
  memset(&a, 0, sizeof(a));
  a.size = 4096;
  errno = 0;
  int r1 = ioctl(fd, DD_IOCTL_GPUMEM_ALLOC_ID, &a);
  int e1 = errno;
  errno = 0;
  int r2 = ioctl(fd, DD_IOCTL_GPUMEM_FREE_ID, &f);
  int e2 = errno;
  printf("alloc=%d,%s free=%d,%s\n", r1, e1 == EACCES ? "EACCES" : e1 == ENOMEM ? "ENOMEM" : "other",
         r2, e2 == EACCES ? "EACCES" : e2 == ENOMEM ? "ENOMEM" : "other");
  return 0;
}
"""

# A library listed after libfakeinput.so in LD_PRELOAD, as Proton builds it.
LATER_SHIM = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdarg.h>
int ioctl(int fd, unsigned long request, ...) {
  typedef int (*fn)(int, unsigned long, void *);
  static fn next;
  va_list ap;
  va_start(ap, request);
  void *arg = va_arg(ap, void *);
  va_end(ap);
  if (request == 0x7E57) return 4242; /* only a library that was reached can say this */
  if (!next) next = (fn)dlsym(RTLD_NEXT, "ioctl");
  return next(fd, request, arg);
}
"""

CHAIN = r"""
#include <fcntl.h>
#include <stdio.h>
#include <sys/ioctl.h>
int main(void) {
  int fd = open("/dev/null", O_RDWR);
  printf("%d\n", ioctl(fd, 0x7E57, 0));
  return 0;
}
"""


def have_gcc():
    return shutil.which("gcc") is not None and shutil.which("g++") is not None


@unittest.skipUnless(have_gcc(), "needs gcc")
class KgslGpuobj(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def compile_c(self, name, source, *extra, shared=False):
        src = self.dir / (name + ".c")
        src.write_text(source)
        out = self.dir / name
        cmd = ["gcc", "-O1", "-Wall", "-I", str(COMPAT_DIR), "-o", str(out), str(src)]
        if shared:
            cmd[1:1] = ["-shared", "-fPIC"]
        subprocess.run(cmd + list(extra), check=True)
        return out

    def run_program(self, program, **env):
        return subprocess.run([str(program)], env=dict(os.environ, **env), capture_output=True, text=True)

    def test_denied_kernel_is_answered_with_gpuobj(self):
        result = self.run_program(self.compile_c("mock_denied", MOCK))
        self.assertEqual(result.stdout.strip(), "ok", result.stdout + result.stderr)

    def test_allowing_kernel_is_left_alone(self):
        result = self.run_program(self.compile_c("mock_allowed", MOCK_ALLOWED))
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "ok"), result.stdout)

    def build_preloads(self):
        if hasattr(self, "_libs"):
            return self._libs
        session = self.dir / "libblsession.so"
        subprocess.run(["gcc", "-shared", "-fPIC", "-O2", "-Wall", "-pthread", "-o", str(session)]
                       + sorted(map(str, PRELOAD.glob("*.c"))) + ["-ldl"], check=True)
        fake = self.dir / "libfakeinput.so"
        subprocess.run(["g++", "-shared", "-fPIC", "-O2", "-Wall", "-Wno-attributes", "-Wno-nonnull-compare",
                        "-pthread", "-std=c++17", "-static-libstdc++", "-static-libgcc",
                        "-Wl,--exclude-libs,ALL", "-o", str(fake), str(FAKEINPUT), "-ldl"], check=True)
        self._libs = (session, fake)
        return self._libs

    def test_session_preload_translates_under_a_denying_filter(self):
        program = self.compile_c("seccomp_probe", SECCOMP)
        session, fake = self.build_preloads()
        control = self.run_program(program)
        if control.returncode == 77:
            self.skipTest("seccomp filters are not available here")
        self.assertEqual(control.stdout.strip(), "alloc=-1,EACCES free=-1,EACCES")
        # The order the session writes ld.so.preload in.
        for preload in (str(session), "%s:%s" % (session, fake)):
            with self.subTest(preload=preload):
                result = self.run_program(program, LD_PRELOAD=preload)
                self.assertEqual(result.stdout.strip(), "alloc=-1,ENOMEM free=-1,ENOMEM",
                                 result.stdout + result.stderr)
                self.assertIn("using GPUOBJ_ALLOC", result.stderr)
        off = self.run_program(program, LD_PRELOAD=str(session), BL_KGSL_GPUOBJ="0")
        self.assertEqual(off.stdout.strip(), "alloc=-1,EACCES free=-1,EACCES")

    def test_fakeinput_forwards_to_libraries_after_it(self):
        session, fake = self.build_preloads()
        shim_src = self.dir / "later_shim.c"
        shim_src.write_text(LATER_SHIM)
        shim = self.dir / "liblater.so"
        subprocess.run(["gcc", "-shared", "-fPIC", "-O1", "-o", str(shim), str(shim_src), "-ldl"], check=True)
        program = self.compile_c("chain", CHAIN)
        result = self.run_program(program, LD_PRELOAD="%s:%s:%s" % (session, fake, shim))
        self.assertEqual(result.stdout.strip(), "4242", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
