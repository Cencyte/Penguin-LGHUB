/* Exercise the WinRT animation setting through activation and its real SPI
 * preference. Run only on private Xvfb with a copied Wine prefix. */
#define COBJMACROS
#define INITGUID
#include <windows.h>
#include <roapi.h>
#include <winstring.h>
#include <windows.ui.viewmanagement.h>
#include <stdio.h>
#include <wchar.h>

typedef __x_ABI_CWindows_CUI_CViewManagement_CIUISettings IUISettings;
static unsigned checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static IUISettings *activate(void)
{
    HSTRING name = NULL;
    IInspectable *instance = NULL;
    IUISettings *settings = NULL;
    const WCHAR *text = L"Windows.UI.ViewManagement.UISettings";
    HRESULT hr = WindowsCreateString(text, wcslen(text), &name);
    CHECK(hr == S_OK);
    if (FAILED(hr)) return NULL;
    hr = RoActivateInstance(name, &instance);
    CHECK(hr == S_OK && instance);
    WindowsDeleteString(name);
    if (FAILED(hr) || !instance) return NULL;
    hr = IInspectable_QueryInterface(instance, &IID___x_ABI_CWindows_CUI_CViewManagement_CIUISettings, (void **)&settings);
    CHECK(hr == S_OK && settings);
    IInspectable_Release(instance);
    return settings;
}
static void query(IUISettings *settings, BOOL expected)
{
    struct { unsigned char before, value, after; } out = {0x55, 0xcc, 0xaa};
    HRESULT hr = settings->lpVtbl->get_AnimationsEnabled(settings, &out.value);
    CHECK(hr == S_OK);
    CHECK(out.value == !!expected);
    CHECK(out.before == 0x55 && out.after == 0xaa); /* WinRT boolean is one byte. */
}
int main(void)
{
    HRESULT hr = RoInitialize(RO_INIT_MULTITHREADED);
    IUISettings *existing = NULL, *fresh;
    BOOL saved = FALSE, current = FALSE, have_saved = FALSE;
    unsigned i;
    CHECK(SUCCEEDED(hr));
    if (FAILED(hr)) goto done;
    have_saved = SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, &saved, 0);
    CHECK(have_saved);
    if (!have_saved) goto cleanup;
    existing = activate();
    if (!existing) goto cleanup;
    query(existing, saved);
    for (i = 0; i < 4; ++i)
    {
        BOOL state = i & 1;
        CHECK(SystemParametersInfoW(SPI_SETCLIENTAREAANIMATION, 0, (void *)(ULONG_PTR)state, 0));
        CHECK(SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, &current, 0));
        CHECK(current == state);
        query(existing, state);
        fresh = activate();
        if (fresh)
        {
            query(fresh, state);
            fresh->lpVtbl->Release(fresh);
        }
    }
cleanup:
    if (have_saved)
    {
        CHECK(SystemParametersInfoW(SPI_SETCLIENTAREAANIMATION, 0, (void *)(ULONG_PTR)saved, 0));
        CHECK(SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, &current, 0));
        CHECK(current == saved);
        if (existing) query(existing, saved);
    }
    if (existing) existing->lpVtbl->Release(existing);
    RoUninitialize();
done:
    printf("UISettings: %u checks, %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
