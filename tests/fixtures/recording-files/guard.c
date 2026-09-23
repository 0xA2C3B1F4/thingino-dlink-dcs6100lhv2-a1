#include <assert.h>
#include <stdlib.h>
#include "recording_file.h"

static void stamp(const char *path, char *out)
{
    struct stat st;
    assert(stat(path, &st) == 0);
    assert(snprintf(out, RECORDING_IDENTITY_MAX, "v1 %ju %ju %ju %jd %ld", (uintmax_t)st.st_dev,
                    (uintmax_t)st.st_ino, (uintmax_t)st.st_size, (intmax_t)st.st_mtime,
                    RECORDING_NSEC(st)) > 0);
}
static void write_file(const char *path, const char *data)
{
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    assert(fd >= 0);
    assert(write(fd, data, strlen(data)) == (ssize_t)strlen(data));
    assert(close(fd) == 0);
}
int main(int argc, char **argv)
{
    assert(argc == 2 && chdir(argv[1]) == 0);
    assert(mkdir("day", 0700) == 0);
    write_file("day/closed.mp4", "closed recording");
    char expected[RECORDING_IDENTITY_MAX];
    stamp("day/closed.mp4", expected);
    int root = open(".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    assert(root >= 0);
    int fd = recording_file_open_at(root, "day/closed.mp4", expected);
    assert(fd >= 0);
    char data[32] = {0};
    assert(read(fd, data, sizeof(data)) == 16 && !strcmp(data, "closed recording"));
    assert(lseek(fd, 7, SEEK_SET) == 7);
    assert(read(fd, data, 9) == 9 && !memcmp(data, "recording", 9));
    close(fd);
    assert(recording_file_open_at(root, "day/closed.mp4", "") < 0);
    assert(recording_file_open_at(root, "day/closed.mp4", "v1 0 0 0 0 0") < 0);
    assert(recording_file_open_at(root, "day/../day/closed.mp4", expected) < 0);
    assert(recording_file_open_at(root, "day//closed.mp4", expected) < 0);
    assert(recording_file_open("/etc/passwd", expected) < 0);
    assert(symlink("day", "alias") == 0);
    assert(recording_file_open_at(root, "alias/closed.mp4", expected) < 0);
    assert(symlink("closed.mp4", "day/alias.mp4") == 0);
    assert(recording_file_open_at(root, "day/alias.mp4", expected) < 0);
    assert(rename("day/closed.mp4", "day/old.mp4") == 0);
    write_file("day/closed.mp4", "closed recording");
    assert(recording_file_open_at(root, "day/closed.mp4", expected) < 0);
    stamp("day/closed.mp4", expected);
    assert(link("day/closed.mp4", "day/hard.mp4") == 0);
    assert(recording_file_open_at(root, "day/closed.mp4", expected) < 0);
    assert(mkfifo("day/fifo.mp4", 0600) == 0);
    assert(recording_file_open_at(root, "day/fifo.mp4", expected) < 0);
    close(root);
    puts("recording descriptor identity and path checks: PASS");
    return 0;
}
