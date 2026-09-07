from __future__ import annotations

import unittest

from installer import rtsp_protocol, rtsp_verify
from installer.rtsp_verify import (
    _Rtsp,
    RtspVerificationError,
    _control_url,
    _rtp_payload,
    _video_track,
    h264_sps_dimensions,
)


class FakeSocket:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.sent = b""

    def recv(self, count: int) -> bytes:
        result, self.response = self.response[:count], self.response[count:]
        return result

    def sendall(self, raw: bytes) -> None:
        self.sent += raw


class RtspVerificationTests(unittest.TestCase):
    def test_rtsp_verify_preserves_protocol_facade_identities(self) -> None:
        self.assertIs(RtspVerificationError, rtsp_protocol.RtspVerificationError)
        self.assertIs(h264_sps_dimensions, rtsp_protocol.h264_sps_dimensions)
        self.assertIs(_control_url, rtsp_protocol._control_url)
        self.assertIs(_video_track, rtsp_protocol._video_track)
        self.assertIs(_rtp_payload, rtsp_protocol._rtp_payload)
        self.assertIs(rtsp_verify.RtspVerificationError, RtspVerificationError)

    def test_rtsp_response_parser_does_not_drop_header_bytes(self) -> None:
        connection = FakeSocket(
            b"RTSP/1.0 200 OK\r\nContent-Length: 4\r\n\r\ntest"
        )
        headers, body = _Rtsp(connection, "192.0.2.2", None).request(
            "OPTIONS", "rtsp://192.0.2.2/ch0"
        )
        self.assertEqual(headers["content-length"], "4")
        self.assertEqual(body, b"test")
        self.assertNotIn(b"Authorization:", connection.sent)

    def test_rtsp_parser_requires_observed_unauthorized_status(self) -> None:
        connection = FakeSocket(b"RTSP/1.0 401 Unauthorized\r\n\r\n")
        _Rtsp(connection, "192.0.2.2", None).request(
            "OPTIONS", "rtsp://192.0.2.2/ch0", expected_status=401
        )

    def test_rtsp_parser_maps_non_ascii_headers_to_public_error(self) -> None:
        connection = FakeSocket(b"RTSP/1.0 200 OK\r\nX-Bad: \xff\r\n\r\n")
        with self.assertRaisesRegex(RtspVerificationError, "not ASCII"):
            _Rtsp(connection, "192.0.2.2", None).request(
                "OPTIONS", "rtsp://192.0.2.2/ch0"
            )

    def test_parses_1920x1080_sps(self) -> None:
        sps = bytes.fromhex(
            "67640028acd940780227e5c044000003000400000300083c60c658"
        )
        self.assertEqual(h264_sps_dimensions(sps), (1920, 1080))

    def test_rejects_non_sps(self) -> None:
        with self.assertRaisesRegex(RtspVerificationError, "SPS NAL"):
            h264_sps_dimensions(b"\x65\x00\x00\x00\x00")

    def test_rejects_truncated_sps_as_the_public_exception(self) -> None:
        with self.assertRaisesRegex(RtspVerificationError, "SPS ended early"):
            h264_sps_dimensions(b"\x67\x64\x00\x28\x80")

    def test_parses_one_bounded_h264_sdp_track(self) -> None:
        sdp = (
            "v=0\r\n"
            "m=audio 0 RTP/AVP 0\r\n"
            "a=control:audio\r\n"
            "m=video 0 RTP/AVP 96\r\n"
            "a=rtpmap:96 H264/90000\r\n"
            "a=control:trackID=1\r\n"
        )
        self.assertEqual(_video_track(sdp), ("trackID=1", 96))
        self.assertEqual(
            _control_url("rtsp://192.0.2.2/ch0", "trackID=1"),
            "rtsp://192.0.2.2/ch0/trackID=1",
        )

    def test_rejects_sdp_without_one_h264_payload(self) -> None:
        sdp = (
            "m=video 0 RTP/AVP 96 97\r\n"
            "a=rtpmap:96 H264/90000\r\n"
            "a=rtpmap:97 H264/90000\r\n"
            "a=control:trackID=1\r\n"
        )
        with self.assertRaisesRegex(RtspVerificationError, "one H.264"):
            _video_track(sdp)

    def test_parses_rtp_header_and_payload(self) -> None:
        packet = bytes.fromhex(
            "80e01234"
            "01020304"
            "a0b0c0d0"
            "67112233"
        )
        self.assertEqual(
            _rtp_payload(packet),
            (96, 0x1234, 0x01020304, True, 0xA0B0C0D0, b"\x67\x11\x22\x33"),
        )

    def test_rejects_truncated_rtp_extension(self) -> None:
        packet = bytes.fromhex("9000123401020304a0b0c0d0")
        with self.assertRaisesRegex(RtspVerificationError, "extension is truncated"):
            _rtp_payload(packet)


if __name__ == "__main__":
    unittest.main()
