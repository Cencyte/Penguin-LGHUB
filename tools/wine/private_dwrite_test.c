/* ABI/lifetime/coverage tests for the native-D2D private DirectWrite contract.
 * Optional argv[1]: user's native DWrite DLL, for independent contract checks.
 * Pixel comparisons use each implementation's own font rasterizer; Wine and
 * Microsoft do not claim identical hinting. No native DLL is distributed. */
#define COBJMACROS
#include <windows.h>
#include <initguid.h>
#include <dwrite_3.h>
#include <d2d1.h>
#include <stdio.h>
#include <math.h>
#include <stdint.h>
DEFINE_GUID(private_iid, 0xbd6f5199, 0xf548, 0x493f, 0xa8, 0x8e, 0xf9, 0x41, 0x98, 0x45, 0x91, 0x9b);
DEFINE_GUID(cache_iid, 0xd4e275f2, 0x7b28, 0x4d55, 0xa1, 0xe0, 0x54, 0x61, 0x0b, 0x55, 0xe8, 0x3d);
DEFINE_GUID(unsupported_iid, 0xdeadbeef, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
typedef struct Cache Cache;
typedef struct Entry Entry;
struct Entry
{
    const struct EntryVtbl
    {
        void *(WINAPI *get)(Entry *);
        void(WINAPI *set)(Entry *, void *);
    } *lpVtbl;
};
struct Info
{
    UINT16 width, height;
    INT16 left, top;
    BYTE flags, pad;
};
struct Cache
{
    const struct CacheVtbl
    {
        HRESULT(WINAPI *qi)(Cache *, REFIID, void **);
        ULONG(WINAPI *add)(Cache *);
        ULONG(WINAPI *release)(Cache *);
        HRESULT(WINAPI *bitmap)(Cache *, IDWriteFontFace *, FLOAT, BOOL, const DWRITE_MATRIX *, UINT, UINT, UINT,
                                Entry **, UINT *, void **);
        HRESULT(WINAPI *outline)(Cache *, IDWriteFontFace *, BOOL, Entry **, void **);
        HRESULT(WINAPI *info)(Cache *, IDWriteFontFace *, Entry *, UINT, const UINT16 *, struct Info *);
        HRESULT(WINAPI *fill)(Cache *, IDWriteFontFace *, Entry *, UINT16, UINT, INT, INT, UINT, UINT, void *);
        HRESULT(WINAPI *geometry)(Cache *, IDWriteFontFace *, Entry *, FLOAT, UINT16, BOOL, UINT,
                                  ID2D1SimplifiedGeometrySink *);
        HRESULT(WINAPI *keep)(Cache *, IDWriteFontFace *, Entry *, UINT16);
        HRESULT(WINAPI *uncompressed)(Cache *, IDWriteFontFace *, Entry *, UINT16, UINT, UINT *, UINT *, const void **);
        void(WINAPI *cleanup)(Cache *);
    } *lpVtbl;
};
typedef HRESULT(WINAPI *Create)(DWRITE_FACTORY_TYPE, REFIID, IUnknown **);
typedef HRESULT(WINAPI *CreateCache)(IUnknown *, Cache **);
typedef void(WINAPI *Blend)(IUnknown *, IDWriteRenderingParams *, IDWriteFontFace *, UINT, UINT, BOOLEAN, float, float,
                            float, float *, float *, float *);
typedef HRESULT(WINAPI *Origins)(IUnknown *, const DWRITE_GLYPH_RUN *, UINT, D2D1_POINT_2F, float,
                                 const DWRITE_MATRIX *, D2D1_POINT_2F *);
static unsigned checks, failures;
#define CHECK(x)                                                                                                       \
    do                                                                                                                 \
    {                                                                                                                  \
        ++checks;                                                                                                      \
        if (!(x))                                                                                                      \
        {                                                                                                              \
            ++failures;                                                                                                \
            printf("FAIL %d: %s\n", __LINE__, #x);                                                                     \
        }                                                                                                              \
    } while (0)
#define HR(x)                                                                                                          \
    do                                                                                                                 \
    {                                                                                                                  \
        HRESULT h = (x);                                                                                               \
        CHECK(h == S_OK);                                                                                              \
        if (FAILED(h))                                                                                                 \
        {                                                                                                              \
            printf("HRESULT %08lx: %s\n", h, #x);                                                                      \
            goto done;                                                                                                 \
        }                                                                                                              \
    } while (0)
static int sample(const BYTE *p, UINT pitch, UINT raster, UINT x, UINT y)
{
    UINT bit;
    if (raster == 4)
    {
        bit = (y % 4) * 4 + x % 4;
        return !!(p[(y / 4) * pitch + (x / 4) * 2 + bit / 8] & (1u << (bit % 8)));
    }
    return !!(p[y * pitch + x / 8] & (1u << (x % 8)));
}
static void put(BYTE *p, UINT pitch, UINT raster, UINT x, UINT y)
{
    UINT bit;
    if (raster == 4)
    {
        bit = (y % 4) * 4 + x % 4;
        p[(y / 4) * pitch + (x / 4) * 2 + bit / 8] |= 1u << (bit % 8);
    }
    else
        p[y * pitch + x / 8] |= 1u << (x % 8);
}
struct Sink
{
    ID2D1SimplifiedGeometrySink iface;
    LONG refs;
    UINT figures, lines, curves;
    float total;
};
static HRESULT WINAPI sink_qi(IUnknown *i, REFIID iid, void **out)
{
    *out = NULL;
    if (!IsEqualGUID(iid, &IID_IUnknown) && !IsEqualGUID(iid, &IID_ID2D1SimplifiedGeometrySink))
        return E_NOINTERFACE;
    *out = i;
    IUnknown_AddRef(i);
    return S_OK;
}
static ULONG WINAPI sink_add(IUnknown *i)
{
    return ++((struct Sink *)i)->refs;
}
static ULONG WINAPI sink_release(IUnknown *i)
{
    return --((struct Sink *)i)->refs;
}
static void WINAPI sink_fill(ID2D1SimplifiedGeometrySink *i, D2D1_FILL_MODE m)
{
    (void)i;
    (void)m;
}
static void WINAPI sink_flags(ID2D1SimplifiedGeometrySink *i, D2D1_PATH_SEGMENT m)
{
    (void)i;
    (void)m;
}
static void WINAPI sink_begin(ID2D1SimplifiedGeometrySink *i, D2D1_POINT_2F p, D2D1_FIGURE_BEGIN b)
{
    struct Sink *s = (void *)i;
    (void)b;
    ++s->figures;
    s->total += fabsf(p.x) + fabsf(p.y);
}
static void WINAPI sink_lines(ID2D1SimplifiedGeometrySink *i, const D2D1_POINT_2F *p, UINT n)
{
    struct Sink *s = (void *)i;
    s->lines += n;
    for (UINT j = 0; j < n; ++j)
        s->total += fabsf(p[j].x) + fabsf(p[j].y);
}
static void WINAPI sink_curves(ID2D1SimplifiedGeometrySink *i, const D2D1_BEZIER_SEGMENT *p, UINT n)
{
    struct Sink *s = (void *)i;
    s->curves += n;
    for (UINT j = 0; j < n; ++j)
        s->total += fabsf(p[j].point1.x) + fabsf(p[j].point2.y);
}
static void WINAPI sink_end(ID2D1SimplifiedGeometrySink *i, D2D1_FIGURE_END e)
{
    (void)i;
    (void)e;
}
static HRESULT WINAPI sink_close(ID2D1SimplifiedGeometrySink *i)
{
    (void)i;
    return S_OK;
}
static const ID2D1SimplifiedGeometrySinkVtbl sink_vtbl = {{sink_qi, sink_add, sink_release},
                                                          sink_fill,
                                                          sink_flags,
                                                          sink_begin,
                                                          sink_lines,
                                                          sink_curves,
                                                          sink_end,
                                                          sink_close};
static void test(const char *path, BOOL native)
{
    HMODULE module = NULL;
    IDWriteFactory *factory = NULL;
    IDWriteFontFile *file = NULL;
    IDWriteFontFace *face = NULL;
    IDWriteRenderingParams *params = NULL;
    IUnknown *priv = NULL, *identity = NULL, *identity2 = NULL;
    Cache *cache = NULL, *other = NULL;
    Create create;
    UINT cp[3] = {'A', ' ', 0x67};
    UINT16 glyphs[3];
    UINT before = checks;
    module = LoadLibraryA(path);
    CHECK(module != NULL);
    if (!module)
        goto done;
    create = (Create)(void *)GetProcAddress(module, "DWriteCreateFactory");
    CHECK(create != NULL);
    if (!create)
        goto done;
    HR(create(DWRITE_FACTORY_TYPE_ISOLATED, &IID_IDWriteFactory, (IUnknown **)&factory));
    HR(IDWriteFactory_QueryInterface(factory, &private_iid, (void **)&priv));
    HR(IDWriteFactory_QueryInterface(factory, &IID_IUnknown, (void **)&identity));
    HR(IUnknown_QueryInterface(priv, &IID_IUnknown, (void **)&identity2));
    CHECK(identity == identity2);
    IUnknown_Release(identity);
    identity = NULL;
    IUnknown_Release(identity2);
    identity2 = NULL;
    HR(IDWriteFactory_CreateFontFileReference(factory, L"C:\\windows\\Fonts\\arial.ttf", NULL, &file));
    HR(IDWriteFactory_CreateFontFace(factory, DWRITE_FONT_FACE_TYPE_TRUETYPE, 1, &file, 0, DWRITE_FONT_SIMULATIONS_NONE,
                                     &face));
    HR(IDWriteFontFace_GetGlyphIndices(face, cp, 3, glyphs));
    HR(((CreateCache)((void **)priv->lpVtbl)[3])(priv, &cache));
    HR(((CreateCache)((void **)priv->lpVtbl)[3])(priv, &other));
    CHECK(cache != other);
    HR(cache->lpVtbl->qi(cache, &cache_iid, (void **)&identity));
    CHECK((void *)identity == cache);
    IUnknown_Release(identity);
    identity = NULL;
    CHECK(cache->lpVtbl->qi(cache, &unsupported_iid, (void **)&identity) == E_NOINTERFACE && !identity);
    /* Shared-factory COM identity must also round-trip. */
    {
        IUnknown *shared = NULL, *p = NULL, *u = NULL;
        HR(create(DWRITE_FACTORY_TYPE_SHARED, &IID_IUnknown, &shared));
        HRESULT h = IUnknown_QueryInterface(shared, &private_iid, (void **)&p);
        CHECK(h == S_OK);
        if (p)
        {
            h = IUnknown_QueryInterface(p, &IID_IUnknown, (void **)&u);
            CHECK(h == S_OK && u == shared);
            if (u)
                IUnknown_Release(u);
            IUnknown_Release(p);
        }
        IUnknown_Release(shared);
    }
    for (UINT aa = 0; aa < 2; ++aa)
        for (UINT mode = 1; mode <= 7; ++mode)
        {
            if (mode == 6)
                continue;
            Entry *e = NULL, *same = NULL;
            UINT raster = 99, r2 = 99;
            void *item = NULL;
            struct
            {
                BYTE before[8];
                struct Info info[3];
                BYTE after[8];
            } guard;
            BYTE base[8192] = {0}, shifted[8192], expected[8192], saved[8192];
            const UINT pitch = 64, rows = 128;
            UINT expect = mode == 1 ? 0 : !aa ? (mode >= 5 ? 2 : 1) : (mode == 7 ? 5 : mode == 5 ? 4 : 3);
            HR(cache->lpVtbl->bitmap(cache, face, 20, FALSE, NULL, mode, 2, aa, &e, &raster, &item));
            CHECK(e && raster == expect && !item);
            e->lpVtbl->set(e, (void *)(uintptr_t)0x1234);
            DWRITE_MATRIX translated = {1, 0, 0, 1, 27, -13};
            HR(cache->lpVtbl->bitmap(cache, face, 20, FALSE, &translated, mode, 2, aa, &same, &r2, &item));
            CHECK(same == e && r2 == raster && item == (void *)(uintptr_t)0x1234);
            memset(&guard, 0xa5, sizeof(guard));
            HR(cache->lpVtbl->info(cache, face, e, 3, glyphs, guard.info));
            for (UINT i = 0; i < 8; ++i)
                CHECK(guard.before[i] == 0xa5 && guard.after[i] == 0xa5);
            CHECK(guard.info[0].width > 0 && guard.info[0].height > 0);
            CHECK(!guard.info[1].width && !guard.info[1].height);
            CHECK(guard.info[0].flags == (mode == 1 ? 3 : mode <= 3 ? 2 : 0));
            HR(cache->lpVtbl->fill(cache, face, e, glyphs[0], 0, 0, 0, rows, pitch, base));
            UINT hits = 0;
            for (UINT i = 0; i < sizeof(base); ++i)
                hits += base[i] != 0;
            CHECK(hits > 0);
            for (UINT trial = 0; trial < 4; ++trial)
            {
                int ox = trial == 0   ? 3
                         : trial == 1 ? -5
                         : trial == 2 ? 400
                                      : 0,
                    oy = trial == 0   ? 7
                         : trial == 1 ? -3
                         : trial == 2 ? 500
                                      : 0;
                UINT w = raster == 4 ? pitch * 2 : pitch * 8, h = raster == 4 ? rows * 4 : rows;
                memset(shifted, 0x52, sizeof(shifted));
                memcpy(expected, shifted, sizeof(expected));
                for (UINT y = 0; y < guard.info[0].height; ++y)
                    for (UINT x = 0; x < guard.info[0].width; ++x)
                    {
                        int tx = x + ox, ty = y + oy;
                        if (tx >= 0 && ty >= 0 && (UINT)tx < w && (UINT)ty < h && sample(base, pitch, raster, x, y))
                            put(expected, pitch, raster, tx, ty);
                    }
                HR(cache->lpVtbl->fill(cache, face, e, glyphs[0], 0, ox, oy, rows, pitch, shifted));
                CHECK(!memcmp(expected, shifted, sizeof(expected)));
            }
            memcpy(saved, base, sizeof(saved));
            HR(cache->lpVtbl->fill(cache, face, e, glyphs[1], 0, 0, 0, rows, pitch, base));
            CHECK(!memcmp(base, saved, sizeof(base)));
            {
                UINT n = 99, stride = 99;
                const void *bits = (void *)1;
                HR(cache->lpVtbl->uncompressed(cache, face, e, glyphs[0], 1, &n, &stride, &bits));
                if (bits)
                {
                    CHECK(n > 0 && stride > 0);
                    BOOL equal = TRUE;
                    for (UINT y = 0; y < guard.info[0].height; ++y)
                        for (UINT x = 0; x < guard.info[0].width; ++x)
                            if (sample(bits, stride, raster, x, y) != sample(base, pitch, raster, x, y))
                                equal = FALSE;
                    CHECK(equal);
                }
                else
                    CHECK(n == 0 && stride == 0);
            }
            if (!native)
            {
                CHECK(other->lpVtbl->info(other, face, e, 1, glyphs, guard.info) == E_INVALIDARG);
                CHECK(cache->lpVtbl->fill(cache, face, e, glyphs[0], 0, 0, 0, 1, 1, NULL) == E_INVALIDARG);
            }
            cache->lpVtbl->cleanup(cache);
            HR(cache->lpVtbl->bitmap(cache, face, 20, FALSE, NULL, mode, 2, aa, &same, &r2, &item));
            CHECK(same == e && item == (void *)(uintptr_t)0x1234);
            memset(base, 0, sizeof(base));
            HR(cache->lpVtbl->fill(cache, face, e, glyphs[0], 0, 0, 0, rows, pitch, base));
            CHECK(!memcmp(base, saved, sizeof(base)));
            e->lpVtbl->set(e, NULL);
        }
    {
        Entry *e = NULL;
        void *data = NULL;
        float advance = 0;
        struct Sink a = {{&sink_vtbl}, 1, 0, 0, 0, 0}, b = {{&sink_vtbl}, 1, 0, 0, 0, 0};
        HR(cache->lpVtbl->outline(cache, face, FALSE, &e, &data));
        CHECK(e && !data);
        HR(cache->lpVtbl->keep(cache, face, e, glyphs[0]));
        HR(cache->lpVtbl->geometry(cache, face, e, 20, glyphs[0], FALSE, 0, &a.iface));
        HR(IDWriteFontFace_GetGlyphRunOutline(face, 20, glyphs, &advance, NULL, 1, FALSE, FALSE, &b.iface));
        CHECK(a.figures > 0 && a.figures == b.figures && a.lines == b.lines && a.curves == b.curves &&
              fabsf(a.total - b.total) < .001f);
        CHECK(a.refs == 1 && b.refs == 1);
    }
    HR(IDWriteFactory_CreateCustomRenderingParams(factory, 2.2f, .8f, .7f, DWRITE_PIXEL_GEOMETRY_RGB,
                                                  DWRITE_RENDERING_MODE_NATURAL, &params));
    for (UINT aa = 0; aa < 2; ++aa)
        for (UINT force = 0; force < 2; ++force)
            for (UINT shade = 0; shade < 3; ++shade)
            {
                float gamma = 0, contrast = 0, level = 0, s = shade * .5f;
                ((Blend)((void **)priv->lpVtbl)[4])(priv, params, face, 4, aa, force, s, s, s, &gamma, &contrast,
                                                    &level);
                CHECK(fabsf(gamma - 2.2f) < .001f);
                float enhancement = .8f;
                IDWriteRenderingParams1 *p1 = NULL;
                if (aa && SUCCEEDED(IDWriteRenderingParams_QueryInterface(params, &IID_IDWriteRenderingParams1,
                                                                          (void **)&p1)))
                {
                    enhancement = IDWriteRenderingParams1_GetGrayscaleEnhancedContrast(p1);
                    IDWriteRenderingParams1_Release(p1);
                }
                CHECK(fabsf(contrast - (shade == 2 ? 0 : enhancement)) < .001f);
                CHECK(fabsf(level - ((aa || force) ? 0 : .7f)) < .001f);
                if (shade == 2)
                    CHECK(contrast == 0);
            }
    {
        FLOAT advances[3] = {10, 6, 11};
        DWRITE_GLYPH_RUN run = {face, 20, 3, glyphs, advances, NULL, FALSE, 0};
        D2D1_POINT_2F origins[3], start = {3, 9};
        HR(((Origins)((void **)priv->lpVtbl)[5])(priv, &run, 0, start, 1, NULL, origins));
        CHECK(origins[0].x == 3 && origins[1].x == 13 && origins[2].x == 19 && origins[2].y == 9);
    }
    /* Independent design-metric oracle for transforms, RTL, vertical glyph
     * origins and DIP coordinates at non-unit pixels-per-DIP. */
    {
        DWRITE_FONT_METRICS fm;
        DWRITE_GLYPH_METRICS gm[3];
        DWRITE_MATRIX matrix = {1.2f, .25f, -.1f, .8f, 7, -4};
        DWRITE_GLYPH_OFFSET offsets[3] = {{1, 2}, {-3, 1}, {2, -1}};
        FLOAT advances[3] = {10, 6, 11};
        D2D1_POINT_2F result[3], start = {3, 9};
        IDWriteFontFace_GetMetrics(face, &fm);
        float scale = 20.0f / fm.designUnitsPerEm;
        for (UINT side = 0; side < 2; ++side)
            for (UINT rtl = 0; rtl < 2; ++rtl)
                for (UINT explicit_advance = 0; explicit_advance < 2; ++explicit_advance)
                {
                    HR(IDWriteFontFace_GetDesignGlyphMetrics(face, glyphs, 3, gm, side));
                    DWRITE_GLYPH_RUN run = {face,    20,   3,  glyphs, explicit_advance ? advances : NULL,
                                            offsets, side, rtl};
                    HR(((Origins)((void **)priv->lpVtbl)[5])(priv, &run, 0, start, 1.5f, &matrix, result));
                    float pen = start.x;
                    for (UINT i = 0; i < 3; ++i)
                    {
                        float advance = (side ? gm[i].advanceHeight : gm[i].advanceWidth) * scale;
                        float x = pen + (side ? gm[i].verticalOriginY * scale : 0) - (rtl ? advance : 0) +
                                  (rtl ? -1 : 1) * offsets[i].advanceOffset;
                        float y = start.y + (side ? gm[i].advanceWidth * scale * .5f : 0) - offsets[i].ascenderOffset;
                        CHECK(fabsf(result[i].x - (x * matrix.m11 + y * matrix.m21 + matrix.dx)) < .002f);
                        CHECK(fabsf(result[i].y - (x * matrix.m12 + y * matrix.m22 + matrix.dy)) < .002f);
                        pen += (rtl ? -1 : 1) * (explicit_advance ? advances[i] : advance);
                    }
                }
    }
    /* Cache owns all references needed after the creating factory and font owners leave. */
    IUnknown_Release(priv);
    priv = NULL;
    IDWriteFontFile_Release(file);
    file = NULL;
    IDWriteFactory_Release(factory);
    factory = NULL;
    cache->lpVtbl->cleanup(cache);
    CHECK(cache->lpVtbl->release(cache) == 0);
    cache = NULL;
    printf("%s: %u checks executed\n", native ? "Native contract" : "Candidate", checks - before);
done:
    if (identity)
        IUnknown_Release(identity);
    if (identity2)
        IUnknown_Release(identity2);
    if (cache)
        cache->lpVtbl->release(cache);
    if (other)
        other->lpVtbl->release(other);
    if (params)
        IDWriteRenderingParams_Release(params);
    if (face)
        IDWriteFontFace_Release(face);
    if (file)
        IDWriteFontFile_Release(file);
    if (priv)
        IUnknown_Release(priv);
    if (factory)
        IDWriteFactory_Release(factory);
    if (module)
        FreeLibrary(module);
}
int main(int argc, char **argv)
{
    test("dwrite.dll", FALSE);
    if (argc > 1)
        test(argv[1], TRUE);
    printf("Private factory: %u checks, %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
