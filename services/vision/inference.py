"""Object detection on the Edge TPU, with a fake detector for --fake.

Which Python route works on the Pi 5 is decided by spikes/coral; this module
tries pycoral, tflite_runtime and ai_edge_litert in that order and needs the
libedgetpu delegate. There is deliberately no silent CPU fallback: without
a TPU the service streams video and reports tpu: false.
"""
import random
import time

EDGETPU_LIB = "libedgetpu.so.1"


class DetectorUnavailable(Exception):
    pass


def load_labels(path):
    """Accepts 'id label' lines (Coral test data) or one label per line."""
    labels = {}
    with open(path, encoding="utf-8") as f:
        for index, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            first, _, rest = line.partition(" ")
            if first.isdigit() and rest:
                labels[int(first)] = rest.strip()
            else:
                labels[index] = line
    return labels


def _make_interpreter(model_path):
    errors = []
    try:
        from pycoral.utils.edgetpu import make_interpreter
        return make_interpreter(model_path), "pycoral"
    except Exception as e:
        errors.append("pycoral: %s" % e)
    for module in ("tflite_runtime.interpreter", "ai_edge_litert.interpreter"):
        try:
            mod = __import__(module, fromlist=["Interpreter", "load_delegate"])
            delegate = mod.load_delegate(EDGETPU_LIB)
            return mod.Interpreter(model_path=model_path, experimental_delegates=[delegate]), module.split(".")[0]
        except Exception as e:
            errors.append("%s: %s" % (module.split(".")[0], e))
    raise DetectorUnavailable("; ".join(errors))


def split_ssd_outputs(outputs):
    """Find boxes, classes, scores, count among SSD postprocess outputs.

    Their order differs between models, so go by shape and content: boxes
    are [N, 4], count is a single value, scores lie in 0..1 and are not all
    whole numbers, classes are whole numbers.
    """
    boxes = count = None
    rest = []
    for out in outputs:
        flat = out.reshape(-1)
        if out.ndim >= 2 and out.shape[-1] == 4:
            boxes = out.reshape(-1, 4)
        elif flat.size == 1:
            count = int(flat[0])
        else:
            rest.append(flat)
    if boxes is None or len(rest) != 2:
        raise ValueError("not an SSD postprocess model")

    def looks_like_scores(arr):
        return float(arr.max(initial=0)) <= 1.0 and any(v != int(v) for v in arr.tolist())

    scores, classes = (rest[0], rest[1]) if looks_like_scores(rest[0]) else (rest[1], rest[0])
    if count is None:
        count = len(scores)
    return boxes, classes, scores, count


class TpuDetector:
    def __init__(self, model_path, labels_path, threshold=0.4, max_objects=10):
        self.interpreter, self.backend = _make_interpreter(model_path)
        self.interpreter.allocate_tensors()
        inp = self.interpreter.get_input_details()[0]
        self._input_index = inp["index"]
        self.input_size = (int(inp["shape"][2]), int(inp["shape"][1]))   # (w, h)
        self.labels = load_labels(labels_path)
        self.threshold = threshold
        self.max_objects = max_objects
        self.tpu = True

    def detect(self, rgb):
        """rgb: HxWx3 uint8 at the model's input size. Returns objects, ms."""
        import numpy as np
        if (rgb.shape[1], rgb.shape[0]) != self.input_size:
            raise ValueError("frame is %dx%d, model wants %dx%d (set camera.lores)"
                             % (rgb.shape[1], rgb.shape[0], *self.input_size))
        started = time.perf_counter()
        self.interpreter.set_tensor(self._input_index, np.expand_dims(rgb, 0))
        self.interpreter.invoke()
        outputs = [self.interpreter.get_tensor(d["index"]) for d in self.interpreter.get_output_details()]
        ms = (time.perf_counter() - started) * 1000
        boxes, classes, scores, count = split_ssd_outputs(outputs)
        objects = []
        for i in range(min(count, len(scores))):
            score = float(scores[i])
            if score < self.threshold:
                continue
            ymin, xmin, ymax, xmax = (min(1.0, max(0.0, float(v))) for v in boxes[i])
            objects.append({
                "label": self.labels.get(int(classes[i]), str(int(classes[i]))),
                "score": round(score, 3),
                "box": [round(xmin, 4), round(ymin, 4), round(xmax, 4), round(ymax, 4)],
            })
        objects.sort(key=lambda o: -o["score"])
        return objects[:self.max_objects], ms


class FakeDetector:
    """A person drifting across the frame, sometimes a cat (for --fake)."""

    backend = "fake"
    input_size = (320, 320)
    tpu = True

    def __init__(self):
        self._t0 = time.monotonic()

    def detect(self, rgb):
        t = time.monotonic() - self._t0
        objects = []
        if t % 20 < 14:
            x = 0.1 + 0.5 * ((t % 14) / 14)
            objects.append({"label": "person", "score": round(0.75 + random.random() * 0.2, 3),
                            "box": [round(x, 4), 0.2, round(x + 0.25, 4), 0.9]})
        if 6 < t % 30 < 12:
            objects.append({"label": "cat", "score": round(0.6 + random.random() * 0.2, 3),
                            "box": [0.65, 0.6, 0.85, 0.85]})
        return objects, 8.0 + random.random() * 3
