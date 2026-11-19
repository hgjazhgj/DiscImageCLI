// SPDX-License-Identifier: GPL-3.0-only

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

static void disc_position(const Parameters *p, double arc, int64_t *segment_index,
    double *radius, double *angle)
{
    double r, track_angle;
    if (p->constant_pitch) {
        r = clip(sqrt(p->inner_squared + p->twice_growth * arc),
            p->inner_radius, p->outer_radius);
        double root = sqrt(r * r + p->growth_squared);
        double primitive = 0.5 * (r * root
            + p->growth_squared * asinh(r / p->growth));
        double error = (primitive - p->inner_primitive) / p->growth - arc;
        double derivative = root / p->growth;
        r = clip(r - error / derivative, p->inner_radius, p->outer_radius);
        track_angle = (r - p->inner_radius) / p->growth;
    } else {
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
        r = s[0] + width * t;
        double angle_delta = s[5];
        double angle_first = width * s[8];
        double angle_last = width * s[9];
        track_angle = s[3] + t * (angle_first + t * (
            3.0 * angle_delta - 2.0 * angle_first - angle_last
            + t * (angle_first + angle_last - 2.0 * angle_delta)));
        if (arc <= 0.0) {
            r = p->inner_radius;
            track_angle = 0.0;
        } else if (arc >= p->track_length) {
            r = p->outer_radius;
            track_angle = p->total_angle;
        }
    }
    *radius = r;
    *angle = track_angle;
}

static uint8_t disc_sample(const Parameters *p, const uint8_t *image, double x, double y, int mode)
{
    x = clip(x, 0, (double)p->width - 1);
    y = clip(y, 0, (double)p->height - 1);
    if (mode == 1) {
        int64_t px = (int64_t)floor(x + 0.5);
        int64_t py = (int64_t)floor(y + 0.5);
        return image[py * p->width + px];
    }
    int64_t x0 = (int64_t)floor(x), y0 = (int64_t)floor(y);
    int64_t x1 = x0 < p->width - 1 ? x0 + 1 : x0;
    int64_t y1 = y0 < p->height - 1 ? y0 + 1 : y0;
    double horizontal = x - x0, vertical = y - y0;
    // Match grayscale's weight accumulation and ties-to-even channel
    // rounding before the brightness lookup.
    double a00 = ((1.0 - horizontal) * (1.0 - vertical)) * 255.0;
    double a10 = (horizontal * (1.0 - vertical)) * 255.0;
    double a01 = ((1.0 - horizontal) * vertical) * 255.0;
    double a11 = (horizontal * vertical) * 255.0;
    double alpha = a00;
    alpha += a10;
    alpha += a01;
    alpha += a11;
    double weighted = a00 * image[y0 * p->width + x0];
    weighted += a10 * image[y0 * p->width + x1];
    weighted += a01 * image[y1 * p->width + x0];
    weighted += a11 * image[y1 * p->width + x1];
    double gray = weighted * (1.0 / alpha);
    gray = (0.2126 * gray) + (0.7152 * gray) + (0.0722 * gray);
    int rounded = (int)floor(gray);
    double fraction = gray - rounded;
    if (fraction > 0.5 || (fraction == 0.5 && (rounded & 1)))
        ++rounded;
    return (uint8_t)rounded;
}

typedef struct {
    int64_t carriers, slots, single_byte_carriers, variant_width, variant_height;
    const uint8_t *bytes, *trim;
    const int64_t *counts, *cycle, *rank;
    const float *levels;
    const int64_t *sampling_modes;
    const int64_t *variant_offsets;
    const uint8_t *variant_map;
} CarrierModel;

static int multiview_frame_start(int payload)
{
    int col = (12 + payload) % 172;
    return col == 0 || col == 91;
}

