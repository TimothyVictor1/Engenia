#include "detector.h"
#ifndef PIO_UNIT_TESTING
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    if (argc != 5) return 2;
    Detector d;
    detector_init(&d, atof(argv[1]), atof(argv[2]), atof(argv[3]), atof(argv[4]));
    float samples[100]; size_t count, offset = 0; int first = 1;
    printf("[");
    while ((count = fread(samples, sizeof(float), 100, stdin))) {
        if (detector_process(&d, samples, count)) {
            printf("%s%.6f", first ? "" : ",", offset / d.sample_rate); first = 0;
        }
        offset += count;
    }
    puts("]");
    return ferror(stdin) ? 1 : 0;
}
#endif
