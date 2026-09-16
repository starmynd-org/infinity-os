"""Driving the Windows recorder, and the save check that the 2026-08-17 incident needed.

The recorder itself is `voice/bin/record-windows.ps1`, a Windows process, because WSL2 on this
host has no audio device: `/dev/snd` does not exist, no ffmpeg, no sox, no arecord, and `sudo`
is closed to agents so installing one is not a path. Measured 2026-08-18.

Everything interesting in this file is `save_check`. The recorder can return 0 and leave a file
that is not a recording -- that is what happened to the operator's monologue -- so the file is
STATTED, its RIFF header is PARSED, and its data chunk is compared against what was asked for,
and only a file that survives all three is allowed to advance the capture.
"""

from __future__ import annotations

import re
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config


class SaveFailed(RuntimeError):
    """The recording did not survive the save check. Never swallowed, always counted."""


@dataclass
class Recording:
    path: Path
    bytes: int
    duration_s: float
    elapsed_s: float
    wave_in_devices: int


_KV = re.compile(r"^([a-z_]+)=(.*)$")


def _run_powershell(script: Path, args: list[str], timeout_s: int) -> tuple[int, str, str]:
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", config.windows_path(script)] + args
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    return p.returncode, p.stdout.replace("\r", ""), p.stderr.replace("\r", "")


def record(out_path: Path, seconds: int) -> Recording:
    """Record `seconds` of audio to `out_path`, then prove the file is one.

    Raises `SaveFailed` on every path that does not end in a file that passes the check. The
    caller turns that into `voice failed --stage save`, which is loud (non-zero exit, a named
    sentence) and counted (a row in `brain.voice_capture` with `failure_stage='save'`).
    """
    script = Path(__file__).resolve().parent.parent / "bin" / "record-windows.ps1"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # The recorder is asked for `seconds` and given the stage slack on top. A timeout here is
    # itself a save failure and is reported as one rather than as a Python traceback.
    try:
        rc, out, err = _run_powershell(
            script,
            ["-OutFile", config.windows_path(out_path), "-Seconds", str(seconds),
             "-SampleRate", str(config.SAMPLE_RATE)],
            timeout_s=seconds + config.STAGE_SLACK_SECONDS)
    except subprocess.TimeoutExpired:
        raise SaveFailed(
            f"the Windows recorder did not return within {seconds + config.STAGE_SLACK_SECONDS}s "
            f"for a {seconds}s recording. The microphone may be held by another process; the "
            f"capture is failed at stage 'save' rather than left in flight.") from None

    facts = {}
    for line in out.splitlines():
        m = _KV.match(line.strip())
        if m:
            facts[m.group(1)] = m.group(2)

    # THE EXIT CODE IS NOT THE ANSWER. It is read, and so is every MCI return code the script
    # printed, because a wrapper's rc is not the child's and this script's whole contract is that
    # it says what happened in key=value lines.
    if facts.get("ok") != "yes" or rc != 0:
        raise SaveFailed(
            f"the Windows recorder refused: rc={rc}, ok={facts.get('ok')!r}, "
            f"error={facts.get('error', '(none printed)')!r}, "
            f"open_rc={facts.get('open_rc')} record_rc={facts.get('record_rc')} "
            f"save_rc={facts.get('save_rc')} wave_in_devices={facts.get('wave_in_devices')}."
            + (f" stderr: {err.strip()[:400]}" if err.strip() else ""))

    return save_check(out_path, seconds,
                      elapsed_s=float(facts.get("elapsed_s") or 0.0),
                      wave_in_devices=int(facts.get("wave_in_devices") or 0))


