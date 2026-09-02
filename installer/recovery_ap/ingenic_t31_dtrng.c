// SPDX-License-Identifier: GPL-2.0
/*
 * Minimal T31 DTRNG character device for the RAM-only recovery root.
 * The register interface follows upstream Linux commit 406346d22278.
 */

#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/uaccess.h>

#define DTRNG_PHYS_BASE 0x10072000
#define DTRNG_REGION_SIZE 0x0c
#define DTRNG_CFG 0x00
#define DTRNG_DATA 0x04
#define DTRNG_STATUS 0x08
#define DTRNG_GENERATE_ENABLE BIT(0)
#define DTRNG_DATA_READY BIT(0)
#define DTRNG_POLL_ATTEMPTS 100

static void __iomem *dtrng_base;
static struct clk *dtrng_clock;
static DEFINE_MUTEX(dtrng_lock);

static int dtrng_open(struct inode *inode, struct file *file)
{
	if (!(file->f_mode & FMODE_READ) || (file->f_mode & FMODE_WRITE))
		return -EINVAL;
	return nonseekable_open(inode, file);
}

static int dtrng_word(u32 *word)
{
	unsigned int attempt;

	for (attempt = 0; attempt < DTRNG_POLL_ATTEMPTS; attempt++) {
		if (readl(dtrng_base + DTRNG_STATUS) & DTRNG_DATA_READY) {
			*word = readl(dtrng_base + DTRNG_DATA);
			return 0;
		}
		udelay(10);
	}
	return -ETIMEDOUT;
}

static ssize_t dtrng_read(struct file *file, char __user *buffer,
			  size_t count, loff_t *position)
{
	ssize_t result = 0;
	u32 word;
	size_t chunk;
	int error;

	if (!count)
		return 0;
	if (mutex_lock_interruptible(&dtrng_lock))
		return -ERESTARTSYS;
	while (count) {
		error = dtrng_word(&word);
		if (error) {
			result = result ? result : error;
			break;
		}
		chunk = min(count, sizeof(word));
		if (copy_to_user(buffer, &word, chunk)) {
			result = result ? result : -EFAULT;
			break;
		}
		buffer += chunk;
		count -= chunk;
		result += chunk;
	}
	mutex_unlock(&dtrng_lock);
	return result;
}

static const struct file_operations dtrng_operations = {
	.owner = THIS_MODULE,
	.open = dtrng_open,
	.read = dtrng_read,
	.llseek = no_llseek,
};

static struct miscdevice dtrng_device = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "dtrng",
	.fops = &dtrng_operations,
};

static int __init dtrng_init(void)
{
	int error;
	u32 config;

	dtrng_clock = clk_get(NULL, "dtrng");
	if (IS_ERR(dtrng_clock))
		return PTR_ERR(dtrng_clock);
	error = clk_prepare_enable(dtrng_clock);
	if (error)
		goto put_clock;
	dtrng_base = ioremap(DTRNG_PHYS_BASE, DTRNG_REGION_SIZE);
	if (!dtrng_base) {
		error = -ENOMEM;
		goto disable_clock;
	}
	config = readl(dtrng_base + DTRNG_CFG);
	writel(config | DTRNG_GENERATE_ENABLE, dtrng_base + DTRNG_CFG);
	error = misc_register(&dtrng_device);
	if (error)
		goto unmap;
	pr_info("dcs6100-dtrng: T31 DTRNG ready\n");
	return 0;

unmap:
	config = readl(dtrng_base + DTRNG_CFG);
	writel(config & ~DTRNG_GENERATE_ENABLE, dtrng_base + DTRNG_CFG);
	iounmap(dtrng_base);
disable_clock:
	clk_disable_unprepare(dtrng_clock);
put_clock:
	clk_put(dtrng_clock);
	return error;
}

static void __exit dtrng_exit(void)
{
	u32 config;

	misc_deregister(&dtrng_device);
	config = readl(dtrng_base + DTRNG_CFG);
	writel(config & ~DTRNG_GENERATE_ENABLE, dtrng_base + DTRNG_CFG);
	iounmap(dtrng_base);
	clk_disable_unprepare(dtrng_clock);
	clk_put(dtrng_clock);
}

module_init(dtrng_init);
module_exit(dtrng_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Bounded Ingenic T31 DTRNG source for DCS-6100 recovery");
