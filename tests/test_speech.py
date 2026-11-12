import array
import email
import email.policy
import http.server
import io
import json
import math
import threading
import unittest
import wave

from services.sensor_speech.asr import AsrError, WhisperServer, clean
from services.sensor_speech.audio import FRAME_SAMPLES, SAMPLE_RATE, UtteranceDetector, rms, to_wav


def frame(level):
    samples = array.array("h", (int(level * math.sin(2 * math.pi * 300 * i / SAMPLE_RATE)) for i in range(FRAME_SAMPLES)))
    return samples.tobytes()


SILENCE = frame(50)
VOICE = frame(5000)


class DetectorTest(unittest.TestCase):

    def run_frames(self, detector, frames):
        return [u for u in (detector.feed(f) for f in frames) if u is not None]

    def test_rms(self):
        self.assertEqual(rms(bytes(FRAME_SAMPLES * 2)), 0.0)
        self.assertAlmostEqual(rms(VOICE), 5000 / math.sqrt(2), delta=60)

    def test_utterance_with_pre_roll(self):
        d = UtteranceDetector()
        frames = [SILENCE] * 30 + [VOICE] * 40 + [SILENCE] * 30
        utterances = self.run_frames(d, frames)
        self.assertEqual(len(utterances), 1)
        seconds = len(utterances[0]) / 2 / SAMPLE_RATE
        self.assertGreater(seconds, 1.2)       # 40 voiced frames = 1.2 s, plus pre-roll/tail
        self.assertLess(seconds, 2.0)
        self.assertFalse(d.speaking)

    def test_short_blip_dropped_and_long_speech_cut(self):
        d = UtteranceDetector(max_ms=1500)
        self.assertEqual(self.run_frames(d, [SILENCE] * 30 + [VOICE] * 6 + [SILENCE] * 30), [])
        utterances = self.run_frames(d, [VOICE] * 120)
        self.assertGreaterEqual(len(utterances), 2)

    def test_noise_floor_adapts(self):
        d = UtteranceDetector()
        loud_room = frame(900)
        self.assertEqual(self.run_frames(d, [loud_room] * 100), [])
        self.assertGreater(d.threshold(), 1500)

    def test_wav(self):
        data = to_wav(VOICE * 3)
        with wave.open(io.BytesIO(data)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getnframes()), (16000, 1, FRAME_SAMPLES * 3))


class WhisperStub(http.server.BaseHTTPRequestHandler):
    received = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        message = email.message_from_bytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body,
            policy=email.policy.HTTP)
        WhisperStub.received = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                                for part in message.iter_parts()}
        data = json.dumps({"text": " [Musik] Crawler, geh vorwärts. "}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class AsrTest(unittest.TestCase):

    def test_clean(self):
        self.assertEqual(clean(" [Musik] Hallo (lacht) Welt "), "Hallo Welt")

    def test_whisper_server_client(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), WhisperStub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            asr = WhisperServer("http://127.0.0.1:%d/inference" % server.server_address[1], "de")
            wav = to_wav(VOICE)
            self.assertEqual(asr.transcribe(wav), "Crawler, geh vorwärts.")
            self.assertEqual(WhisperStub.received["language"], b"de")
            self.assertEqual(WhisperStub.received["response_format"], b"json")
            self.assertEqual(WhisperStub.received["file"], wav)
        finally:
            server.shutdown()
            server.server_close()

    def test_unreachable(self):
        with self.assertRaises(AsrError):
            WhisperServer("http://127.0.0.1:9/inference", timeout=1).transcribe(b"x")


if __name__ == "__main__":
    unittest.main()
