from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/onvif/0004-config-lifetime.patch"
FIXTURE_PASSWORD = "fixture-only-password"
RELAY_TOKEN = "relay-token"


TRACKING_HARNESS = r'''
#include "conf.h"
#include "log.h"
#include "onvif_simple_server.h"

#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define MAX_TRACKED 65536

service_context_t service_ctx;
static void *tracked[MAX_TRACKED];
static size_t tracked_count;
static size_t allocation_calls;
static long fail_after = -1;
static int require_credentials;

static int tracked_index(void *ptr)
{
    for (size_t i = 0; i < tracked_count; i++) {
        if (tracked[i] == ptr)
            return (int) i;
    }
    return -1;
}

static void track_new(void *ptr)
{
    if (!ptr)
        return;
    if (tracked_count >= MAX_TRACKED) {
        fprintf(stderr, "tracking table exhausted\n");
        abort();
    }
    tracked[tracked_count++] = ptr;
}

static void track_remove(size_t index)
{
    tracked[index] = tracked[--tracked_count];
}

static int inject_failure(void)
{
    size_t call = allocation_calls++;
    return fail_after >= 0 && call >= (size_t) fail_after;
}

void *tracked_malloc(size_t size)
{
    if (inject_failure())
        return NULL;
    void *ptr = malloc(size);
    track_new(ptr);
    return ptr;
}

void *tracked_calloc(size_t count, size_t size)
{
    if (inject_failure())
        return NULL;
    void *ptr = calloc(count, size);
    track_new(ptr);
    return ptr;
}

void *tracked_realloc(void *old_ptr, size_t size)
{
    int old_index = old_ptr ? tracked_index(old_ptr) : -1;
    if (inject_failure())
        return NULL;
    void *ptr = realloc(old_ptr, size);
    if (!ptr) {
        if (old_ptr && size == 0 && old_index >= 0)
            track_remove((size_t) old_index);
        return NULL;
    }
    if (old_index >= 0)
        tracked[(size_t) old_index] = ptr;
    else
        track_new(ptr);
    return ptr;
}

void *tracked_strdup(const char *source)
{
    if (inject_failure())
        return NULL;
    size_t size = strlen(source) + 1;
    char *copy = (char *) malloc(size);
    if (copy) {
        memcpy(copy, source, size);
        track_new(copy);
    }
    return copy;
}

void tracked_free(void *ptr)
{
    if (ptr) {
        int index = tracked_index(ptr);
        if (index >= 0)
            track_remove((size_t) index);
    }
    free(ptr);
}

void log_log(int level, const char *file, int line, const char *fmt, ...)
{
    (void) level;
    (void) file;
    (void) line;
    (void) fmt;
}

int log_level_from_string(const char *level_str)
{
    static const char *const levels[] = {"FATAL", "ERROR", "WARN", "INFO", "DEBUG", "TRACE"};
    if (!level_str)
        return -1;
    for (int i = 0; i < (int) (sizeof(levels) / sizeof(levels[0])); i++) {
        if (strcasecmp(level_str, levels[i]) == 0)
            return i;
    }
    return -1;
}

static int run_once(const char *path, long allocation_failure)
{
    if (tracked_count != 0) {
        fprintf(stderr, "live allocations before run: %zu\n", tracked_count);
        return 90;
    }
    memset(&service_ctx, 0, sizeof(service_ctx));
    allocation_calls = 0;
    fail_after = allocation_failure;
    int result = process_json_conf_file((char *) path);
    int credentials_lost = require_credentials && result == 0 &&
        (!service_ctx.username || !service_ctx.password ||
         strcmp(service_ctx.username, "fixture-user") ||
         strcmp(service_ctx.password, "fixture-only-password"));
    free_conf_file();
    if (tracked_count != 0) {
        fprintf(stderr, "live allocations after result %d: %zu\n", result, tracked_count);
        return 91;
    }
    if (credentials_lost) {
        fprintf(stderr, "successful load lost configured credentials\n");
        return 92;
    }
    return result;
}

static int run_lifecycle(const char *path, int require_injected_failure)
{
    int result = run_once(path, -1);
    if (result != 0)
        return result == 90 || result == 91 ? result : 20;

    size_t successful_calls = allocation_calls;
    int saw_injected_failure = 0;
    for (int repeat = 0; repeat < 4; repeat++) {
        result = run_once(path, -1);
        if (result != 0)
            return result == 90 || result == 91 ? result : 21;
    }

    for (size_t failure = 0; failure < successful_calls + 2; failure++) {
        result = run_once(path, (long) failure);
        if (result == 90 || result == 91 || result == 92)
            return result;
        if (result < 0)
            saw_injected_failure = 1;
    }
    if (require_injected_failure && !saw_injected_failure)
        return 22;
    return 0;
}

int main(int argc, char **argv)
{
    if (argc >= 3 && strcmp(argv[1], "--credentials") == 0) {
        require_credentials = 1;
        return run_lifecycle(argv[2], 1);
    }
    if (argc >= 3 && strcmp(argv[1], "--expect-failure") == 0) {
        int result = run_once(argv[2], -1);
        return result < 0 ? 0 : 30;
    }
    if (argc >= 3 && strcmp(argv[1], "--require-failure") == 0)
        return run_lifecycle(argv[2], 1);
    if (argc < 2)
        return 2;
    for (int i = 1; i < argc; i++) {
        int result = run_lifecycle(argv[i], 0);
        if (result != 0)
            return result;
    }
    return 0;
}
'''


