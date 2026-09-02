/*
 * Read-only RAM collector for DCS-6100LHV2 A1 backup and recovery preflight.
 *
 * NOR is only opened O_RDONLY. The only writable filesystem is a fixed new
 * directory on the SD card. This collector deliberately has no erase, MTD
 * write, shell, exec, network, rename, unlink, or arbitrary path interface.
 */

#include "generated_contract.h"

typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long size_t;
typedef unsigned long long u64;

enum {
    SYSCALL_READ = 4003,
    SYSCALL_WRITE = 4004,
    SYSCALL_OPEN = 4005,
    SYSCALL_CLOSE = 4006,
    SYSCALL_LSEEK = 4019,
    SYSCALL_MOUNT = 4021,
    SYSCALL_PAUSE = 4029,
    SYSCALL_SYNC = 4036,
    SYSCALL_MKDIR = 4039,
    SYSCALL_IOCTL = 4054,
    SYSCALL_INIT_MODULE = 4128,
    SYSCALL_NANOSLEEP = 4166,
};

enum {
    O_RDONLY = 0,
    O_WRONLY = 1,
    O_CREAT = 0x0100,
    O_EXCL = 0x0400,
    MS_RDONLY = 1,
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
    COPY_SIZE = 0x10000,
    MODEL_MARKER_OFFSET = 223235,
};

#if FUNCTIONAL_CAPTURE
#define FULL_BACKUP_ROOT "/card/DCS6100F"
#define OUTPUT_ROOT FULL_BACKUP_ROOT
#else
#define FULL_BACKUP_ROOT "/card/DCS6100B"
#define OUTPUT_ROOT "/card/DCS6100A1"
#endif
#define VENDOR_ROOT OUTPUT_ROOT "/vendor"
#define VENDOR_FILES VENDOR_ROOT "/files"
#define PRESERVED_ROOT OUTPUT_ROOT "/preserved"
#define PROTECTED_ROOT "/card/DCS6100P"
#define PROTECTED_FILES PROTECTED_ROOT "/preserved"
#define FULL_COPY_A FULL_BACKUP_ROOT "/copy-a"
#define FULL_COPY_B FULL_BACKUP_ROOT "/copy-b"

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

struct timespec32 {
    long seconds;
    long nanoseconds;
};

struct sha256_context {
    u32 state[8];
    u64 length;
    u8 block[64];
    u32 used;
};

static u8 io_buffer[COPY_SIZE] __attribute__((aligned(16)));
static u8 module_buffer[MMC_MODULE_SIZE] __attribute__((aligned(16)));
static struct mtd_info_user mtd_info;

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
    __asm__ volatile("syscall" : "+r"(result), "+r"(error) : "r"(arg0) : "memory");
    return error ? -result : result;
}

static __attribute__((noinline)) long call2(long number, long argument0, long argument1)
{
    register long result __asm__("$2") = number;
    register long arg0 __asm__("$4") = argument0;
    register long arg1 __asm__("$5") = argument1;
    register long error __asm__("$7") = 0;
    __asm__ volatile(
        "syscall" : "+r"(result), "+r"(error) : "r"(arg0), "r"(arg1) : "memory");
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
            SYSCALL_WRITE, 1, (long)(message + offset), (long)(length - offset));
        if (amount <= 0)
            break;
        offset += (size_t)amount;
    }
}

#define EMIT(message) emit((message), sizeof(message) - 1)

static __attribute__((noreturn)) void fail(const char *message, size_t length)
{
    emit(message, length);
    for (;;)
        call1(SYSCALL_PAUSE, 0);
}

#define FAIL(message) fail((message), sizeof(message) - 1)

static u32 rotate_right(u32 value, u32 bits)
{
    return (value >> bits) | (value << (32 - bits));
}

