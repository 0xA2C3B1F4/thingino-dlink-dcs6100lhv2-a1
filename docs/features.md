# Features

The table separates source implementation, private device evidence, and work
still required for a public release.

| Feature | Source state | Validation boundary |
| --- | --- | --- |
| H.264 stream0 and stream1 | Implemented in Prudynt | Both ran at the selected 15 fps on one A1 camera |
| WebRTC preview | Prudynt RSS publisher plus video-only Raptor rwd | Main, substream, Preview, Streamer, disconnect, and reconnect passed on one private candidate |
| MJPEG fallback | Implemented for both streams | Browser and camera checks passed; remains available if rwd fails |
| Snapshots | Separate stream0 and stream1 JPEG paths | Both returned complete JPEG responses on the tested candidate |
| RTSP | Authenticated Prudynt RTSP | H.264 main stream and concurrent preview passed |
| ONVIF | Persistent loopback service behind uhttpd | WS-Security and snapshot paths passed on the development camera |
| Day and night | Auto, Day, Night, IR-cut, and 850 nm IR | Physical transitions passed; selected encoder rate remains 15 fps |
| Audio | Microphone, speaker state, and AAC path | Device checks passed; talkback is separate from the first WebRTC scope |
| OSD and privacy | Prudynt hardware-OSD pipeline | Main and substream behavior passed on the development camera |
| Motion | Prudynt observations to Control | Native state and Home Assistant event flow passed; ROI edge coverage remains open |
| Home Assistant | Native MQTT worker in Control | Host broker coverage passed; live testing also confirmed HA operation on the current private camera candidate |
| Recording and timelapse | Prudynt framed control path | Earlier start, stop, IDR-safe segment, and SD-backed checks passed; start/stop remained blocked in the current correctness checkpoint |
| SD formatting | Confirmed partition-only FAT32 worker | Host CID/device revalidation tests passed; the current physical-card format matrix remains open |
| Static WebUI | Framework-free TypeScript bundle | Host tests and a real browser passed on the development camera |
| Persistent settings | Fixed JFFS2 data region and OverlayFS | Preserve update passed on one camera; corruption and interruption matrix remains open |
| UART-free install | Stock-U-Boot SD bootstrap and fixed stage 1 | Host and one private flow exist; it is not a supported public installer yet |

Unsupported A1 features remain absent. The WebUI does not offer IR940, white
light, generic GPIO remapping, generic firmware flashing, or whole-card
repartitioning.
