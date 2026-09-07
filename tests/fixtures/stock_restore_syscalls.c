/* Host-only syscall fake for the real stock_restore/init.c control flow.
 * No host open/read/write/ioctl/mount is used. Target paths name memory objects.
 * Python replaces ONLY the four assembly syscall wrappers in init_host.inc.
 * LP64 execution of that copy is deliberately not evidence of the MIPS ABI.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include <stdint.h>

#undef memcpy
#define memcpy restorer_memcpy
#define _start restorer_start
#include "init_host.inc"
#undef _start
#undef memcpy

/* The target uses long as a syscall argument. On the host it must carry every
 * pointer without truncation; MIPS-specific struct timespec32 is not serialized
 * by this fake. The production O32 representation is checked separately. */
_Static_assert(sizeof(long) == sizeof(void *), "host syscall pointer width");
_Static_assert(sizeof(uintptr_t) == sizeof(long), "host uintptr width");
_Static_assert(sizeof(u32) == 4, "SHA256 and flash offset width");
_Static_assert(sizeof(struct erase_info_user) == 8, "erase request width");
_Static_assert(sizeof(struct mtd_info_user) == 32, "MTD info width");

enum { FILE_LIMIT = 32, FD_LIMIT = 128, EVENT_LIMIT = 10000 };
struct memory_file {
    const char *path;
    unsigned char *bytes;
    unsigned long length, capacity;
    int exists, mtd, durable;
};
struct descriptor {
    int used, file, flags;
    unsigned long offset;
};
struct event {
    unsigned long first, last, offset, amount, count;
    const char *op, *path;
    int region;
};
static struct memory_file files[FILE_LIMIT];
static struct descriptor descriptors[FD_LIMIT];
static struct event events[EVENT_LIMIT];
static unsigned event_count, file_count;
static unsigned long step, fault_step, mutations, mutations_after_fault;
static unsigned forbidden_writes, unsafe_authorization, unsafe_mutation;
static int fault_hit, short_io, completed, failed, paused;
static const char *fault_mode;
static char messages[64][160];
static unsigned message_count;
static jmp_buf terminal;
static const unsigned long partition_size[6] = {
    0x40000, 0x1c0000, 0x480000, 0x7c0000, 0x180000, 0x40000
};
static const char *mtd_paths[6] = {
    "/dev/mtd0", "/dev/mtd1", "/dev/mtd2", "/dev/mtd3", "/dev/mtd4", "/dev/mtd5"
};
static const char *image_paths[6] = {
    KEEP0_FILE, MTD1_FILE, MTD2_FILE, MTD3_FILE, KEEP4_FILE, KEEP5_FILE
};

static void harness_error(const char *message)
{
    fprintf(stderr, "stock restore harness: %s\n", message);
    exit(2);
}

static int lookup(const char *path)
{
    unsigned index;
    for (index = 0; index < file_count; ++index)
        if (strcmp(files[index].path, path) == 0)
            return (int)index;
    harness_error("unexpected target path");
    return -1;
}

static struct memory_file *file_at(const char *path)
{
    return &files[lookup(path)];
}

static void add_file(const char *path, unsigned long length, int fill, int mtd, int exists)
{
    struct memory_file *file;
    if (file_count == FILE_LIMIT)
        harness_error("file table exhausted");
    file = &files[file_count++];
    file->path = path;
    file->capacity = length > 256 ? length : 256;
    file->bytes = malloc(file->capacity);
    if (!file->bytes)
        harness_error("allocation failed");
    memset(file->bytes, fill, file->capacity);
    file->length = length;
    file->exists = exists;
    file->mtd = mtd;
}

static void status_fixture(const char *path, const char *value)
{
    struct memory_file *file = file_at(path);
    file->length = strlen(value);
    memcpy(file->bytes, value, file->length);
    file->exists = 1;
    file->durable = 1;
}

