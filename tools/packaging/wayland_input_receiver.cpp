// Native Wayland receiver for the isolated KWin feasibility probe.
#include <QApplication>
#include <QKeyEvent>
#include <QWidget>
#include <cstdio>
#include <time.h>

class Receiver : public QWidget {
    bool event(QEvent *event) override {
        if (event->type() == QEvent::WindowActivate) {
            std::puts("ACTIVE");
            std::fflush(stdout);
        }
        return QWidget::event(event);
    }
    void keyPressEvent(QKeyEvent *event) override { record(event, "down"); }
    void keyReleaseEvent(QKeyEvent *event) override { record(event, "up"); }
    void record(QKeyEvent *event, const char *direction) {
        std::printf("KEY %s %x %x %x %d\n", direction, event->key(),
                    event->nativeScanCode(), unsigned(event->modifiers()),
                    event->isAutoRepeat());
        if (qEnvironmentVariable("PENGUIN_PRIVATE_INPUT_TIMING") == "1") {
            timespec now;
            // Wine 10.20 GetTickCount64 includes suspended time on Linux.
            clock_gettime(CLOCK_BOOTTIME, &now);
            std::printf("TIMED_KEY %.6f %s %x %x %x %d\n",
                        now.tv_sec * 1000.0 + now.tv_nsec / 1e6, direction,
                        event->key(), event->nativeScanCode(), unsigned(event->modifiers()),
                        event->isAutoRepeat());
        }
        std::fflush(stdout);
    }
};

int main(int argc, char **argv) {
    if (qEnvironmentVariable("PENGUIN_PRIVATE_WAYLAND_PROBE") != "1") return 2;
    QApplication app(argc, argv);
    const QString platform = qEnvironmentVariable("PENGUIN_PRIVATE_RECEIVER_PLATFORM", "wayland");
    if ((platform != "wayland" && platform != "xcb") || app.platformName() != platform) return 3;
    std::printf("PLATFORM %s\n", qPrintable(platform));
    std::fflush(stdout);
    Receiver receiver;
    receiver.setWindowTitle("Penguin private Wayland input receiver");
    receiver.resize(400, 200);
    receiver.setFocusPolicy(Qt::StrongFocus);
    receiver.show();
    return app.exec();
}
