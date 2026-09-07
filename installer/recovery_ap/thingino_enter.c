/* Fixed, freestanding chroot launcher for the supervised personal root. */

typedef unsigned long size_t;

enum {
    SYSCALL_EXIT = 4001,
    SYSCALL_WRITE = 4004,
    SYSCALL_EXECVE = 4011,
    SYSCALL_CHDIR = 4012,
    SYSCALL_CHROOT = 4061,
};

static __attribute__((noinline)) long call1(long number, long argument0)
{
    register long result __asm__("$2") = number;
    register long arg0 __asm__("$4") = argument0;
    register long error __asm__("$7") = 0;
    __asm__ volatile("syscall" : "+r"(result), "+r"(error) : "r"(arg0) : "memory");
    return error ? -result : result;
}

static __attribute__((noinline)) long call3(
    long number, long argument0, long argument1, long argument2)
{
    register long result __asm__("$2") = number;
    register long arg0 __asm__("$4") = argument0;
    register long arg1 __asm__("$5") = argument1;
    register long arg2 __asm__("$6") = argument2;
    register long error __asm__("$7") = 0;
    __asm__ volatile(
        "syscall" : "+r"(result), "+r"(error)
        : "r"(arg0), "r"(arg1), "r"(arg2) : "memory");
    return error ? -result : result;
}

static __attribute__((noreturn)) void fail(const char *message, size_t length)
{
    call3(SYSCALL_WRITE, 2, (long)message, (long)length);
    call1(SYSCALL_EXIT, 127);
    for (;;)
        ;
}

void _start(void)
{
    static const char root[] = "/mnt/thingino";
    static const char dot[] = ".";
    static const char slash[] = "/";
    static const char shell[] = "/bin/sh";
    static const char option[] = "-c";
    static const char command[] =
        "/etc/init.d/rcS; exec sleep 2147483647";
    static const char path[] = "PATH=/sbin:/usr/sbin:/bin:/usr/bin";
    static const char home[] = "HOME=/root";
    static const char term[] = "TERM=vt100";
    static const char failure[] = "RECOVERY_AP THINGINO_ENTER_FAIL\n";
    const char *arguments[] = {shell, option, command, 0};
    const char *environment[] = {path, home, term, 0};

    if (call1(SYSCALL_CHDIR, (long)root) < 0)
        fail(failure, sizeof(failure) - 1);
    if (call1(SYSCALL_CHROOT, (long)dot) < 0)
        fail(failure, sizeof(failure) - 1);
    if (call1(SYSCALL_CHDIR, (long)slash) < 0)
        fail(failure, sizeof(failure) - 1);
    call3(SYSCALL_EXECVE, (long)shell, (long)arguments, (long)environment);
    fail(failure, sizeof(failure) - 1);
}