static int record(const char *op, const char *path, unsigned long offset,
                  unsigned long amount, int region)
{
    struct event *event;
    ++step;
    /* Losslessly retain call count and first/last step while coalescing the
     * adjacent page writes/reads. Boundary tests can inject at either end. */
    if (event_count && !fault_step) {
        event = &events[event_count - 1];
        if (strcmp(event->op, op) == 0 && strcmp(event->path, path) == 0 &&
            event->region == region) {
            event->last = step;
            event->amount += amount;
            ++event->count;
            return 0;
        }
    }
    /* Fault runs also coalesce, except at the actual failing call. */
    if (event_count && fault_step && step != fault_step && step != fault_step + 1) {
        event = &events[event_count - 1];
        if (strcmp(event->op, op) == 0 && strcmp(event->path, path) == 0 &&
            event->region == region) {
            event->last = step;
            event->amount += amount;
            ++event->count;
            return 0;
        }
    }
    if (event_count == EVENT_LIMIT)
        harness_error("event table exhausted");
    event = &events[event_count++];
    *event = (struct event){step, step, offset, amount, 1, op, path, region};
    if (step == fault_step) {
        fault_hit = 1;
        return 1;
    }
    return 0;
}

static struct descriptor *fd_at(long number)
{
    if (number < 3 || number >= FD_LIMIT || !descriptors[number].used)
        harness_error("invalid descriptor");
    return &descriptors[number];
}

static int region_of(struct memory_file *file, unsigned long offset)
{
    if (file->mtd == 1 || strcmp(file->path, MTD1_FILE) == 0)
        return offset < ACTIVATION_SIZE ? 0 : 1;
    return -1;
}

static void mutation(struct memory_file *file)
{
    if (file->mtd < 0)
        return;
    ++mutations;
    if (fault_hit && step > fault_step)
        ++mutations_after_fault;
    if (!file_at(RUN_FILE)->durable || file_at(AUTH_FILE)->exists)
        ++unsafe_mutation;
}

static long fake_open(const char *path, long flags)
{
    int index = lookup(path), descriptor;
    struct memory_file *file = &files[index];
    if (record("OPEN", path, 0, (unsigned long)flags, -1))
        return -5;
    if (file->mtd == 0 || file->mtd == 4 || file->mtd == 5) {
        if ((flags & 3) != O_RDONLY) {
            ++forbidden_writes;
            return -13;
        }
    }
    if (flags & O_CREAT) {
        if (file->exists && (flags & O_EXCL))
            return -EEXIST;
        if (!file->exists) {
            file->exists = 1;
            file->length = 0;
            file->durable = 0;
        }
    } else if (!file->exists) {
        return -ENOENT;
    }
    for (descriptor = 3; descriptor < FD_LIMIT; ++descriptor)
        if (!descriptors[descriptor].used) {
            descriptors[descriptor] = (struct descriptor){1, index, (int)flags, 0};
            return descriptor;
        }
    harness_error("descriptor table exhausted");
    return -1;
}

