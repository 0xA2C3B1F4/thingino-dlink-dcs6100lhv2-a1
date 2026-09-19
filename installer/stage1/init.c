/*
 * Offline installer/recovery PID 1 for D-Link DCS-6100LHV2 A1.
 *
 * This freestanding MIPS binary has no shell, network, path selection, or
 * arbitrary MTD interface.  An installer-kernel boot accepts exactly one
 * host-bound stage-2 file from FAT, writes only logical mtd1/mtd3/mtd4, reads
 * every byte back, and activates the final kernel's first erase block last.
 * A final-kernel boot verifies mtd1 and mtd3 before switching into Thingino.
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
    SYSCALL_UNLINK = 4010,
    SYSCALL_EXECVE = 4011,
    SYSCALL_CHDIR = 4012,
    SYSCALL_LSEEK = 4019,
    SYSCALL_MOUNT = 4021,
    SYSCALL_PAUSE = 4029,
    SYSCALL_SYNC = 4036,
    SYSCALL_RENAME = 4038,
    SYSCALL_MKDIR = 4039,
    SYSCALL_IOCTL = 4054,
    SYSCALL_UMOUNT2 = 4052,
    SYSCALL_CHROOT = 4061,
    SYSCALL_REBOOT = 4088,
    SYSCALL_INIT_MODULE = 4128,
    SYSCALL_NANOSLEEP = 4166,
};

enum {
    O_RDONLY = 0,
    O_WRONLY = 1,
    O_RDWR = 2,
    O_CREAT = 0x0100,
    O_TRUNC = 0x0200,
    O_EXCL = 0x0400,
    MS_RDONLY = 1,
    MS_NOSUID = 2,
    MS_NODEV = 4,
    MS_NOEXEC = 8,
    MS_MOVE = 8192,
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
    CAMERA_AUTHORIZATION_SIZE = 256,
    CAMERA_AUTHORIZATION_DIGEST_OFFSET = 160,
};

/* MIPS O32: _IOC_WRITE=4 and _IOC_DIRSHIFT=29. */
#define MEMERASE ((long)0x80084d02UL)

#define STOCK_USERDATA_BACKUP_PATH "/card/STOCKM3.BIN"
#define STOCK_USERDATA_BACKUP_PART_PATH "/card/STOCKM3.PART"
#define RECOVERY_CHECKPOINT_PATH "/card/STOCKM3.OK"
#define RECOVERY_CHECKPOINT_PART_PATH "/card/STOCKM3.OK.PART"
#if REQUIRE_CAMERA_AUTHORIZATION
#define RECOVERY_CHECKPOINT_SIZE 144
#else
#define RECOVERY_CHECKPOINT_SIZE 80
#endif
#define INSTALLER_LOGICAL_SYSTEM_MTD "/dev/mtd3"
#define INSTALLER_LOGICAL_DATA_MTD "/dev/mtd4"

typedef char activation_span_must_contain_complete_erase_blocks[
    ACTIVATION_SIZE % MTD_ERASE_SIZE == 0 ? 1 : -1];
typedef char final_kernel_must_have_a_nonactivation_tail[
    FINAL_KERNEL_SIZE > ACTIVATION_SIZE ? 1 : -1];

#if SYSTEM_FLASH_SPAN + DATA_FLASH_SPAN != 0x007c0000
#error "the final system/data split must cover exact stock physical mtd3"
#endif

#define REBOOT_MAGIC1 ((long)0xfee1deadUL)
#define REBOOT_MAGIC2 ((long)672274793UL)
#define REBOOT_RESTART ((long)0x01234567UL)

#define GREEN_LED_BRIGHTNESS_PATH "/sys/class/leds/led_g/brightness"
#define RED_LED_BRIGHTNESS_PATH "/sys/class/leds/led_r/brightness"

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

struct sha256_context {
    u32 state[8];
    u64 length;
    u8 block[64];
    u32 used;
};

static u8 io_buffer[COPY_SIZE] __attribute__((aligned(16)));
static u8 activation_buffer[ACTIVATION_SIZE] __attribute__((aligned(16)));
static u8 module_buffer[MMC_MODULE_SIZE] __attribute__((aligned(16)));
static u8 stage2_buffer[STAGE2_EXPECTED_SIZE] __attribute__((aligned(16)));
#if REQUIRE_CAMERA_AUTHORIZATION
static u8 provisioning_buffer[DATA_FLASH_SPAN] __attribute__((aligned(16)));
static u8 camera_authorization[CAMERA_AUTHORIZATION_SIZE] __attribute__((aligned(16)));
#endif
static struct mtd_info_user mtd_info;
static int status_leds_ready;
static const u8 recovery_checkpoint_magic[8] = {
#if REQUIRE_CAMERA_AUTHORIZATION
    'D', 'C', 'S', '6', 'R', 'C', '0', '2'};
#else
    'D', 'C', 'S', '6', 'R', 'C', '0', '1'};
#endif
#if REQUIRE_CAMERA_AUTHORIZATION
static const u8 camera_authorization_magic[16] = {
    'D', 'C', 'S', '6', 'A', 'U', 'T', 'H', 'V', '1', 0, 0, 0, 0, 0, 0};
#endif

/* Clang may lower an ABI structure copy to this symbol even with -nostdlib. */
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
    __asm__ volatile("syscall" : "+r"(result), "+r"(error) : "r"(arg0), "r"(arg1) : "memory");
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

static __attribute__((noinline)) long call4(
    long number, long argument0, long argument1, long argument2, long argument3)
{
    register long result __asm__("$2") = number;
    register long arg0 __asm__("$4") = argument0;
    register long arg1 __asm__("$5") = argument1;
    register long arg2 __asm__("$6") = argument2;
    register long arg3 __asm__("$7") = argument3;
    register long error __asm__("$8") = 0;
    __asm__ volatile(
        "syscall\n\tmove %[error], $7"
        : "+r"(result), [error] "+r"(error)
        : "r"(arg0), "r"(arg1), "r"(arg2), "r"(arg3)
        : "$7", "memory");
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

static long write_status_file(
    const char *path, const char *value, size_t length)
{
    size_t offset = 0;
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_WRONLY);
    long result = 0;
    if (descriptor < 0)
        return descriptor;
    while (offset < length) {
        long amount = call3(
            SYSCALL_WRITE,
            descriptor,
            (long)(value + offset),
            (long)(length - offset));
        if (amount <= 0) {
            result = amount < 0 ? amount : -1;
            break;
        }
        offset += (size_t)amount;
    }
    if (call1(SYSCALL_CLOSE, descriptor) != 0 && result == 0)
        result = -1;
    return result;
}

