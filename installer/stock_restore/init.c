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

#include "runtime.inc"

#include "verify.inc"

#include "flash.inc"

#include "authorization.inc"

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