static long fake_io(long number, long descriptor, unsigned char *buffer, unsigned long length)
{
    struct descriptor *fd;
    struct memory_file *file;
    unsigned long amount = length;
    int inject;
    if (number == SYSCALL_WRITE && descriptor == 1) {
        char *message;
        if (message_count == 64 || length >= sizeof(messages[0]))
            harness_error("unexpected console output");
        message = messages[message_count++];
        memcpy(message, buffer, length);
        message[length] = 0;
        if (length && message[length - 1] == '\n')
            message[length - 1] = 0;
        record("EMIT", message, 0, 0, -1);
        if (strstr(message, "RESTORE FAIL ") == message)
            failed = 1;
        if (strcmp(message, "RESTORE COMPLETE physical_readback_verified") == 0)
            completed = 1;
        return (long)length;
    }
    fd = fd_at(descriptor);
    file = &files[fd->file];
    inject = record(number == SYSCALL_READ ? "READ" : "WRITE", file->path,
                    fd->offset, length, region_of(file, fd->offset));
    if (inject && strcmp(fault_mode, "zero") == 0)
        return 0;
    if (inject && strcmp(fault_mode, "error") == 0)
        return -5;
    if (short_io && amount > (number == SYSCALL_READ ? 4093UL : 73UL))
        amount = number == SYSCALL_READ ? 4093UL : 73UL;
    if (number == SYSCALL_READ) {
        if ((fd->flags & 3) == O_WRONLY)
            harness_error("read on write-only descriptor");
        if (fd->offset >= file->length)
            amount = 0;
        else if (amount > file->length - fd->offset)
            amount = file->length - fd->offset;
        memcpy(buffer, file->bytes + fd->offset, amount);
        if (inject && strcmp(fault_mode, "corrupt") == 0 && amount)
            buffer[0] ^= 1;
    } else {
        if ((fd->flags & 3) == O_RDONLY || fd->offset + amount > file->capacity)
            harness_error("unbounded or read-only write");
        if (inject && strcmp(fault_mode, "partial") == 0)
            amount /= 2;
        if (file->mtd >= 0) {
            unsigned long index;
            for (index = 0; index < amount; ++index)
                if ((file->bytes[fd->offset + index] & buffer[index]) != buffer[index])
                    harness_error("NOR write without erase");
        }
        mutation(file);
        memcpy(file->bytes + fd->offset, buffer, amount);
        if (fd->offset + amount > file->length)
            file->length = fd->offset + amount;
        file->durable = 0;
    }
    fd->offset += amount;
    if (inject && strcmp(fault_mode, "partial") == 0)
        return -5;
    return (long)amount;
}

static long fake_ioctl(long descriptor, long request, long argument)
{
    struct descriptor *fd = fd_at(descriptor);
    struct memory_file *file = &files[fd->file];
    if (file->mtd < 0 || file->mtd > 5)
        harness_error("ioctl on non-MTD object");
    if (request == MEMGETINFO) {
        struct mtd_info_user *info = (struct mtd_info_user *)argument;
        if (record("INFO", file->path, 0, 0, -1))
            return -5;
        memset(info, 0, sizeof(*info));
        info->type = MTD_NORFLASH;
        info->flags = file->mtd >= 1 && file->mtd <= 3 ? MTD_WRITEABLE : 0;
        info->size = (u32)file->length;
        info->erasesize = MTD_ERASE_SIZE;
        info->writesize = MTD_WRITE_SIZE;
        return 0;
    }
    if (request == MEMERASE) {
        struct erase_info_user *erase = (struct erase_info_user *)argument;
        unsigned long amount = erase->length;
        int inject = record("ERASE", file->path, erase->start, amount, region_of(file, erase->start));
        if (file->mtd == 0 || file->mtd == 4 || file->mtd == 5) {
            ++forbidden_writes;
            return -13;
        }
        if ((fd->flags & 3) != O_RDWR || erase->start % MTD_ERASE_SIZE ||
            amount != MTD_ERASE_SIZE || erase->start + amount > file->length)
            harness_error("unbounded erase");
        if (inject && strcmp(fault_mode, "partial") != 0)
            return -5;
        if (inject)
            amount /= 2;
        mutation(file);
        memset(file->bytes + erase->start, 0xff, amount);
        file->durable = 0;
        return inject ? -5 : 0;
    }
    harness_error("unexpected ioctl");
    return -1;
}

static long call1(long number, long argument0)
{
    unsigned index;
    if (number == SYSCALL_CLOSE) {
        struct descriptor *fd = fd_at(argument0);
        int inject = record("CLOSE", files[fd->file].path, fd->offset, 0, -1);
        fd->used = 0;
        return inject ? -5 : 0;
    }
    if (number == SYSCALL_SYNC) {
        if (record("SYNC", "", 0, 0, -1))
            return -5;
        for (index = 0; index < file_count; ++index)
            files[index].durable = files[index].exists;
        return 0;
    }
    if (number == SYSCALL_UNLINK) {
        struct memory_file *file = file_at((const char *)argument0);
        if (record("UNLINK", file->path, 0, 0, -1))
            return -5;
        if (!file->exists)
            return -ENOENT;
        if (strcmp(file->path, AUTH_FILE) == 0 && !file_at(RUN_FILE)->durable)
            ++unsafe_authorization;
        file->exists = file->durable = 0;
        return 0;
    }
    if (number == SYSCALL_MLOCKALL)
        return record("MLOCK", "", 0, (unsigned long)argument0, -1) ? -5 : 0;
    if (number == SYSCALL_PAUSE) {
        record("PAUSE", "", 0, 0, -1);
        paused = 1;
        longjmp(terminal, 1);
    }
    harness_error("unexpected call1");
    return -1;
}

