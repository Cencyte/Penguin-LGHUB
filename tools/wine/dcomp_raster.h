// Translation-only BGRA rasterization. Source pixels are premultiplied.
// Pixel centers are at n + 0.5; bitmap texel centers have the same convention.
#pragma once
#include <limits>

struct RasterModes
{
    DCOMPOSITION_BITMAP_INTERPOLATION_MODE interpolation = DCOMPOSITION_BITMAP_INTERPOLATION_MODE_NEAREST_NEIGHBOR;
    DCOMPOSITION_BORDER_MODE border = DCOMPOSITION_BORDER_MODE_HARD;
    DCOMPOSITION_BACKFACE_VISIBILITY backface = DCOMPOSITION_BACKFACE_VISIBILITY_VISIBLE;

    RasterModes inherit(RasterModes parent) const
    {
        return {interpolation == DCOMPOSITION_BITMAP_INTERPOLATION_MODE_INHERIT ? parent.interpolation : interpolation,
                border == DCOMPOSITION_BORDER_MODE_INHERIT ? parent.border : border,
                backface == DCOMPOSITION_BACKFACE_VISIBILITY_INHERIT ? parent.backface : backface};
    }
    bool visible(bool front_facing) const
    {
        return front_facing || backface == DCOMPOSITION_BACKFACE_VISIBILITY_VISIBLE;
    }
};
struct RasterRect
{
    double left, top, right, bottom;

    HRESULT bounds(RECT &out, bool soft = true) const
    {
        out = {};
        if (left >= right || top >= bottom) return S_OK;
        double edges[] = {soft ? std::floor(left) : std::ceil(left - .5),
                          soft ? std::floor(top) : std::ceil(top - .5),
                          soft ? std::ceil(right) : std::ceil(right - .5),
                          soft ? std::ceil(bottom) : std::ceil(bottom - .5)};
        for (double edge : edges)
            if (!std::isfinite(edge) || edge < std::numeric_limits<LONG>::min()
                || edge > std::numeric_limits<LONG>::max()) return E_INVALIDARG;
        out = {static_cast<LONG>(edges[0]), static_cast<LONG>(edges[1]),
               static_cast<LONG>(edges[2]), static_cast<LONG>(edges[3])};
        return S_OK;
    }
    double coverage(LONG x, LONG y) const
    {
        return std::max(0., std::min(right, x + 1.) - std::max(left, static_cast<double>(x)))
             * std::max(0., std::min(bottom, y + 1.) - std::max(top, static_cast<double>(y)));
    }
    bool integral() const
    {
        return left == std::floor(left) && top == std::floor(top)
            && right == std::floor(right) && bottom == std::floor(bottom);
    }
};
static void source_over(BYTE *dst, const BYTE *src)
{
    for (unsigned c = 0; c < 4; ++c)
        dst[c] = static_cast<BYTE>(std::min(255u, src[c] + (dst[c] * (255u - src[3]) + 127u) / 255u));
}
static HRESULT raster_bitmap(std::vector<BYTE> &dest, UINT width, const BYTE *pixels, UINT sw, UINT sh,
                             double left, double top, RECT clip, RasterModes modes)
{
    if (!sw || !sh) return S_OK;
    RasterRect rectangle{left, top, left + sw, top + sh};
    RECT area;
    const bool soft = modes.border == DCOMPOSITION_BORDER_MODE_SOFT;
    HRESULT hr = rectangle.bounds(area, soft);
    if (FAILED(hr) || !IntersectRect(&area, &area, &clip)) return hr;
    if (rectangle.integral())
    {
        // At unit scale on integer coordinates both filters and both border
        // modes agree exactly. Keep the common path free of floating sampling.
        LONG origin_x = static_cast<LONG>(left), origin_y = static_cast<LONG>(top);
        for (LONG y = area.top; y < area.bottom; ++y)
            for (LONG x = area.left; x < area.right; ++x)
                source_over(dest.data() + (static_cast<size_t>(y) * width + x) * 4,
                            pixels + (static_cast<size_t>(y - origin_y) * sw + x - origin_x) * 4);
        return S_OK;
    }
    for (LONG y = area.top; y < area.bottom; ++y)
        for (LONG x = area.left; x < area.right; ++x)
        {
            // Clamp sampling to edge texels; geometric edge coverage is applied
            // separately, exactly once, to premultiplied colour AND alpha.
            double u = std::clamp(x - left, 0., sw - 1.);
            double v = std::clamp(y - top, 0., sh - 1.);
            double coverage = soft ? rectangle.coverage(x, y) : 1.;
            BYTE sample[4];
            if (modes.interpolation == DCOMPOSITION_BITMAP_INTERPOLATION_MODE_LINEAR)
            {
                UINT x0 = static_cast<UINT>(u), y0 = static_cast<UINT>(v);
                UINT x1 = std::min(x0 + 1, sw - 1), y1 = std::min(y0 + 1, sh - 1);
                double fx = u - x0, fy = v - y0;
                for (unsigned c = 0; c < 4; ++c)
                {
                    double a = pixels[(static_cast<size_t>(y0) * sw + x0) * 4 + c];
                    double b = pixels[(static_cast<size_t>(y0) * sw + x1) * 4 + c];
                    double d = pixels[(static_cast<size_t>(y1) * sw + x0) * 4 + c];
                    double e = pixels[(static_cast<size_t>(y1) * sw + x1) * 4 + c];
                    sample[c] = static_cast<BYTE>(std::lround(((a + (b - a) * fx) * (1 - fy)
                                                            + (d + (e - d) * fx) * fy) * coverage));
                }
            }
            else
            {
                UINT sx = static_cast<UINT>(std::floor(u + .5)), sy = static_cast<UINT>(std::floor(v + .5));
                const BYTE *src = pixels + (static_cast<size_t>(sy) * sw + sx) * 4;
                for (unsigned c = 0; c < 4; ++c) sample[c] = static_cast<BYTE>(std::lround(src[c] * coverage));
            }
            source_over(dest.data() + (static_cast<size_t>(y) * width + x) * 4, sample);
        }
    return S_OK;
}