static void sha256_transform(struct sha256_context *context, const u8 *block)
{
    static const u32 constants[64] = {
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
        0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
        0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
        0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
        0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
        0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
        0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
        0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    };
    u32 words[64];
    u32 a, b, c, d, e, f, g, h, index;
    for (index = 0; index < 16; ++index) {
        u32 offset = index * 4;
        words[index] = ((u32)block[offset] << 24) |
            ((u32)block[offset + 1] << 16) |
            ((u32)block[offset + 2] << 8) | block[offset + 3];
    }
    for (index = 16; index < 64; ++index) {
        u32 x = words[index - 15];
        u32 y = words[index - 2];
        u32 s0 = rotate_right(x, 7) ^ rotate_right(x, 18) ^ (x >> 3);
        u32 s1 = rotate_right(y, 17) ^ rotate_right(y, 19) ^ (y >> 10);
        words[index] = words[index - 16] + s0 + words[index - 7] + s1;
    }
    a = context->state[0]; b = context->state[1];
    c = context->state[2]; d = context->state[3];
    e = context->state[4]; f = context->state[5];
    g = context->state[6]; h = context->state[7];
    for (index = 0; index < 64; ++index) {
        u32 sum1 = rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
        u32 choice = (e & f) ^ ((~e) & g);
        u32 temporary1 = h + sum1 + choice + constants[index] + words[index];
        u32 sum0 = rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
        u32 majority = (a & b) ^ (a & c) ^ (b & c);
        u32 temporary2 = sum0 + majority;
        h = g; g = f; f = e; e = d + temporary1;
        d = c; c = b; b = a; a = temporary1 + temporary2;
    }
    context->state[0] += a; context->state[1] += b;
    context->state[2] += c; context->state[3] += d;
    context->state[4] += e; context->state[5] += f;
    context->state[6] += g; context->state[7] += h;
}

static void sha256_init(struct sha256_context *context)
{
    static const u32 initial[8] = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    };
    u32 index;
    for (index = 0; index < 8; ++index)
        context->state[index] = initial[index];
    context->length = 0;
    context->used = 0;
}

static void sha256_update(struct sha256_context *context, const u8 *data, size_t length)
{
    size_t index;
    context->length += length;
    for (index = 0; index < length; ++index) {
        context->block[context->used++] = data[index];
        if (context->used == 64) {
            sha256_transform(context, context->block);
            context->used = 0;
        }
    }
}

static void sha256_final(struct sha256_context *context, u8 digest[32])
{
    u64 bits = context->length * 8;
    u32 index;
    context->block[context->used++] = 0x80;
    if (context->used > 56) {
        while (context->used < 64)
            context->block[context->used++] = 0;
        sha256_transform(context, context->block);
        context->used = 0;
    }
    while (context->used < 56)
        context->block[context->used++] = 0;
    for (index = 0; index < 8; ++index)
        context->block[56 + index] = (u8)(bits >> (56 - index * 8));
    sha256_transform(context, context->block);
    for (index = 0; index < 8; ++index) {
        digest[index * 4] = (u8)(context->state[index] >> 24);
        digest[index * 4 + 1] = (u8)(context->state[index] >> 16);
        digest[index * 4 + 2] = (u8)(context->state[index] >> 8);
        digest[index * 4 + 3] = (u8)context->state[index];
    }
}

static int bytes_equal(const u8 *left, const u8 *right, size_t length)
{
    u8 difference = 0;
    size_t index;
    for (index = 0; index < length; ++index)
        difference |= left[index] ^ right[index];
    return difference == 0;
}

static void close_checked(long descriptor)
{
    if (call1(SYSCALL_CLOSE, descriptor) != 0)
        FAIL("COLLECT FAIL close\n");
}

static void write_exact(long descriptor, const u8 *input, u32 length)
{
    u32 offset = 0;
    while (offset < length) {
        long amount = call3(
            SYSCALL_WRITE, descriptor, (long)(input + offset), length - offset);
        if (amount <= 0)
            FAIL("COLLECT FAIL sd_write\n");
        offset += (u32)amount;
    }
}

static void make_new_directory(const char *path, long mode)
{
    if (call2(SYSCALL_MKDIR, (long)path, mode) != 0)
        FAIL("COLLECT FAIL output_exists_or_mkdir\n");
}

static void make_mount_directory(const char *path, long mode)
{
    long result = call2(SYSCALL_MKDIR, (long)path, mode);
    if (result != 0 && result != -EEXIST)
        FAIL("COLLECT FAIL mount_directory\n");
}

