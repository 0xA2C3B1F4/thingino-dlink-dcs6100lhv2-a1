from __future__ import annotations

import unittest
from unittest.mock import patch

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

    def settimeout(self, timeout: float) -> None:
        pass

    def close(self) -> None:
        pass


class RtspVerificationTests(unittest.TestCase):
    def test_digest_auth_matches_rfc_2617_vector(self) -> None:
        with patch.object(rtsp_verify.secrets, "token_hex", return_value="0a4f113b"):
            authorization, scheme = rtsp_verify._rtsp_authorization(
                'Digest realm="testrealm@host.com", qop="auth,auth-int", '
                'nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", '
                'opaque="5ccc069c403ebaf9f0171e9517f40e41"',
                "Mufasa", b"Circle Of Life",
            )
        self.assertEqual(scheme, "digest")
        header = authorization("GET", "/dir/index.html")
        self.assertIn('response="6629fae49393a05397450978507c4ef1"', header)
        self.assertIn("nc=00000001", header)
        self.assertIn("qop=auth", header)
        second = authorization("GET", "/dir/index.html")
        self.assertIn("nc=00000002", second)
        self.assertNotEqual(second, header)

    def test_raptor_digest_without_qop_binds_method_and_uri(self) -> None:
        authorization, scheme = rtsp_verify._rtsp_authorization(
            'Digest realm="Raptor", nonce="test-nonce"', "viewer", b"test-password"
        )
        options = authorization("OPTIONS", "rtsp://192.0.2.2:554/ch0")
        describe = authorization("DESCRIBE", "rtsp://192.0.2.2:554/ch0")
        setup = authorization("SETUP", "rtsp://192.0.2.2:554/ch0/track1")
        self.assertEqual(scheme, "digest")
        self.assertEqual(len({options, describe, setup}), 3)
        self.assertNotIn("qop=", options)
        self.assertNotIn("test-password", options)
        self.assertIn('uri="rtsp://192.0.2.2:554/ch0/track1"', setup)

    def test_legacy_basic_auth_remains_supported(self) -> None:
        authorization, scheme = rtsp_verify._rtsp_authorization(
            'Basic realm="camera"', "viewer", b"password"
        )
        self.assertEqual(scheme, "basic")
        self.assertEqual(authorization, "Basic dmlld2VyOnBhc3N3b3Jk")

    def test_digest_rejects_malformed_or_unsupported_challenges(self) -> None:
        for challenge in (
            "", "Bearer token", "Digest realm=x", "Digest nonce=x",
            "Digest realm=x, nonce=", "Digest realm=x, nonce=n, nonce=m",
            "Digest realm=x, nonce=n, algorithm=SHA-512",
            "Digest realm=x, nonce=n, qop=auth-int",
            "Digest realm=x, nonce=n\r\nInjected: value",
        ):
            with self.subTest(challenge=challenge):
                with self.assertRaises(RtspVerificationError):
                    rtsp_verify._rtsp_authorization(challenge, "viewer", b"password")

    def test_full_rtp_acceptance_uses_observed_digest_challenge(self) -> None:
        challenge = FakeSocket(
            b'RTSP/1.0 401 Unauthorized\r\n'
            b'WWW-Authenticate: Digest realm="Raptor", nonce="test-nonce"\r\n\r\n'
        )
        sdp = b'm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\na=control:track1\r\n'
        response = b'RTSP/1.0 200 OK\r\n\r\n'
        sps = bytes.fromhex("67640028acd940780227e5c044000003000400000300083c60c658")
        sps_rtp = bytes.fromhex("806000010000000100000001") + sps
        idr_rtp = bytes.fromhex("80e0000200000002000000016501")
        frames = b''.join(b'$\x00' + len(frame).to_bytes(2, 'big') + frame for frame in (sps_rtp, idr_rtp))
        stream = FakeSocket(
            response
            + b'RTSP/1.0 200 OK\r\nContent-Length: ' + str(len(sdp)).encode() + b'\r\n\r\n' + sdp
            + b'RTSP/1.0 200 OK\r\nSession: test-session\r\n\r\n'
            + response + frames
        )
        with patch.object(rtsp_verify.socket, "create_connection", side_effect=[challenge, stream]):
            result = rtsp_verify.verify_rtsp_h264_1080p(
                host="192.0.2.2", username="root", password=b"test-password",
                stream_path="/stream0",
            )
        self.assertTrue(result["authentication_enforced"])
        self.assertEqual(result["rtsp_authentication"], "digest-hash-locked-closure-credential")
        self.assertEqual(result["width"], 1920)
        self.assertTrue(result["complete_idr_received"])
        self.assertNotIn(b"Authorization:", challenge.sent)
        self.assertEqual(stream.sent.count(b"Authorization: Digest "), 4)
        self.assertNotIn(b"Basic", stream.sent)
        self.assertIn(b'DESCRIBE rtsp://192.0.2.2:554/stream0 RTSP/1.0', stream.sent)

    def test_invalid_stream_path_never_opens_a_connection(self) -> None:
        with patch.object(rtsp_verify.socket, "create_connection") as connect:
            for path in ("//other/ch0", "/ch0\r\nHeader: value", "rtsp://other/ch0", "/ch0?query"):
                with self.subTest(path=path), self.assertRaises(RtspVerificationError):
                    rtsp_verify.verify_rtsp_h264_1080p(
                        host="192.0.2.2", username="root", password=b"test-password",
                        stream_path=path,
                    )
            connect.assert_not_called()

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
