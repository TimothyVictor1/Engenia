#include <unity.h>
#include <math.h>
#include "../src/detector.h"
void setUp(void) {}
void tearDown(void) {}
static void tone(float *s, double frequency) {
    for (int i=0; i<100; ++i) s[i] = (float)(0.4*sin(2*3.141592653589793*frequency*i/2000));
}
void test_hold_and_rearm(void) {
    Detector d; float s[100]; tone(s, 200);
    detector_init(&d, 200, 2000, 0.15, 0.15);
    TEST_ASSERT_EQUAL(0, detector_process(&d,s,100));
    TEST_ASSERT_EQUAL(0, detector_process(&d,s,100));
    TEST_ASSERT_EQUAL(1, detector_process(&d,s,100));
    TEST_ASSERT_EQUAL(0, detector_process(&d,s,100));
    float silence[100] = {0}; detector_process(&d,silence,100);
    detector_process(&d,s,100); detector_process(&d,s,100);
    TEST_ASSERT_EQUAL(1, detector_process(&d,s,100));
}
void test_frequency_and_threshold(void) {
    Detector d; float s[100]; tone(s, 400);
    detector_init(&d, 200, 2000, 0.15, 0.05);
    TEST_ASSERT_EQUAL(0, detector_process(&d,s,100));
    detector_init(&d, 400, 2000, 0.15, 0.05);
    TEST_ASSERT_EQUAL(1, detector_process(&d,s,100));
    detector_init(&d, 400, 2000, 0.8, 0.05);
    TEST_ASSERT_EQUAL(0, detector_process(&d,s,100));
}
int main(void) {
    UNITY_BEGIN(); RUN_TEST(test_hold_and_rearm); RUN_TEST(test_frequency_and_threshold);
    return UNITY_END();
}
