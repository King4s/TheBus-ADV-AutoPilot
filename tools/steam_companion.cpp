/**
 * TheBusSteamCompanion.exe - start the AI bridge together with the game.
 *
 * Steam has no plugin system, but its Launch Options can wrap the game
 * command. Set on The Bus (Properties -> Launch Options):
 *
 *     "C:\thebus-ai-connector\dist\TheBusSteamCompanion.exe" %command%
 *
 * Steam substitutes %command% with the full game command line; this
 * launcher then:
 *   1. starts "TheBus Copilot.exe" from its own folder (fallback:
 *      "python -m thebus_ai_bridge gui") unless one is already running,
 *   2. runs the game command and waits for the game to exit,
 *   3. closes the Copilot it started (WM_CLOSE first, kill as last
 *      resort - the pad watchdog neutralizes controls either way).
 *
 * Diagnostics land in companion.log next to the exe.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>
#include <cwchar>

static wchar_t g_dir[MAX_PATH];

static void log_line(const wchar_t *msg)
{
    wchar_t path[MAX_PATH + 16];
    swprintf(path, MAX_PATH + 16, L"%s\\companion.log", g_dir);
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

// Ask the Copilot to close gracefully. PyInstaller --onefile runs the
// real app as a CHILD of the bootloader we spawned, so matching by pid
// is not enough - match the window title too.
static BOOL CALLBACK close_window(HWND hwnd, LPARAM lparam)
{
    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    wchar_t title[64] = L"";
    GetWindowTextW(hwnd, title, 63);
    if (pid == (DWORD)lparam
        || wcsncmp(title, L"The Bus AI Bridge", 17) == 0)
        PostMessageW(hwnd, WM_CLOSE, 0, 0);
    return TRUE;
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int)
{
    GetModuleFileNameW(NULL, g_dir, MAX_PATH);
    wchar_t *slash = wcsrchr(g_dir, L'\\');
    if (slash) *slash = 0;

    const wchar_t *game_cmd = skip_argv0(GetCommandLineW());
    if (!*game_cmd) {
        log_line(L"FATAL: no game command given - set Steam Launch "
                 L"Options to: \"...\\TheBusSteamCompanion.exe\" %command%");
        return 1;
    }

    // 1. start the Copilot (skip when one is already running).
    // It goes SUSPENDED into a kill-on-close job first, so the child
    // process a PyInstaller --onefile bootloader spawns is captured too.
    PROCESS_INFORMATION copilot;
    ZeroMemory(&copilot, sizeof(copilot));
    HANDLE job = NULL;
    HANDLE mutex = CreateMutexW(NULL, TRUE, L"TheBusCopilotCompanion");
    bool started_copilot = false;
    if (GetLastError() != ERROR_ALREADY_EXISTS) {
        wchar_t exe[MAX_PATH + 32];
        swprintf(exe, MAX_PATH + 32, L"%s\\TheBus Copilot.exe", g_dir);
        static wchar_t cmd[MAX_PATH + 40];
        STARTUPINFOW si;
        ZeroMemory(&si, sizeof(si));
        si.cb = sizeof(si);
        if (GetFileAttributesW(exe) != INVALID_FILE_ATTRIBUTES) {
            swprintf(cmd, MAX_PATH + 40, L"\"%s\"", exe);
            started_copilot = CreateProcessW(exe, cmd, NULL, NULL, FALSE,
                                             CREATE_SUSPENDED, NULL, NULL,
                                             &si, &copilot);
        } else {  // dev fallback: run from the installed python package
            wchar_t python[MAX_PATH] = L"";
            if (SearchPathW(NULL, L"pythonw.exe", NULL, MAX_PATH, python,
                            NULL)
                || SearchPathW(NULL, L"python.exe", NULL, MAX_PATH, python,
                               NULL)) {
                swprintf(cmd, MAX_PATH + 40,
                         L"\"%s\" -m thebus_ai_bridge gui", python);
                started_copilot = CreateProcessW(
                    python, cmd, NULL, NULL, FALSE,
                    CREATE_NO_WINDOW | CREATE_SUSPENDED,
                    NULL, NULL, &si, &copilot);
            }
        }
        if (started_copilot) {
            job = CreateJobObjectW(NULL, NULL);
            if (job) {
                JOBOBJECT_EXTENDED_LIMIT_INFORMATION jeli;
                ZeroMemory(&jeli, sizeof(jeli));
                jeli.BasicLimitInformation.LimitFlags =
                    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                SetInformationJobObject(job,
                                        JobObjectExtendedLimitInformation,
                                        &jeli, sizeof(jeli));
                AssignProcessToJobObject(job, copilot.hProcess);
            }
            ResumeThread(copilot.hThread);
        }
        log_line(started_copilot ? L"copilot started"
                                 : L"copilot NOT started (exe/python missing)");
    } else {
        log_line(L"copilot already running - leaving it alone");
    }

    // 2. run the game and wait for it
    static wchar_t game[8192];
    wcsncpy(game, game_cmd, 8191);
    STARTUPINFOW si2;
    ZeroMemory(&si2, sizeof(si2));
    si2.cb = sizeof(si2);
    PROCESS_INFORMATION game_pi;
    ZeroMemory(&game_pi, sizeof(game_pi));
    if (!CreateProcessW(NULL, game, NULL, NULL, FALSE, 0, NULL, NULL,
                        &si2, &game_pi)) {
        log_line(L"FATAL: could not start the game command:");
        log_line(game);
        return 2;
    }
    CloseHandle(game_pi.hThread);
    WaitForSingleObject(game_pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(game_pi.hProcess, &code);
    CloseHandle(game_pi.hProcess);
    log_line(L"game exited");

    // 3. close the Copilot WE started (leave an independent one alone):
    // WM_CLOSE for a clean shutdown, the job kills any leftovers
    if (started_copilot) {
        EnumWindows(close_window, (LPARAM)copilot.dwProcessId);
        Sleep(3000);  // give the app time to close cleanly
        CloseHandle(copilot.hProcess);
        CloseHandle(copilot.hThread);
        if (job)
            CloseHandle(job);  // KILL_ON_JOB_CLOSE reaps the whole tree
        log_line(L"copilot closed");
    }
    if (mutex)
        CloseHandle(mutex);
    return (int)code;
}