static long read_status_value(const char *path, char expected)
{
    char value[3];
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    long amount;
    long trailing;
    long close_result;
    if (descriptor < 0)
        return descriptor;
    amount = call3(SYSCALL_READ, descriptor, (long)value, 2);
    trailing = amount == 2 ? call3(SYSCALL_READ, descriptor, (long)(value + 2), 1) : -1;
    close_result = call1(SYSCALL_CLOSE, descriptor);
    if (amount != 2 || value[0] != expected || value[1] != '\n' || trailing != 0)
        return -1;
    return close_result;
}

static void set_status_leds_best_effort(int green_on, int red_on)
{
    if (!status_leds_ready)
        return;
    write_status_file(GREEN_LED_BRIGHTNESS_PATH, green_on ? "1" : "0", 1);
    write_status_file(RED_LED_BRIGHTNESS_PATH, red_on ? "1" : "0", 1);
}

static __attribute__((noreturn)) void fail(const char *message, size_t length)
{
    set_status_leds_best_effort(0, 1);
    emit(message, length);
    for (;;)
        call1(SYSCALL_PAUSE, 0);
}

#define FAIL(message) fail((message), sizeof(message) - 1)

static void set_status_leds_checked(int green_on, int red_on)
{
    char green_value = green_on ? '1' : '0';
    char red_value = red_on ? '1' : '0';
    if (!status_leds_ready ||
        write_status_file(
            GREEN_LED_BRIGHTNESS_PATH, &green_value, 1) != 0 ||
        write_status_file(RED_LED_BRIGHTNESS_PATH, &red_value, 1) != 0 ||
        read_status_value(GREEN_LED_BRIGHTNESS_PATH, green_value) != 0 ||
        read_status_value(RED_LED_BRIGHTNESS_PATH, red_value) != 0)
        FAIL("STAGE1 FAIL status_led_write\n");
}

static void initialize_status_leds(void)
{
    status_leds_ready = 1;
    set_status_leds_checked(1, 0);
}

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

static int all_ff(const u8 *value, size_t length)
{
    size_t index;
    for (index = 0; index < length; ++index)
        if (value[index] != 0xff)
            return 0;
    return 1;
}

static void make_directory(const char *path, long mode)
{
    long result = call2(SYSCALL_MKDIR, (long)path, mode);
    if (result != 0 && result != -EEXIST)
        FAIL("STAGE1 FAIL directory\n");
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
        FAIL("STAGE1 FAIL mount\n");
}

static void seek_checked(long descriptor, u32 offset)
{
    if (call3(SYSCALL_LSEEK, descriptor, offset, 0) != (long)offset)
        FAIL("STAGE1 FAIL seek\n");
}

static void close_checked(long descriptor)
{
    if (call1(SYSCALL_CLOSE, descriptor) != 0)
        FAIL("STAGE1 FAIL close\n");
}

static void read_exact(long descriptor, u8 *output, u32 length)
{
    u32 offset = 0;
    while (offset < length) {
        long amount = call3(SYSCALL_READ, descriptor, (long)(output + offset), length - offset);
        if (amount <= 0)
            FAIL("STAGE1 FAIL read\n");
        offset += (u32)amount;
    }
}

static void write_exact(long descriptor, const u8 *input, u32 length)
{
    u32 offset = 0;
    while (offset < length) {
        long amount = call3(SYSCALL_WRITE, descriptor, (long)(input + offset), length - offset);
        if (amount <= 0)
            FAIL("STAGE1 FAIL write\n");
        offset += (u32)amount;
    }
}

static void digest_descriptor(long descriptor, u32 length, u8 output[32])
{
    struct sha256_context context;
    u32 remaining = length;
    sha256_init(&context);
    while (remaining) {
        u32 request = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        read_exact(descriptor, io_buffer, request);
        sha256_update(&context, io_buffer, request);
        remaining -= request;
    }
    sha256_final(&context, output);
}

