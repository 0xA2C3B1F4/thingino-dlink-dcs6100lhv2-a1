from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from installer.private_config import (
    PrivateConfigError,
    _manifest_v2_for,
    derive_rtsp_viewer_credential,
    generate_private_config,
    import_private_wpa_config,
    inspect_private_config,
    load_private_config,
    load_private_wpa_config,
    read_authorized_key,
    read_confirmed_private_input,
    render_private_wpa_config,
    seal_private_config,
    station_wifi_binding,
    rotate_private_config_role,
)


def ed25519_public_key(*, comment: str = "") -> bytes:
    algorithm = b"ssh-ed25519"
    key = b"k" * 32
    blob = len(algorithm).to_bytes(4, "big") + algorithm + len(key).to_bytes(4, "big") + key
    suffix = f" {comment}" if comment else ""
    return f"ssh-ed25519 {base64.b64encode(blob).decode()}{suffix}\n".encode()


class PrivateConfigTests(unittest.TestCase):
    def test_rtsp_viewer_credential_is_stable_and_domain_separated(self) -> None:
        management = b"a" * 64 + b"\n"
        viewer = derive_rtsp_viewer_credential(management)
        self.assertEqual(viewer, derive_rtsp_viewer_credential(management))
        self.assertNotEqual(viewer, management)
        self.assertRegex(viewer.decode("ascii"), r"^[0-9a-f]{64}\n$")
        self.assertNotEqual(
            viewer,
            derive_rtsp_viewer_credential(b"b" * 64 + b"\n"),
        )

    def test_guided_wifi_derives_psk_without_storing_plaintext(self) -> None:
        payload = render_private_wpa_config(
            ssid="CameraLab",
            passphrase="correct horse battery staple",
        )
        text = payload.decode("ascii")
        self.assertIn('ssid="CameraLab"', text)
        self.assertRegex(text, r"(?m)^    psk=[0-9a-f]{64}$")
        self.assertNotIn("correct horse battery staple", text)

    def test_guided_wifi_accepts_a_64_hex_raw_psk(self) -> None:
        raw_psk = "A1" * 32
        payload = render_private_wpa_config(
            ssid="CameraLab",
            passphrase=raw_psk,
        )
        self.assertIn(f"    psk={raw_psk.lower()}\n".encode(), payload)

    def test_guided_wifi_rejects_a_non_hex_64_byte_credential(self) -> None:
        with self.assertRaisesRegex(PrivateConfigError, "64 hexadecimal"):
            render_private_wpa_config(
                ssid="CameraLab",
                passphrase="z" * 64,
            )

    def test_confirmed_wifi_can_arrive_only_through_inherited_descriptor(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(
                write_fd,
                json.dumps(
                    {
                        "confirmation_passphrase": "__SET_LOCALLY__",
                        "confirmation_ssid": "private-test-ssid",
                        "passphrase": "__SET_LOCALLY__",
                        "ssid": "private-test-ssid",
                    }
                ).encode(),
            )
        finally:
            os.close(write_fd)
        try:
            self.assertEqual(
                read_confirmed_private_input(secrets_fd=read_fd),
                (
                    "private-test-ssid",
                    "__SET_LOCALLY__",
                    "private-test-ssid",
                    "__SET_LOCALLY__",
                ),
            )
        finally:
            os.close(read_fd)

    def test_expected_wpa_source_requires_private_canonical_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            source = Path(directory_name) / "expected-wpa.conf"
            payload = (
                b"ap_scan=1\nnetwork={\n    ssid=43616d6572614c6162\n"
                + b"    psk="
                + b"a" * 64
                + b"\n}\n"
            )
            source.write_bytes(payload)
            source.chmod(0o600)

            self.assertEqual(load_private_wpa_config(source), payload)

            with self.assertRaisesRegex(PrivateConfigError, "independent"):
                load_private_wpa_config(source, independent_from=source)

            source.chmod(0o644)
            with self.assertRaisesRegex(PrivateConfigError, "outside its owner"):
                load_private_wpa_config(source)

    def test_station_wifi_binding_is_semantic_and_password_sensitive(self) -> None:
        first = (
            b"ctrl_interface=/run/wpa_supplicant\n"
            b"network={\n"
            b'  ssid="CameraLab"\n'
            + b"  psk="
            + b"A" * 64
            + b"\n  key_mgmt=WPA-PSK\n  proto=RSN WPA\n}\n"
        )
        same = (
            b"ap_scan=1\nnetwork={\n"
            b"proto=WPA RSN\n"
            b"scan_ssid=1\n"
            b"ssid=43616d6572614c6162\n"
            + b"psk="
            + b"a" * 64
            + b"\nkey_mgmt=WPA-PSK\n}\n"
        )
        changed_password = same.replace(b"a" * 64, b"b" * 64)

        self.assertEqual(station_wifi_binding(first), station_wifi_binding(same))
        self.assertNotEqual(
            station_wifi_binding(first),
            station_wifi_binding(changed_password),
        )

    def test_explicit_management_credential_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            output = Path(directory_name) / "private-bootstrap"
            credential = b"a" * 64 + b"\n"
            generate_private_config(
                output_dir=output,
                ssid="private-test-ssid",
                passphrase="private-test-passphrase",
                authorized_key=ed25519_public_key(),
                credential=credential,
            )
            self.assertEqual(load_private_config(output).credential, credential)

    def test_inspect_outputs_only_role_metadata_and_keyed_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            output = Path(directory_name) / "private-bootstrap"
            generate_private_config(
                output_dir=output,
                ssid="private-test-ssid",
                passphrase="private-test-passphrase",
                authorized_key=ed25519_public_key(),
            )
            credential = (output / "installer.credential").read_text().strip()
            api_key = (output / "webui-api.key").read_text().strip()
            inspected = inspect_private_config(output_dir=output)
            serialized = json.dumps(inspected, sort_keys=True)
            self.assertNotIn(credential, serialized)
            self.assertNotIn(api_key, serialized)
            self.assertEqual(inspected["roles"]["webui_session_id"]["storage"], "runtime-memory-only")
            self.assertEqual(
                len(inspected["roles"]["management_credential"]["fingerprint"]),
                16,
            )

    def test_rotation_is_explicit_and_reseals_without_build_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            output = Path(directory_name) / "private-bootstrap"
            generate_private_config(
                output_dir=output,
                ssid="private-test-ssid",
                passphrase="private-test-passphrase",
                authorized_key=ed25519_public_key(),
            )
            before = load_private_config(output)
            result = rotate_private_config_role(
                output_dir=output,
                role="webui_api_key",
            )
            after = load_private_config(output)
            self.assertTrue(result["rotated"])
            self.assertEqual(before.credential, after.credential)
            self.assertNotEqual(before.api_key, after.api_key)
            self.assertNotEqual(before.credential_set_id, after.credential_set_id)

    def test_ssh_rotation_requires_rebuilding_the_recovery_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            output = Path(directory_name) / "private-bootstrap"
            generate_private_config(
                output_dir=output,
                ssid="private-test-ssid",
                passphrase="private-test-passphrase",
                authorized_key=ed25519_public_key(),
            )
            with self.assertRaisesRegex(PrivateConfigError, "rebuilding"):
                rotate_private_config_role(
                    output_dir=output,
                    role="ssh_authorized_key",
                )

    def test_secrets_stay_only_in_mode_0700_private_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            output = Path(directory_name) / "private-bootstrap"
            result = generate_private_config(
                output_dir=output,
                ssid="private-test-ssid",
                passphrase="private-test-passphrase",
                authorized_key=ed25519_public_key(comment="identifier-removed"),
            )
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                result.files,
                (
                    "authorized_keys",
                    "installer.credential",
                    "webui-api.key",
                    "wpa_supplicant.conf",
                ),
            )
            for path in output.iterdir():
                self.assertEqual(path.stat().st_mode & 0o077, 0)
            wpa = (output / "wpa_supplicant.conf").read_text(encoding="utf-8")
            self.assertIn('ssid="private-test-ssid"', wpa)
            self.assertNotIn("private-test-passphrase", wpa)
            authorized = (output / "authorized_keys").read_text(encoding="ascii")
            self.assertNotIn("identifier-removed", authorized)
            self.assertTrue(authorized.startswith("ssh-ed25519 "))
            manifest = (output / "private-config.json").read_text(encoding="utf-8")
            manifest_document = json.loads(manifest)
            self.assertEqual(manifest_document["schema_version"], 3)
            self.assertEqual(
                manifest_document["rotation_policy"],
                "explicit-only-no-build-regeneration",
            )
            self.assertEqual(
                set(manifest_document["bindings"]),
                {
                    "management_credential",
                    "rtsp_viewer_credential",
                    "ssh_authorized_key",
                    "station_wifi",
                    "webui_api_key",
                },
            )
            self.assertNotIn("private-test-ssid", manifest)
            self.assertNotIn("private-test-passphrase", manifest)
            self.assertNotIn(
                (output / "installer.credential").read_text(encoding="ascii").strip(),
                manifest,
            )
            self.assertNotIn(
                (output / "webui-api.key").read_text(encoding="ascii").strip(),
                manifest,
            )
            loaded = load_private_config(output)
            self.assertEqual(loaded.authorized_key, (output / "authorized_keys").read_bytes())
            self.assertEqual(loaded.credential, (output / "installer.credential").read_bytes())

    def test_schema_one_is_sealed_without_rotating_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            output = Path(directory_name) / "private-bootstrap"
            generate_private_config(
                output_dir=output,
                ssid="private-test-ssid",
                passphrase="private-test-passphrase",
                authorized_key=ed25519_public_key(),
            )
            before = {
                name: (output / name).read_bytes()
                for name in (
                    "installer.credential",
                    "webui-api.key",
                    "wpa_supplicant.conf",
                )
            }
            (output / "private-config.json").write_text(
                json.dumps(
                    {
                        "files": [
                            "authorized_keys",
                            "installer.credential",
                            "webui-api.key",
                            "wpa_supplicant.conf",
                        ],
                        "schema_version": 1,
                        "target": {
                            "hardware_revision": "A1",
                            "model": "DCS-6100LHV2",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (output / "private-config.json").chmod(0o600)
            replacement_key = ed25519_public_key(comment="replacement")

            seal_private_config(output_dir=output, authorized_key=replacement_key)

            for name, raw in before.items():
                self.assertEqual((output / name).read_bytes(), raw)
            self.assertEqual(
                (output / "authorized_keys").read_bytes(),
                read_authorized_key(output / "authorized_keys"),
            )
            self.assertEqual(
                json.loads((output / "private-config.json").read_text())["schema_version"],
                3,
            )

    def test_schema_two_is_sealed_without_rotating_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            output = Path(directory_name) / "private-bootstrap"
            generate_private_config(
                output_dir=output,
                ssid="private-test-ssid",
                passphrase="private-test-passphrase",
                authorized_key=ed25519_public_key(),
            )
            raw = {
                name: (output / name).read_bytes()
                for name in (
                    "authorized_keys",
                    "installer.credential",
                    "webui-api.key",
                    "wpa_supplicant.conf",
                )
            }
            (output / "private-config.json").write_text(
                json.dumps(_manifest_v2_for(raw), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (output / "private-config.json").chmod(0o600)
            before_set_id = _manifest_v2_for(raw)["credential_set_id"]

            seal_private_config(output_dir=output)

            for name, value in raw.items():
                self.assertEqual((output / name).read_bytes(), value)
            self.assertEqual(
                json.loads((output / "private-config.json").read_text())["schema_version"],
                3,
            )
            self.assertEqual(
                json.loads((output / "private-config.json").read_text())[
                    "credential_set_id"
                ],
                before_set_id,
            )

    def test_sealed_manifest_detects_file_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            output = Path(directory_name) / "private-bootstrap"
            generate_private_config(
                output_dir=output,
                ssid="private-test-ssid",
                passphrase="private-test-passphrase",
                authorized_key=ed25519_public_key(),
            )
            (output / "installer.credential").write_bytes(b"f" * 64 + b"\n")
            with self.assertRaisesRegex(PrivateConfigError, "does not match"):
                load_private_config(output)

    def test_invalid_wifi_input_fails_before_output_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            output = Path(directory_name) / "private-bootstrap"
            with self.assertRaises(PrivateConfigError):
                generate_private_config(
                    output_dir=output,
                    ssid="",
                    passphrase="short",
                    authorized_key=ed25519_public_key(),
                )
            self.assertFalse(output.exists())

    def test_imports_only_single_network_with_derived_psk(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source = root / "existing-wpa.conf"
            source.write_text(
                "ap_scan=1\n"
                "network={\n"
                "    ssid=43616d6572614c6162\n"
                "    scan_ssid=1\n"
                "    key_mgmt=WPA-PSK\n"
                f"    psk={'a' * 64}\n"
                "}\n",
                encoding="ascii",
            )
            output = root / "private-import"
            result = import_private_wpa_config(
                output_dir=output,
                source=source,
                authorized_key=ed25519_public_key(),
            )
            self.assertEqual(
                (output / "wpa_supplicant.conf").read_bytes(), source.read_bytes()
            )
            self.assertEqual(
                result.files,
                (
                    "authorized_keys",
                    "installer.credential",
                    "webui-api.key",
                    "wpa_supplicant.conf",
                ),
            )

    def test_import_rejects_plaintext_psk_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source = root / "existing-wpa.conf"
            source.write_text(
                'network={\nssid="CameraLab"\npsk="plaintext-secret"\n}\n',
                encoding="ascii",
            )
            output = root / "private-import"
            with self.assertRaisesRegex(PrivateConfigError, "derived 64-hex"):
                import_private_wpa_config(
                    output_dir=output,
                    source=source,
                    authorized_key=ed25519_public_key(),
                )
            self.assertFalse(output.exists())

    def test_authorized_key_must_be_one_regular_ed25519_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source = root / "id.pub"
            source.write_bytes(ed25519_public_key(comment="local-comment"))
            normalized = read_authorized_key(source)
            self.assertTrue(normalized.startswith(b"ssh-ed25519 "))
            self.assertNotIn(b"local-comment", normalized)

            source.write_text("ssh-rsa invalid\n", encoding="ascii")
            with self.assertRaisesRegex(PrivateConfigError, "Ed25519"):
                read_authorized_key(source)

            link = root / "id-link.pub"
            link.symlink_to(source)
            with self.assertRaisesRegex(PrivateConfigError, "cannot read"):
                read_authorized_key(link)


if __name__ == "__main__":
    unittest.main()
