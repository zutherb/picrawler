import os
import tempfile
import threading
import unittest
import urllib.request

from services.vision.camera import FrameBuffer
from services.vision.inference import load_labels
from services.vision.stream import StreamServer

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


class LabelsTest(unittest.TestCase):

    def test_both_formats(self):
        with tempfile.TemporaryDirectory() as d:
            numbered = os.path.join(d, "a.txt")
            plain = os.path.join(d, "b.txt")
            with open(numbered, "w") as f:
                f.write("0  person\n1  bicycle\n17 cat\n")
            with open(plain, "w") as f:
                f.write("person\nbicycle\n\n")
            self.assertEqual(load_labels(numbered), {0: "person", 1: "bicycle", 17: "cat"})
            self.assertEqual(load_labels(plain), {0: "person", 1: "bicycle"})


@unittest.skipIf(np is None, "numpy not installed")
class SsdOutputsTest(unittest.TestCase):

    def test_split_regardless_of_order(self):
        from services.vision.inference import split_ssd_outputs
        boxes = np.array([[[0.1, 0.2, 0.5, 0.6], [0, 0, 1, 1]]], dtype=np.float32)
        classes = np.array([[0.0, 17.0]], dtype=np.float32)
        scores = np.array([[0.91, 0.42]], dtype=np.float32)
        count = np.array([2.0], dtype=np.float32)
        for order in ([boxes, classes, scores, count], [boxes, scores, count, classes]):
            b, c, s, n = split_ssd_outputs(order)
            self.assertEqual(n, 2)
            self.assertEqual(c.tolist(), [0.0, 17.0])
            self.assertAlmostEqual(float(s[0]), 0.91, places=5)
            self.assertEqual(b.shape, (2, 4))


class StreamTest(unittest.TestCase):

    def test_snapshot_and_mjpeg(self):
        buffer = FrameBuffer()
        server = StreamServer(buffer, port=0)
        server.start()
        try:
            base = "http://127.0.0.1:%d" % server.port
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/snapshot.jpg", timeout=2)
            cm.exception.close()
            self.assertEqual(cm.exception.code, 503)

            buffer.put(b"\xff\xd8frame1\xff\xd9")
            with urllib.request.urlopen(base + "/snapshot.jpg", timeout=2) as resp:
                self.assertEqual(resp.read(), b"\xff\xd8frame1\xff\xd9")

            feeder = threading.Timer(0.2, lambda: buffer.put(b"\xff\xd8frame2\xff\xd9"))
            feeder.start()
            with urllib.request.urlopen(base + "/stream.mjpg", timeout=3) as resp:
                self.assertIn("multipart/x-mixed-replace", resp.headers["Content-Type"])
                chunk = resp.read1(4096)
                while b"frame2" not in chunk:
                    chunk += resp.read1(4096)
            self.assertIn(b"--frame\r\nContent-Type: image/jpeg", chunk)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