EXPORT void dvd_resolve_sector(const CarrierModel *m,
    const uint16_t *code, uint8_t *output)
{
    if (m->single_byte_carriers) {
        for (int i = 0; i < 2048; ++i)
            output[i] = (code[i] >> 8) < m->carriers ?
                m->bytes[m->variant_offsets[code[i] >> 8] * 91] : 0xA5;
        return;
    }
    // Runs stop at each SYNC, even when the same plane continues afterwards.
    // Two filler words settle arbitrary incoming EFM+ states. One suffices if
    // the filler itself started immediately after SYNC (known state 1).
    int previous_start = 0;
    int previous = -1;
    for (int start = 0; start < 2048;) {
        int group = code[start] == UINT16_MAX ? -1 : code[start] >> 8;
        int end = start + 1;
        while (end < 2048 && !multiview_frame_start(end) &&
            (code[end] == UINT16_MAX ? -1 : code[end] >> 8) == group)
            ++end;
        int carrier = group >= 0;
        int fs = multiview_frame_start(start);
        int prev_fill = start > 0 && previous < 0;
        int behind = multiview_frame_start(previous_start) ? 0 :
            (int)clip(2 - (start - previous_start), 0, 2);
        int need = carrier ? (fs ? 0 : start == 0 ? 2 : prev_fill ? behind : 2) : 0;
        int length = end - start - need;
        int64_t sid = carrier && length > 0 ?
            m->variant_offsets[group] + (code[start + need] & 255) : 0;
        int trim = carrier && length > 0 && end < 2048 && !multiview_frame_start(end) ?
            m->trim[sid * 92 + (int)clip(length, 0, 91)] : 0;
        for (int i = start; i < end; ++i) {
            int pos = i - start - need;
            output[i] = carrier && pos >= 0 && pos < length - trim ?
                m->bytes[sid * 91 + pos] : 0xA5;
        }
        previous_start = start;
        previous = group;
        start = end;
    }
}

EXPORT void dvd_generate(const Parameters *p, const CarrierModel *m,
    const uint8_t *field, const int64_t *centres,
    const uint8_t *masks, int64_t start_block, int64_t block_count, uint8_t *output)
{
    const double pi = 3.1415926535897932384626433832795;
    static const int bayer[16] = {0,8,2,10,12,4,14,6,3,11,1,9,15,7,13,5};
    int64_t segment_index = 0;
    uint16_t code[2048];
    uint8_t resolved[2048];
    for (int64_t offset = 0; offset < block_count; ++offset) {
        int64_t block = start_block + offset;
        int64_t first_bit = block * INT64_C(619008);
        int64_t mask_index = (((block * 16 + p->psn_offset) >> 4) & 15) * 2048;
        for (int sector = 0; sector < 16; ++sector) {
            for (int byte = 0; byte < 2048; ++byte) {
                int payload = sector * 2048 + byte;
                double arc = (double)(first_bit + centres[payload]) * p->bit_length;
                double r, track_angle;
                disc_position(p, arc, &segment_index, &r, &track_angle);
                double angle = p->start_angle + p->direction * track_angle;
                double x = p->centre_x + r * cos(angle) * p->scale;
                double y = p->centre_y - r * sin(angle) * p->scale;
                int64_t turn = (int64_t)floor(track_angle / (2 * pi) + 0.5);
                int slot = (int)((turn % m->slots + m->slots) % m->slots);
                int plane = (int)m->cycle[slot];
                int mode = (int)m->sampling_modes[plane];
                x = clip(x, 0, (double)p->width - 1);
                y = clip(y, 0, (double)p->height - 1);
                int64_t col = (int64_t)floor(x + (mode == 1 ? 0.5 : 0.0));
                int64_t row = (int64_t)floor(y + (mode == 1 ? 0.5 : 0.0));
                const uint8_t *pixels = field + plane * p->height * p->width;
                uint8_t pixel = disc_sample(p, pixels, x, y, mode);
                int64_t rank = (int64_t)floor(turn / (double)m->slots) * m->counts[plane] + m->rank[slot];
                int k = (int)((rank % 32 + 32) % 32), reversed = 0;
                for (int bit = 0; bit < 5; ++bit)
                    reversed |= ((k >> bit) & 1) << (4 - bit);
                double threshold = (reversed + 0.5) / 32.0;
                double dither = ((bayer[(row % 4) * 4 + col % 4] + 0.5) / 16.0 - 0.5) / 32.0;
                double level = m->levels[plane * 256 + pixel];
                if (level + dither > threshold) {
                    uint16_t variant = 0;
                    if (m->variant_width && m->variant_height) {
                        int64_t vx = (int64_t)clip(floor(x * m->variant_width / (p->width - 1)),
                            0, (double)m->variant_width - 1);
                        int64_t vy = (int64_t)clip(floor(y * m->variant_height / (p->height - 1)),
                            0, (double)m->variant_height - 1);
                        variant = m->variant_map[(plane * m->variant_height + vy) * m->variant_width + vx];
                    }
                    code[byte] = (uint16_t)((plane << 8) | variant);
                } else {
                    code[byte] = UINT16_MAX;
                }
            }
            dvd_resolve_sector(m, code, resolved);
            for (int byte = 0; byte < 2048; ++byte)
                output[offset * 32768 + sector * 2048 + byte] = resolved[byte] ^ masks[mask_index + byte];
        }
    }
}
