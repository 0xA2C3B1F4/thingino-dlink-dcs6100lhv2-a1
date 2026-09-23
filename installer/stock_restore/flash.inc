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
