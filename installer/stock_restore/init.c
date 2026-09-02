/*
 * Same-device DCS-6100LHV2 A1 stock restore, executed only from RAM.
 *
 * There is no shell, network, exec, reboot, environment writer, arbitrary
 * path, or generic flash interface.  The program accepts fixed host-bound
 * files from FAT, proves mtd0/mtd4/mtd5 against same-device references, and
 * opens only mtd1/mtd2/mtd3 for mutation.  Stock mtd1 activation is last.
 */

#include "generated_contract.h"

typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long size_t;
typedef unsigned long long u64;

#include "freestanding_sha256.h"

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

static void digest_descriptor(long descriptor, u32 length, u8 output[32])
{
    struct dcs_sha256_context context;
    u32 remaining = length;
    dcs_sha256_init(&context);
    while (remaining) {
        u32 amount = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        read_exact(descriptor, io_buffer, amount);
        dcs_sha256_update(&context, io_buffer, amount);
        remaining -= amount;
    }
    if (call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("RESTORE FAIL exact_size\n");
    dcs_sha256_final(&context, output);
}

static void verify_file(
    const char *path, u32 size, const u8 expected_digest[32])
{
    u8 digest[32];
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (descriptor < 0)
        FAIL("RESTORE FAIL source_open\n");
    digest_descriptor(descriptor, size, digest);
    close_checked(descriptor);
    if (!dcs_bytes_equal(digest, expected_digest, 32))
        FAIL("RESTORE FAIL source_identity\n");
}

static void verify_mtd(
    const char *path, u32 expected_size, int expected_writable)
{
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    int actual_writable;
    if (descriptor < 0 ||
        call3(SYSCALL_IOCTL, descriptor, MEMGETINFO, (long)&mtd_info) != 0)
        FAIL("RESTORE FAIL mtd_info\n");
    close_checked(descriptor);
    actual_writable = (mtd_info.flags & MTD_WRITEABLE) != 0;
    if (mtd_info.type != MTD_NORFLASH ||
        mtd_info.size != expected_size ||
        mtd_info.erasesize != MTD_ERASE_SIZE ||
        mtd_info.writesize != MTD_WRITE_SIZE ||
        actual_writable != expected_writable)
        FAIL("RESTORE FAIL mtd_layout\n");
}

static void verify_target_model_marker(void)
{
    static const u8 marker[] = {'6', '1', '0', '0', 'L', 'H', 'V', '2'};
    long descriptor = call2(SYSCALL_OPEN, (long)"/dev/mtd0", O_RDONLY);
    if (descriptor < 0)
        FAIL("RESTORE FAIL target_model_open\n");
    seek_checked(descriptor, MODEL_MARKER_OFFSET);
    read_exact(descriptor, io_buffer, sizeof(marker));
    close_checked(descriptor);
    if (!dcs_bytes_equal(io_buffer, marker, sizeof(marker)))
        FAIL("RESTORE FAIL target_model\n");
}

static void verify_exact_layout(void)
{
    long unexpected;
    verify_mtd("/dev/mtd0", 0x00040000, 0);
    verify_mtd(MTD1_PATH, 0x001c0000, 1);
    verify_mtd(MTD2_PATH, 0x00480000, 1);
    verify_mtd(MTD3_PATH, 0x007c0000, 1);
    verify_mtd("/dev/mtd4", 0x00180000, 0);
    verify_mtd("/dev/mtd5", 0x00040000, 0);
    verify_target_model_marker();
    unexpected = call2(SYSCALL_OPEN, (long)"/dev/mtd6", O_RDONLY);
    if (unexpected >= 0) {
        close_checked(unexpected);
        FAIL("RESTORE FAIL extra_mtd\n");
    }
    EMIT("RESTORE exact_bounded_layout_verified\n");
}

static void compare_paths(
    const char *left_path, const char *right_path, u32 length)
{
    u32 remaining = length;
    long left = call2(SYSCALL_OPEN, (long)left_path, O_RDONLY);
    long right = call2(SYSCALL_OPEN, (long)right_path, O_RDONLY);
    if (left < 0 || right < 0)
        FAIL("RESTORE FAIL compare_open\n");
    while (remaining) {
        u32 amount = remaining < COPY_SIZE / 2 ? remaining : COPY_SIZE / 2;
        read_exact(left, io_buffer, amount);
        read_exact(right, io_buffer + COPY_SIZE / 2, amount);
        if (!dcs_bytes_equal(io_buffer, io_buffer + COPY_SIZE / 2, amount))
            FAIL("RESTORE FAIL same_device\n");
        remaining -= amount;
    }
    if (call3(SYSCALL_READ, left, (long)io_buffer, 1) != 0 ||
        call3(SYSCALL_READ, right, (long)io_buffer, 1) != 0)
        FAIL("RESTORE FAIL compare_size\n");
    close_checked(left);
    close_checked(right);
}

static void verify_protected(void)
{
    verify_file(KEEP0_FILE, KEEP0_SIZE, KEEP0_SHA256);
    verify_file(KEEP4_FILE, KEEP4_SIZE, KEEP4_SHA256);
    verify_file(KEEP5_FILE, KEEP5_SIZE, KEEP5_SHA256);
    compare_paths("/dev/mtd0", KEEP0_FILE, KEEP0_SIZE);
    compare_paths("/dev/mtd4", KEEP4_FILE, KEEP4_SIZE);
    compare_paths("/dev/mtd5", KEEP5_FILE, KEEP5_SIZE);
}

static void erase_range(long descriptor, u32 start, u32 length)
{
    struct erase_info_user erase;
    u32 current;
    if (!length || start % MTD_ERASE_SIZE || length % MTD_ERASE_SIZE ||
        start + length < start)
        FAIL("RESTORE FAIL erase_alignment\n");
    erase.length = MTD_ERASE_SIZE;
    for (current = start; current < start + length; current += MTD_ERASE_SIZE) {
        erase.start = current;
        if (call3(SYSCALL_IOCTL, descriptor, MEMERASE, (long)&erase) != 0)
            FAIL("RESTORE FAIL erase\n");
    }
}

static void copy_file_region_to_mtd(
    const char *source_path,
    const char *mtd_path,
    u32 source_offset,
    u32 mtd_offset,
    u32 length)
{
    u32 remaining = length;
    long source = call2(SYSCALL_OPEN, (long)source_path, O_RDONLY);
    long destination = call2(SYSCALL_OPEN, (long)mtd_path, O_RDWR);
    if (source < 0 || destination < 0 || length % MTD_WRITE_SIZE)
        FAIL("RESTORE FAIL bounded_write_open\n");
    seek_checked(source, source_offset);
    seek_checked(destination, mtd_offset);
    while (remaining) {
        u32 amount = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        u32 page;
        read_exact(source, io_buffer, amount);
        for (page = 0; page < amount; page += MTD_WRITE_SIZE)
            write_exact(destination, io_buffer + page, MTD_WRITE_SIZE);
        remaining -= amount;
    }
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("RESTORE FAIL write_sync\n");
    close_checked(source);
    close_checked(destination);
}

static void digest_mtd(
    const char *path, u32 size, const u8 expected_digest[32])
{
    u8 digest[32];
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (descriptor < 0)
        FAIL("RESTORE FAIL readback_open\n");
    digest_descriptor(descriptor, size, digest);
    close_checked(descriptor);
    if (!dcs_bytes_equal(digest, expected_digest, 32))
        FAIL("RESTORE FAIL physical_readback\n");
}

static void verify_kernel_before_activation(void)
{
    struct dcs_sha256_context context;
    u8 digest[32];
    u32 remaining = MTD1_SIZE - ACTIVATION_SIZE;
    long descriptor = call2(SYSCALL_OPEN, (long)MTD1_PATH, O_RDONLY);
    if (descriptor < 0)
        FAIL("RESTORE FAIL preactivation_open\n");
    seek_checked(descriptor, ACTIVATION_SIZE);
    dcs_sha256_init(&context);
    dcs_sha256_update(&context, activation_buffer, ACTIVATION_SIZE);
    while (remaining) {
        u32 amount = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        read_exact(descriptor, io_buffer, amount);
        dcs_sha256_update(&context, io_buffer, amount);
        remaining -= amount;
    }
    close_checked(descriptor);
    dcs_sha256_final(&context, digest);
    if (!dcs_bytes_equal(digest, MTD1_SHA256, 32))
        FAIL("RESTORE FAIL preactivation_digest\n");
    EMIT("RESTORE preactivation_kernel_verified\n");
}

static void restore_complete_partition(
    const char *source_path,
    const char *mtd_path,
    u32 size,
    const u8 expected_digest[32])
{
    long descriptor;
    verify_file(source_path, size, expected_digest);
    descriptor = call2(SYSCALL_OPEN, (long)mtd_path, O_RDWR);
    if (descriptor < 0)
        FAIL("RESTORE FAIL mutation_open\n");
    erase_range(descriptor, 0, size);
    close_checked(descriptor);
    copy_file_region_to_mtd(source_path, mtd_path, 0, 0, size);
    digest_mtd(mtd_path, size, expected_digest);
    verify_file(source_path, size, expected_digest);
}

static int exact_status_exists(
    const char *path, const char *value, u32 length);

static void write_new_status(
    const char *temporary_path, const char *path, const char *value, u32 length)
{
    long descriptor = call2(SYSCALL_OPEN, (long)temporary_path, O_RDONLY);
    if (descriptor == -ENOENT) {
        descriptor = call3(
            SYSCALL_OPEN, (long)temporary_path,
            O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (descriptor < 0)
            FAIL("RESTORE FAIL status_temporary_exists\n");
        write_exact(descriptor, (const u8 *)value, length);
        if (call1(SYSCALL_SYNC, 0) != 0)
            FAIL("RESTORE FAIL status_temporary_sync\n");
        close_checked(descriptor);
    } else if (descriptor < 0) {
        FAIL("RESTORE FAIL status_temporary_open\n");
    } else {
        close_checked(descriptor);
    }
    descriptor = call2(SYSCALL_OPEN, (long)temporary_path, O_RDONLY);
    if (descriptor < 0)
        FAIL("RESTORE FAIL status_temporary_readback_open\n");
    read_exact(descriptor, io_buffer, length);
    if (!dcs_bytes_equal(io_buffer, (const u8 *)value, length) ||
        call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("RESTORE FAIL status_temporary_readback\n");
    close_checked(descriptor);
    if (call2(SYSCALL_RENAME, (long)temporary_path, (long)path) != 0 ||
        call1(SYSCALL_SYNC, 0) != 0)
        FAIL("RESTORE FAIL status_activation\n");
    if (call2(SYSCALL_OPEN, (long)temporary_path, O_RDONLY) != -ENOENT)
        FAIL("RESTORE FAIL status_temporary_still_present\n");
    if (!exact_status_exists(path, value, length))
        FAIL("RESTORE FAIL status_activation_readback\n");
}

static int exact_status_exists(const char *path, const char *value, u32 length)
{
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (descriptor == -ENOENT)
        return 0;
    if (descriptor < 0)
        FAIL("RESTORE FAIL status_open\n");
    read_exact(descriptor, io_buffer, length);
    if (!dcs_bytes_equal(io_buffer, (const u8 *)value, length) ||
        call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("RESTORE FAIL status_existing\n");
    close_checked(descriptor);
    return 1;
}

static void require_absent_status(const char *path)
{
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (descriptor >= 0) {
        close_checked(descriptor);
        FAIL("RESTORE FAIL terminal_status_exists\n");
    }
    if (descriptor != -ENOENT)
        FAIL("RESTORE FAIL terminal_status_open\n");
}

static void consume_authorization(void)
{
    static const char run_status[] =
        "write_set=mtd3,mtd2,mtd1;activation=last\n";
    int retry;
    verify_file(AUTH_FILE, AUTH_SIZE, AUTH_SHA256);
    require_absent_status(COMPLETE_FILE);
    require_absent_status(COMPLETE_TEMP_FILE);
    retry = exact_status_exists(RUN_FILE, run_status, sizeof(run_status) - 1);
    if (!retry)
        write_new_status(
            RUN_TEMP_FILE, RUN_FILE, run_status, sizeof(run_status) - 1);
    if (call1(SYSCALL_UNLINK, (long)AUTH_FILE) != 0 ||
        call1(SYSCALL_SYNC, 0) != 0)
        FAIL("RESTORE FAIL authorization_consume\n");
    if (call2(SYSCALL_OPEN, (long)AUTH_FILE, O_RDONLY) != -ENOENT)
        FAIL("RESTORE FAIL authorization_still_present\n");
    if (retry)
        EMIT("RESTORE retry_authorization_consumed\n");
    EMIT("RESTORE live_write_authorization_consumed\n");
}

static void load_mmc_and_mount_card(void)
{
    static const struct timespec32 delay = {0, 100000000};
    struct dcs_sha256_context context;
    u8 digest[32];
    u32 attempt;
    u32 offset = 0;
    long descriptor = call2(SYSCALL_OPEN, (long)MMC_MODULE_PATH, O_RDONLY);
    if (descriptor < 0)
        FAIL("RESTORE FAIL mmc_module_open\n");
    while (offset < MMC_MODULE_SIZE) {
        long amount = call3(
            SYSCALL_READ, descriptor, (long)(module_buffer + offset),
            MMC_MODULE_SIZE - offset);
        if (amount <= 0)
            FAIL("RESTORE FAIL mmc_module_read\n");
        offset += (u32)amount;
    }
    if (call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("RESTORE FAIL mmc_module_size\n");
    close_checked(descriptor);
    dcs_sha256_init(&context);
    dcs_sha256_update(&context, module_buffer, MMC_MODULE_SIZE);
    dcs_sha256_final(&context, digest);
    if (!dcs_bytes_equal(digest, MMC_MODULE_SHA256, 32))
        FAIL("RESTORE FAIL mmc_module_digest\n");
    if (call3(
            SYSCALL_INIT_MODULE, (long)module_buffer, MMC_MODULE_SIZE,
            (long)"cd_gpio_pin=59") != 0)
        FAIL("RESTORE FAIL mmc_module_load\n");
    for (attempt = 0; attempt < 100; ++attempt) {
        descriptor = call2(SYSCALL_OPEN, (long)"/dev/mmcblk0p1", O_RDONLY);
        if (descriptor >= 0) {
            close_checked(descriptor);
            break;
        }
        call2(SYSCALL_NANOSLEEP, (long)&delay, 0);
    }
    if (attempt == 100)
        FAIL("RESTORE FAIL sd_partition_missing\n");
    make_directory("/card", 0500);
    mount_checked(
        "/dev/mmcblk0p1", "/card", "vfat", MS_NOSUID | MS_NODEV | MS_NOEXEC,
        "shortname=winnt");
}

static void restore_stock(void)
{
    long descriptor;
    load_mmc_and_mount_card();
    verify_file(MTD1_FILE, MTD1_SIZE, MTD1_SHA256);
    verify_file(MTD2_FILE, MTD2_SIZE, MTD2_SHA256);
    verify_file(MTD3_FILE, MTD3_SIZE, MTD3_SHA256);
    verify_protected();
    EMIT("RESTORE same_device_binding_verified\n");
    consume_authorization();

    restore_complete_partition(MTD3_FILE, MTD3_PATH, MTD3_SIZE, MTD3_SHA256);
    EMIT("RESTORE mtd3_physical_readback_verified\n");
    restore_complete_partition(MTD2_FILE, MTD2_PATH, MTD2_SIZE, MTD2_SHA256);
    EMIT("RESTORE mtd2_physical_readback_verified\n");

    verify_file(MTD1_FILE, MTD1_SIZE, MTD1_SHA256);
    descriptor = call2(SYSCALL_OPEN, (long)MTD1_PATH, O_RDWR);
    if (descriptor < 0)
        FAIL("RESTORE FAIL mtd1_tail_open\n");
    erase_range(descriptor, ACTIVATION_SIZE, MTD1_SIZE - ACTIVATION_SIZE);
    close_checked(descriptor);
    copy_file_region_to_mtd(
        MTD1_FILE, MTD1_PATH, ACTIVATION_SIZE, ACTIVATION_SIZE,
        MTD1_SIZE - ACTIVATION_SIZE);
    {
        u32 remaining = MTD1_SIZE - ACTIVATION_SIZE;
        long source = call2(SYSCALL_OPEN, (long)MTD1_FILE, O_RDONLY);
        long target = call2(SYSCALL_OPEN, (long)MTD1_PATH, O_RDONLY);
        if (source < 0 || target < 0)
            FAIL("RESTORE FAIL mtd1_tail_readback_open\n");
        seek_checked(source, ACTIVATION_SIZE);
        seek_checked(target, ACTIVATION_SIZE);
        while (remaining) {
            u32 amount = remaining < COPY_SIZE / 2 ? remaining : COPY_SIZE / 2;
            read_exact(source, io_buffer, amount);
            read_exact(target, io_buffer + COPY_SIZE / 2, amount);
            if (!dcs_bytes_equal(io_buffer, io_buffer + COPY_SIZE / 2, amount))
                FAIL("RESTORE FAIL mtd1_tail_readback\n");
            remaining -= amount;
        }
        close_checked(source);
        close_checked(target);
    }
    verify_file(MTD1_FILE, MTD1_SIZE, MTD1_SHA256);
    EMIT("RESTORE mtd1_tail_physical_readback_verified\n");

    descriptor = call2(SYSCALL_OPEN, (long)MTD1_FILE, O_RDONLY);
    if (descriptor < 0)
        FAIL("RESTORE FAIL activation_source\n");
    read_exact(descriptor, activation_buffer, ACTIVATION_SIZE);
    close_checked(descriptor);
    verify_kernel_before_activation();
    descriptor = call2(SYSCALL_OPEN, (long)MTD1_PATH, O_RDWR);
    if (descriptor < 0)
        FAIL("RESTORE FAIL activation_open\n");
    erase_range(descriptor, 0, ACTIVATION_SIZE);
    seek_checked(descriptor, 0);
    {
        u32 offset;
        for (offset = 0; offset < ACTIVATION_SIZE; offset += MTD_WRITE_SIZE)
            write_exact(descriptor, activation_buffer + offset, MTD_WRITE_SIZE);
    }
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("RESTORE FAIL activation_sync\n");
    close_checked(descriptor);
    digest_mtd(MTD1_PATH, MTD1_SIZE, MTD1_SHA256);
    verify_file(MTD1_FILE, MTD1_SIZE, MTD1_SHA256);
    EMIT("RESTORE stock_activation_physical_readback_verified\n");

    digest_mtd(MTD2_PATH, MTD2_SIZE, MTD2_SHA256);
    digest_mtd(MTD3_PATH, MTD3_SIZE, MTD3_SHA256);
    verify_protected();
    write_new_status(
        COMPLETE_TEMP_FILE, COMPLETE_FILE,
        "physical_readback_verified=true;write_set=mtd3,mtd2,mtd1\n",
        sizeof("physical_readback_verified=true;write_set=mtd3,mtd2,mtd1\n") - 1);
    set_led(RED_LED, '0');
    set_led(GREEN_LED, '1');
    EMIT("RESTORE COMPLETE physical_readback_verified\n");
}

void _start(void)
{
    long result;
    if (call1(SYSCALL_MLOCKALL, MCL_CURRENT | MCL_FUTURE) != 0)
        FAIL("RESTORE FAIL ram_residency\n");
    EMIT("RESTORE ram_residency_locked\n");
    make_directory("/dev", 0755);
    result = call5(
        SYSCALL_MOUNT, (long)"devtmpfs", (long)"/dev", (long)"devtmpfs",
        MS_NOSUID | MS_NOEXEC, (long)"mode=0755");
    if (result != 0 && result != -EBUSY)
        FAIL("RESTORE FAIL devtmpfs\n");
    make_directory("/sys", 0555);
    mount_checked("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, 0);
    status_leds_ready = 1;
    set_led(GREEN_LED, '1');
    set_led(RED_LED, '1');
    verify_exact_layout();
    restore_stock();
    for (;;)
        call1(SYSCALL_PAUSE, 0);
}
