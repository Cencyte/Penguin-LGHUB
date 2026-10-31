// Disposable-session focus receiver and window fixtures. Not installed.
#include <QApplication>
#include <QCoreApplication>
#include <QDBusConnection>
#include <QDBusConnectionInterface>
#include <QDBusContext>
#include <QDBusMessage>
#include <QDBusReply>
#include <QDateTime>
#include <QJsonDocument>
#include <QJsonObject>
#include <QWidget>
#include <cstdio>

static void output(QJsonObject event)
{
    event["received_ms"] = double(QDateTime::currentMSecsSinceEpoch());
    const QByteArray text = QJsonDocument(event).toJson(QJsonDocument::Compact);
    std::printf("%s\n", text.constData());
    std::fflush(stdout);
}

class FocusReceiver : public QObject, protected QDBusContext {
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.penguinLGHUB.FocusProbe")
    int sequence = 0;
    int boundarySequence = 0;
    bool accept(const QString &payload, int &sequence)
    {
        auto bus = QDBusConnection::sessionBus();
        QDBusReply<QString> owner = bus.interface()->serviceOwner("org.kde.KWin");
        if (!calledFromDBus() || !owner.isValid() || message().service() != owner.value()) {
            output({{"kind", "rejected"}, {"reason", "sender is not KWin"}});
            return false;
        }
        QJsonParseError error;
        const QJsonDocument doc = QJsonDocument::fromJson(payload.toUtf8(), &error);
        auto event = doc.object();
        if (payload.size() > 16384 || error.error != QJsonParseError::NoError
            || !doc.isObject() || event["protocol"].toInt() != 1
            || event["sequence"].toInt() <= sequence) {
            output({{"kind", "rejected"}, {"reason", "invalid message"}});
            return false;
        }
        sequence = event["sequence"].toInt();
        event["kind"] = "focus";
        event["sender"] = message().service();
        output(event);
        return true;
    }
public slots:
    bool Observe(const QString &payload) { return accept(payload, sequence); }
    bool ObserveBoundary(const QString &payload) { return accept(payload, boundarySequence); }
};

class FocusWindow : public QWidget {
    bool event(QEvent *event) override
    {
        if (event->type() == QEvent::WindowActivate) {
            output({{"kind", "active"}, {"pid", double(QCoreApplication::applicationPid())}});
        }
        return QWidget::event(event);
    }
};

int main(int argc, char **argv)
{
    if (qEnvironmentVariable("PENGUIN_PRIVATE_PROFILE_PROBE") != "1"
        || qEnvironmentVariable("XDG_RUNTIME_DIR") != "/tmp/penguin-profile-probe/run") return 2;
    if (argc == 2 && QString::fromLocal8Bit(argv[1]) == "--receiver") {
        QCoreApplication app(argc, argv);
        auto bus = QDBusConnection::sessionBus();
        FocusReceiver receiver;
        if (!bus.registerService("org.penguinLGHUB.FocusProbe")
            || !bus.registerObject("/Focus", &receiver, QDBusConnection::ExportAllSlots)) return 3;
        output({{"kind", "ready"}});
        return app.exec();
    }
    if (argc != 3 || QString::fromLocal8Bit(argv[1]) != "--window") return 4;
    QApplication app(argc, argv);
    if (app.platformName() != qEnvironmentVariable("QT_QPA_PLATFORM")) return 5;
    app.setDesktopFileName(QString::fromLocal8Bit(argv[2]));
    FocusWindow window;
    window.setWindowTitle(QString::fromLocal8Bit(argv[2]));
    window.resize(400, 200);
    output({{"kind", "window"}, {"platform", app.platformName()},
            {"pid", double(app.applicationPid())}, {"application", app.desktopFileName()}});
    window.show();
    return app.exec();
}

#include "wayland_profile_probe.moc"
