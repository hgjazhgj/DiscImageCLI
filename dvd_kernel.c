// SPDX-License-Identifier: GPL-3.0-only
// Fused implementation of the Disc Image Studio-compatible Python sampler.
// Color recipes adapted from Neo-626/DiscColorStudio, 2026-11-09.
// Compiled with MSVC x64 /O2 /fp:strict /MT /LD.

#include <math.h>
#include <stdint.h>
#include <stdlib.h>

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
    int64_t binary_image;
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
    double dither_strength;
    const double *segments;
} Parameters;

typedef struct {
    int64_t node_count;
    int64_t dither_seed;
    double model_start_deg;
    double model_end_deg;
    double rotation_deg;
    const void *lut_entries;
    const uint8_t *motif_bytes;
    const int64_t *motif_offsets;
    const int64_t *motif_lengths;
} ColorParameters;

// DCCLUT03 entries from the single bank selected by the Python loader.
typedef struct {
    uint16_t ids[16];
    float weights[16];
} ColorEntry;

typedef struct {
    int last_rgb;
    int last_node;
    int count;
    int ids[1154];
    uint8_t used[1154];
    double dense0[1154];
    double dense1[1154];
    double cdf0[1154];
    double cdf1[1154];
    double sum0;
    double sum1;
} ColorRecipe;

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

static uint8_t sample_gray(const Parameters *p, const uint8_t *image, double x, double y)
{
    x = clip(x, 0.0, (double)(p->width - 1));
    y = clip(y, 0.0, (double)(p->height - 1));
    if (p->binary_image) {
        int64_t px = (int64_t)floor(x + 0.5);
        int64_t py = (int64_t)floor(y + 0.5);
        return image[py * p->width + px];
    }

    int64_t x0 = (int64_t)floor(x), y0 = (int64_t)floor(y);
    int64_t x1 = x0 < p->width - 1 ? x0 + 1 : x0;
    int64_t y1 = y0 < p->height - 1 ? y0 + 1 : y0;
    double horizontal = x - x0, vertical = y - y0;
    // Retain RasterImage.SampleDisc's alpha-weighted accumulation order even
    // though this reader supplies opaque gray pixels. Its final Math.Round
    // uses ties-to-even, before byte-level tone mapping.
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

static int use_white(uint8_t gray, int64_t sample_index, double strength)
{
    if (strength == 0.0)
        return gray >= 128;
    // ToneDither.Phase is keyed to the absolute payload byte, independently
    // of sector headers, ECC/SYNC gaps, batch size and the PSN scrambler.
    uint32_t value = (uint32_t)sample_index * UINT32_C(2654435761);
    value ^= value >> 16;
    value *= UINT32_C(0x7FEB352D);
    value ^= value >> 15;
    value *= UINT32_C(0x846CA68B);
    value ^= value >> 16;
    double phase = value / 4294967296.0;
    double threshold = 128.0 + (strength * 255.0 * (phase - 0.5));
    return gray >= threshold;
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
            uint8_t gray = sample_gray(p, image, x, y);
            uint8_t desired = use_white(gray, block * 32768 + byte, p->dither_strength)
                ? 0x92 : 0xA5;
            output[offset * 32768 + byte] = desired ^ masks[mask_index + byte % 2048];
        }
    }
}

static int compare_ids(const void *left, const void *right)
{
    int a = *(const int *)left, b = *(const int *)right;
    return (a > b) - (a < b);
}

static void prepare_color_recipe(ColorRecipe *recipe, const ColorParameters *color,
    int red, int green, int blue, int node)
{
    for (int j = 0; j < recipe->count; ++j) {
        int id = recipe->ids[j];
        recipe->dense0[id] = recipe->dense1[id] = 0.0;
        recipe->used[id] = 0;
    }
    recipe->count = 0;

    // FastRecipes uses single precision for the tetrahedral RGB weights.
    float x = (float)(red * 16) / 255.0f;
    float y = (float)(green * 16) / 255.0f;
    float z = (float)(blue * 16) / 255.0f;
    int a = (int)x < 15 ? (int)x : 15;
    int b = (int)y < 15 ? (int)y : 15;
    int d = (int)z < 15 ? (int)z : 15;
    float p = x - a, q = y - b, t = z - d;
    int s0 = 289, s1 = 17, s2 = 1;
    if (p < q) {
        float f = p; p = q; q = f;
        int s = s0; s0 = s1; s1 = s;
    }
    if (q < t) {
        float f = q; q = t; t = f;
        int s = s1; s1 = s2; s2 = s;
    }
    if (p < q) {
        float f = p; p = q; q = f;
        int s = s0; s0 = s1; s1 = s;
    }
    int indices[4];
    float weights[4] = { 1.0f - p, p - q, q - t, t };
    indices[0] = a * 289 + b * 17 + d;
    indices[1] = indices[0] + s0;
    indices[2] = indices[1] + s1;
    indices[3] = indices[2] + s2;

    const ColorEntry *entries = (const ColorEntry *)color->lut_entries;
    for (int endpoint = 0; endpoint < 2; ++endpoint) {
        double *dense = endpoint == 0 ? recipe->dense0 : recipe->dense1;
        for (int vertex = 0; vertex < 4; ++vertex) {
            double weight = weights[vertex];
            if (weight <= 0.0) continue;
            const ColorEntry *entry = entries + (node + endpoint) * 4913 + indices[vertex];
            for (int k = 0; k < 16; ++k) {
                if (entry->weights[k] <= 0.0f) continue;
                int id = entry->ids[k];
                if (!recipe->used[id]) {
                    recipe->used[id] = 1;
                    recipe->ids[recipe->count++] = id;
                }
                dense[id] += weight * entry->weights[k];
            }
        }
    }
    qsort(recipe->ids, recipe->count, sizeof(recipe->ids[0]), compare_ids);
    recipe->sum0 = recipe->sum1 = 0.0;
    for (int k = 0; k < recipe->count; ++k) {
        recipe->sum0 += recipe->dense0[recipe->ids[k]];
        recipe->sum1 += recipe->dense1[recipe->ids[k]];
        recipe->cdf0[k] = recipe->sum0;
        recipe->cdf1[k] = recipe->sum1;
    }
    recipe->last_rgb = (red << 16) | (green << 8) | blue;
    recipe->last_node = node;
}