class ConfigLifetimePatchContractTests(unittest.TestCase):
    def test_patch_contains_tree_destructor_and_failure_cleanup(self) -> None:
        patch = PATCH.read_text(encoding="utf-8")
        self.assertIn("free_json_value(json_file);", patch)
        self.assertIn("goto cleanup;", patch)
        self.assertIn("free_event_strings", patch)
        self.assertIn("free_ptz_strings", patch)
        self.assertIn("relay_outputs[i].token", patch)
        self.assertIn("service_ctx.events != NULL", patch)
        self.assertNotIn("+    if (service_ctx.events_enable == 1)", patch)

    def test_patch_is_a_single_conf_source_change(self) -> None:
        patch = PATCH.read_text(encoding="utf-8")
        self.assertEqual(patch.count("diff --git "), 1)
        self.assertIn("diff --git a/src/conf.c b/src/conf.c", patch)


class AppliedConfigLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source_value = os.environ.get("ONVIF_LIFECYCLE_SOURCE")
        jct_value = os.environ.get("ONVIF_LIFECYCLE_JCT")
        if not source_value or not jct_value or "TMPDIR" not in os.environ:
            raise unittest.SkipTest(
                "set ONVIF_LIFECYCLE_SOURCE, ONVIF_LIFECYCLE_JCT, and TMPDIR for the full-source test"
            )

        cls.source = Path(source_value)
        cls.jct = Path(jct_value)
        if not (cls.source / "src/conf.c").is_file():
            raise unittest.SkipTest(f"missing pinned source: {cls.source / 'src/conf.c'}")
        if not (cls.jct / "json_config.c").is_file():
            raise unittest.SkipTest(f"missing pinned jct source: {cls.jct}")
        compiler = os.environ.get("CC", "cc")
        if shutil.which(compiler) is None:
            raise unittest.SkipTest(f"compiler not found: {compiler}")
        cls.compiler = compiler

        cls.temp = tempfile.TemporaryDirectory(
            prefix="onvif-config-lifetime-", dir=os.environ["TMPDIR"]
        )
        root = Path(cls.temp.name)
        cls.tree = root / "source"
        shutil.copytree(cls.source, cls.tree)
        cls.jct_copy = root / "jct"
        shutil.copytree(cls.jct, cls.jct_copy)

        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=cls.tree,
            check=True,
            capture_output=True,
            timeout=20,
        )
        subprocess.run(
            ["git", "apply", str(PATCH)],
            cwd=cls.tree,
            check=True,
            capture_output=True,
            timeout=20,
        )
        applied = (cls.tree / "src/conf.c").read_text(encoding="utf-8")
        if "free_json_value(json_file);" not in applied:
            raise AssertionError("git apply did not produce the lifecycle implementation")

        cls.harness = root / "tracked_harness.c"
        cls.harness.write_text(TRACKING_HARNESS, encoding="utf-8")
        build = root / "build"
        build.mkdir()
        cls.binary = build / "config-lifetime-harness"
        allocator_defines = [
            "-Dmalloc=tracked_malloc",
            "-Dcalloc=tracked_calloc",
            "-Drealloc=tracked_realloc",
            "-Dfree=tracked_free",
            "-Dstrdup=tracked_strdup",
        ]
        source_flags = [
            cls.compiler,
            "-std=c99",
            "-D_GNU_SOURCE",
            "-O0",
            "-g",
            *allocator_defines,
            "-I",
            str(cls.tree / "src"),
            "-I",
            str(cls.jct_copy),
        ]
        cls.run_command(
            [*source_flags, "-c", str(cls.tree / "src/conf.c"), "-o", str(build / "conf.o")]
        )
        jct_objects = []
        for name in ("json_config", "json_parse", "json_serialize", "json_value", "jsonpath"):
            obj = build / f"{name}.o"
            cls.run_command(
                [
                    cls.compiler,
                    "-std=c99",
                    "-D_GNU_SOURCE",
                    "-O0",
                    "-g",
                    *allocator_defines,
                    "-I",
                    str(cls.jct_copy),
                    "-c",
                    str(cls.jct_copy / f"{name}.c"),
                    "-o",
                    str(obj),
                ]
            )
            jct_objects.append(obj)
        cls.run_command(
            [
                cls.compiler,
                "-std=c99",
                "-D_GNU_SOURCE",
                "-O0",
                "-g",
                "-I",
                str(cls.tree / "src"),
                "-I",
                str(cls.jct_copy),
                str(cls.harness),
                str(build / "conf.o"),
                *(str(obj) for obj in jct_objects),
                "-lm",
                "-o",
                str(cls.binary),
            ]
        )

        cls.disabled = root / "events-and-ptz-disabled.json"
        cls.disabled.write_text(
            json.dumps(
                {
                    "camera": {"manufacturer": "Acme", "model": "Cam"},
                    "server": {"ifs": "lo", "port": 8080},
                    "scopes": ["onvif://www.onvif.org/name/test"],
                    "profiles": {
                        "main": {
                            "name": "Main",
                            "width": 1920,
                            "height": 1080,
                            "url": "rtsp://camera/main",
                        }
                    },
                    "ptz": {
                        "enable": 0,
                        "move_x": "unused-x",
                        "move_y": "unused-y",
                        "jump_to_rel_speed": "unused-speed",
                    },
                    "events_enable": 0,
                    "events": [
                        {
                            "topic": "topic",
                            "source_name": "source",
                            "source_type": "type",
                            "source_value": "value",
                            "input_file": "input",
                        }
                    ],
                    "imaging": [{"video_source_token": "video", "cmd_ircut_on": "on"}],
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        cls.enabled = root / "events-and-ptz-enabled.json"
        cls.enabled.write_text(
            json.dumps(
                {
                    "camera": {"manufacturer": "Acme", "model": "Cam"},
                    "server": {"ifs": "lo", "port": 8080},
                    "ptz": {
                        "enable": 1,
                        "get_position": "get-position",
                        "move_x": "move-x",
                        "move_y": "move-y",
                        "move_both": "move-both",
                        "move_stop": "move-stop",
                        "jump_to_rel_speed": "jump-relative",
                    },
                    "events_enable": 1,
                    "events": [
                        {
                            "topic": "topic",
                            "source_name": "source",
                            "source_type": "type",
                            "source_value": "value",
                            "input_file": "input",
                        }
                    ],
                    "relays": [{"token": RELAY_TOKEN, "close": "close", "open": "open"}],
                    "imaging": [{"video_source_token": "video", "cmd_ircut_on": "on"}],
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        cls.overflow = root / "event-overflow.json"
        cls.overflow.write_text(
            json.dumps(
                {
                    "events_enable": 1,
                    "events": [
                        {
                            "topic": f"topic-{index}",
                            "source_name": "source",
                            "source_type": "type",
                            "source_value": "value",
                            "input_file": "input",
                        }
                        for index in range(8)
                    ],
                    "relays": [{"token": RELAY_TOKEN, "close": "close", "open": "open"}],
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        cls.credentials = []
        for name, config in (
            ("root", {"username": "fixture-user", "password": FIXTURE_PASSWORD}),
            ("nested", {"server": {"username": "fixture-user", "password": FIXTURE_PASSWORD}}),
            ("precedence", {"username": "ignored-root", "password": RELAY_TOKEN,
                            "server": {"username": "fixture-user", "password": FIXTURE_PASSWORD}}),
        ):
            fixture = root / f"credentials-{name}.json"
            fixture.write_text(json.dumps(config), encoding="utf-8")
            cls.credentials.append(fixture)

    @classmethod
    def run_command(cls, command: list[str]) -> None:
        subprocess.run(command, check=True, capture_output=True, timeout=60)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "temp"):
            cls.temp.cleanup()

    def test_applied_source_repeated_loads_and_allocation_failures(self) -> None:
        result = subprocess.run(
            [str(self.binary), str(self.disabled), str(self.enabled)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_applied_source_cleans_event_overflow(self) -> None:
        result = subprocess.run(
            [str(self.binary), "--expect-failure", str(self.overflow)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_configured_credentials_survive_or_loading_fails(self) -> None:
        for fixture in self.credentials:
            with self.subTest(fixture=fixture.name):
                result = subprocess.run(
                    [str(self.binary), "--credentials", str(fixture)],
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
