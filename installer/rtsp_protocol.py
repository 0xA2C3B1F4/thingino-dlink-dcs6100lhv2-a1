"""Pure, bounded parsers for the RTSP/RTP/H.264 acceptance protocol."""

from __future__ import annotations

import re


class RtspVerificationError(ValueError):
    """The live RTSP stream did not provide one verifiable 1080p picture."""


class _Bits:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.position = 0

    def bit(self) -> int:
        if self.position >= len(self.raw) * 8:
            raise RtspVerificationError("H.264 SPS ended early")
        value = (self.raw[self.position // 8] >> (7 - self.position % 8)) & 1
        self.position += 1
        return value

    def bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def ue(self) -> int:
        zeros = 0
        while self.bit() == 0:
            zeros += 1
            if zeros > 31:
                raise RtspVerificationError("H.264 SPS Exp-Golomb value is invalid")
        return (1 << zeros) - 1 + (self.bits(zeros) if zeros else 0)

    def se(self) -> int:
        value = self.ue()
        return (value + 1) // 2 if value & 1 else -(value // 2)


def _rbsp(raw: bytes) -> bytes:
    result = bytearray()
    zeros = 0
    for byte in raw:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        result.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(result)


def _skip_scaling(bits: _Bits, count: int) -> None:
    last = 8
    next_value = 8
    for _ in range(count):
        if next_value:
            next_value = (last + bits.se() + 256) % 256
        last = next_value or last


def h264_sps_dimensions(nal: bytes) -> tuple[int, int]:
    if len(nal) < 5 or nal[0] & 0x1F != 7:
        raise RtspVerificationError("H.264 SPS NAL is invalid")
    bits = _Bits(_rbsp(nal[1:]))
    profile = bits.bits(8)
    bits.bits(8)
    bits.bits(8)
    bits.ue()
    chroma = 1
    separate_colour_plane = 0
    if profile in {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135}:
        chroma = bits.ue()
        if chroma == 3:
            separate_colour_plane = bits.bit()
        bits.ue()
        bits.ue()
        bits.bit()
        if bits.bit():
            for index in range(8 if chroma != 3 else 12):
                if bits.bit():
                    _skip_scaling(bits, 16 if index < 6 else 64)
    bits.ue()
    pic_order = bits.ue()
    if pic_order == 0:
        bits.ue()
    elif pic_order == 1:
        bits.bit()
        bits.se()
        bits.se()
        for _ in range(bits.ue()):
            bits.se()
    bits.ue()
    bits.bit()
    width_mbs = bits.ue() + 1
    height_map_units = bits.ue() + 1
    frame_mbs_only = bits.bit()
    if not frame_mbs_only:
        bits.bit()
    bits.bit()
    crop_left = crop_right = crop_top = crop_bottom = 0
    if bits.bit():
        crop_left = bits.ue()
        crop_right = bits.ue()
        crop_top = bits.ue()
        crop_bottom = bits.ue()
    chroma_array = 0 if separate_colour_plane else chroma
    sub_width = 1 if chroma_array in (0, 3) else 2
    sub_height = 2 if chroma_array == 1 else 1
    crop_x = sub_width
    crop_y = sub_height * (2 - frame_mbs_only)
    width = width_mbs * 16 - (crop_left + crop_right) * crop_x
    height = height_map_units * 16 * (2 - frame_mbs_only) - (
        crop_top + crop_bottom
    ) * crop_y
    if width <= 0 or height <= 0:
        raise RtspVerificationError("H.264 SPS dimensions are invalid")
    return width, height


def _control_url(base: str, control: str) -> str:
    if control.startswith("rtsp://"):
        return control
    if control.startswith("/"):
        match = re.match(r"(rtsp://[^/]+)", base)
        if match is None:
            raise RtspVerificationError("RTSP base URL is invalid")
        return match.group(1) + control
    return base.rstrip("/") + "/" + control


def _video_track(sdp: str) -> tuple[str, int]:
    sections = re.split(r"(?=^m=)", sdp, flags=re.MULTILINE)
    for section in sections:
        if not section.startswith("m=video "):
            continue
        media = section.splitlines()[0].split()
        if len(media) < 4:
            continue
        payloads = [value for value in media[3:] if value.isdigit()]
        h264 = [
            value
            for value in payloads
            if re.search(
                rf"^a=rtpmap:{re.escape(value)} H264/90000\s*$",
                section,
                re.MULTILINE | re.IGNORECASE,
            )
        ]
        control = re.search(r"^a=control:(\S+)\s*$", section, re.MULTILINE)
        if control and len(h264) == 1 and 0 <= int(h264[0]) <= 127:
            return control.group(1), int(h264[0])
    raise RtspVerificationError("RTSP SDP lacks one H.264 video control track")


def _rtp_payload(packet: bytes) -> tuple[int, int, int, bool, int, bytes]:
    if len(packet) < 12 or packet[0] >> 6 != 2:
        raise RtspVerificationError("RTP packet is malformed")
    offset = 12 + (packet[0] & 0x0F) * 4
    if packet[0] & 0x10:
        if len(packet) < offset + 4:
            raise RtspVerificationError("RTP extension is truncated")
        offset += 4 + int.from_bytes(packet[offset + 2 : offset + 4], "big") * 4
    end = len(packet) - (packet[-1] if packet[0] & 0x20 else 0)
    if offset >= end:
        raise RtspVerificationError("RTP payload is empty")
    return (
        packet[1] & 0x7F,
        int.from_bytes(packet[2:4], "big"),
        int.from_bytes(packet[4:8], "big"),
        bool(packet[1] & 0x80),
        int.from_bytes(packet[8:12], "big"),
        packet[offset:end],
    )
