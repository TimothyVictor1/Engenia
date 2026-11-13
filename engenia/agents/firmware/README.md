# Native detector firmware

`propose_firmware()` snapshots the contract pins and electronics duty fractions,
then copies the PlatformIO templates and creates deterministic PCM16 fixtures.
Firmware depends on electronics so an available interface is current. Older
interfaces without duty fractions use the electronics design defaults (sample
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
and replays both fixtures. Detection rate counts ground-truth bursts containing
at least one event. False alarms per hour is quiet-event count multiplied by six.
These are synthetic calculated results, not field validation. `flash_kb` is an
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
