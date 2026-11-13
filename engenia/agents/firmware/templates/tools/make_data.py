"""Fixed PCG64 seed, 2 kHz mono PCM16; 0 dB RMS signal/noise ratio."""
import json
import wave
from pathlib import Path

import numpy as np


def generate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.Generator(np.random.PCG64(20261113))
    bursts = [[2 + 3 * i, 3 + 3 * i] for i in range(10)]
    for name, seconds in (("tone_in_noise", 32), ("quiet", 600)):
        samples = rng.normal(0, 0.2, seconds * 2000)
        if name == "tone_in_noise":
            for start, end in bursts:
                t = np.arange((end - start) * 2000) / 2000
                samples[start * 2000:end * 2000] += np.sqrt(2) * 0.2 * np.sin(2*np.pi*200*t)
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
        with wave.open(str(directory / (name + ".wav")), "wb") as stream:
            stream.setparams((1, 2, 2000, 0, "NONE", "not compressed"))
            stream.writeframes(pcm.tobytes())
    (directory / "truth.json").write_text(json.dumps(bursts) + "\n")


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1] / "data")
