enum {
    SYSCALL_READ = 4003,
    SYSCALL_WRITE = 4004,
    SYSCALL_OPEN = 4005,
    SYSCALL_CLOSE = 4006,
    SYSCALL_UNLINK = 4010,
    SYSCALL_LSEEK = 4019,
    SYSCALL_MOUNT = 4021,
    SYSCALL_PAUSE = 4029,
    SYSCALL_SYNC = 4036,
    SYSCALL_RENAME = 4038,
    SYSCALL_MKDIR = 4039,
    SYSCALL_IOCTL = 4054,
    SYSCALL_INIT_MODULE = 4128,
    SYSCALL_NANOSLEEP = 4166,
    SYSCALL_MLOCKALL = 4156,
};

enum {
    O_RDONLY = 0,
    O_WRONLY = 1,
    O_RDWR = 2,
    O_CREAT = 0x0100,
    O_EXCL = 0x0400,
    MS_NOSUID = 2,
    MS_NODEV = 4,
    MS_NOEXEC = 8,
    ENOENT = 2,
    EBUSY = 16,
    EEXIST = 17,
    MTD_NORFLASH = 3,
    MTD_WRITEABLE = 0x400,
    MEMGETINFO = 0x40204d01,
    MTD_ERASE_SIZE = 0x8000,
    MTD_WRITE_SIZE = 0x100,
    ACTIVATION_SIZE = 0x10000,
    COPY_SIZE = 0x10000,
    MODEL_MARKER_OFFSET = 223235,
    MCL_CURRENT = 1,
    MCL_FUTURE = 2,
};

/* MIPS O32: _IOC_WRITE=4 and _IOC_DIRSHIFT=29. */
#define MEMERASE ((long)0x80084d02UL)

#define MTD1_PATH "/dev/mtd1"
#define MTD2_PATH "/dev/mtd2"
#define MTD3_PATH "/dev/mtd3"
#define MTD1_FILE "/card/STOCK1.BIN"
#define MTD2_FILE "/card/STOCK2.BIN"
#define MTD3_FILE "/card/STOCK3.BIN"
#define KEEP0_FILE "/card/KEEP0.BIN"
#define KEEP4_FILE "/card/KEEP4.BIN"
#define KEEP5_FILE "/card/KEEP5.BIN"
#define AUTH_FILE "/card/RESTORE.GO"
#define RUN_FILE "/card/RESTORE.RUN"
#define RUN_TEMP_FILE "/card/.RESTORE.RUN.part"
#define COMPLETE_FILE "/card/RESTORE.OK"
#define COMPLETE_TEMP_FILE "/card/.RESTORE.OK.part"
#define GREEN_LED "/sys/class/leds/led_g/brightness"
#define RED_LED "/sys/class/leds/led_r/brightness"

typedef char activation_must_be_complete_erase_blocks[
    ACTIVATION_SIZE % MTD_ERASE_SIZE == 0 ? 1 : -1];
typedef char stock_kernel_must_have_a_nonactivation_tail[
    MTD1_SIZE > ACTIVATION_SIZE ? 1 : -1];

struct mtd_info_user {
    u8 type;
    u8 padding0[3];
    u32 flags;
    u32 size;
    u32 erasesize;
    u32 writesize;
    u32 oobsize;
    u64 padding1;
};

struct erase_info_user {
    u32 start;
    u32 length;
};

struct timespec32 {
    long seconds;
    long nanoseconds;
};

static u8 io_buffer[COPY_SIZE] __attribute__((aligned(16)));
static u8 activation_buffer[ACTIVATION_SIZE] __attribute__((aligned(16)));
static u8 module_buffer[MMC_MODULE_SIZE] __attribute__((aligned(16)));
static struct mtd_info_user mtd_info;
static int status_leds_ready;

void *memcpy(void *destination, const void *source, size_t length)
{
    u8 *output = destination;
    const u8 *input = source;
    size_t index;
    for (index = 0; index < length; ++index)
        output[index] = input[index];
    return destination;
}

static __attribute__((noinline)) long call1(long number, long argument0)
{
    register long result __asm__("$2") = number;
    register long arg0 __asm__("$4") = argument0;
    register long error __asm__("$7") = 0;
    __asm__ volatile(
        "syscall" : "+r"(result), "+r"(error) : "r"(arg0) : "memory");
    return error ? -result : result;
}