static long call2(long number, long argument0, long argument1)
{
    if (number == SYSCALL_OPEN)
        return fake_open((const char *)argument0, argument1);
    if (number == SYSCALL_RENAME) {
        struct memory_file *source = file_at((const char *)argument0);
        struct memory_file *destination = file_at((const char *)argument1);
        if (record("RENAME", destination->path, 0, 0, -1))
            return -5;
        if (!source->exists || source->length > destination->capacity)
            harness_error("invalid status rename");
        memcpy(destination->bytes, source->bytes, source->length);
        destination->length = source->length;
        destination->exists = 1;
        destination->durable = 0;
        source->exists = source->durable = 0;
        return 0;
    }
    if (number == SYSCALL_MKDIR)
        return record("MKDIR", (const char *)argument0, 0, (unsigned long)argument1, -1) ? -5 : 0;
    if (number == SYSCALL_NANOSLEEP)
        return record("SLEEP", "", 0, 0, -1) ? -5 : 0;
    harness_error("unexpected call2");
    return -1;
}

static long call3(long number, long argument0, long argument1, long argument2)
{
    if (number == SYSCALL_OPEN)
        return fake_open((const char *)argument0, argument1);
    if (number == SYSCALL_READ || number == SYSCALL_WRITE)
        return fake_io(number, argument0, (unsigned char *)argument1, (unsigned long)argument2);
    if (number == SYSCALL_IOCTL)
        return fake_ioctl(argument0, argument1, argument2);
    if (number == SYSCALL_LSEEK) {
        struct descriptor *fd = fd_at(argument0);
        struct memory_file *file = &files[fd->file];
        if (record("SEEK", file->path, (unsigned long)argument1, 0, -1))
            return -5;
        if (argument2 || argument1 < 0 || (unsigned long)argument1 > file->length)
            harness_error("unexpected seek");
        fd->offset = (unsigned long)argument1;
        return argument1;
    }
    if (number == SYSCALL_INIT_MODULE)
        return record("MODULE", MMC_MODULE_PATH, 0, (unsigned long)argument1, -1) ? -5 : 0;
    harness_error("unexpected call3");
    return -1;
}

static long call5(long number, long argument0, long argument1, long argument2,
                  long argument3, long argument4)
{
    (void)argument0; (void)argument2; (void)argument3; (void)argument4;
    if (number == SYSCALL_MOUNT)
        return record("MOUNT", (const char *)argument1, 0, 0, -1) ? -5 : 0;
    harness_error("unexpected call5");
    return -1;
}

static void json_string(const char *value)
{
    putchar('"');
    while (*value) {
        unsigned char byte = (unsigned char)*value++;
        if (byte == '"' || byte == '\\')
            putchar('\\');
        if (byte < 32)
            printf("\\u%04x", byte);
        else
            putchar(byte);
    }
    putchar('"');
}

static void run_restorer(void)
{
    /* Keep report locals outside the setjmp frame and its longjmp lifetime. */
    if (!setjmp(terminal))
        restorer_start();
}

