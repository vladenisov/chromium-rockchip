import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("verify_browser", Path(__file__).with_name("verify-browser.py"))
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def events(*properties):
    return [{"method": "Media.playerPropertiesChanged", "params": {
        "playerId": "recorded-player", "properties": [
            {"name": key, "value": value} for key, value in properties]}}]


class VerifyTests(unittest.TestCase):
    def test_mojo_wrapper_does_not_prove_hardware(self):
        for platform in ("false", "true", ""):
            with self.subTest(platform=platform):
                recorded = events(("kVideoDecoderName", "MojoVideoDecoder"),
                                  ("kIsPlatformVideoDecoder", platform))
                proof = verifier.classify_hardware(recorded, [], 180)
                self.assertFalse(proof["hardware_verified"])

    def test_render_gpu_and_dma_fds_do_not_prove_hardware_decoding(self):
        proof = verifier.classify_hardware([], ["/dev/dri/renderD128", "/dev/dma_heap/system", "/dev/rga"], 180)
        self.assertFalse(proof["hardware_verified"])

    def test_platform_decoder_and_advancing_frames_are_required(self):
        recorded = events(("kVideoDecoderName", "V4L2VideoDecoder"),
                          ("kIsPlatformVideoDecoder", "true"))
        self.assertTrue(verifier.classify_hardware(recorded, [], 100)["hardware_verified"])
        self.assertFalse(verifier.classify_hardware(recorded, [], 0)["hardware_verified"])
        self.assertFalse(verifier.classify_hardware(recorded, [], 20)["hardware_verified"])

    def test_rockchip_decoder_fd_without_decoder_proof_is_insufficient(self):
        proof = verifier.classify_hardware(events(("kVideoDecoderName", "MojoVideoDecoder")), ["/dev/mpp_service"], 90)
        self.assertFalse(proof["hardware_verified"])

    def test_software_fallback_with_retained_mpp_fd_is_not_hardware(self):
        recorded = events(("kVideoDecoderName", "FFmpegVideoDecoder"), ("kIsPlatformVideoDecoder", "false"))
        self.assertFalse(verifier.classify_hardware(recorded, ["/dev/mpp_service"], 100)["hardware_verified"])

    def test_final_software_decoder_overrides_earlier_hardware_decoder(self):
        recorded = events(("kVideoDecoderName", "V4L2VideoDecoder"), ("kIsPlatformVideoDecoder", "true"))
        recorded += events(("kVideoDecoderName", "FFmpegVideoDecoder"), ("kIsPlatformVideoDecoder", "false"))
        self.assertFalse(verifier.classify_hardware(recorded, ["/dev/mpp_service"], 100)["hardware_verified"])

    def test_other_session_decoder_cannot_prove_current_video_hardware(self):
        recorded = events(("kVideoDecoderName", "V4L2VideoDecoder"), ("kIsPlatformVideoDecoder", "true"))
        recorded[0]["sessionId"] = "other-session"
        self.assertFalse(verifier.classify_hardware(recorded, ["/dev/mpp_service"], 100, session="video-session")["hardware_verified"])

    def test_decoder_without_explicit_platform_proof_is_insufficient(self):
        recorded = events(("kVideoDecoderName", "V4L2VideoDecoder"))
        self.assertFalse(verifier.classify_hardware(recorded, ["/dev/mpp_service"], 100)["hardware_verified"])

    def test_ambiguous_multiple_players_cannot_inherit_hardware(self):
        recorded = events(("kVideoDecoderName", "V4L2VideoDecoder"), ("kIsPlatformVideoDecoder", "true"))
        software = events(("kVideoDecoderName", "FFmpegVideoDecoder"), ("kIsPlatformVideoDecoder", "false"))
        software[0]["params"]["playerId"] = "new-player"
        self.assertFalse(verifier.classify_hardware(recorded + software, ["/dev/mpp_service"], 100)["hardware_verified"])

    def test_explicit_software_player_cannot_inherit_other_players_hardware(self):
        recorded = events(("kVideoDecoderName", "FFmpegVideoDecoder"), ("kIsPlatformVideoDecoder", "false"))
        self.assertFalse(verifier.classify_hardware(recorded, [], 99)["hardware_verified"])

    def test_only_explicit_video_route_can_access_user_file(self):
        allowed = Path("/tmp/private/movie.mp4")
        self.assertEqual(verifier.video_route("/video?cache=1", allowed), allowed)
        for url in ("/../movie.mp4", "/%2e%2e/movie.mp4", "/video/../../secret", "/etc/passwd", "//video"):
            with self.assertRaises(FileNotFoundError):
                verifier.video_route(url, allowed)


if __name__ == "__main__":
    unittest.main()
