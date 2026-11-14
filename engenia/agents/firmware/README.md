# Native detector firmware

`propose_firmware()` snapshots the contract pins and electronics duty fractions,
then copies the PlatformIO templates and creates deterministic PCM16 fixtures.
An available electronics interface supplies duty fractions. Older interfaces
without duty fractions use the electronics design defaults (sample
0.003, transmit 0.0005, sleep 0.9965).

The pure C Goertzel detector uses nonoverlapping 100-sample windows. Defaults are
200 Hz, 2 kHz sampling, amplitude threshold 0.15, and 150 ms continuous hold.
An event fires once per continuous tone and rearms when amplitude falls below
threshold. Replay accepts `--frequency`, `--threshold`, and `--hold` overrides.
It feeds normalized float samples to the actual PlatformIO native executable.

`tools/make_data.py` uses PCG64 seed 20261113, Gaussian noise with RMS 0.2 and
200 Hz bursts of amplitude sqrt(2)*0.2 (nominal 0 dB RMS SNR before PCM clipping).
The tone fixture lasts 32 seconds with ten one-second bursts; quiet lasts 600
seconds. Both are mono 2 kHz PCM16. Regenerate with the locked NumPy dependency.

Build runs PlatformIO compilation and Unity tests, restores the replay executable,
and replays the fixtures with the detector settings the proposer chose: the tone at
-6, 0 and +6 dB SNR (`tone_snr_m6db`, `tone_in_noise`, `tone_snr_p6db`; same seed and
noise realisation, only tone amplitude changes) and the noise-only `quiet` recording.
Detection rate counts ground-truth bursts containing at least one event; the
`detection_rate` fact is the 0 dB case and `detection_rate_by_snr_db` holds all three.
False alarms per hour is quiet-event count multiplied by six. The facts carry
`evidence: "simulated"`, so the verifiers report simulated verdicts. If `pio` is not on
PATH the build does not raise: it publishes `not_run: {reason}` and no rates, and both
verifiers return `error` quoting the reason. These are synthetic results, not field
validation. `flash_kb` is an
explicit source-byte estimate, not measured MCU flash usage. Host executables,
PlatformIO caches and variable console logs are not copied into build outputs.
Only sorted JSON detections and metrics are published there.

From a generated source directory:

```sh
pio run -e native
pio test -e native
pio run -e native
python tools/replay.py data/tone_in_noise.wav
python tools/replay.py data/quiet.wav
```
