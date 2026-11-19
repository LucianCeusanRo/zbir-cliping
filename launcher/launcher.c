/* Zbir Cliping.exe - tiny launcher: starts the app with the project's own Python (.venv).
   If the program isn't installed yet, it runs setup.bat. Build: see build_exe.bat / build_launcher.sh */
#include <windows.h>
#include <shellapi.h>
#include <wchar.h>

static int exists(const wchar_t *p) { DWORD a = GetFileAttributesW(p); return a != INVALID_FILE_ATTRIBUTES; }

int WINAPI wWinMain(HINSTANCE h, HINSTANCE p, PWSTR cmd, int show) {
    wchar_t dir[MAX_PATH], py[MAX_PATH], setup[MAX_PATH], cl[MAX_PATH * 2];
    GetModuleFileNameW(NULL, dir, MAX_PATH);
    wchar_t *s = wcsrchr(dir, L'\\'); if (s) *s = 0;
    swprintf(py, MAX_PATH, L"%ls\\.venv\\Scripts\\pythonw.exe", dir);
    swprintf(setup, MAX_PATH, L"%ls\\setup.bat", dir);
    if (!exists(py)) {
        if (!exists(setup)) {
            MessageBoxW(NULL, L"Nu gasesc setup.bat langa Zbir Cliping.exe. Pune exe-ul in folderul programului.",
                        L"Zbir Cliping", MB_ICONERROR);
            return 1;
        }
        if (MessageBoxW(NULL, L"Programul nu e instalat inca. Pornesc instalarea acum (setup.bat)?",
                        L"Zbir Cliping", MB_YESNO | MB_ICONQUESTION) == IDYES)
            ShellExecuteW(NULL, L"open", setup, NULL, dir, SW_SHOWNORMAL);
        return 0;
    }
    swprintf(cl, MAX_PATH * 2, L"\"%ls\" -m zbirclip app", py);
    STARTUPINFOW si = { sizeof(si) }; PROCESS_INFORMATION pi;
    if (!CreateProcessW(NULL, cl, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, dir, &si, &pi)) {
        MessageBoxW(NULL, L"Nu pot porni Python din .venv. Ruleaza din nou setup.bat.", L"Zbir Cliping", MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return 0;
}
