"""Speech to text backends.

WhisperServer talks to whisper.cpp's `whisper-server` on localhost
(POST /inference, multipart). Ansible builds and runs it as its own unit,
so the model stays loaded and this service needs no native libraries.
"""
import json
import time
import urllib.error
import urllib.request
import uuid


class AsrError(Exception):
    pass


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                      % (boundary, name, value)).encode())
    for name, (filename, content, ctype) in files.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                      'Content-Type: %s\r\n\r\n' % (boundary, name, filename, ctype)).encode() + content + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    return b"".join(parts), "multipart/form-data; boundary=%s" % boundary


class WhisperServer:
    name = "whisper.cpp"

    def __init__(self, url="http://127.0.0.1:8082/inference", language="de", timeout=30.0):
        self.url = url
        self.language = language
        self.timeout = timeout

    def transcribe(self, wav):
        body, ctype = multipart({"response_format": "json", "language": self.language, "temperature": "0.0"},
                                {"file": ("speech.wav", wav, "audio/wav")})
        request = urllib.request.Request(self.url, data=body, headers={"Content-Type": ctype}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise AsrError("%s: %s" % (self.url, e))
        return clean(data.get("text", ""))


def clean(text):
    """whisper adds markers like [Musik] or (Lachen) for non-speech."""
    out, depth = [], 0
    for ch in text:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return " ".join("".join(out).split())


class FakeAsr:
    name = "fake"
    PHRASES = ["Crawler, steh auf", "Crawler, was siehst du?", "Crawler, geh zwei Schritte vorwärts",
               "Hallo zusammen", "Crawler, wink mal", "Crawler, setz dich"]

    def __init__(self):
        self._i = 0

    def transcribe(self, wav):
        time.sleep(0.2)
        text = self.PHRASES[self._i % len(self.PHRASES)]
        self._i += 1
        return text
