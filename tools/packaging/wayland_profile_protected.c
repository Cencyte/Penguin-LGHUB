/* Private headless test only: reproduce the procfs protection of a KWin
 * executable with file capabilities, without changing any host executable. */
#define _GNU_SOURCE
#include <string.h>
#include <sys/prctl.h>
#include <unistd.h>

extern char *program_invocation_short_name;

__attribute__((constructor)) static void protect_compositor(void)
{
    if (!strcmp(program_invocation_short_name, "kwin_wayland") &&
        prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) < 0)
        _exit(125);
}
