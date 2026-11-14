#ifndef DETECTOR_H
#define DETECTOR_H
#include <stddef.h>
typedef struct {
    double frequency, sample_rate, threshold, hold_seconds, elapsed;
    int active;
} Detector;
void detector_init(Detector *d, double frequency, double rate, double threshold, double hold);
/* Return one on the rising edge after a continuous hold; reset on silence. */
int detector_process(Detector *d, const float *samples, size_t count);
#endif
