/* Compile with the existing MIPS/O32 compiler and matching target headers:
 * "$MIPS_CC" -std=gnu11 -Wall -Wextra -Werror -c mips_abi_probe.c \
 *   -o "$TMPDIR/platform-abi-probe.o"
 * This checks the C ABI assumptions; it does not replace a MIPS Rust build. */
#define _GNU_SOURCE 1
#include <stddef.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/statvfs.h>
#include <sys/sem.h>
#include <sys/shm.h>
#include <time.h>

/* Match platform/abi.rs and platform/sysv.rs for the existing MIPS/O32 libc.
 * SysvTimespec uses C long. The separate clock_settime Timespec remains the
 * original two-i64 definition and is not claimed to match this C timespec. */
_Static_assert(sizeof(void *) == 4, "MIPS/O32 pointer");
_Static_assert(sizeof(size_t) == 4, "usize");
_Static_assert(sizeof(long) == 4, "c_long");
_Static_assert(sizeof(struct timespec) == 8, "SysvTimespec size");
_Static_assert(__alignof__(struct timespec) == 4, "SysvTimespec alignment");
_Static_assert(sizeof(struct sembuf) == 6, "SemBuf size");
_Static_assert(sizeof(struct shmid_ds) == 72, "ShmidDs size");
_Static_assert(__alignof__(struct shmid_ds) == 4, "ShmidDs alignment");
_Static_assert(offsetof(struct shmid_ds, shm_segsz) == 36, "segment size offset");
_Static_assert(sizeof(((struct shmid_ds *)0)->shm_segsz) == 4, "segment size width");
_Static_assert(sizeof(struct sockaddr_un) == 110, "SockaddrUn size");
_Static_assert(offsetof(struct sockaddr_un, sun_path) == 2, "SockaddrUn path");
_Static_assert(sizeof(struct statvfs) == 72, "Statvfs size");
_Static_assert(offsetof(struct statvfs, f_flag) == 40, "Statvfs flags offset");
_Static_assert(offsetof(struct statvfs, f_namemax) == 44, "Statvfs name_max offset");
_Static_assert(AF_UNIX == 1, "AF_UNIX");
_Static_assert(SOCK_DGRAM == 1, "SOCK_DGRAM");
_Static_assert(SOCK_CLOEXEC == 0x80000, "SOCK_CLOEXEC");
_Static_assert(MSG_DONTWAIT == 0x40, "MSG_DONTWAIT");
_Static_assert(O_NONBLOCK == 0x80, "O_NONBLOCK");
_Static_assert(O_NOFOLLOW == 0x20000, "O_NOFOLLOW");
static int (*const checked_semtimedop)(int, struct sembuf *, size_t,
                                      const struct timespec *) = semtimedop;
int probe(void) { return checked_semtimedop != 0; }
