/* Isolated pressure-vessel transport probe; never installed or run live. */
#include <dbus/dbus.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    const char *marker = getenv("PENGUIN_PRIVATE_PROFILE_PROBE");
    if (argc != 5 || !marker || strcmp(marker, "1")) return 2;
    char proc[128], executable[4096];
    snprintf(proc, sizeof proc, "/proc/%ld/exe", strtol(argv[1], NULL, 10));
    ssize_t n = readlink(proc, executable, sizeof executable - 1);
    if (n < 0) {
        perror(proc);
        puts("HOST_PROCESS_PATH_UNREADABLE");
    } else {
        executable[n] = 0;
        if (strcmp(executable, argv[2])) return 3;
        puts("HOST_PROCESS_PATH_READABLE");
    }
    if (access(argv[3], R_OK)) return 4;
    FILE *mailbox = fopen(argv[4], "rb");
    unsigned char packet[4196];
    size_t bytes = mailbox ? fread(packet, 1, sizeof packet, mailbox) : 0;
    if (mailbox) fclose(mailbox);
    char translated[4096];
    snprintf(translated, sizeof translated, "Z:%s", argv[2]);
    for (char *p = translated; *p; ++p) if (*p == '/') *p = '\\';
    if (bytes != 100 + strlen(translated) || memcmp(packet, "PGFOCUS1", 8)
        || memcmp(packet + 100, translated, strlen(translated))) return 9;
    puts("HOST_MAILBOX_READABLE_AND_CORRECT");

    DBusError error;
    dbus_error_init(&error);
    DBusConnection *bus = dbus_bus_get_private(DBUS_BUS_SESSION, &error);
    if (!bus) { fprintf(stderr, "%s\n", error.message); dbus_error_free(&error); return 5; }
    const char *name = "org.kde.KWin";
    DBusMessage *request = dbus_message_new_method_call("org.freedesktop.DBus",
        "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner");
    dbus_message_append_args(request, DBUS_TYPE_STRING, &name, DBUS_TYPE_INVALID);
    DBusMessage *reply = dbus_connection_send_with_reply_and_block(bus, request, 3000, &error);
    dbus_message_unref(request);
    const char *owner = NULL;
    if (!reply || !dbus_message_get_args(reply, &error, DBUS_TYPE_STRING, &owner, DBUS_TYPE_INVALID)) return 6;
    printf("KWIN_OWNER %s\n", owner);
    dbus_message_unref(reply);

    const char *script = argv[3], *plugin = "penguin-private-container-script";
    request = dbus_message_new_method_call("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "loadScript");
    dbus_message_append_args(request, DBUS_TYPE_STRING, &script, DBUS_TYPE_STRING, &plugin, DBUS_TYPE_INVALID);
    reply = dbus_connection_send_with_reply_and_block(bus, request, 3000, &error);
    dbus_message_unref(request);
    dbus_int32_t id = -1;
    if (!reply || !dbus_message_get_args(reply, &error, DBUS_TYPE_INT32, &id, DBUS_TYPE_INVALID) || id < 0) return 7;
    dbus_message_unref(reply);
    char object[128];
    snprintf(object, sizeof object, "/Scripting/Script%d", id);
    request = dbus_message_new_method_call("org.kde.KWin", object, "org.kde.kwin.Script", "run");
    reply = dbus_connection_send_with_reply_and_block(bus, request, 3000, &error);
    dbus_message_unref(request);
    if (!reply) return 8;
    dbus_message_unref(reply);
    puts("COMPOSITOR_BUS_AND_SCRIPT_OK");
    dbus_connection_close(bus);
    dbus_connection_unref(bus);
    return 0;
}
