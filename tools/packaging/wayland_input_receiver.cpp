// Native Wayland receiver for the isolated KWin feasibility probe.
#include <QApplication>
#include <QKeyEvent>
#include <QWidget>
#include <cstdio>

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
        std::fflush(stdout);
    }
};

int main(int argc, char **argv) {
    if (qEnvironmentVariable("PENGUIN_PRIVATE_WAYLAND_PROBE") != "1") return 2;
    QApplication app(argc, argv);
    if (app.platformName() != "wayland") return 3;
    std::puts("PLATFORM wayland");
    std::fflush(stdout);
    Receiver receiver;
    receiver.setWindowTitle("Penguin private Wayland input receiver");
    receiver.resize(400, 200);
    receiver.setFocusPolicy(Qt::StrongFocus);
    receiver.show();
    return app.exec();
}
