"""Bounded authenticated RTSP/H.264 acceptance without credential arguments."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import math
import re
import secrets
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.request import parse_http_list, parse_keqv_list

from .rtsp_protocol import (
    RtspVerificationError,
    _control_url,
    _rtp_payload,
    _video_track,
    h264_sps_dimensions,
)


def _rtsp_authorization(
    challenge: str, username: str, password: bytes
) -> tuple[str | Callable[[str, str], str], str]:
    """Honor the observed scheme; never retry by weakening authentication."""
    if not challenge or len(challenge) > 4096 or any(
        ord(char) < 32 or ord(char) > 126 for char in challenge
    ):
        raise RtspVerificationError("RTSP authentication challenge is invalid")
    scheme, _, parameters = challenge.partition(" ")
    if scheme.lower() == "basic":
        token = base64.b64encode(username.encode("ascii") + b":" + password).decode("ascii")
        return "Basic " + token, "basic"
    if scheme.lower() != "digest":
        raise RtspVerificationError("RTSP authentication scheme is unsupported")
    entries = parse_http_list(parameters)
    if any("=" not in entry for entry in entries):
        raise RtspVerificationError("RTSP Digest challenge is malformed")
    names = [entry.split("=", 1)[0].strip().lower() for entry in entries]
    if len(names) != len(set(names)):
        raise RtspVerificationError("RTSP Digest challenge has duplicate fields")
    try:
        fields = {key.strip().lower(): value for key, value in parse_keqv_list(entries).items()}
    except (ValueError, IndexError) as exc:
        raise RtspVerificationError("RTSP Digest challenge is malformed") from exc
    realm, nonce = fields.get("realm"), fields.get("nonce")
    if not realm or not nonce or fields.get("algorithm", "MD5").upper() != "MD5":
        raise RtspVerificationError("RTSP Digest challenge lacks supported parameters")
    qop = fields.get("qop")
    if qop is not None and "auth" not in [item.strip() for item in qop.split(",")]:
        raise RtspVerificationError("RTSP Digest protection is unsupported")

    def md5(value: bytes) -> str:
        return hashlib.md5(value).hexdigest()

    def quoted(value: str) -> str:
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

    ha1 = md5(username.encode("ascii") + b":" + realm.encode("ascii") + b":" + password)
    cnonce = secrets.token_hex(16) if qop is not None else None
    count = 0

    def authorization(method: str, url: str) -> str:
        nonlocal count
        count += 1
        nc = f"{count:08x}"
        ha2 = md5(f"{method}:{url}".encode("ascii"))
        middle = f"{nonce}:{nc}:{cnonce}:auth" if cnonce is not None else nonce
        response = md5(f"{ha1}:{middle}:{ha2}".encode("ascii"))
        result = [
            "username=" + quoted(username), "realm=" + quoted(realm),
            "nonce=" + quoted(nonce), "uri=" + quoted(url),
            "response=" + quoted(response), "algorithm=MD5",
        ]
        if cnonce is not None:
            result.extend(["qop=auth", "nc=" + nc, "cnonce=" + quoted(cnonce)])
        if "opaque" in fields:
            result.append("opaque=" + quoted(fields["opaque"]))
        return "Digest " + ", ".join(result)

    return authorization, "digest"


@dataclass
class _Rtsp:
    connection: socket.socket
    host: str
    authorization: str | Callable[[str, str], str] | None
    sequence: int = 0
    buffer: bytes = b""

    def _fill(self, count: int) -> None:
        while len(self.buffer) < count:
            chunk = self.connection.recv(65536)
            if not chunk:
                raise RtspVerificationError("RTSP server closed the connection")
            self.buffer += chunk
            if len(self.buffer) > 2 * 1024 * 1024:
                raise RtspVerificationError("RTSP response exceeded its bound")

    def _receive(self, count: int) -> bytes:
        self._fill(count)
        result, self.buffer = self.buffer[:count], self.buffer[count:]
        return result

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        expected_status: int = 200,
    ) -> tuple[dict[str, str], bytes]:
        self.sequence += 1
        fields = {"CSeq": str(self.sequence), "User-Agent": "thingino-dlink/1"}
        if self.authorization is not None:
            fields["Authorization"] = (
                self.authorization(method, url)
                if callable(self.authorization) else self.authorization
            )
        fields.update(headers or {})
        raw = (
            f"{method} {url} RTSP/1.0\r\n"
            + "".join(f"{name}: {value}\r\n" for name, value in fields.items())
            + "\r\n"
        ).encode("ascii")
        self.connection.sendall(raw)
        marker = b"\r\n\r\n"
        while marker not in self.buffer:
            self._fill(len(self.buffer) + 1)
        header_raw, self.buffer = self.buffer.split(marker, 1)
        try:
            lines = header_raw.decode("ascii", "strict").split("\r\n")
        except UnicodeDecodeError as exc:
            raise RtspVerificationError("RTSP response headers are not ASCII") from exc
        if not lines or re.fullmatch(
            rf"RTSP/1\.0 {expected_status} .+", lines[0]
        ) is None:
            raise RtspVerificationError(f"RTSP {method} was not accepted")
        response: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                raise RtspVerificationError("RTSP response header is malformed")
            name, value = line.split(":", 1)
            response[name.lower()] = value.strip()
        length_text = response.get("content-length", "0")
        if (
            not length_text.isdigit()
            or len(length_text) > 9
            or int(length_text) > 256 * 1024
        ):
            raise RtspVerificationError("RTSP response length is invalid")
        return response, self._receive(int(length_text))

    def interleaved(self) -> tuple[int, bytes]:
        while True:
            first = self._receive(1)
            if first == b"$":
                header = self._receive(3)
                size = int.from_bytes(header[1:3], "big")
                if size > 65535:
                    raise RtspVerificationError("RTSP interleaved frame is too large")
                return header[0], self._receive(size)
            self.buffer = first + self.buffer
            marker = b"\r\n\r\n"
            while marker not in self.buffer:
                self._fill(len(self.buffer) + 1)
            _, self.buffer = self.buffer.split(marker, 1)


def verify_rtsp_h264_1080p(
    *, host: str, username: str, password: bytes, timeout: float = 20.0,
    stream_path: str = "/ch0",
) -> dict[str, object]:
    try:
        username_raw = username.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RtspVerificationError("RTSP credential input is invalid") from exc
    if (
        not 1 <= len(username_raw) <= 64
        or any(byte < 0x21 or byte > 0x7E or byte == 0x3A for byte in username_raw)
        or not 8 <= len(password) <= 128
        or any(byte in b"\r\n\0" for byte in password)
    ):
        raise RtspVerificationError("RTSP credential input is invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise RtspVerificationError("RTSP host is not one literal address") from exc
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_multicast
        or address.is_unspecified
    ):
        raise RtspVerificationError("RTSP host is not one private IPv4 address")
    if not math.isfinite(timeout) or not 5.0 <= timeout <= 60.0:
        raise RtspVerificationError("RTSP timeout is outside policy")
    if re.fullmatch(r"/[A-Za-z0-9_.-]{1,64}", stream_path) is None:
        raise RtspVerificationError("RTSP stream path is invalid")
    url = f"rtsp://{host}:554{stream_path}"

    try:
        unauthenticated_connection = socket.create_connection((host, 554), timeout=5.0)
        unauthenticated_connection.settimeout(2.0)
        unauthenticated = _Rtsp(unauthenticated_connection, host, None)
        unauthenticated_headers, _ = unauthenticated.request(
            "OPTIONS", url, expected_status=401
        )
        challenge = unauthenticated_headers.get("www-authenticate", "")
        authorization, auth_scheme = _rtsp_authorization(challenge, username, password.strip())
    except OSError as exc:
        raise RtspVerificationError("RTSP authentication control failed") from exc
    finally:
        if "unauthenticated_connection" in locals():
            unauthenticated_connection.close()

    try:
        connection = socket.create_connection((host, 554), timeout=5.0)
        connection.settimeout(2.0)
    except OSError as exc:
        raise RtspVerificationError("RTSP endpoint is not reachable") from exc
    client = _Rtsp(connection, host, authorization)
    started = time.monotonic()
    sample = hashlib.sha256()
    timestamps: set[int] = set()
    packets = 0
    sps: bytes | None = None
    stream_ssrc: int | None = None
    complete_idr = False
    fu_idr: tuple[int, int, int] | None = None
    try:
        client.request("OPTIONS", url)
        describe_headers, sdp_raw = client.request(
            "DESCRIBE", url, headers={"Accept": "application/sdp"}
        )
        try:
            sdp = sdp_raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RtspVerificationError("RTSP SDP is not ASCII") from exc
        base = describe_headers.get("content-base", url)
        control, payload_type = _video_track(sdp)
        track = _control_url(base, control)
        setup_headers, _ = client.request(
            "SETUP",
            track,
            headers={"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"},
        )
        session = setup_headers.get("session", "").split(";", 1)[0]
        if not session or len(session) > 128:
            raise RtspVerificationError("RTSP SETUP lacks a bounded session")
        client.request("PLAY", url, headers={"Session": session})
        while time.monotonic() - started < timeout:
            try:
                channel, frame = client.interleaved()
            except socket.timeout:
                continue
            if channel != 0:
                continue
            packet_type, sequence, timestamp, marker, ssrc, payload = _rtp_payload(frame)
            if packet_type != payload_type:
                continue
            if stream_ssrc is None:
                stream_ssrc = ssrc
            if ssrc != stream_ssrc:
                continue
            packets += 1
            timestamps.add(timestamp)
            sample.update(payload)
            nal_type = payload[0] & 0x1F
            if nal_type == 7:
                sps = payload
            elif nal_type == 5:
                complete_idr = marker
            elif nal_type == 24:
                offset = 1
                while offset + 2 <= len(payload):
                    length = int.from_bytes(payload[offset : offset + 2], "big")
                    offset += 2
                    if length < 1 or offset + length > len(payload):
                        raise RtspVerificationError("H.264 STAP-A is truncated")
                    nal = payload[offset : offset + length]
                    offset += length
                    if nal and nal[0] & 0x1F == 7:
                        sps = nal
                    if nal and nal[0] & 0x1F == 5 and marker:
                        complete_idr = True
                if offset != len(payload):
                    raise RtspVerificationError("H.264 STAP-A framing is invalid")
            elif nal_type == 28 and len(payload) >= 2:
                reconstructed_type = payload[1] & 0x1F
                start = bool(payload[1] & 0x80)
                end = bool(payload[1] & 0x40)
                if reconstructed_type == 5 and start and not end:
                    fu_idr = (ssrc, timestamp, sequence)
                elif reconstructed_type == 5 and fu_idr is not None:
                    expected_ssrc, expected_timestamp, previous_sequence = fu_idr
                    if (
                        ssrc != expected_ssrc
                        or timestamp != expected_timestamp
                        or sequence != ((previous_sequence + 1) & 0xFFFF)
                    ):
                        fu_idr = None
                    elif end:
                        complete_idr = marker
                        fu_idr = None
                    else:
                        fu_idr = (ssrc, timestamp, sequence)
            if sps is not None and complete_idr and len(timestamps) >= 2:
                break
        if sps is None or not complete_idr or len(timestamps) < 2:
            raise RtspVerificationError("RTSP did not deliver a decodable H.264 picture")
        width, height = h264_sps_dimensions(sps)
        if (width, height) != (1920, 1080):
            raise RtspVerificationError("RTSP H.264 stream is not 1920x1080")
    except OSError as exc:
        raise RtspVerificationError("RTSP exchange failed") from exc
    finally:
        connection.close()
    return {
        "codec": "H264",
        "authentication_enforced": True,
        "height": height,
        "complete_idr_received": True,
        "rtp_packets": packets,
        "rtp_payload_sha256": sample.hexdigest(),
        "rtp_timestamps": len(timestamps),
        "rtsp_authentication": f"{auth_scheme}-hash-locked-closure-credential",
        "rtsp_transport": "interleaved-tcp",
        "width": width,
    }