static __attribute__((noinline)) long call2(
    long number, long argument0, long argument1)
{
    register long result __asm__("$2") = number;
    register long arg0 __asm__("$4") = argument0;
    register long arg1 __asm__("$5") = argument1;
    register long error __asm__("$7") = 0;
    __asm__ volatile(
        "syscall" : "+r"(result), "+r"(error)
        : "r"(arg0), "r"(arg1) : "memory");
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

static __attribute__((noinline)) long call5(
    long number,
    long argument0,
    long argument1,
    long argument2,
    long argument3,
    long argument4)
{
    register long result __asm__("$2") = number;
    register long arg0 __asm__("$4") = argument0;
    register long arg1 __asm__("$5") = argument1;
    register long arg2 __asm__("$6") = argument2;
    register long arg3 __asm__("$7") = argument3;
    register long error __asm__("$8") = 0;
    __asm__ volatile(
        "addiu $sp, $sp, -32\n\t"
        "sw %[fifth], 16($sp)\n\t"
        "syscall\n\t"
        "move %[error], $7\n\t"
        "addiu $sp, $sp, 32"
        : "+r"(result), [error] "+r"(error)
        : "r"(arg0), "r"(arg1), "r"(arg2), "r"(arg3),
          [fifth] "r"(argument4)
        : "$7", "memory");
    return error ? -result : result;
}

static void emit(const char *message, size_t length)
{
    size_t offset = 0;
    while (offset < length) {
        long amount = call3(
            SYSCALL_WRITE, 1, (long)(message + offset),
            (long)(length - offset));
        if (amount <= 0)
            break;
        offset += (size_t)amount;
    }
}

#define EMIT(message) emit((message), sizeof(message) - 1)

static void set_led(const char *path, char value)
{
    long descriptor;
    if (!status_leds_ready)
        return;
    descriptor = call2(SYSCALL_OPEN, (long)path, O_WRONLY);
    if (descriptor >= 0) {
        call3(SYSCALL_WRITE, descriptor, (long)&value, 1);
        call1(SYSCALL_CLOSE, descriptor);
    }
}

static __attribute__((noreturn)) void fail(const char *message, size_t length)
{
    set_led(GREEN_LED, '0');
    set_led(RED_LED, '1');
    emit(message, length);
    for (;;)
        call1(SYSCALL_PAUSE, 0);
}

#define FAIL(message) fail((message), sizeof(message) - 1)

static void close_checked(long descriptor)
{
    if (call1(SYSCALL_CLOSE, descriptor) != 0)
        FAIL("RESTORE FAIL close\n");
}

static void seek_checked(long descriptor, u32 offset)
{
    if (call3(SYSCALL_LSEEK, descriptor, offset, 0) != (long)offset)
        FAIL("RESTORE FAIL seek\n");
}

static void read_exact(long descriptor, u8 *output, u32 length)
{
    u32 offset = 0;
    while (offset < length) {
        long amount = call3(
            SYSCALL_READ, descriptor, (long)(output + offset), length - offset);
        if (amount <= 0)
            FAIL("RESTORE FAIL read\n");
        offset += (u32)amount;
    }
}

static void write_exact(long descriptor, const u8 *input, u32 length)
{
    u32 offset = 0;
    while (offset < length) {
        long amount = call3(
            SYSCALL_WRITE, descriptor, (long)(input + offset), length - offset);
        if (amount <= 0)
            FAIL("RESTORE FAIL write\n");
        offset += (u32)amount;
    }
}

static void make_directory(const char *path, long mode)
{
    long result = call2(SYSCALL_MKDIR, (long)path, mode);
    if (result != 0 && result != -EEXIST)
        FAIL("RESTORE FAIL directory\n");
}

static void mount_checked(
    const char *source,
    const char *target,
    const char *filesystem,
    long flags,
    const char *data)
{
    if (call5(
            SYSCALL_MOUNT, (long)source, (long)target, (long)filesystem,
            flags, (long)data) != 0)
        FAIL("RESTORE FAIL mount\n");
}
