#include "detector.h"
#include <math.h>
void detector_init(Detector *d, double frequency, double rate, double threshold, double hold) {
    *d = (Detector){frequency, rate, threshold, hold, 0, 0};
}
int detector_process(Detector *d, const float *samples, size_t count) {
    if (!count || d->sample_rate <= 0) return 0;
    double coefficient = 2 * cos(2 * 3.141592653589793 * d->frequency / d->sample_rate);
    double q1 = 0, q2 = 0;
    for (size_t i = 0; i < count; ++i) {
        double q0 = samples[i] + coefficient * q1 - q2;
        q2 = q1; q1 = q0;
    }
    double power = 4 * (q1*q1 + q2*q2 - coefficient*q1*q2) / ((double)count*count);
    if (power < d->threshold*d->threshold) {
        d->elapsed = 0; d->active = 0; return 0;
    }
    d->elapsed += count / d->sample_rate;
    if (!d->active && d->elapsed + 1e-9 >= d->hold_seconds) {
        d->active = 1; return 1;
    }
    return 0;
}
