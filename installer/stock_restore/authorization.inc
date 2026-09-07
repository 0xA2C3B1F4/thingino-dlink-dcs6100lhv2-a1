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