def save_check(path: Path, requested_seconds: int, *, elapsed_s: float = 0.0,
               wave_in_devices: int = 0) -> Recording:
    """Three assertions, and the incident needed all three.

    1. **The file is there and is not empty.** The 2026-08-17 monologue was 0 bytes.
    2. **It is a wav with a PCM data chunk.** A file of exactly 44 bytes is a RIFF header with no
       audio behind it: a 0-byte save wearing a hat, and a size check alone lets it through.
    3. **The data chunk holds something like the audio that was asked for.** A recording that ran
       for 30 seconds and produced 0.2 seconds of samples is a device failure, not a recording,
       and it is the one the operator would not spot by looking at a file size.

    Every refusal names the number it measured. "Save check failed" with no figure costs the
    reader the same investigation the incident already cost.
    """
    if not path.exists():
        raise SaveFailed(
            f"the recorder reported success and there is no file at {path}. That is the "
            f"2026-08-17 shape exactly: a save that said it had saved.")
    size = path.stat().st_size
    if size == 0:
        raise SaveFailed(
            f"{path} is 0 bytes. This is the 2026-08-17 incident to the byte: the operator's "
            f"monologue saved as 0 bytes, he believed it had saved, and nobody found out until a "
            f"human opened it by hand.")
    if size < config.MIN_WAV_BYTES:
        raise SaveFailed(
            f"{path} is {size} bytes, under the {config.MIN_WAV_BYTES}-byte floor "
            f"(a RIFF header is {config.WAV_HEADER_BYTES} bytes and a quarter second of "
            f"{config.SAMPLE_RATE}Hz 16-bit mono is 8000 more). A file this small is a header "
            f"with nothing behind it.")

    fmt = read_wav_facts(path)
    if fmt["data_bytes"] <= 0:
        raise SaveFailed(
            f"{path} is {size} bytes and its RIFF data chunk is {fmt['data_bytes']} bytes. The "
            f"header is there and the audio is not.")

    duration = fmt["duration_s"]
    # A tenth of what was asked for. Deliberately generous: MCI's own startup costs a fraction of
    # a second and a recording the operator cut short himself is his call, not a defect. What
    # this catches is a device that opened, produced nothing, and closed.
    floor = max(0.25, requested_seconds * 0.10)
    if duration < floor:
        raise SaveFailed(
            f"{path} holds {duration:.2f}s of audio and {requested_seconds}s were requested "
            f"(floor {floor:.2f}s). The device opened and captured next to nothing; this is a "
            f"failed save, not a short note.")

    return Recording(path=path, bytes=size, duration_s=round(duration, 2),
                     elapsed_s=elapsed_s, wave_in_devices=wave_in_devices)


def read_wav_facts(path: Path) -> dict:
    """Parse the RIFF header far enough to know how much audio is really in there.

    Hand-rolled rather than `wave` from the standard library on purpose: `wave.open` raises
    `wave.Error` on a truncated or malformed file, and this function has to be able to REPORT a
    malformed file rather than to fail on one. The three failure demonstrations feed it exactly
    such files.
    """
    with path.open("rb") as fh:
        head = fh.read(12)
        if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise SaveFailed(
                f"{path} is not a RIFF/WAVE file: its first 12 bytes are {head!r}. The recorder "
                f"wrote something, and it is not audio.")
        sample_rate, channels, bits, data_bytes = 0, 0, 0, 0
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                break
            cid, csize = struct.unpack("<4sI", hdr)
            if cid == b"fmt ":
                body = fh.read(min(csize, 16))
                if len(body) >= 16:
                    _, channels, sample_rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
                fh.seek(max(0, csize - len(body)), 1)
            elif cid == b"data":
                # The chunk's DECLARED size and the bytes actually on disk, whichever is smaller.
                # A truncated file declares the size it meant to write, and believing the header
                # over the filesystem is how a half-written recording reads as whole.
                here = fh.tell()
                real = max(0, path.stat().st_size - here)
                data_bytes = min(csize, real)
                fh.seek(csize + (csize & 1), 1)
            else:
                fh.seek(csize + (csize & 1), 1)
    bytes_per_frame = max(1, channels * max(1, bits // 8))
    duration = data_bytes / (sample_rate * bytes_per_frame) if sample_rate else 0.0
    return {"sample_rate": sample_rate, "channels": channels, "bits": bits,
            "data_bytes": data_bytes, "duration_s": duration}
