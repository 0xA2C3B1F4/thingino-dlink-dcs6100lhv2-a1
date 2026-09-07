// SPDX-License-Identifier: MIT
/* Credit a health-checked T31 DTRNG sample to Linux 3.10's input pool. */

#include <errno.h>
#include <fcntl.h>
#include <linux/random.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define SAMPLE_BYTES 64
#define CREDITED_BITS 384
#define MIN_SET_BITS 160
#define MAX_SET_BITS 352

struct entropy_sample {
	int entropy_count;
	int buffer_size;
	unsigned char data[SAMPLE_BYTES];
};

static int read_exact(int descriptor, unsigned char *buffer, size_t size)
{
	size_t offset = 0;
	ssize_t count;

	while (offset < size) {
		count = read(descriptor, buffer + offset, size - offset);
		if (count < 0 && errno == EINTR)
			continue;
		if (count <= 0)
			return -1;
		offset += (size_t)count;
	}
	return 0;
}

static unsigned int set_bits(const unsigned char *data, size_t size)
{
	unsigned int total = 0;
	size_t index;
	unsigned char value;

	for (index = 0; index < size; index++) {
		value = data[index];
		while (value) {
			total += value & 1;
			value >>= 1;
		}
	}
	return total;
}

static int sample_is_sane(const unsigned char *data)
{
	unsigned int ones = set_bits(data, SAMPLE_BYTES);
	size_t offset;

	if (ones < MIN_SET_BITS || ones > MAX_SET_BITS)
		return 0;
	for (offset = sizeof(uint32_t); offset < SAMPLE_BYTES;
	     offset += sizeof(uint32_t)) {
		if (!memcmp(data + offset - sizeof(uint32_t), data + offset,
			    sizeof(uint32_t)))
			return 0;
	}
	return 1;
}

int main(void)
{
	struct entropy_sample sample;
	int source = -1;
	int random = -1;
	int available = 0;
	int result = 1;

	memset(&sample, 0, sizeof(sample));
	source = open("/dev/dtrng", O_RDONLY | O_CLOEXEC);
	if (source < 0 || read_exact(source, sample.data, sizeof(sample.data))) {
		fprintf(stderr, "entropy: DTRNG read failed\n");
		goto out;
	}
	if (!sample_is_sane(sample.data)) {
		fprintf(stderr, "entropy: DTRNG health test failed\n");
		goto out;
	}
	random = open("/dev/random", O_RDONLY | O_CLOEXEC);
	if (random < 0) {
		fprintf(stderr, "entropy: random device unavailable\n");
		goto out;
	}
	sample.entropy_count = CREDITED_BITS;
	sample.buffer_size = sizeof(sample.data);
	if (ioctl(random, RNDADDENTROPY, &sample) < 0 ||
	    ioctl(random, RNDGETENTCNT, &available) < 0 ||
	    available < CREDITED_BITS) {
		fprintf(stderr, "entropy: kernel pool credit failed\n");
		goto out;
	}
	printf("RECOVERY_AP ENTROPY_READY %d\n", available);
	result = 0;
out:
	memset(&sample, 0, sizeof(sample));
	if (random >= 0)
		close(random);
	if (source >= 0)
		close(source);
	return result;
}
