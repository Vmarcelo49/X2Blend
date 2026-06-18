// d3d_context.cpp — Headless Direct3D 9 device lifecycle implementation.
//
// Line-for-line port of XLoader::initD3D9 / cleanupD3D9 from the original
// x_loader.cpp (lines 232-316).  The Win32 dummy window class name, the
// 640x480 WS_POPUP window dimensions, the D3DPRESENT_PARAMETERS defaults
// (Windowed + Discard swap), the HAL→NULLREF fallback order, and the
// D3DCREATE_SOFTWARE_VERTEXPROCESSING flag are all preserved verbatim so
// the device that comes out of init() is equivalent to what the original
// loader produced.  The only changes are the LOG_ERROR substitution and
// the class spelling (D3DContext vs XLoader).
#include "d3d/d3d_context.h"

#include "core/log.h"

D3DContext::D3DContext() = default;

D3DContext::~D3DContext() {
    cleanup();
}

bool D3DContext::init() {
    // Register a standard Win32 class and create a dummy hidden window.
    // The window is never shown; D3D9 just needs an HWND to anchor the
    // device's swap chain, even when running headless under Wine.
    HINSTANCE hInst = GetModuleHandle(nullptr);
    WNDCLASSA wc = {};
    wc.lpfnWndProc   = DefWindowProcA;
    wc.hInstance     = hInst;
    wc.lpszClassName = "X2GltfDummyWindow";
    RegisterClassA(&wc);

    HWND hwnd = CreateWindowA(
        "X2GltfDummyWindow", "Headless",
        WS_POPUP, 0, 0, 640, 480,
        nullptr, nullptr, hInst, nullptr
    );
    if (!hwnd) {
        LOG_ERROR("[D3DContext] Failed to create dummy window.");
        return false;
    }
    m_hwnd = hwnd;

    // Create the D3D9 core instance.
    m_pD3D = Direct3DCreate9(D3D_SDK_VERSION);
    if (!m_pD3D) {
        LOG_ERROR("[D3DContext] Failed to initialize Direct3D 9.");
        return false;
    }

    D3DPRESENT_PARAMETERS d3dpp = {};
    d3dpp.Windowed      = TRUE;
    d3dpp.SwapEffect    = D3DSWAPEFFECT_DISCARD;
    d3dpp.hDeviceWindow = hwnd;

    // Try the high-performance hardware device first.
    HRESULT hr = m_pD3D->CreateDevice(
        D3DADAPTER_DEFAULT,
        D3DDEVTYPE_HAL,
        hwnd,
        D3DCREATE_SOFTWARE_VERTEXPROCESSING,
        &d3dpp,
        &m_pDevice
    );

    // Fallback to the Null Reference device — ideal for headless
    // conversion tools (and for Wine containers without a GPU).
    if (FAILED(hr)) {
        hr = m_pD3D->CreateDevice(
            D3DADAPTER_DEFAULT,
            D3DDEVTYPE_NULLREF,
            hwnd,
            D3DCREATE_SOFTWARE_VERTEXPROCESSING,
            &d3dpp,
            &m_pDevice
        );
    }

    if (FAILED(hr)) {
        LOG_ERROR("[D3DContext] Failed to create a Direct3D 9 device (hr = "
                  + std::to_string(hr) + ").");
        return false;
    }

    return true;
}

void D3DContext::cleanup() {
    if (m_pDevice) {
        m_pDevice->Release();
        m_pDevice = nullptr;
    }
    if (m_pD3D) {
        m_pD3D->Release();
        m_pD3D = nullptr;
    }
    if (m_hwnd) {
        DestroyWindow(m_hwnd);
        UnregisterClassA("X2GltfDummyWindow", GetModuleHandle(nullptr));
        m_hwnd = nullptr;
    }
}
