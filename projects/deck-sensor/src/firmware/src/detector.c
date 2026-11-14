#include "detector.h"
#include <math.h>
void detector_init(Detector *d, double rate, double threshold, double hold) {
    size_t block = rate > 0 ? (size_t)(DETECTOR_BLOCK_SECONDS * rate) : 0;
    *d = (Detector){rate, threshold, hold, 0.0, 0, block, 0.0, 0};
}
int detector_process(Detector *d, const float *samples, size_t count) {
    if (!count || d->block == 0) return 0;
    int fired = 0;
    for (size_t i = 0; i < count; ++i) {
        d->energy += (double)samples[i] * samples[i];
        if (++d->filled < d->block) continue;
        double rms = sqrt(d->energy / (double)d->block);
        if (rms < d->threshold) {
            d->elapsed = 0; d->active = 0;
        } else {
            d->elapsed += DETECTOR_BLOCK_SECONDS;
            if (!d->active && d->elapsed + 1e-9 >= d->hold_seconds) {
                d->active = 1; fired = 1;
            }
        }
        d->energy = 0.0; d->filled = 0;
    }
    return fired;
}