static uint8_t sample_color_recipe(ColorRecipe *recipe, const ColorParameters *color,
    const uint8_t *rgb, double beta, uint64_t turn, uint64_t logical)
{
    beta = clip(beta, color->model_start_deg, color->model_end_deg);
    double x = beta - color->model_start_deg;
    int node = (int)x;
    if (node > color->node_count - 2) node = (int)(color->node_count - 2);
    double fraction = x - node;
    int packed = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2];
    if (packed != recipe->last_rgb || node != recipe->last_node)
        prepare_color_recipe(recipe, color, rgb[0], rgb[1], rgb[2], node);

    uint32_t phase = ((uint32_t)turn * UINT32_C(40503) + (uint32_t)color->dither_seed)
        & UINT32_C(65535);
    double u = ((phase + 0.5) / 65536.0)
        * (recipe->sum0 * (1.0 - fraction) + recipe->sum1 * fraction);
    int left = 0, right = recipe->count - 1;
    while (left < right) {
        int middle = (left + right) / 2;
        double cumulative = recipe->cdf0[middle] * (1.0 - fraction)
            + recipe->cdf1[middle] * fraction;
        if (u < cumulative) right = middle;
        else left = middle + 1;
    }
    int id = recipe->ids[left];
    return color->motif_bytes[color->motif_offsets[id]
        + logical % (uint64_t)color->motif_lengths[id]];
}

EXPORT void dvd_generate_color(const Parameters *p, const ColorParameters *color,
    const uint8_t *rgb, const int64_t *centres, const uint8_t *masks,
    int64_t start_block, int64_t block_count, uint8_t *output)
{
    const double pi = 3.1415926535897932384626433832795;
    int64_t segment_index = 0;
    ColorRecipe recipe = { 0 };
    recipe.last_rgb = recipe.last_node = -1;
    for (int64_t offset = 0; offset < block_count; ++offset) {
        int64_t block = start_block + offset;
        int64_t first_bit = block * INT64_C(619008);
        int64_t mask_index = (((block * 16 + p->psn_offset) >> 4) & 15) * 2048;
        for (int64_t byte = 0; byte < 32768; ++byte) {
            double arc = (double)(first_bit + centres[byte]) * p->bit_length;
            double radius, track_angle;
            position(p, arc, &segment_index, &radius, &track_angle);
            double angle = p->start_angle + p->direction * track_angle;
            double x = radius * cos(angle), y_down = -radius * sin(angle);
            // RGB matches DCS's floor sampler; its physical extent follows
            // this application's outer-radius mapping rather than a 120 mm canvas.
            int64_t px = (int64_t)floor(clip(p->centre_x + x * p->scale,
                0.0, (double)(p->width - 1)));
            int64_t py = (int64_t)floor(clip(p->centre_y + y_down * p->scale,
                0.0, (double)(p->height - 1)));
            double alpha = atan2(y_down, x) * 180.0 / pi;
            alpha = fmod(alpha + 540.0, 360.0) - 180.0;
            double beta = color->rotation_deg + alpha;
            uint64_t turn = (uint64_t)floor(track_angle / (2.0 * pi));
            uint64_t logical = (uint64_t)block * UINT64_C(32768) + (uint64_t)byte;
            uint8_t desired = sample_color_recipe(&recipe, color,
                rgb + (py * p->width + px) * 3, beta, turn, logical);
            output[offset * 32768 + byte] = desired ^ masks[mask_index + byte % 2048];
        }
    }
}
