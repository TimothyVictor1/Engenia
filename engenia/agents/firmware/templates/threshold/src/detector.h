#ifndef DETECTOR_H
#define DETECTOR_H
#include <stddef.h>
#define DETECTOR_BLOCK_SECONDS 0.05
typedef struct {
    double sample_rate, threshold, hold_seconds, energy;
    size_t filled, block;
    double elapsed;
    int active;
} Detector;
void detector_init(Detector *d, double rate, double threshold, double hold);
/* Return one on the rising edge after a continuous hold; reset on quiet blocks. */
int detector_process(Detector *d, const float *samples, size_t count);
#endif
