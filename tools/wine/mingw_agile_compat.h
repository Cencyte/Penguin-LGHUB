/* WinRT declaration missing from MinGW-w64 12; matching Wine roapi.h ABI. */
#pragma once
#include <roapi.h>
#if defined(__MINGW64_VERSION_MAJOR) && __MINGW64_VERSION_MAJOR <= 12
enum AgileReferenceOptions { AGILEREFERENCE_DEFAULT = 0, AGILEREFERENCE_DELAYEDMARSHAL = 1 };
extern "C" HRESULT WINAPI RoGetAgileReference(AgileReferenceOptions, REFIID, IUnknown *, IAgileReference **);
#endif
