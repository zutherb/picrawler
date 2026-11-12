#!/usr/bin/env python3
"""Spike M-V0: load a TFLite model on the Edge TPU (or CPU) and time it.

Tries the available Python backends and prints one JSON line with the
result, so it can be pasted into docs/target-architecture.md.
"""
import argparse
import importlib
import importlib.metadata
import json
import platform
import statistics
import sys
import time

BACKENDS = ("pycoral", "tflite_runtime", "ai_edge_litert")
DISTRIBUTIONS = {
    "pycoral": "pycoral",
    "tflite_runtime": "tflite-runtime",
    "ai_edge_litert": "ai-edge-litert",
}


def package_version(backend):
    try:
        return importlib.metadata.version(DISTRIBUTIONS[backend])
    except importlib.metadata.PackageNotFoundError:
        return None


def make_interpreter(backend, model, use_tpu):
    if backend == "pycoral":
        if not use_tpu:
            raise RuntimeError("pycoral only runs on the Edge TPU")
        from pycoral.utils.edgetpu import make_interpreter as pycoral_make
        return pycoral_make(model)

    if backend == "tflite_runtime":
        module = importlib.import_module("tflite_runtime.interpreter")
    else:
        module = importlib.import_module("ai_edge_litert.interpreter")
    delegates = [module.load_delegate("libedgetpu.so.1")] if use_tpu else []
    return module.Interpreter(model_path=model, experimental_delegates=delegates)


def random_input(detail):
    import numpy as np

    shape, dtype = detail["shape"], detail["dtype"]
    if dtype == np.uint8:
        return np.random.randint(0, 256, size=shape, dtype=np.uint8)
    if dtype == np.int8:
        return np.random.randint(-128, 128, size=shape, dtype=np.int8)
    return np.random.random_sample(shape).astype(dtype)


def bench(interpreter, runs, warmup):
    interpreter.allocate_tensors()
    detail = interpreter.get_input_details()[0]

    # The first invoke uploads the model to the TPU and is much slower.
    interpreter.set_tensor(detail["index"], random_input(detail))
    start = time.perf_counter()
    interpreter.invoke()
    first_ms = (time.perf_counter() - start) * 1000

    for _ in range(warmup):
        interpreter.invoke()

    times = []
    for _ in range(runs):
        interpreter.set_tensor(detail["index"], random_input(detail))
        start = time.perf_counter()
        interpreter.invoke()
        times.append((time.perf_counter() - start) * 1000)
    times.sort()
    return {
        "input_shape": [int(x) for x in detail["shape"]],
        "first_ms": round(first_ms, 2),
        "median_ms": round(statistics.median(times), 2),
        "p95_ms": round(times[int(len(times) * 0.95) - 1], 2),
        "runs": runs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="path to a .tflite model")
    parser.add_argument("--backend", choices=("auto",) + BACKENDS, default="auto")
    parser.add_argument("--cpu", action="store_true", help="run on the CPU (use a non-edgetpu model)")
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=5)
    args = parser.parse_args()

    backends = BACKENDS if args.backend == "auto" else (args.backend,)
    errors = {}
    for backend in backends:
        try:
            interpreter = make_interpreter(backend, args.model, use_tpu=not args.cpu)
        except Exception as e:  # report every failure: that is the point of the spike
            errors[backend] = f"{type(e).__name__}: {e}"
            continue
        result = {
            "backend": backend,
            "backend_version": package_version(backend),
            "device": "cpu" if args.cpu else "edgetpu",
            "model": args.model,
            "python": platform.python_version(),
            "machine": platform.machine(),
        }
        result.update(bench(interpreter, args.runs, args.warmup))
        if errors:
            result["failed_backends"] = errors
        print(json.dumps(result))
        return 0

    print(json.dumps({"error": "no backend worked", "failed_backends": errors}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
