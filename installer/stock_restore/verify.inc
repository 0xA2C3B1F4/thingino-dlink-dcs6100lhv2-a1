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