int main(int argc, char **argv)
{
    unsigned index;
    int protected_equal = 1;
    const char *scenario;
    if (argc != 5)
        harness_error("expected fault-step mode short-io scenario");
    fault_step = strtoul(argv[1], 0, 10);
    fault_mode = argv[2];
    short_io = atoi(argv[3]);
    scenario = argv[4];
    for (index = 0; index < 6; ++index) {
        add_file(image_paths[index], partition_size[index], 0x10 + (int)index, -1, 1);
        add_file(mtd_paths[index], partition_size[index],
                 index >= 1 && index <= 3 ? 0x90 + (int)index : 0x10 + (int)index,
                 (int)index, 1);
    }
    memcpy(file_at(KEEP0_FILE)->bytes + MODEL_MARKER_OFFSET, "6100LHV2", 8);
    memcpy(file_at("/dev/mtd0")->bytes + MODEL_MARKER_OFFSET, "6100LHV2", 8);
    add_file("/dev/mtd6", 0, 0, 6, 0);
    add_file(MMC_MODULE_PATH, MMC_MODULE_SIZE, 0x67, -1, 1);
    add_file("/dev/mmcblk0p1", 0, 0, -1, 1);
    add_file(AUTH_FILE, AUTH_SIZE, 'T', -1, 1);
    add_file(RUN_FILE, 0, 0, -1, 0);
    add_file(RUN_TEMP_FILE, 0, 0, -1, 0);
    add_file(COMPLETE_FILE, 0, 0, -1, 0);
    add_file(COMPLETE_TEMP_FILE, 0, 0, -1, 0);
    add_file(GREEN_LED, 1, '0', -1, 1);
    add_file(RED_LED, 1, '0', -1, 1);
    if (strcmp(scenario, "retry") == 0 || strcmp(scenario, "run-temp") == 0)
        status_fixture(strcmp(scenario, "retry") == 0 ? RUN_FILE : RUN_TEMP_FILE,
                       "write_set=mtd3,mtd2,mtd1;activation=last\n");
    else if (strcmp(scenario, "bad-run-temp") == 0)
        status_fixture(RUN_TEMP_FILE, "partial");
    else if (strcmp(scenario, "complete") == 0)
        status_fixture(COMPLETE_FILE, "physical_readback_verified=true;write_set=mtd3,mtd2,mtd1\n");
    else if (strcmp(scenario, "bad-auth") == 0)
        file_at(AUTH_FILE)->bytes[0] ^= 1;
    else if (strcmp(scenario, "normal") != 0)
        harness_error("unknown fixture scenario");

    run_restorer();

    for (index = 0; index < 6; ++index)
        if ((index == 0 || index == 4 || index == 5) &&
            memcmp(file_at(mtd_paths[index])->bytes, file_at(image_paths[index])->bytes,
                   partition_size[index]) != 0)
            protected_equal = 0;
    printf("{\"completed\":%d,\"failed\":%d,\"paused\":%d,\"fault_hit\":%d,\"fault_step\":%lu,"
           "\"mutations\":%lu,\"mutations_after_fault\":%lu,\"forbidden_writes\":%u,"
           "\"unsafe_authorization\":%u,\"unsafe_mutation\":%u,\"protected_equal\":%d,"
           "\"run_exists\":%d,\"run_durable\":%d,\"go_exists\":%d,"
           "\"ok_exists\":%d,\"ok_durable\":%d,\"restored\":[",
           completed, failed, paused, fault_hit, fault_step, mutations, mutations_after_fault,
           forbidden_writes, unsafe_authorization, unsafe_mutation, protected_equal,
           file_at(RUN_FILE)->exists, file_at(RUN_FILE)->durable, file_at(AUTH_FILE)->exists,
           file_at(COMPLETE_FILE)->exists, file_at(COMPLETE_FILE)->durable);
    for (index = 1; index <= 3; ++index)
        printf("%s%d", index == 1 ? "" : ",",
               memcmp(file_at(mtd_paths[index])->bytes, file_at(image_paths[index])->bytes,
                      partition_size[index]) == 0);
    printf("],\"events\":[");
    for (index = 0; index < event_count; ++index) {
        struct event *event = &events[index];
        printf("%s[%lu,%lu,", index ? "," : "", event->first, event->last);
        json_string(event->op); putchar(','); json_string(event->path);
        printf(",%lu,%lu,%lu,%d]", event->offset, event->amount, event->count, event->region);
    }
    puts("]}");
    for (index = 0; index < file_count; ++index)
        free(files[index].bytes);
    return 0;
}
