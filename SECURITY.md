# Security policy

## Supported state

There is no supported installable release yet. One authorized private A1
completed the two-stage installation and a same-device D-Link 1.02.02 restore,
including storage and NOR readback. A reproducible public-source build, the
physical interruption and retry matrix, and release licensing remain open.
Source or test success does not authorize another live installation.

The authoritative readiness and release-gate list is maintained in
`docs/status.md`.

## Reporting a vulnerability

Report vulnerabilities privately through the repository's
[GitHub Security Advisory form](https://github.com/0xA2C3B1F4/thingino-dlink-dcs6100lhv2-a1/security/advisories/new).
If that form is unavailable, contact
[`0xA2C3B1F4`](https://github.com/0xA2C3B1F4) without sensitive details and ask
for a private reporting channel.

Do not open a public issue containing device identifiers, credentials, private
keys, firmware dumps, UART logs, or details that would expose an owned device.
Redact all per-device values from the initial report.

## Sensitive material

Never submit:

- NOR, MTD, EEPROM, or full-flash dumps;
- official D-Link firmware payloads or extracted proprietary files;
- MAC addresses, serial numbers, setup PINs, passwords, tokens, Wi-Fi or MQTT
  credentials, certificates, or private keys;
- raw UART logs, packet captures, or device-specific recovery images; or
- OS02G10 tuning data without documented redistribution rights for the exact
  file.

Use dummy values in public examples. Generate real credentials locally and
store them only in ignored private output.

## Hardware safety

Never write a raw block device based only on a path supplied in documentation.
The repository media tool must identify the exact external physical device,
reject system and ambiguous targets, show model and capacity, and require
confirmation for that exact target before any erase or format operation.
