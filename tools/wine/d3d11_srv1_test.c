/* Exercise actual sampling through a version-1 SRV, with descriptor and COM
 * checks. Uses the existing Wine compiler DLL; run on a private Xvfb display. */
#define COBJMACROS
#include <windows.h>
#include <d3d11_3.h>
#include <d3dcompiler.h>
#include <stdio.h>
#include <math.h>

static unsigned failures;
#define CHECK(test) do { if (!(test)) { fprintf(stderr, "FAIL line %u: %s\n", __LINE__, #test); ++failures; } } while (0)
#define RELEASE(type, obj) do { if (obj) type##_Release(obj); } while (0)

int main(void)
{
    static const char shader_source[] =
        "Texture2D<float4> image : register(t0);"
        "RWTexture2D<float4> output : register(u0);"
        "[numthreads(1,1,1)] void main(uint3 p : SV_DispatchThreadID)"
        "{ output[p.xy] = image.Load(int3(p.xy,0)); }";
    static const BYTE pixels[16] = {255,0,0,255, 0,255,0,255, 0,0,255,255, 32,64,128,255};
    ID3D11Device *device = NULL;
    ID3D11Device3 *device3 = NULL;
    ID3D11DeviceContext *context = NULL;
    ID3D11Texture2D *texture = NULL, *array = NULL;
    ID3D11ShaderResourceView1 *view = NULL, *extra = NULL;
    ID3D11ShaderResourceView *legacy = NULL;
    ID3D11Resource *resource = NULL;
    ID3D11Texture2D *output = NULL, *staging = NULL;
    ID3D11UnorderedAccessView *uav = NULL, *null_uav = NULL;
    ID3D11ComputeShader *shader = NULL;
    ID3DBlob *code = NULL, *errors = NULL;
    IUnknown *identity = NULL, *roundtrip = NULL;
    D3D11_TEXTURE2D_DESC td = {0};
    D3D11_SUBRESOURCE_DATA data = {pixels, 8, 16};
    D3D11_SHADER_RESOURCE_VIEW_DESC1 desc = {0}, got = {0};
    D3D11_SHADER_RESOURCE_VIEW_DESC old = {0};
    D3D11_TEXTURE2D_DESC out_desc = {0};
    D3D11_UNORDERED_ACCESS_VIEW_DESC ud = {0};
    D3D11_MAPPED_SUBRESOURCE mapped;
    HMODULE compiler = NULL;
    pD3DCompile compile;
    HRESULT hr;
    unsigned i;
    setbuf(stderr, NULL);
    hr = D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, 0, NULL, 0,
        D3D11_SDK_VERSION, &device, NULL, &context);
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    hr = ID3D11Device_QueryInterface(device, &IID_ID3D11Device3, (void **)&device3);
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    td.Width = td.Height = 2; td.MipLevels = td.ArraySize = td.SampleDesc.Count = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM; td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    hr = ID3D11Device_CreateTexture2D(device, &td, &data, &texture);
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    desc.Format = td.Format; desc.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
    desc.Texture2D.MipLevels = 1;
    hr = ID3D11Device3_CreateShaderResourceView1(device3, (ID3D11Resource *)texture, &desc, &view);
    CHECK(hr == S_OK && view); if (!view) goto done;
    ID3D11ShaderResourceView1_GetDesc1(view, &got);
    CHECK(got.Format == desc.Format && got.ViewDimension == desc.ViewDimension);
    CHECK(got.Texture2D.MipLevels == 1 && !got.Texture2D.MostDetailedMip && !got.Texture2D.PlaneSlice);
    ID3D11ShaderResourceView1_GetDesc(view, &old);
    CHECK(old.Format == desc.Format && old.Texture2D.MipLevels == 1);
    ID3D11ShaderResourceView1_GetResource(view, &resource);
    CHECK(resource == (ID3D11Resource *)texture);
    hr = ID3D11ShaderResourceView1_QueryInterface(view, &IID_ID3D11ShaderResourceView, (void **)&legacy);
    CHECK(hr == S_OK);
    hr = ID3D11ShaderResourceView1_QueryInterface(view, &IID_IUnknown, (void **)&identity);
    CHECK(hr == S_OK);
    if (legacy) {
        hr = ID3D11ShaderResourceView_QueryInterface(legacy, &IID_IUnknown, (void **)&roundtrip);
        CHECK(hr == S_OK && roundtrip == identity);
    }
    hr = ID3D11Device3_CreateShaderResourceView1(device3, (ID3D11Resource *)texture, NULL, NULL);
    CHECK(hr == S_FALSE);
    hr = ID3D11Device3_CreateShaderResourceView1(device3, (ID3D11Resource *)texture, NULL, &extra);
    CHECK(hr == S_OK && extra);
    if (extra) { ID3D11ShaderResourceView1_Release(extra); extra = NULL; }
    desc.Texture2D.PlaneSlice = 1;
    hr = ID3D11Device3_CreateShaderResourceView1(device3, (ID3D11Resource *)texture, &desc, &extra);
    CHECK(FAILED(hr) && !extra);
    hr = ID3D11Device3_CreateShaderResourceView1(device3, NULL, NULL, &extra);
    CHECK(hr == E_INVALIDARG && !extra);
    td.ArraySize = 3;
    hr = ID3D11Device_CreateTexture2D(device, &td, NULL, &array);
    CHECK(hr == S_OK);
    memset(&desc, 0, sizeof(desc)); desc.Format = td.Format;
    desc.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2DARRAY;
    desc.Texture2DArray.MipLevels = 1; desc.Texture2DArray.FirstArraySlice = 1; desc.Texture2DArray.ArraySize = 2;
    if (array) {
        hr = ID3D11Device3_CreateShaderResourceView1(device3, (ID3D11Resource *)array, &desc, &extra);
        CHECK(hr == S_OK && extra);
        if (extra) {
            ID3D11ShaderResourceView1_GetDesc1(extra, &got);
            CHECK(got.Texture2DArray.FirstArraySlice == 1 && got.Texture2DArray.ArraySize == 2 && !got.Texture2DArray.PlaneSlice);
            ID3D11ShaderResourceView1_Release(extra); extra = NULL;
        }
    }
    compiler = LoadLibraryW(L"d3dcompiler_47.dll");
    CHECK(compiler != NULL); if (!compiler) goto done;
    compile = (pD3DCompile)(void *)GetProcAddress(compiler, "D3DCompile");
    CHECK(compile != NULL); if (!compile) goto done;
    hr = compile(shader_source, sizeof(shader_source)-1, NULL, NULL, NULL, "main", "cs_5_0", 0, 0, &code, &errors);
    if (errors) fprintf(stderr, "%s\n", (char *)ID3D10Blob_GetBufferPointer(errors));
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    hr = ID3D11Device_CreateComputeShader(device, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), NULL, &shader);
    CHECK(hr == S_OK); if (FAILED(hr)) goto done;
    out_desc.Width = out_desc.Height = 2;
    out_desc.MipLevels = out_desc.ArraySize = out_desc.SampleDesc.Count = 1;
    out_desc.Format = DXGI_FORMAT_R32G32B32A32_FLOAT;
    out_desc.BindFlags = D3D11_BIND_UNORDERED_ACCESS;
    hr = ID3D11Device_CreateTexture2D(device, &out_desc, NULL, &output);
    CHECK(hr == S_OK); if (FAILED(hr)) goto done;
    ud.Format = out_desc.Format; ud.ViewDimension = D3D11_UAV_DIMENSION_TEXTURE2D;
    hr = ID3D11Device_CreateUnorderedAccessView(device, (ID3D11Resource *)output, &ud, &uav);
    CHECK(hr == S_OK); if (FAILED(hr)) goto done;
    ID3D11DeviceContext_CSSetShader(context, shader, NULL, 0);
    ID3D11DeviceContext_CSSetShaderResources(context, 0, 1, &legacy);
    ID3D11DeviceContext_CSSetUnorderedAccessViews(context, 0, 1, &uav, NULL);
    ID3D11DeviceContext_Dispatch(context, 2, 2, 1);
    ID3D11DeviceContext_CSSetUnorderedAccessViews(context, 0, 1, &null_uav, NULL);
    out_desc.Usage = D3D11_USAGE_STAGING; out_desc.BindFlags = 0;
    out_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    hr = ID3D11Device_CreateTexture2D(device, &out_desc, NULL, &staging);
    CHECK(hr == S_OK); if (FAILED(hr)) goto done;
    ID3D11DeviceContext_CopyResource(context, (ID3D11Resource *)staging, (ID3D11Resource *)output);
    hr = ID3D11DeviceContext_Map(context, (ID3D11Resource *)staging, 0, D3D11_MAP_READ, 0, &mapped);
    CHECK(hr == S_OK); if (FAILED(hr)) goto done;
    for (i=0; i<16; ++i) {
        const float *row = (const float *)((const BYTE *)mapped.pData + (i/8)*mapped.RowPitch);
        CHECK(fabsf(row[i%8] - pixels[i]/255.0f) < 0.001f);
    }
    ID3D11DeviceContext_Unmap(context, (ID3D11Resource *)staging, 0);
done:
    if (context) ID3D11DeviceContext_ClearState(context);
    RELEASE(ID3D11Texture2D, staging); RELEASE(ID3D11UnorderedAccessView, uav);
    RELEASE(ID3D11Texture2D, output); RELEASE(ID3D11ComputeShader, shader);
    RELEASE(ID3D10Blob, errors); RELEASE(ID3D10Blob, code);
    if (compiler) FreeLibrary(compiler);
    RELEASE(IUnknown, roundtrip); RELEASE(IUnknown, identity);
    RELEASE(ID3D11Resource, resource); RELEASE(ID3D11ShaderResourceView, legacy);
    RELEASE(ID3D11ShaderResourceView1, extra); RELEASE(ID3D11ShaderResourceView1, view);
    RELEASE(ID3D11Texture2D, texture); RELEASE(ID3D11Texture2D, array);
    RELEASE(ID3D11DeviceContext, context); RELEASE(ID3D11Device3, device3); RELEASE(ID3D11Device, device);
    fprintf(stderr, "D3D11 SRV1 descriptors, identity and shader readback: %s (%u failures)\n", failures ? "FAIL" : "PASS", failures);
    return !!failures;
}
