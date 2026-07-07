/**
 * thebuslauncher.exe - the Stream Deck app can only start an executable, but
 * our plugin backend is Python. This launcher resolves the interpreter and
 * execs:  python -m thebus_ai_bridge.deck_plugin <args forwarded verbatim>
 *
 * Interpreter resolution: a "launcher.cfg" next to the exe containing the
 * absolute python path (written by install.ps1) wins; otherwise python.exe
 * from PATH. Failures are appended to launcher.log next to the exe.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>
#include <cwchar>

static wchar_t g_dir[MAX_PATH];

static void log_line(const wchar_t *msg)
{
    wchar_t path[MAX_PATH + 16];
    swprintf(path, MAX_PATH + 16, L"%s\\launcher.log", g_dir);
    FILE *f = _wfopen(path, L"a, ccs=UTF-8");
    if (f) {
        fwprintf(f, L"%s\r\n", msg);
        fclose(f);
    }
}

// Skip the (possibly quoted) program token of a command line.
static const wchar_t *skip_argv0(const wchar_t *cl)
{
    if (*cl == L'"') {
        ++cl;
        while (*cl && *cl != L'"') ++cl;
        if (*cl == L'"') ++cl;
    } else {
        while (*cl && *cl != L' ' && *cl != L'\t') ++cl;
    }
    while (*cl == L' ' || *cl == L'\t') ++cl;
    return cl;
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int)
{
    GetModuleFileNameW(NULL, g_dir, MAX_PATH);
    wchar_t *slash = wcsrchr(g_dir, L'\\');
    if (slash) *slash = 0;

    // interpreter: launcher.cfg overrides PATH lookup
    wchar_t python[MAX_PATH] = L"";
    wchar_t cfg[MAX_PATH + 16];
    swprintf(cfg, MAX_PATH + 16, L"%s\\launcher.cfg", g_dir);
    FILE *f = _wfopen(cfg, L"r, ccs=UTF-8");
    if (f) {
        if (fgetws(python, MAX_PATH, f)) {
            wchar_t *nl = wcspbrk(python, L"\r\n");
            if (nl) *nl = 0;
        }
        fclose(f);
    }
    if (!python[0] || GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES) {
        if (!SearchPathW(NULL, L"python.exe", NULL, MAX_PATH, python, NULL)) {
            log_line(L"FATAL: python.exe not found (no launcher.cfg, not on PATH)");
            return 1;
        }
    }

    const wchar_t *fwd = skip_argv0(GetCommandLineW());
    static wchar_t cmd[8192];
    swprintf(cmd, 8192, L"\"%s\" -m thebus_ai_bridge.deck_plugin %s", python, fwd);

    STARTUPINFOW si;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(python, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW,
                        NULL, NULL, &si, &pi)) {
        log_line(L"FATAL: CreateProcess failed:");
        log_line(cmd);
        return 2;
    }
    CloseHandle(pi.hThread);
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    return (int)code;
}
