#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libgen.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[]) {
    char exe_path[1024];
    uint32_t size = sizeof(exe_path);
    if (_NSGetExecutablePath(exe_path, &size) != 0) {
        return 1;
    }
    char *dir = dirname(exe_path);
    char script_path[2048];
    snprintf(script_path, sizeof(script_path), "%s/../Resources/playrepo_mac.py", dir);

    char *python_candidates[] = {
        "/usr/bin/python3",
        "/usr/local/bin/python3",
        "/opt/homebrew/bin/python3",
        "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3",
        NULL
    };

    char *chosen_python = NULL;
    for (int i = 0; python_candidates[i] != NULL; i++) {
        if (access(python_candidates[i], X_OK) == 0) {
            chosen_python = python_candidates[i];
            break;
        }
    }
    if (!chosen_python) {
        chosen_python = "python3";
    }

    setenv("TK_SILENCE_DEPRECATION", "1", 1);
    char *args[] = { chosen_python, script_path, NULL };
    execv(chosen_python, args);
    return 1;
}