static void mount_checked(
    const char *source,
    const char *target,
    const char *filesystem,
    long flags,
    const char *data)
{
    if (call5(
            SYSCALL_MOUNT,
            (long)source,
            (long)target,
            (long)filesystem,
            flags,
            (long)data) != 0)
        FAIL("COLLECT FAIL mount\n");
}

static void digest_descriptor(long descriptor, u32 length, u8 output[32])
{
    struct sha256_context context;
    u32 remaining = length;
    sha256_init(&context);
    while (remaining) {
        u32 request = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        u32 offset = 0;
        while (offset < request) {
            long amount = call3(
                SYSCALL_READ,
                descriptor,
                (long)(io_buffer + offset),
                request - offset);
            if (amount <= 0)
                FAIL("COLLECT FAIL bounded_read\n");
            offset += (u32)amount;
        }
        sha256_update(&context, io_buffer, request);
        remaining -= request;
    }
    if (call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("COLLECT FAIL source_size\n");
    sha256_final(&context, output);
}

static void verify_mtd(const char *path, u32 expected_size)
{
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (descriptor < 0 ||
        call3(SYSCALL_IOCTL, descriptor, MEMGETINFO, (long)&mtd_info) != 0)
        FAIL("COLLECT FAIL mtd_info\n");
    close_checked(descriptor);
    if (mtd_info.type != MTD_NORFLASH ||
        (mtd_info.flags & MTD_WRITEABLE) != 0 ||
        mtd_info.size != expected_size ||
        mtd_info.erasesize != MTD_ERASE_SIZE ||
        mtd_info.writesize != MTD_WRITE_SIZE)
        FAIL("COLLECT FAIL mtd_layout_or_writeable\n");
}

static void verify_target_model_marker(void)
{
    static const u8 marker[] = {'6', '1', '0', '0', 'L', 'H', 'V', '2'};
    long descriptor = call2(SYSCALL_OPEN, (long)"/dev/mtd0", O_RDONLY);
    if (descriptor < 0 ||
        call3(SYSCALL_LSEEK, descriptor, MODEL_MARKER_OFFSET, 0) !=
            MODEL_MARKER_OFFSET ||
        call3(SYSCALL_READ, descriptor, (long)io_buffer, sizeof(marker)) !=
            sizeof(marker) ||
        !bytes_equal(io_buffer, marker, sizeof(marker)))
        FAIL("COLLECT FAIL target_model\n");
    close_checked(descriptor);
}

static void verify_exact_read_only_layout(void)
{
    long unexpected;
    verify_mtd("/dev/mtd0", 0x00040000);
    verify_mtd("/dev/mtd1", 0x001c0000);
    verify_mtd("/dev/mtd2", 0x00480000);
    verify_mtd("/dev/mtd3", 0x007c0000);
    verify_mtd("/dev/mtd4", 0x00180000);
    verify_mtd("/dev/mtd5", 0x00040000);
    verify_target_model_marker();
    unexpected = call2(SYSCALL_OPEN, (long)"/dev/mtd6", O_RDONLY);
    if (unexpected >= 0) {
        close_checked(unexpected);
        FAIL("COLLECT FAIL extra_mtd\n");
    }
    EMIT("COLLECT exact_read_only_layout_verified\n");
}

static void write_new_file(const char *path, const char *body, u32 length)
{
    long descriptor = call3(
        SYSCALL_OPEN,
        (long)path,
        O_WRONLY | O_CREAT | O_EXCL,
        0600);
    if (descriptor < 0)
        FAIL("COLLECT FAIL metadata_create\n");
    write_exact(descriptor, (const u8 *)body, length);
    close_checked(descriptor);
}

static void copy_mtd_with_sd_readback(
    const char *source_path, const char *destination_path, u32 length)
{
    struct sha256_context context;
    u8 source_digest[32];
    u8 destination_digest[32];
    u32 remaining = length;
    long source = call2(SYSCALL_OPEN, (long)source_path, O_RDONLY);
    long destination = call3(
        SYSCALL_OPEN,
        (long)destination_path,
        O_WRONLY | O_CREAT | O_EXCL,
        0600);
    if (source < 0 || destination < 0)
        FAIL("COLLECT FAIL preserved_open\n");
    sha256_init(&context);
    while (remaining) {
        u32 request = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        u32 offset = 0;
        while (offset < request) {
            long amount = call3(
                SYSCALL_READ, source, (long)(io_buffer + offset), request - offset);
            if (amount <= 0)
                FAIL("COLLECT FAIL preserved_read\n");
            offset += (u32)amount;
        }
        sha256_update(&context, io_buffer, request);
        write_exact(destination, io_buffer, request);
        remaining -= request;
    }
    if (call3(SYSCALL_READ, source, (long)io_buffer, 1) != 0)
        FAIL("COLLECT FAIL preserved_size\n");
    sha256_final(&context, source_digest);
    close_checked(source);
    close_checked(destination);
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("COLLECT FAIL preserved_sync\n");
    destination = call2(SYSCALL_OPEN, (long)destination_path, O_RDONLY);
    if (destination < 0)
        FAIL("COLLECT FAIL preserved_readback_open\n");
    digest_descriptor(destination, length, destination_digest);
    close_checked(destination);
    if (!bytes_equal(source_digest, destination_digest, 32))
        FAIL("COLLECT FAIL preserved_readback\n");
}

#if !FULL_BACKUP_CAPTURE && !PROTECTED_CAPTURE
static int copy_vendor_file(
    const char *source_path,
    const char *destination_path,
    u32 expected_size,
    const u8 expected_digest[32],
    int required)
{
    struct sha256_context context;
    u8 digest[32];
    u32 remaining = expected_size;
    long source = call2(SYSCALL_OPEN, (long)source_path, O_RDONLY);
    long destination;
    if (source == -ENOENT && !required)
        return 0;
    if (source < 0)
        FAIL("COLLECT FAIL vendor_source\n");
    destination = call3(
        SYSCALL_OPEN,
        (long)destination_path,
        O_WRONLY | O_CREAT | O_EXCL,
        0600);
    if (destination < 0)
        FAIL("COLLECT FAIL vendor_destination\n");
    sha256_init(&context);
    while (remaining) {
        u32 request = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        u32 offset = 0;
        while (offset < request) {
            long amount = call3(
                SYSCALL_READ, source, (long)(io_buffer + offset), request - offset);
            if (amount <= 0)
                FAIL("COLLECT FAIL vendor_size\n");
            offset += (u32)amount;
        }
        sha256_update(&context, io_buffer, request);
        write_exact(destination, io_buffer, request);
        remaining -= request;
    }
    if (call3(SYSCALL_READ, source, (long)io_buffer, 1) != 0)
        FAIL("COLLECT FAIL vendor_size\n");
    sha256_final(&context, digest);
    close_checked(source);
    close_checked(destination);
    if (!bytes_equal(digest, expected_digest, 32))
        FAIL("COLLECT FAIL vendor_digest\n");
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("COLLECT FAIL vendor_sync\n");
    destination = call2(SYSCALL_OPEN, (long)destination_path, O_RDONLY);
    if (destination < 0)
        FAIL("COLLECT FAIL vendor_readback_open\n");
    digest_descriptor(destination, expected_size, digest);
    close_checked(destination);
    if (!bytes_equal(digest, expected_digest, 32))
        FAIL("COLLECT FAIL vendor_readback\n");
    return 1;
}
#endif

static void load_mmc_and_mount_card(void)
{
    static const struct timespec32 delay = {0, 100000000};
    struct sha256_context context;
    u8 digest[32];
    u32 offset = 0;
    u32 attempt;
    long descriptor = call2(SYSCALL_OPEN, (long)MMC_MODULE_PATH, O_RDONLY);
    if (descriptor < 0)
        FAIL("COLLECT FAIL mmc_module_open\n");
    while (offset < MMC_MODULE_SIZE) {
        long amount = call3(
            SYSCALL_READ,
            descriptor,
            (long)(module_buffer + offset),
            MMC_MODULE_SIZE - offset);
        if (amount <= 0)
            FAIL("COLLECT FAIL mmc_module_read\n");
        offset += (u32)amount;
    }
    if (call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("COLLECT FAIL mmc_module_size\n");
    close_checked(descriptor);
    sha256_init(&context);
    sha256_update(&context, module_buffer, MMC_MODULE_SIZE);
    sha256_final(&context, digest);
    if (!bytes_equal(digest, MMC_MODULE_SHA256, 32))
        FAIL("COLLECT FAIL mmc_module_digest\n");
    if (call3(
            SYSCALL_INIT_MODULE,
            (long)module_buffer,
            MMC_MODULE_SIZE,
            (long)"cd_gpio_pin=59") != 0)
        FAIL("COLLECT FAIL mmc_module_load\n");
    for (attempt = 0; attempt < 100; ++attempt) {
        descriptor = call2(SYSCALL_OPEN, (long)"/dev/mmcblk0p1", O_RDONLY);
        if (descriptor >= 0) {
            close_checked(descriptor);
            break;
        }
        call2(SYSCALL_NANOSLEEP, (long)&delay, 0);
    }
    if (attempt == 100)
        FAIL("COLLECT FAIL sd_partition_missing\n");
    make_mount_directory("/card", 0500);
    mount_checked(
        "/dev/mmcblk0p1",
        "/card",
        "vfat",
        MS_NOSUID | MS_NODEV | MS_NOEXEC,
        "shortname=winnt");
}

#if !FULL_BACKUP_CAPTURE && !FUNCTIONAL_CAPTURE && !PROTECTED_CAPTURE
static void collect_existing_recovery_preflight(void)
{
    int audio_present;
    load_mmc_and_mount_card();
    make_new_directory(OUTPUT_ROOT, 0700);
    make_new_directory(VENDOR_ROOT, 0700);
    make_new_directory(VENDOR_FILES, 0700);
    make_new_directory(PRESERVED_ROOT, 0700);

    copy_mtd_with_sd_readback(
        "/dev/mtd0", PRESERVED_ROOT "/mtd0.bin", 0x00040000);
    copy_mtd_with_sd_readback(
        "/dev/mtd4", PRESERVED_ROOT "/mtd4.bin", 0x00180000);
    copy_mtd_with_sd_readback(
        "/dev/mtd5", PRESERVED_ROOT "/mtd5.bin", 0x00040000);
    write_new_file(
        PRESERVED_ROOT "/device-layout.private.json",
        DEVICE_LAYOUT_JSON,
        sizeof(DEVICE_LAYOUT_JSON) - 1);

    make_mount_directory("/stock", 0500);
    mount_checked("/dev/mtdblock3", "/stock", "jffs2",
        MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC, 0);
    copy_vendor_file(
        "/stock/lib/libimp.so", VENDOR_FILES "/libimp.so",
        LIBIMP_SIZE, LIBIMP_SHA256, 1);
    copy_vendor_file(
        "/stock/lib/libalog.so", VENDOR_FILES "/libalog.so",
        LIBALOG_SIZE, LIBALOG_SHA256, 1);
    copy_vendor_file(
        "/stock/lib/libsysutils.so", VENDOR_FILES "/libsysutils.so",
        LIBSYSUTILS_SIZE, LIBSYSUTILS_SHA256, 1);
    audio_present = copy_vendor_file(
        "/stock/lib/libaudioProcess.so", VENDOR_FILES "/libaudioProcess.so",
        LIBAUDIOPROCESS_SIZE, LIBAUDIOPROCESS_SHA256, 0);
    if (audio_present)
        write_new_file(
            VENDOR_ROOT "/vendor-bundle.private.json",
            VENDOR_MANIFEST_OPTIONAL_JSON,
            sizeof(VENDOR_MANIFEST_OPTIONAL_JSON) - 1);
    else
        write_new_file(
            VENDOR_ROOT "/vendor-bundle.private.json",
            VENDOR_MANIFEST_REQUIRED_JSON,
            sizeof(VENDOR_MANIFEST_REQUIRED_JSON) - 1);
    if (audio_present)
        write_new_file(
            OUTPUT_ROOT "/COLLECT.OK",
            "{\"audio_process_archived\":true,\"mode\":\"existing-verified-same-device-pair\",\"nor_writes\":false,\"schema_version\":1}\n",
            sizeof("{\"audio_process_archived\":true,\"mode\":\"existing-verified-same-device-pair\",\"nor_writes\":false,\"schema_version\":1}\n") - 1);
    else
        write_new_file(
            OUTPUT_ROOT "/COLLECT.OK",
            "{\"audio_process_archived\":false,\"mode\":\"existing-verified-same-device-pair\",\"nor_writes\":false,\"schema_version\":1}\n",
            sizeof("{\"audio_process_archived\":false,\"mode\":\"existing-verified-same-device-pair\",\"nor_writes\":false,\"schema_version\":1}\n") - 1);
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("COLLECT FAIL final_sync\n");
    EMIT("COLLECT COMPLETE host_validation_required\n");
}
#endif

#if PROTECTED_CAPTURE
static void capture_protected_readback(void)
{
    load_mmc_and_mount_card();
    make_new_directory(PROTECTED_ROOT, 0700);
    make_new_directory(PROTECTED_FILES, 0700);

    copy_mtd_with_sd_readback(
        "/dev/mtd0", PROTECTED_FILES "/mtd0.bin", 0x00040000);
    copy_mtd_with_sd_readback(
        "/dev/mtd4", PROTECTED_FILES "/mtd4.bin", 0x00180000);
    copy_mtd_with_sd_readback(
        "/dev/mtd5", PROTECTED_FILES "/mtd5.bin", 0x00040000);
    write_new_file(
        PROTECTED_FILES "/device-layout.private.json",
        DEVICE_LAYOUT_JSON,
        sizeof(DEVICE_LAYOUT_JSON) - 1);
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("COLLECT FAIL protected_sync\n");
    write_new_file(
        PROTECTED_ROOT "/PROTECT.OK",
        "{\"mode\":\"protected-readback\",\"nor_writes\":false,\"partition_storage_readback_verified\":true,\"schema_version\":1}\n",
        sizeof("{\"mode\":\"protected-readback\",\"nor_writes\":false,\"partition_storage_readback_verified\":true,\"schema_version\":1}\n") - 1);
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("COLLECT FAIL final_sync\n");
    EMIT("COLLECT COMPLETE host_validation_required\n");
}
#endif

#if FULL_BACKUP_CAPTURE || FUNCTIONAL_CAPTURE
static void capture_complete_backup(void)
{
#if FUNCTIONAL_CAPTURE
    int audio_present;
#endif
    load_mmc_and_mount_card();
    make_new_directory(FULL_BACKUP_ROOT, 0700);
    make_new_directory(FULL_COPY_A, 0700);
    make_new_directory(FULL_COPY_B, 0700);
#if FUNCTIONAL_CAPTURE
    make_new_directory(VENDOR_ROOT, 0700);
    make_new_directory(VENDOR_FILES, 0700);
    make_new_directory(PRESERVED_ROOT, 0700);
#endif

    copy_mtd_with_sd_readback("/dev/mtd0", FULL_COPY_A "/mtd0.bin", 0x00040000);
    copy_mtd_with_sd_readback("/dev/mtd1", FULL_COPY_A "/mtd1.bin", 0x001c0000);
    copy_mtd_with_sd_readback("/dev/mtd2", FULL_COPY_A "/mtd2.bin", 0x00480000);
    copy_mtd_with_sd_readback("/dev/mtd3", FULL_COPY_A "/mtd3.bin", 0x007c0000);
    copy_mtd_with_sd_readback("/dev/mtd4", FULL_COPY_A "/mtd4.bin", 0x00180000);
    copy_mtd_with_sd_readback("/dev/mtd5", FULL_COPY_A "/mtd5.bin", 0x00040000);

    copy_mtd_with_sd_readback("/dev/mtd0", FULL_COPY_B "/mtd0.bin", 0x00040000);
    copy_mtd_with_sd_readback("/dev/mtd1", FULL_COPY_B "/mtd1.bin", 0x001c0000);
    copy_mtd_with_sd_readback("/dev/mtd2", FULL_COPY_B "/mtd2.bin", 0x00480000);
    copy_mtd_with_sd_readback("/dev/mtd3", FULL_COPY_B "/mtd3.bin", 0x007c0000);
    copy_mtd_with_sd_readback("/dev/mtd4", FULL_COPY_B "/mtd4.bin", 0x00180000);
    copy_mtd_with_sd_readback("/dev/mtd5", FULL_COPY_B "/mtd5.bin", 0x00040000);

#if FUNCTIONAL_CAPTURE
    copy_mtd_with_sd_readback(
        "/dev/mtd0", PRESERVED_ROOT "/mtd0.bin", 0x00040000);
    copy_mtd_with_sd_readback(
        "/dev/mtd4", PRESERVED_ROOT "/mtd4.bin", 0x00180000);
    copy_mtd_with_sd_readback(
        "/dev/mtd5", PRESERVED_ROOT "/mtd5.bin", 0x00040000);
    write_new_file(
        PRESERVED_ROOT "/device-layout.private.json",
        DEVICE_LAYOUT_JSON,
        sizeof(DEVICE_LAYOUT_JSON) - 1);

    make_mount_directory("/stock", 0500);
    mount_checked("/dev/mtdblock3", "/stock", "jffs2",
        MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC, 0);
    copy_vendor_file(
        "/stock/lib/libimp.so", VENDOR_FILES "/libimp.so",
        LIBIMP_SIZE, LIBIMP_SHA256, 1);
    copy_vendor_file(
        "/stock/lib/libalog.so", VENDOR_FILES "/libalog.so",
        LIBALOG_SIZE, LIBALOG_SHA256, 1);
    copy_vendor_file(
        "/stock/lib/libsysutils.so", VENDOR_FILES "/libsysutils.so",
        LIBSYSUTILS_SIZE, LIBSYSUTILS_SHA256, 1);
    audio_present = copy_vendor_file(
        "/stock/lib/libaudioProcess.so", VENDOR_FILES "/libaudioProcess.so",
        LIBAUDIOPROCESS_SIZE, LIBAUDIOPROCESS_SHA256, 0);
    if (audio_present)
        write_new_file(
            VENDOR_ROOT "/vendor-bundle.private.json",
            VENDOR_MANIFEST_OPTIONAL_JSON,
            sizeof(VENDOR_MANIFEST_OPTIONAL_JSON) - 1);
    else
        write_new_file(
            VENDOR_ROOT "/vendor-bundle.private.json",
            VENDOR_MANIFEST_REQUIRED_JSON,
            sizeof(VENDOR_MANIFEST_REQUIRED_JSON) - 1);

    write_new_file(
        FULL_BACKUP_ROOT "/device-layout.private.json",
        FUNCTIONAL_LAYOUT_JSON,
        sizeof(FUNCTIONAL_LAYOUT_JSON) - 1);
    write_new_file(
        FULL_BACKUP_ROOT "/CAPTURE.OK",
        "functional_duplicate_reads=complete;host_validation=required;pre_capture_writes=mtd1,mtd2\n",
        sizeof("functional_duplicate_reads=complete;host_validation=required;pre_capture_writes=mtd1,mtd2\n") - 1);
#else
    write_new_file(
        FULL_BACKUP_ROOT "/device-layout.private.json",
        FULL_BACKUP_LAYOUT_JSON,
        sizeof(FULL_BACKUP_LAYOUT_JSON) - 1);
    write_new_file(
        FULL_BACKUP_ROOT "/CAPTURE.OK",
        "duplicate_reads=complete;host_validation=required;nor_writes=false\n",
        sizeof("duplicate_reads=complete;host_validation=required;nor_writes=false\n") - 1);
#endif
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("COLLECT FAIL final_sync\n");
    EMIT("COLLECT COMPLETE host_validation_required\n");
}
#endif

void _start(void)
{
    long result;
    make_mount_directory("/dev", 0755);
    result = call5(
        SYSCALL_MOUNT,
        (long)"devtmpfs",
        (long)"/dev",
        (long)"devtmpfs",
        MS_NOSUID | MS_NOEXEC,
        (long)"mode=0755");
    if (result != 0 && result != -EBUSY)
        FAIL("COLLECT FAIL devtmpfs\n");
    verify_exact_read_only_layout();
#if FULL_BACKUP_CAPTURE || FUNCTIONAL_CAPTURE
    capture_complete_backup();
#elif PROTECTED_CAPTURE
    capture_protected_readback();
#else
    collect_existing_recovery_preflight();
#endif
    for (;;)
        call1(SYSCALL_PAUSE, 0);
}
