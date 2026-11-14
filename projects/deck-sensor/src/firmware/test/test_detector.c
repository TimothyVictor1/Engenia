#include <unity.h>
#include <math.h>
#include "../src/detector.h"
void setUp(void) {}
void tearDown(void) {}
static void square(float *s, double amplitude) {
    for (int i=0; i<100; ++i) s[i] = (float)(i % 2 ? -amplitude : amplitude);
}
void test_hold_and_rearm(void) {
    Detector d; float s[100]; square(s, 0.5);
    detector_init(&d, 2000, 0.25, 0.15);
    TEST_ASSERT_EQUAL(0, detector_process(&d,s,100));
    TEST_ASSERT_EQUAL(0, detector_process(&d,s,100));
    TEST_ASSERT_EQUAL(1, detector_process(&d,s,100));
    TEST_ASSERT_EQUAL(0, detector_process(&d,s,100));
    float quiet[100] = {0}; detector_process(&d,quiet,100);
    detector_process(&d,s,100); detector_process(&d,s,100);
    TEST_ASSERT_EQUAL(1, detector_process(&d,s,100));
}
void test_threshold_and_quiet(void) {
    Detector d; float s[100]; square(s, 0.2);
    detector_init(&d, 2000, 0.25, 0.05);
    TEST_ASSERT_EQUAL(0, detector_process(&d,s,100));
    detector_init(&d, 2000, 0.1, 0.05);
    TEST_ASSERT_EQUAL(1, detector_process(&d,s,100));
}
int main(void) {
    UNITY_BEGIN(); RUN_TEST(test_hold_and_rearm); RUN_TEST(test_threshold_and_quiet);
    return UNITY_END();
}
