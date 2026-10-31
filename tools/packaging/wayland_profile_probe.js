// Research fixture, loaded ONLY into the probe's disposable KWin session.
// No window titles, input interception, shortcuts, or focus changes.
var sequence = 0;
function reportFocus(reason) {
    var w = workspace.activeWindow;
    callDBus("org.penguinLGHUB.FocusProbe", "/Focus",
             "org.penguinLGHUB.FocusProbe", "Observe", JSON.stringify({
        protocol: 1, sequence: ++sequence, reason: reason,
        pid: w ? w.pid : 0,
        window: w ? String(w.internalId) : "",
        application: w ? w.desktopFileName : "",
        resourceClass: w ? w.resourceClass : "",
        desktop: w ? w.desktopWindow : false,
        dock: w ? w.dock : false
    }));
}
workspace.windowActivated.connect(function () { reportFocus("activation"); });
reportFocus("initial");
// Heartbeats let a future broker detect a missing script/compositor and
// recover the latest focus after a temporary receiver outage.
var heartbeat = new QTimer();
heartbeat.interval = 500;
heartbeat.timeout.connect(function () { reportFocus("heartbeat"); });
heartbeat.start();
