// d3d_context.h — Headless Direct3D 9 device lifecycle.
//
// Owns the dummy Win32 window, the IDirect3D9 instance, and the
// IDirect3DDevice9 device that D3DX needs to load and parse .x files.
// The device is created in headless-friendly mode: hardware (HAL) first,
// with a NULLREF fallback for environments without a GPU (e.g. Wine
// headless containers).  The class is RAII — the destructor calls
// cleanup() — but callers may also call cleanup() explicitly to release
// the device before destruction.
//
// Ported from x_loader.cpp (lines 232-316): initD3D9 / cleanupD3D9.
// Only changes: (1) std::cerr -> LOG_ERROR, (2) class is now standalone
// instead of being private state on XLoader, (3) RAII destructor.
#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d9.h>

class D3DContext {
public:
    D3DContext();
    ~D3DContext();

    // Creates the dummy window + IDirect3D9 + IDirect3DDevice9 (HAL with
    // a NULLREF fallback).  Returns false on any failure; LOG_ERROR
    // describes the cause.  Safe to call again after cleanup().
    bool init();

    // Releases the device, the D3D9 instance, and the dummy window in
    // that order.  Idempotent: safe to call when not initialized.
    void cleanup();

    // Returns the device, or nullptr if init() has not been called or
    // cleanup() has been called.  The pointer is owned by the context;
    // callers must not Release() it.
    IDirect3DDevice9* device() const { return m_pDevice; }

private:
    HWND                 m_hwnd    = nullptr;
    IDirect3D9*          m_pD3D    = nullptr;
    IDirect3DDevice9*    m_pDevice = nullptr;
};
