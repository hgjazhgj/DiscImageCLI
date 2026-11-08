// SPDX-License-Identifier: GPL-3.0-or-later
// Fused implementation of the Disc Image Studio-compatible Python sampler.
// Compiled with MSVC x64 /O2 /fp:strict /MT /LD.

#include <math.h>
#include <stdint.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

typedef struct {
    int64_t segment_count;
    int64_t constant_pitch;
    int64_t width;
    int64_t height;
    int64_t psn_offset;
    double inner_radius;
    double outer_radius;
    double track_length;
    double length_scale;
    double total_angle;
    double growth;
    double inner_squared;
    double growth_squared;
    double twice_growth;
    double inner_primitive;
    double start_angle;
    double direction;
    double scale;
    double centre_x;
    double centre_y;
    double bit_length;
    const double *segments;
} Parameters;

// Segment rows: radius, width, arc, angle, arc delta, angle delta,
//              arc derivative start/end, angle derivative start/end.
static double clip(double value, double lower, double upper)
{
    return value < lower ? lower : (value > upper ? upper : value);
}

static double hermite(double t, double delta, double first, double last)
{
    return t * (first + t * (3.0 * delta - 2.0 * first - last
        + t * (first + last - 2.0 * delta)));
}

static void position(const Parameters *p, double arc, int64_t *segment_index,
    double *radius, double *angle)
{
    if (p->constant_pitch) {
        double r = clip(sqrt(p->inner_squared + p->twice_growth * arc),
            p->inner_radius, p->outer_radius);
        double root = sqrt(r * r + p->growth_squared);
        double primitive = 0.5 * (r * root
            + p->growth_squared * asinh(r / p->growth));
        double error = (primitive - p->inner_primitive) / p->growth - arc;
        double derivative = root / p->growth;
        r = clip(r - error / derivative, p->inner_radius, p->outer_radius);
        *radius = r;
        *angle = (r - p->inner_radius) / p->growth;
        return;
    }

    double unscaled = arc / p->length_scale;
    int64_t index = *segment_index;
    while (index + 1 < p->segment_count && p->segments[(index + 1) * 10 + 2] <= unscaled)
        ++index;
    *segment_index = index;
    const double *s = p->segments + index * 10;
    double width = s[1];
    double value = unscaled - s[2];
    double delta = s[4];
    double first = width * s[6];
    double last = width * s[7];
    double t = clip(value / delta, 0.0, 1.0);
    double quadratic = 3.0 * delta - 2.0 * first - last;
    double cubic = first + last - 2.0 * delta;
    for (int iteration = 0; iteration < 4; ++iteration) {
        double error = t * (first + t * (quadratic + t * cubic)) - value;
        double derivative = first + t * (2.0 * quadratic + 3.0 * t * cubic);
        t = clip(t - error / derivative, 0.0, 1.0);
    }
    *radius = s[0] + width * t;
    *angle = s[3] + hermite(t, s[5], width * s[8], width * s[9]);
    if (arc <= 0.0) {
        *radius = p->inner_radius;
        *angle = 0.0;
    } else if (arc >= p->track_length) {
        *radius = p->outer_radius;
        *angle = p->total_angle;
    }
}

EXPORT void dvd_generate(const Parameters *p, const uint8_t *image,
    const int64_t *centres, const uint8_t *masks, int64_t start_block,
    int64_t block_count, uint8_t *output)
{
    int64_t segment_index = 0;
    for (int64_t offset = 0; offset < block_count; ++offset) {
        int64_t block = start_block + offset;
        int64_t first_bit = block * INT64_C(619008);
        int64_t mask_index = (((block * 16 + p->psn_offset) >> 4) & 15) * 2048;
        for (int64_t byte = 0; byte < 32768; ++byte) {
            double arc = (double)(first_bit + centres[byte]) * p->bit_length;
            double radius, angle;
            position(p, arc, &segment_index, &radius, &angle);
            angle = p->start_angle + p->direction * angle;
            double x = p->centre_x + radius * cos(angle) * p->scale;
            double y = p->centre_y - radius * sin(angle) * p->scale;
            int64_t px = (int64_t)floor(x + 0.5);
            int64_t py = (int64_t)floor(y + 0.5);
            if (px < 0) px = 0;
            else if (px >= p->width) px = p->width - 1;
            if (py < 0) py = 0;
            else if (py >= p->height) py = p->height - 1;
            uint8_t desired = image[py * p->width + px] ? 0x92 : 0xA5;
            output[offset * 32768 + byte] = desired ^ masks[mask_index + byte % 2048];
        }
    }
}
