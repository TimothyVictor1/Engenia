"""Feed PCM WAV samples to the PlatformIO native detector, output JSON seconds."""
import argparse
import json
import subprocess
import wave
from pathlib import Path

import numpy as np


def replay(path, threshold=0.25, hold=0.15):
    root = Path(__file__).resolve().parents[1]
    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getsampwidth() != 2:
            raise ValueError("expected mono PCM16 WAV")
        rate = stream.getframerate()
        samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2")
    result = subprocess.run(
        [str(root / ".pio/build/native/program"), str(rate), str(threshold), str(hold)],
        input=(samples.astype(np.float32) / 32768).tobytes(), capture_output=True, check=True,
    )
    return json.loads(result.stdout)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    parser.add_argument("--threshold", type=float, default=0.25)
    parser.add_argument("--hold", type=float, default=0.15)
    args = parser.parse_args()
    print(json.dumps(replay(args.wav, args.threshold, args.hold)))