static void digest_path(const char *path, u32 length, u8 output[32])
{
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL digest_open\n");
    digest_descriptor(descriptor, length, output);
    if (call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("STAGE1 FAIL digest_size\n");
    close_checked(descriptor);
}

#if REQUIRE_CAMERA_AUTHORIZATION
static u32 read_be32(const u8 *value)
{
    return ((u32)value[0] << 24) | ((u32)value[1] << 16) |
        ((u32)value[2] << 8) | (u32)value[3];
}

static void update_camera_identity_partition(
    struct sha256_context *context,
    u8 mtd,
    const char *path,
    u32 length)
{
    u8 prefix[9];
    u32 remaining = length;
    long descriptor;
    u32 index;
    prefix[0] = mtd;
    for (index = 0; index < 8; ++index)
        prefix[1 + index] = (u8)((u64)length >> (56 - index * 8));
    sha256_update(context, prefix, sizeof(prefix));
    descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL camera_identity_open\n");
    while (remaining) {
        u32 amount = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        read_exact(descriptor, io_buffer, amount);
        sha256_update(context, io_buffer, amount);
        remaining -= amount;
    }
    if (call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("STAGE1 FAIL camera_identity_size\n");
    close_checked(descriptor);
}

static void digest_camera_binding(
    const u8 *domain, u32 domain_size, u8 output[32])
{
    static const u8 model[] = "DCS-6100LHV2";
    static const u8 revision[] = "A1";
    static const u8 nor_size[8] = {0, 0, 0, 0, 1, 0, 0, 0};
    struct sha256_context context;
    sha256_init(&context);
    sha256_update(&context, domain, domain_size);
    sha256_update(&context, model, sizeof(model));
    sha256_update(&context, revision, sizeof(revision));
    sha256_update(&context, nor_size, sizeof(nor_size));
    update_camera_identity_partition(&context, 0, "/dev/mtd0", 0x00040000);
    update_camera_identity_partition(&context, 4, "/dev/mtd5", 0x00180000);
    update_camera_identity_partition(&context, 5, "/dev/mtd6", 0x00040000);
    sha256_final(&context, output);
}

static void hmac_sha256(
    const u8 key[32], const u8 *input, u32 length, u8 output[32])
{
    struct sha256_context context;
    u8 pad[64];
    u8 inner[32];
    u32 index;
    for (index = 0; index < 64; ++index)
        pad[index] = (index < 32 ? key[index] : 0) ^ 0x36;
    sha256_init(&context);
    sha256_update(&context, pad, sizeof(pad));
    sha256_update(&context, input, length);
    sha256_final(&context, inner);
    for (index = 0; index < 64; ++index)
        pad[index] = (index < 32 ? key[index] : 0) ^ 0x5c;
    sha256_init(&context);
    sha256_update(&context, pad, sizeof(pad));
    sha256_update(&context, inner, sizeof(inner));
    sha256_final(&context, output);
}

#endif

static void verify_camera_authorization(void)
{
#if REQUIRE_CAMERA_AUTHORIZATION
    static const u8 identity_domain[] =
        "thingino-dcs6100-camera-identity-v1";
    static const u8 authorization_domain[] =
        "thingino-dcs6100-camera-authorization-key-v1";
    u8 digest[32];
    u8 identity[32];
    u8 authorization_key[32];
    u32 provisioning_size;
    u32 index;
    int universal_nonzero = 0;
    long descriptor = call2(
        SYSCALL_OPEN, (long)CAMERA_AUTHORIZATION_PATH, O_RDONLY);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL camera_authorization_missing\n");
    read_exact(descriptor, camera_authorization, CAMERA_AUTHORIZATION_SIZE);
    if (call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("STAGE1 FAIL camera_authorization_size\n");
    close_checked(descriptor);
    if (!bytes_equal(camera_authorization, camera_authorization_magic, 16) ||
        read_be32(camera_authorization + 16) != 1 ||
        read_be32(camera_authorization + 20) != CAMERA_AUTHORIZATION_SIZE)
        FAIL("STAGE1 FAIL camera_authorization_header\n");
    for (index = 192; index < CAMERA_AUTHORIZATION_SIZE; ++index)
        if (camera_authorization[index] != 0)
            FAIL("STAGE1 FAIL camera_authorization_reserved\n");
    digest_camera_binding(
        identity_domain, sizeof(identity_domain), identity);
    digest_camera_binding(
        authorization_domain, sizeof(authorization_domain), authorization_key);
    for (index = 0; index < 32; ++index) {
        io_buffer[index] =
            camera_authorization[CAMERA_AUTHORIZATION_DIGEST_OFFSET + index];
        camera_authorization[CAMERA_AUTHORIZATION_DIGEST_OFFSET + index] = 0;
    }
    hmac_sha256(
        authorization_key,
        camera_authorization,
        CAMERA_AUTHORIZATION_SIZE,
        digest);
    if (!bytes_equal(digest, io_buffer, 32))
        FAIL("STAGE1 FAIL camera_authorization_hmac\n");
    for (index = 0; index < 32; ++index)
        camera_authorization[CAMERA_AUTHORIZATION_DIGEST_OFFSET + index] =
            io_buffer[index];
    if (!bytes_equal(identity, camera_authorization + 24, 32))
        FAIL("STAGE1 FAIL camera_authorization_device\n");
    for (index = 56; index < 88; ++index)
        universal_nonzero |= camera_authorization[index];
    if (!universal_nonzero)
        FAIL("STAGE1 FAIL camera_authorization_firmware\n");
    if (!bytes_equal(
            STAGE2_EXPECTED_SHA256, camera_authorization + 88, 32))
        FAIL("STAGE1 FAIL camera_authorization_stage2\n");
    provisioning_size = read_be32(camera_authorization + 152);
    if (provisioning_size != DATA_FLASH_SPAN)
        FAIL("STAGE1 FAIL camera_authorization_provisioning_size\n");
    if (read_be32(camera_authorization + 156) != DATA_ACTION)
        FAIL("STAGE1 FAIL camera_authorization_data_action\n");
    descriptor = call2(SYSCALL_OPEN, (long)PROVISIONING_PATH, O_RDONLY);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL provisioning_missing\n");
    read_exact(descriptor, provisioning_buffer, DATA_FLASH_SPAN);
    if (call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("STAGE1 FAIL provisioning_size\n");
    close_checked(descriptor);
    {
        struct sha256_context context;
        sha256_init(&context);
        sha256_update(&context, provisioning_buffer, DATA_FLASH_SPAN);
        sha256_final(&context, digest);
    }
    if (!bytes_equal(digest, camera_authorization + 120, 32))
        FAIL("STAGE1 FAIL camera_authorization_provisioning\n");
    EMIT("STAGE1 camera_authorization_and_provisioning_snapshotted\n");
#endif
}

static void verify_file(void)
{
    struct sha256_context context;
    u8 digest[32];
    long descriptor = call2(SYSCALL_OPEN, (long)STAGE2_PATH, O_RDONLY);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL stage2_missing\n");
    read_exact(descriptor, stage2_buffer, STAGE2_EXPECTED_SIZE);
    if (call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("STAGE1 FAIL stage2_size\n");
    close_checked(descriptor);
    sha256_init(&context);
    sha256_update(&context, stage2_buffer, STAGE2_EXPECTED_SIZE);
    sha256_final(&context, digest);
    if (!bytes_equal(digest, STAGE2_EXPECTED_SHA256, 32))
        FAIL("STAGE1 FAIL stage2_digest\n");
    EMIT("STAGE1 stage2_snapshotted_and_verified\n");
}

static void remove_consumed_bootstrap(void)
{
    long result = call1(SYSCALL_UNLINK, (long)BOOTSTRAP_PATH);
    if (result != 0 && result != -ENOENT)
        FAIL("STAGE1 FAIL bootstrap_remove\n");
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("STAGE1 FAIL bootstrap_remove_sync\n");
    if (result == -ENOENT)
        EMIT("STAGE1 bootstrap_absent\n");
    else
        EMIT("STAGE1 bootstrap_removed\n");
}

static struct mtd_info_user get_mtd_info(const char *path)
{
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (descriptor < 0 ||
        call3(SYSCALL_IOCTL, descriptor, MEMGETINFO, (long)&mtd_info) != 0)
        FAIL("STAGE1 FAIL mtd_info\n");
    close_checked(descriptor);
    return mtd_info;
}

static int verify_mtd(const char *path, u32 size, int writable)
{
    struct mtd_info_user info = get_mtd_info(path);
    int actual_writable = (info.flags & MTD_WRITEABLE) != 0;
    if (info.type != MTD_NORFLASH || info.size != size ||
        info.erasesize != MTD_ERASE_SIZE ||
        info.writesize != MTD_WRITE_SIZE ||
        actual_writable != writable)
        FAIL("STAGE1 FAIL mtd_layout\n");
    return actual_writable;
}

static int verify_layout(void)
{
    int kernel_writable;
    int system_writable;
    long unexpected;
    /*
     * The installer kernel splits stock physical mtd3. Its logical mtd3/mtd4
     * are system/data; stock physical mtd4/mtd5 shift to logical mtd5/mtd6.
     */
    verify_mtd("/dev/mtd0", 0x00040000, 0);
    kernel_writable = verify_mtd("/dev/mtd1", 0x001c0000,
        (get_mtd_info("/dev/mtd1").flags & MTD_WRITEABLE) != 0);
    verify_mtd("/dev/mtd2", 0x00480000, 0);
    system_writable = verify_mtd(
        INSTALLER_LOGICAL_SYSTEM_MTD, SYSTEM_FLASH_SPAN,
        (get_mtd_info(INSTALLER_LOGICAL_SYSTEM_MTD).flags & MTD_WRITEABLE) != 0);
    verify_mtd(INSTALLER_LOGICAL_DATA_MTD, DATA_FLASH_SPAN, 1);
    verify_mtd("/dev/mtd5", 0x00180000, 0);
    verify_mtd("/dev/mtd6", 0x00040000, 0);
    unexpected = call2(SYSCALL_OPEN, (long)"/dev/mtd7", O_RDONLY);
    if (unexpected >= 0) {
        close_checked(unexpected);
        FAIL("STAGE1 FAIL extra_mtd_partition\n");
    }
    if (kernel_writable != system_writable)
        FAIL("STAGE1 FAIL mixed_kernel_mode\n");
    EMIT("STAGE1 device_verified\n");
    return kernel_writable;
}

static void verify_region(
    const char *path, u32 payload_size, u32 span, const u8 expected_digest[32])
{
    struct sha256_context context;
    u8 digest[32];
    u32 remaining = payload_size;
    u32 tail = span - payload_size;
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (descriptor < 0 || payload_size > span)
        FAIL("STAGE1 FAIL verify_open\n");
    sha256_init(&context);
    while (remaining) {
        u32 amount = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        read_exact(descriptor, io_buffer, amount);
        sha256_update(&context, io_buffer, amount);
        remaining -= amount;
    }
    sha256_final(&context, digest);
    if (!bytes_equal(digest, expected_digest, 32))
        FAIL("STAGE1 FAIL readback_digest\n");
    while (tail) {
        u32 amount = tail < COPY_SIZE ? tail : COPY_SIZE;
        read_exact(descriptor, io_buffer, amount);
        if (!all_ff(io_buffer, amount))
            FAIL("STAGE1 FAIL readback_tail\n");
        tail -= amount;
    }
    close_checked(descriptor);
}

static void verify_kernel_before_activation(void)
{
    struct sha256_context context;
    u8 digest[32];
    u32 remaining = FINAL_KERNEL_SIZE - ACTIVATION_SIZE;
    long descriptor = call2(SYSCALL_OPEN, (long)"/dev/mtd1", O_RDONLY);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL preactivation_open\n");
    seek_checked(descriptor, ACTIVATION_SIZE);
    sha256_init(&context);
    sha256_update(&context, activation_buffer, ACTIVATION_SIZE);
    while (remaining) {
        u32 amount = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        read_exact(descriptor, io_buffer, amount);
        sha256_update(&context, io_buffer, amount);
        remaining -= amount;
    }
    close_checked(descriptor);
    sha256_final(&context, digest);
    if (!bytes_equal(digest, FINAL_KERNEL_SHA256, 32))
        FAIL("STAGE1 FAIL preactivation_digest\n");
    EMIT("STAGE1 preactivation_kernel_verified\n");
}

static void erase_range(long descriptor, u32 start, u32 length)
{
    struct erase_info_user erase;
    u32 current;
    if (!length || start % MTD_ERASE_SIZE || length % MTD_ERASE_SIZE ||
        start + length < start)
        FAIL("STAGE1 FAIL erase\n");
    erase.length = MTD_ERASE_SIZE;
    for (current = start; current < start + length; current += MTD_ERASE_SIZE) {
        erase.start = current;
        if (call3(SYSCALL_IOCTL, descriptor, MEMERASE, (long)&erase) != 0)
            FAIL("STAGE1 FAIL erase\n");
    }
}

static void write_mtd_pages(long descriptor, const u8 *input, u32 length)
{
    u32 offset;
    if (!length || length % MTD_WRITE_SIZE)
        FAIL("STAGE1 FAIL mtd_write_alignment\n");
    for (offset = 0; offset < length; offset += MTD_WRITE_SIZE)
        write_exact(descriptor, input + offset, MTD_WRITE_SIZE);
}

static void verify_erased(const char *path, u32 start, u32 length)
{
    u32 remaining = length;
    long descriptor = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL erased_open\n");
    seek_checked(descriptor, start);
    while (remaining) {
        u32 amount = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        read_exact(descriptor, io_buffer, amount);
        if (!all_ff(io_buffer, amount))
            FAIL("STAGE1 FAIL erased_readback\n");
        remaining -= amount;
    }
    close_checked(descriptor);
}

static void copy_stage2_to_mtd(
    u32 file_offset,
    const char *mtd_path,
    u32 mtd_offset,
    u32 payload_size)
{
    u32 remaining = payload_size;
    u32 source_offset = file_offset;
    long destination = call2(SYSCALL_OPEN, (long)mtd_path, O_RDWR);
    if (destination < 0 || file_offset + payload_size < file_offset ||
        file_offset + payload_size > STAGE2_EXPECTED_SIZE)
        FAIL("STAGE1 FAIL copy_open\n");
    seek_checked(destination, mtd_offset);
    while (remaining) {
        u32 amount = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        u32 write_amount =
            (amount + MTD_WRITE_SIZE - 1) & ~(MTD_WRITE_SIZE - 1);
        u32 padding;
        memcpy(io_buffer, stage2_buffer + source_offset, amount);
        for (padding = amount; padding < write_amount; padding++)
            io_buffer[padding] = 0xff;
        write_mtd_pages(destination, io_buffer, write_amount);
        source_offset += amount;
        remaining -= amount;
    }
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("STAGE1 FAIL sync\n");
    close_checked(destination);
}

static void verify_stage2_matches_mtd(
    u32 file_offset, const char *mtd_path, u32 mtd_offset, u32 length)
{
    u32 remaining = length;
    u32 source_offset = file_offset;
    long destination = call2(SYSCALL_OPEN, (long)mtd_path, O_RDONLY);
    if (destination < 0 || file_offset + length < file_offset ||
        file_offset + length > STAGE2_EXPECTED_SIZE)
        FAIL("STAGE1 FAIL compare_open\n");
    seek_checked(destination, mtd_offset);
    while (remaining) {
        u32 amount = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        read_exact(destination, io_buffer, amount);
        if (!bytes_equal(stage2_buffer + source_offset, io_buffer, amount))
            FAIL("STAGE1 FAIL compare_readback\n");
        source_offset += amount;
        remaining -= amount;
    }
    close_checked(destination);
}

#if REQUIRE_CAMERA_AUTHORIZATION
static void write_provisioning_to_data(void)
{
    long descriptor = call2(
        SYSCALL_OPEN, (long)INSTALLER_LOGICAL_DATA_MTD, O_RDWR);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL provisioning_data_open\n");
    write_mtd_pages(descriptor, provisioning_buffer, DATA_FLASH_SPAN);
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("STAGE1 FAIL provisioning_data_sync\n");
    close_checked(descriptor);
    verify_region(
        INSTALLER_LOGICAL_DATA_MTD,
        DATA_FLASH_SPAN,
        DATA_FLASH_SPAN,
        camera_authorization + 120);
    EMIT("STAGE1 provisioning_data_written_and_verified\n");
}
#endif

static void append_mtd_to_backup(
    const char *mtd_path, long backup, u32 length)
{
    u32 remaining = length;
    long source = call2(SYSCALL_OPEN, (long)mtd_path, O_RDONLY);
    if (source < 0)
        FAIL("STAGE1 FAIL backup_source_open\n");
    while (remaining) {
        u32 amount = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        read_exact(source, io_buffer, amount);
        write_exact(backup, io_buffer, amount);
        remaining -= amount;
    }
    close_checked(source);
}

static int mtd_matches_backup(
    const char *mtd_path, long backup, u32 length)
{
    int matches = 1;
    u32 remaining = length;
    long source = call2(SYSCALL_OPEN, (long)mtd_path, O_RDONLY);
    if (source < 0)
        FAIL("STAGE1 FAIL backup_verify_source\n");
    while (remaining) {
        u32 amount = remaining < (COPY_SIZE / 2) ? remaining : (COPY_SIZE / 2);
        read_exact(source, io_buffer, amount);
        read_exact(backup, io_buffer + COPY_SIZE / 2, amount);
        if (!bytes_equal(io_buffer, io_buffer + COPY_SIZE / 2, amount))
            matches = 0;
        remaining -= amount;
    }
    close_checked(source);
    return matches;
}

static int stock_userdata_backup_matches_current_nor(const char *path)
{
    int matches;
    long backup = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (backup < 0)
        FAIL("STAGE1 FAIL backup_readback_open\n");
    matches = mtd_matches_backup(
        INSTALLER_LOGICAL_SYSTEM_MTD, backup, SYSTEM_FLASH_SPAN);
    if (!mtd_matches_backup(
            INSTALLER_LOGICAL_DATA_MTD, backup, DATA_FLASH_SPAN))
        matches = 0;
    if (call3(SYSCALL_READ, backup, (long)io_buffer, 1) != 0)
        FAIL("STAGE1 FAIL backup_size\n");
    close_checked(backup);
    return matches;
}

static void digest_stock_userdata_backup(const char *path, u8 digest[32])
{
    struct sha256_context context;
    u32 remaining = SYSTEM_FLASH_SPAN + DATA_FLASH_SPAN;
    long backup = call2(SYSCALL_OPEN, (long)path, O_RDONLY);
    if (backup < 0)
        FAIL("STAGE1 FAIL backup_digest_open\n");
    sha256_init(&context);
    while (remaining) {
        u32 amount = remaining < COPY_SIZE ? remaining : COPY_SIZE;
        read_exact(backup, io_buffer, amount);
        sha256_update(&context, io_buffer, amount);
        remaining -= amount;
    }
    if (call3(SYSCALL_READ, backup, (long)io_buffer, 1) != 0)
        FAIL("STAGE1 FAIL backup_size\n");
    close_checked(backup);
    sha256_final(&context, digest);
}

static void build_recovery_checkpoint(
    const u8 backup_digest[32], u8 checkpoint[RECOVERY_CHECKPOINT_SIZE])
{
    u32 index;
    u32 backup_size = SYSTEM_FLASH_SPAN + DATA_FLASH_SPAN;
    for (index = 0; index < 8; ++index)
        checkpoint[index] = recovery_checkpoint_magic[index];
    checkpoint[8] = (u8)(backup_size >> 24);
    checkpoint[9] = (u8)(backup_size >> 16);
    checkpoint[10] = (u8)(backup_size >> 8);
    checkpoint[11] = (u8)backup_size;
    checkpoint[12] = (u8)(STAGE2_EXPECTED_SIZE >> 24);
    checkpoint[13] = (u8)(STAGE2_EXPECTED_SIZE >> 16);
    checkpoint[14] = (u8)(STAGE2_EXPECTED_SIZE >> 8);
    checkpoint[15] = (u8)STAGE2_EXPECTED_SIZE;
    for (index = 0; index < 32; ++index) {
        checkpoint[16 + index] = backup_digest[index];
        checkpoint[48 + index] = STAGE2_EXPECTED_SHA256[index];
    }
#if REQUIRE_CAMERA_AUTHORIZATION
    {
        struct sha256_context context;
        u8 authorization_digest[32];
        sha256_init(&context);
        sha256_update(
            &context, camera_authorization, CAMERA_AUTHORIZATION_SIZE);
        sha256_final(&context, authorization_digest);
        for (index = 0; index < 32; ++index) {
            checkpoint[80 + index] = authorization_digest[index];
            checkpoint[112 + index] = camera_authorization[120 + index];
        }
    }
#endif
}

static void write_recovery_checkpoint(const u8 backup_digest[32])
{
    u8 checkpoint[RECOVERY_CHECKPOINT_SIZE];
    long descriptor;
    long result = call1(SYSCALL_UNLINK, (long)RECOVERY_CHECKPOINT_PART_PATH);
    if (result != 0 && result != -ENOENT)
        FAIL("STAGE1 FAIL recovery_checkpoint_part_cleanup\n");
    build_recovery_checkpoint(backup_digest, checkpoint);
    descriptor = call3(
        SYSCALL_OPEN,
        (long)RECOVERY_CHECKPOINT_PART_PATH,
        O_WRONLY | O_CREAT | O_EXCL,
        0600);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL recovery_checkpoint_create\n");
    write_exact(descriptor, checkpoint, RECOVERY_CHECKPOINT_SIZE);
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("STAGE1 FAIL recovery_checkpoint_sync\n");
    close_checked(descriptor);

    descriptor = call2(
        SYSCALL_OPEN, (long)RECOVERY_CHECKPOINT_PART_PATH, O_RDONLY);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL recovery_checkpoint_readback_open\n");
    read_exact(descriptor, io_buffer, RECOVERY_CHECKPOINT_SIZE);
    if (!bytes_equal(checkpoint, io_buffer, RECOVERY_CHECKPOINT_SIZE) ||
        call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("STAGE1 FAIL recovery_checkpoint_readback\n");
    close_checked(descriptor);
    if (call2(
            SYSCALL_RENAME,
            (long)RECOVERY_CHECKPOINT_PART_PATH,
            (long)RECOVERY_CHECKPOINT_PATH) != 0 ||
        call1(SYSCALL_SYNC, 0) != 0)
        FAIL("STAGE1 FAIL recovery_checkpoint_commit\n");
    EMIT("STAGE1 recovery_checkpoint_committed\n");
}

static void validate_recovery_checkpoint(const u8 backup_digest[32])
{
    u8 expected[RECOVERY_CHECKPOINT_SIZE];
    long descriptor = call2(
        SYSCALL_OPEN, (long)RECOVERY_CHECKPOINT_PATH, O_RDONLY);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL recovery_checkpoint_missing\n");
    build_recovery_checkpoint(backup_digest, expected);
    read_exact(descriptor, io_buffer, RECOVERY_CHECKPOINT_SIZE);
    if (!bytes_equal(expected, io_buffer, RECOVERY_CHECKPOINT_SIZE) ||
        call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("STAGE1 FAIL recovery_checkpoint_invalid\n");
    close_checked(descriptor);
    EMIT("STAGE1 recovery_checkpoint_accepted\n");
}

static void export_stock_userdata_backup(void)
{
    u8 backup_digest[32];
    long existing = call2(
        SYSCALL_OPEN, (long)STOCK_USERDATA_BACKUP_PATH, O_RDONLY);
    long backup;
    if (existing >= 0) {
        close_checked(existing);
        digest_stock_userdata_backup(STOCK_USERDATA_BACKUP_PATH, backup_digest);
        if (stock_userdata_backup_matches_current_nor(
                STOCK_USERDATA_BACKUP_PATH)) {
            write_recovery_checkpoint(backup_digest);
            EMIT("STAGE1 stock_userdata_backup_reused\n");
        } else {
            validate_recovery_checkpoint(backup_digest);
            EMIT("STAGE1 interrupted_install_recovery\n");
        }
        return;
    }
    if (existing != -ENOENT)
        FAIL("STAGE1 FAIL backup_open\n");
    {
        long cleanup = call1(
            SYSCALL_UNLINK, (long)STOCK_USERDATA_BACKUP_PART_PATH);
        if (cleanup != 0 && cleanup != -ENOENT)
            FAIL("STAGE1 FAIL backup_part_cleanup\n");
    }
    backup = call3(
        SYSCALL_OPEN,
        (long)STOCK_USERDATA_BACKUP_PART_PATH,
        O_WRONLY | O_CREAT | O_EXCL,
        0600);
    if (backup < 0)
        FAIL("STAGE1 FAIL backup_create\n");
    append_mtd_to_backup(
        INSTALLER_LOGICAL_SYSTEM_MTD, backup, SYSTEM_FLASH_SPAN);
    append_mtd_to_backup(INSTALLER_LOGICAL_DATA_MTD, backup, DATA_FLASH_SPAN);
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("STAGE1 FAIL backup_sync\n");
    close_checked(backup);

    if (!stock_userdata_backup_matches_current_nor(
            STOCK_USERDATA_BACKUP_PART_PATH))
        FAIL("STAGE1 FAIL backup_readback\n");
    digest_stock_userdata_backup(
        STOCK_USERDATA_BACKUP_PART_PATH, backup_digest);
    if (call2(
            SYSCALL_RENAME,
            (long)STOCK_USERDATA_BACKUP_PART_PATH,
            (long)STOCK_USERDATA_BACKUP_PATH) != 0 ||
        call1(SYSCALL_SYNC, 0) != 0)
        FAIL("STAGE1 FAIL backup_commit\n");
    digest_stock_userdata_backup(STOCK_USERDATA_BACKUP_PATH, backup_digest);
    write_recovery_checkpoint(backup_digest);
    EMIT("STAGE1 stock_userdata_backup_committed\n");
}

static void load_mmc_and_mount_card(void)
{
    static const struct timespec32 delay = {0, 100000000};
    u8 digest[32];
    long descriptor = call2(SYSCALL_OPEN, (long)MMC_MODULE_PATH, O_RDONLY);
    u32 attempt;
    if (descriptor < 0)
        FAIL("STAGE1 FAIL mmc_module_open\n");
    read_exact(descriptor, module_buffer, MMC_MODULE_SIZE);
    if (call3(SYSCALL_READ, descriptor, (long)io_buffer, 1) != 0)
        FAIL("STAGE1 FAIL mmc_module_size\n");
    close_checked(descriptor);
    {
        struct sha256_context context;
        sha256_init(&context);
        sha256_update(&context, module_buffer, MMC_MODULE_SIZE);
        sha256_final(&context, digest);
    }
    if (!bytes_equal(digest, MMC_MODULE_SHA256, 32))
        FAIL("STAGE1 FAIL mmc_module_digest\n");
    if (call3(
            SYSCALL_INIT_MODULE,
            (long)module_buffer,
            MMC_MODULE_SIZE,
            (long)"cd_gpio_pin=59") != 0)
        FAIL("STAGE1 FAIL mmc_module_load\n");
    for (attempt = 0; attempt < 100; ++attempt) {
        descriptor = call2(SYSCALL_OPEN, (long)"/dev/mmcblk0p1", O_RDONLY);
        if (descriptor >= 0) {
            close_checked(descriptor);
            break;
        }
        call2(SYSCALL_NANOSLEEP, (long)&delay, 0);
    }
    if (attempt == 100)
        FAIL("STAGE1 FAIL sd_partition_missing\n");
    make_directory("/card", 0500);
    mount_checked(
        "/dev/mmcblk0p1",
        "/card",
        "vfat",
        MS_NOSUID | MS_NODEV | MS_NOEXEC,
        "utf8=1,shortname=winnt");
    EMIT("STAGE1 sd_mounted\n");
}

#if REQUIRE_CAMERA_AUTHORIZATION
static void passivate_camera_install_files(void)
{
    const char *paths[4];
    u32 index;
    int complete = 1;
    paths[0] = PROVISIONING_PATH;
    paths[1] = CAMERA_AUTHORIZATION_PATH;
    paths[2] = CAMERA_AUTHORIZATION_MANIFEST_PATH;
    paths[3] = CAMERA_AUTHORIZATION_SIGNATURE_PATH;
    for (index = 0; index < 4; ++index) {
        long result = call1(SYSCALL_UNLINK, (long)paths[index]);
        if (result != 0 && result != -ENOENT)
            complete = 0;
    }
    if (call1(SYSCALL_SYNC, 0) != 0)
        complete = 0;
    if (complete)
        EMIT("STAGE1 camera_install_files_passivated\n");
    else
        EMIT("STAGE1 camera_install_files_cleanup_pending\n");
}
#endif

static void install_stage2(void)
{
    long descriptor;
    u32 kernel_tail_size = FINAL_KERNEL_SIZE - ACTIVATION_SIZE;
    u8 preserved_data_digest[32];
    EMIT("STAGE1 install_started\n");
    load_mmc_and_mount_card();
    verify_file();
    verify_camera_authorization();
    remove_consumed_bootstrap();
    if (DATA_ACTION == DATA_ACTION_INITIALIZE)
        export_stock_userdata_backup();
    else if (DATA_ACTION == DATA_ACTION_PRESERVE) {
        digest_path(
            INSTALLER_LOGICAL_DATA_MTD, DATA_FLASH_SPAN, preserved_data_digest);
        EMIT("STAGE1 existing_data_bound_for_update\n");
    } else if (DATA_ACTION == DATA_ACTION_FACTORY_RESET) {
        EMIT("STAGE1 factory_reset_requested\n");
    } else {
        FAIL("STAGE1 FAIL data_action\n");
    }
    set_status_leds_checked(1, 1);
    EMIT("STAGE1 final_write_phase\n");

    if (DATA_ACTION != DATA_ACTION_PRESERVE) {
        descriptor = call2(
            SYSCALL_OPEN, (long)INSTALLER_LOGICAL_DATA_MTD, O_RDWR);
        if (descriptor < 0)
            FAIL("STAGE1 FAIL data_open\n");
        erase_range(descriptor, 0, DATA_FLASH_SPAN);
        close_checked(descriptor);
        verify_erased(INSTALLER_LOGICAL_DATA_MTD, 0, DATA_FLASH_SPAN);
#if REQUIRE_CAMERA_AUTHORIZATION
        if (DATA_ACTION != DATA_ACTION_INITIALIZE)
            FAIL("STAGE1 FAIL universal_data_action\n");
        write_provisioning_to_data();
#else
        EMIT("STAGE1 final_data_reset\n");
#endif
    }

    descriptor = call2(
        SYSCALL_OPEN, (long)INSTALLER_LOGICAL_SYSTEM_MTD, O_RDWR);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL system_open\n");
    erase_range(descriptor, 0, SYSTEM_FLASH_SPAN);
    close_checked(descriptor);
    copy_stage2_to_mtd(
        STAGE2_SYSTEM_OFFSET, INSTALLER_LOGICAL_SYSTEM_MTD, 0, SYSTEM_SIZE);
    verify_region(
        INSTALLER_LOGICAL_SYSTEM_MTD,
        SYSTEM_SIZE,
        SYSTEM_FLASH_SPAN,
        SYSTEM_SHA256);
    EMIT("STAGE1 final_system_written\n");

    if (DATA_ACTION == DATA_ACTION_PRESERVE) {
        u8 readback_data_digest[32];
        digest_path(
            INSTALLER_LOGICAL_DATA_MTD, DATA_FLASH_SPAN, readback_data_digest);
        if (!bytes_equal(preserved_data_digest, readback_data_digest, 32))
            FAIL("STAGE1 FAIL preserved_data_changed\n");
        EMIT("STAGE1 final_data_preserved\n");
    }

    descriptor = call2(SYSCALL_OPEN, (long)"/dev/mtd1", O_RDWR);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL kernel_open\n");
    erase_range(descriptor, ACTIVATION_SIZE, 0x001c0000 - ACTIVATION_SIZE);
    close_checked(descriptor);
    copy_stage2_to_mtd(
        STAGE2_KERNEL_OFFSET + ACTIVATION_SIZE,
        "/dev/mtd1",
        ACTIVATION_SIZE,
        kernel_tail_size);
    verify_stage2_matches_mtd(
        STAGE2_KERNEL_OFFSET + ACTIVATION_SIZE,
        "/dev/mtd1",
        ACTIVATION_SIZE,
        kernel_tail_size);
    verify_erased(
        "/dev/mtd1",
        FINAL_KERNEL_SIZE,
        0x001c0000 - FINAL_KERNEL_SIZE);
    EMIT("STAGE1 final_kernel_tail_written\n");

    memcpy(
        activation_buffer,
        stage2_buffer + STAGE2_KERNEL_OFFSET,
        ACTIVATION_SIZE);
    verify_kernel_before_activation();
    descriptor = call2(SYSCALL_OPEN, (long)"/dev/mtd1", O_RDWR);
    if (descriptor < 0)
        FAIL("STAGE1 FAIL activation_open\n");
    erase_range(descriptor, 0, ACTIVATION_SIZE);
    seek_checked(descriptor, 0);
    write_mtd_pages(descriptor, activation_buffer, ACTIVATION_SIZE);
    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("STAGE1 FAIL activation_sync\n");
    close_checked(descriptor);
    verify_region("/dev/mtd1", FINAL_KERNEL_SIZE, 0x001c0000, FINAL_KERNEL_SHA256);
    set_status_leds_checked(1, 0);
    EMIT("STAGE1 activation_written_and_verified\n");
#if REQUIRE_CAMERA_AUTHORIZATION
    passivate_camera_install_files();
#endif

    if (call1(SYSCALL_SYNC, 0) != 0)
        FAIL("STAGE1 FAIL final_sync\n");
    /* sync alone does not clear FAT's mounted/dirty state before reboot. */
    if (call2(SYSCALL_UMOUNT2, (long)"/card", 0) != 0)
        FAIL("STAGE1 FAIL card_unmount\n");
    EMIT("STAGE1 sd_unmounted\n");
    EMIT("STAGE1 rebooting_final\n");
    call4(SYSCALL_REBOOT, REBOOT_MAGIC1, REBOOT_MAGIC2, REBOOT_RESTART, 0);
    FAIL("STAGE1 FAIL reboot\n");
}

static __attribute__((noreturn)) void switch_to_thingino(void)
{
    static const char init_path[] = "/init";
    static const char path_environment[] = "PATH=/sbin:/usr/sbin:/bin:/usr/bin";
    static const char home_environment[] = "HOME=/root";
    static const char term_environment[] = "TERM=vt100";
    static const char verified_mtd_environment[] =
        "THINGINO_DLINK_VERIFIED_MTD_ROOT=1";
    char *arguments[2];
    char *environment[5];
    verify_region("/dev/mtd1", FINAL_KERNEL_SIZE, 0x001c0000, FINAL_KERNEL_SHA256);
    verify_region(
        INSTALLER_LOGICAL_SYSTEM_MTD,
        SYSTEM_SIZE,
        SYSTEM_FLASH_SPAN,
        SYSTEM_SHA256);
    make_directory("/newroot", 0555);
    mount_checked(
        "/dev/mtdblock3",
        "/newroot",
        "squashfs",
        MS_RDONLY | MS_NOSUID | MS_NODEV,
        0);
    mount_checked("/dev", "/newroot/dev", 0, MS_MOVE, 0);
    if (call2(SYSCALL_UMOUNT2, (long)"/proc", 0) != 0 ||
        call2(SYSCALL_UMOUNT2, (long)"/sys", 0) != 0)
        FAIL("STAGE1 FAIL virtual_unmount\n");
    if (call1(SYSCALL_CHDIR, (long)"/newroot") != 0)
        FAIL("STAGE1 FAIL newroot_chdir\n");
    mount_checked(".", "/", 0, MS_MOVE, 0);
    if (call1(SYSCALL_CHROOT, (long)".") != 0 ||
        call1(SYSCALL_CHDIR, (long)"/") != 0)
        FAIL("STAGE1 FAIL root_handoff\n");
    arguments[0] = (char *)init_path;
    arguments[1] = 0;
    environment[0] = (char *)path_environment;
    environment[1] = (char *)home_environment;
    environment[2] = (char *)term_environment;
    environment[3] = (char *)verified_mtd_environment;
    environment[4] = 0;
    EMIT("STAGE1 thingino_verified_switch_root\n");
    call3(SYSCALL_EXECVE, (long)init_path, (long)arguments, (long)environment);
    FAIL("STAGE1 FAIL thingino_exec\n");
}

void _start(void)
{
    int installer_mode;
    make_directory("/dev", 0755);
    {
        long result = call5(
            SYSCALL_MOUNT,
            (long)"devtmpfs",
            (long)"/dev",
            (long)"devtmpfs",
            MS_NOSUID | MS_NOEXEC,
            (long)"mode=0755");
        if (result != 0 && result != -EBUSY)
            FAIL("STAGE1 FAIL devtmpfs\n");
    }
    make_directory("/proc", 0555);
    make_directory("/sys", 0555);
    make_directory("/newroot", 0555);
    mount_checked("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, 0);
    mount_checked("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, 0);
    initialize_status_leds();
    EMIT("STAGE1 booted\n");
    installer_mode = verify_layout();
    if (installer_mode)
        install_stage2();
    switch_to_thingino();
}
