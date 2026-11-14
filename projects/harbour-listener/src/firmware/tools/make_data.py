"""Fixed PCG64 seed, 2 kHz mono PCM16; tone bursts in noise at -6, 0 and +6 dB RMS SNR."""
import json
import wave
from pathlib import Path

import numpy as np

SEED = 20261113
NOISE_RMS = 0.2
# SNR (dB) -> file name. 0 dB keeps its original name so existing fixtures stay identical.
SNR_FILES = {-6: "tone_snr_m6db", 0: "tone_in_noise", 6: "tone_snr_p6db"}


def _write(path, samples):
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 2000, 0, "NONE", "not compressed"))
        stream.writeframes(pcm.tobytes())


def generate_snr_levels(directory, bursts):
    """Same noise realisation (same seed) for every level; only the tone amplitude changes."""
    for snr_db, name in SNR_FILES.items():
        if snr_db == 0:
            continue
        rng = np.random.Generator(np.random.PCG64(SEED))
        samples = rng.normal(0, NOISE_RMS, 32 * 2000)
        amplitude = np.sqrt(2) * NOISE_RMS * 10 ** (snr_db / 20)
        for start, end in bursts:
            t = np.arange((end - start) * 2000) / 2000
            samples[start * 2000:end * 2000] += amplitude * np.sin(2 * np.pi * 200 * t)
        _write(directory / (name + ".wav"), samples)


def generate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.Generator(np.random.PCG64(SEED))
    bursts = [[2 + 3 * i, 3 + 3 * i] for i in range(10)]
    for name, seconds in (("tone_in_noise", 32), ("quiet", 600)):
        samples = rng.normal(0, NOISE_RMS, seconds * 2000)
        if name == "tone_in_noise":
            for start, end in bursts:
                t = np.arange((end - start) * 2000) / 2000
                samples[start * 2000:end * 2000] += np.sqrt(2) * 0.2 * np.sin(2*np.pi*200*t)
        _write(directory / (name + ".wav"), samples)
    generate_snr_levels(directory, bursts)
    (directory / "truth.json").write_text(json.dumps(bursts) + "\n")


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1] / "data")
