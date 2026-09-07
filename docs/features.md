# Features

The table separates implemented features from their device validation.
The validated candidate passed main/substream WebRTC, Motion, Privacy,
Day/Night and Information checks. Features marked as having earlier evidence
have not been revalidated on this candidate. See
[current acceptance](status.md#tested-installation) for measured results and limits.

| Feature | Source state | Validation boundary |
| --- | --- | --- |
| H.264 stream0 and stream1 | Implemented in Prudynt | Both ran at the selected 15 fps on one A1 camera |
| WebRTC preview | Prudynt RSS publisher plus video-only Raptor rwd | Main/substream Preview passed on September 6; Streamer, disconnect and reconnect have earlier device evidence |
| MJPEG fallback | Implemented for both streams | Browser and camera checks passed; remains available if rwd fails |
| Snapshots | Separate stream0 and stream1 JPEG paths | Both returned complete JPEG responses on the tested candidate |
| RTSP | Authenticated Prudynt RTSP | H.264 main stream and concurrent preview passed |
| ONVIF | Persistent loopback service behind uhttpd | SOAP retains its compatibility listener; snapshot paths now require management authentication and need renewed device acceptance |
| Day and night | Auto, Day, Night, IR-cut, and 850 nm IR | Physical transitions passed; selected encoder rate remains 15 fps |
| Audio | Microphone, speaker state, and AAC path | Device checks passed; talkback is separate from the first WebRTC scope |
| OSD and privacy | Prudynt hardware-OSD pipeline | Main/substream Privacy passed on September 6; 60 sampled main Privacy frames were black. General OSD controls have earlier evidence |
| Motion | Prudynt observations to Control | On/off confirmation passed on September 6; Home Assistant event flow has earlier evidence and ROI edge coverage remains open |
| Home Assistant | Native MQTT worker in Control | Host broker and earlier live-device checks passed; not repeated in the September 6 acceptance run |
| Recording and timelapse | Prudynt framed control path | Earlier start, stop, IDR-safe segment and SD-backed checks exist; current-candidate acceptance remains open |
| SD formatting | Confirmed partition-only FAT32 worker | Host CID/device revalidation tests passed; the current physical-card format matrix remains open |
| Static WebUI | Framework-free TypeScript bundle with the classic layout | Host tests and selected controls in a real browser passed on September 6 |
| Persistent settings | Fixed JFFS2 data region and OverlayFS | Preserve update passed on one camera; corruption and interruption matrix remains open |
| UART-free install | Universal stage, stock updater, host handoff, and fixed Stage 1 | Clean source build and one installation passed without UART on September 6, using existing recovery inputs; broader physical acceptance remains open |

The A1 control scope excludes IR940, white light, generic GPIO remapping,
generic firmware flashing, and whole-card repartitioning.
