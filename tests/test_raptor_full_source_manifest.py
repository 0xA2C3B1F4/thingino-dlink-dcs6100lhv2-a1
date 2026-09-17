"""Host-only tests for the full Raptor source manifest generator."""

import hashlib
import configparser
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "raptor_full_source_manifest", ROOT / "scripts/raptor_full_source_manifest.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ManifestTests(unittest.TestCase):
    def test_camera_profile_starts_the_audio_reader(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "components/raptor/raptor-webrtc.conf")
        self.assertFalse(config.getboolean("webrtc", "video_only"))
        self.assertTrue(config.getboolean("webrtc", "signaling_loopback"))
        self.assertEqual(config.getint("webrtc", "max_clients"), 1)
        audio = configparser.ConfigParser(interpolation=None)
        audio.read(ROOT / "components/raptor/raptor-audio.conf")
        self.assertFalse(audio.getboolean("audio", "ai_enabled"))
        self.assertFalse(audio.getboolean("audio", "ao_enabled"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(os.environ["TMPDIR"]).resolve(strict=True))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "base"
        self.repo.mkdir()
        subprocess.check_call(["git", "-C", str(self.repo), "init", "--quiet"])
        subprocess.check_call(["git", "-C", str(self.repo), "config", "user.name", "Fixture"])
        subprocess.check_call(
            [
                "git",
                "-C",
                str(self.repo),
                "config",
                "user.email",
                "fixture@example.invalid",
            ]
        )
        (self.repo / "LICENSE").write_text("license\n")
        (self.repo / "source.c").write_text("base\n")
        subprocess.check_call(["git", "-C", str(self.repo), "add", "."])
        subprocess.check_call(["git", "-C", str(self.repo), "commit", "--quiet", "-m", "base"])
        self.spec = {
            "url": "https://github.com/example/project.git",
            "base": self.git("rev-parse", "HEAD"),
            "base_tree": self.git("rev-parse", "HEAD^{tree}"),
            "license": "LICENSE",
            "license_sha256": hashlib.sha256(b"license\n").hexdigest(),
        }
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "LICENSE").write_text("license\n")
        changed = self.source / "source.c"
        changed.write_text("matched\n")
        changed.chmod(0o755)

    def git(self, *arguments):
        return subprocess.check_output(["git", "-C", str(self.repo), *arguments], text=True).strip()

    def test_inventory_binds_bytes_modes_and_symlinks(self):
        (self.source / "link").symlink_to("source.c")
        inventory = MODULE.tree_inventory(self.source)
        digest = MODULE.inventory_sha256(inventory)
        self.assertEqual(inventory["link"], {"symlink": "source.c"})
        self.assertTrue(inventory["source.c"]["executable"])
        (self.source / "source.c").chmod(0o644)
        self.assertNotEqual(digest, MODULE.inventory_sha256(MODULE.tree_inventory(self.source)))

    def test_flat_patch_reconstructs_exact_git_tree_without_private_paths(self):
        tree, patch = MODULE.materialize_source(
            name="sample",
            base_repo=self.repo,
            source=self.source,
            spec=self.spec,
            destination=self.root / "materialized",
        )
        self.assertTrue(patch)
        self.assertNotIn(str(self.root).encode(), patch)
        MODULE._verify_patch(
            name="sample",
            base_repo=self.repo,
            spec=self.spec,
            patch=patch,
            tree=tree,
            root=self.root,
        )

    def test_public_lock_shape_rejects_private_and_incomplete_source_sets(self):
        sources = {}
        matched = {}
        for name in MODULE.MEDIA_SOURCE_NAMES:
            sources[name] = dict(self.spec)
            matched[name] = {"file_count": 2, "inventory_sha256": "1" * 64}
        tls_sources = {}
        for name in MODULE.TLS_SOURCE_NAMES:
            tls_sources[name] = {
                **self.spec,
                "tree": self.spec["base_tree"],
                "patches": [],
            }
        source_lock = {"schema_version": 1, "sources": sources}
        tls_lock = {"schema_version": 1, "sources": tls_sources}
        matched_lock = {
            "schema_version": 1,
            "sources": matched,
            "raptor_git_tree": "2" * 40,
        }
        combined, _ = MODULE._validate_locks(source_lock, matched_lock, tls_lock)
        self.assertEqual(tuple(combined), MODULE.SOURCE_NAMES)
        sources["raptor"]["url"] = "ssh://private.invalid/source"
        with self.assertRaisesRegex(ValueError, "public GitHub HTTPS"):
            MODULE._validate_locks(source_lock, matched_lock, tls_lock)

    def test_patch_privacy_scan_rejects_local_provenance(self):
        synthetic_host_path = "/".join(("", "Volumes", "fixture", "source"))
        (self.source / "source.c").write_text(synthetic_host_path + "\n")
        with self.assertRaisesRegex(ValueError, "private provenance"):
            MODULE.materialize_source(
                name="sample",
                base_repo=self.repo,
                source=self.source,
                spec=self.spec,
                destination=self.root / "rejected",
            )

    def test_rwd_audio_profile_guard_is_scoped_to_aac(self):
        patch_text = (
            ROOT / "patches/raptor-full-source/raptor.patch"
        ).read_text()
        self.assertIn(
            "+#ifdef RAPTOR_AAC\n \tuint8_t audio_profile = 0;\n+#endif", patch_text
        )
        self.assertIn(
            "+#ifdef RAPTOR_AAC\n \t\t\taudio_profile = ahdr->profile;\n+#endif",
            patch_text,
        )
        fixture = self.root / "audio-profile.c"
        fixture.write_text(
            """#include <stdint.h>
typedef struct { uint8_t profile; } header_t;
static int is_he_aac(const header_t *ahdr) {
#ifdef RAPTOR_AAC
    uint8_t audio_profile = 0;
    audio_profile = ahdr->profile;
    return audio_profile == 5;
#else
    return ahdr->profile == 5;
#endif
}
int main(void) { const header_t header = {5}; return is_he_aac(&header) ? 0 : 1; }
"""
        )
        for enabled in (False, True):
            binary = self.root / ("audio-profile-aac" if enabled else "audio-profile-no-aac")
            command = ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror"]
            if enabled:
                command.append("-DRAPTOR_AAC")
            subprocess.check_call(command + [str(fixture), "-o", str(binary)], timeout=15)
            subprocess.check_call([str(binary)], timeout=5)

    def test_actual_listener_accepts_only_credentialed_rtsp_wildcard(self):
        patch_text = (ROOT / "patches/raptor-full-source/raptor.patch").read_text()
        section = patch_text.split("diff --git a/raptor_listener.h b/raptor_listener.h\n", 1)[1]
        section = section.split("\ndiff --git ", 1)[0]
        header = "\n".join(line[1:] for line in section.splitlines()
                           if line.startswith("+") and not line.startswith("+++")) + "\n"
        (self.root / "raptor_listener.h").write_text(header)
        (self.root / "rss_common.h").write_text('''#pragma once
#include <stdbool.h>
#include <errno.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <string.h>
#include <fcntl.h>
typedef struct { const char *address, *user, *password; } rss_config_t;
static const char *rss_config_get_str(rss_config_t *cfg, const char *section,
                                    const char *key, const char *fallback) {
    (void)section;
    if (!strcmp(key, "bind_address")) return cfg->address;
    if (!strcmp(key, "username")) return cfg->user;
    if (!strcmp(key, "password")) return cfg->password;
    return fallback;
}
static bool rss_config_get_bool(rss_config_t *cfg, const char *section,
                                const char *key, bool fallback) {
    (void)cfg; (void)section; (void)key; return fallback;
}
''')
        (self.root / "rss_net.h").write_text('''#pragma once
#include "rss_common.h"
static int rss_listen_tcp(int port, int backlog) {
    (void)port; (void)backlog; errno = EINVAL; return -1;
}
static int rss_set_nonblocking(int fd) {
    return fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
}
''')
        fixture = self.root / "listener.c"
        fixture.write_text('''#include <assert.h>
#include "raptor_listener.h"
int main(void) {
    rss_config_t cfg = {"0.0.0.0", "root", "fixture-password"};
    int fd = rsd_listen_tcp(&cfg, 0);
    assert(fd >= 0);
    struct sockaddr_in address = {0};
    socklen_t length = sizeof(address);
    assert(getsockname(fd, (struct sockaddr *)&address, &length) == 0);
    assert(address.sin_addr.s_addr == htonl(INADDR_ANY));
    assert(fcntl(fd, F_GETFL) & O_NONBLOCK);
    close(fd);
    assert(rhd_listen_tcp(&cfg, 0) == -1 && errno == EINVAL);
    cfg.password = "";
    assert(rsd_listen_tcp(&cfg, 0) == -1 && errno == EINVAL);
    cfg.password = "fixture-password"; cfg.user = "";
    assert(rsd_listen_tcp(&cfg, 0) == -1 && errno == EINVAL);
    cfg.address = "127.0.0.1";
    fd = rhd_listen_tcp(&cfg, 0);
    assert(fd >= 0);
    assert(getsockname(fd, (struct sockaddr *)&address, &length) == 0);
    assert(address.sin_addr.s_addr == htonl(INADDR_LOOPBACK));
    close(fd);
    cfg.address = "example.invalid";
    assert(rsd_listen_tcp(&cfg, 0) == -1 && errno == EINVAL);
    cfg.address = "192.0.2.1";
    assert(rsd_listen_tcp(&cfg, 0) == -1 && errno == EINVAL);
    return 0;
}
''')
        binary = self.root / "listener"
        subprocess.check_call(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                               "-I", str(self.root), str(fixture), "-o", str(binary)], timeout=15)
        subprocess.check_call([str(binary)], timeout=5)

    def test_full_rwd_keeps_reviewed_camera_runtime_fixes(self):
        patch_text = (ROOT / "patches/raptor-full-source/raptor.patch").read_text()
        def additions(path):
            section = patch_text.split(f"diff --git a/{path} b/{path}\n", 1)[1]
            section = section.split("\ndiff --git ", 1)[0]
            return "\n".join(line[1:] for line in section.splitlines()
                              if line.startswith("+") and not line.startswith("+++"))
        main = additions("rwd/rwd_main.c")
        media = additions("rwd/rwd_media.c")
        self.assertIn('rss_config_get_bool(dctx.cfg, "webrtc", "video_only", false)', main)
        self.assertIn('rss_config_get_bool(dctx.cfg, "webrtc", "signaling_loopback", false)', main)
        self.assertIn("create_http_socket(srv.http_port, srv.signaling_loopback)", main)
        self.assertIn("if (!srv->video_only &&", main)
        self.assertIn("if (audio_started)", main)
        self.assertIn("rss_ring_release(srv.video_rings[s])", main)
        self.assertEqual(media.count("rss_ring_acquire(srv->video_rings[s])"), 2)
        self.assertEqual(media.count("rss_ring_release(srv->video_rings[s])"), 2)
        self.assertIn('mbedtls_platform_dev_random = "/dev/urandom";', additions("rwd/rwd_dtls.c"))
        # Compile the exact new loopback branch from the shipped patch.
        branch = main.split("\tif (loopback_only) {", 1)[1].split("\n\t}", 1)[0]
        fixture = self.root / "rwd-loopback.c"
        fixture.write_text('''#include <assert.h>
#include <stdbool.h>
#include <unistd.h>
#include <stdint.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <netinet/in.h>
static int create_http_socket(int port, bool loopback_only) {
    if (loopback_only) {''' + branch + '''
    }
    return -1;
}
int main(void) {
    int fd = create_http_socket(0, true);
    assert(fd >= 0);
    struct sockaddr_in address = {0};
    socklen_t length = sizeof(address);
    assert(getsockname(fd, (struct sockaddr *)&address, &length) == 0);
    assert(address.sin_addr.s_addr == htonl(INADDR_LOOPBACK));
    assert(fcntl(fd, F_GETFL) & O_NONBLOCK);
    close(fd);
    return 0;
}
''')
        binary = self.root / "rwd-loopback"
        subprocess.check_call(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                               str(fixture), "-o", str(binary)], timeout=15)
        subprocess.check_call([str(binary)], timeout=5)


if __name__ == "__main__":
    unittest.main()
