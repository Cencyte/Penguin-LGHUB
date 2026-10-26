#!/usr/bin/env python3
"""Trace the SHA-pinned tray overlay initializer inside run_private_build.py.

Requires a NEW stopped copy of the lab prefix, plus --lab and an unused --output
directory. Registry/DLL changes remain in that disposable prefix. Always uses
Xvfb, never the user's display. Native instructions are temporarily breakpointed
by tray_overlay_trace.exe, but return values and object data are not changed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess

DWM_SHA = "833b78602b12d8f1d81c327158021a8548accef185718eb41acf24407c647cdd"
TRAY_SHA = "07e3872b0b9cee9cb98ad39b53bff3b731e7ef34dea0785970676ea95aa44fc6"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--dcomp-build-dir", type=Path,
                        help="test a new isolated compositor build; defaults to the preserved lab DLL")
    parser.add_argument("--dispatcher-build-dir", type=Path,
                        help="private dispatcher bridge to test")
    parser.add_argument("--anchor-menu", action="store_true",
                        help="enable the dispatcher bridge host-window placement")
    parser.add_argument("--native-d3dcompiler", type=Path,
                        help="copy an existing user-owned x64 D3DCompiler_47.dll into the disposable tray directory")
    parser.add_argument("--native-d2d", type=Path,
                        default=Path("/mnt/firesongzpc_win11/Windows/System32/d2d1.dll"),
                        help="existing native Direct2D DLL; permits a saved copy when the Windows drive is offline")
    parser.add_argument("--exercise-tray", action="store_true",
                        help="inspect owned tray windows and queue its registered shell right-click callback on private Xvfb")
    parser.add_argument("--exercise-resize", action="store_true",
                        help="also grow and restore the owned tray window on private Xvfb; implies --exercise-tray")
    parser.add_argument("--exercise-pointer", action="store_true",
                        help="move the pointer over the owned popup on private Xvfb; implies --exercise-tray")
    args = parser.parse_args()
    args.exercise_tray = args.exercise_tray or args.exercise_resize or args.exercise_pointer
    lab = args.lab.resolve(strict=True)
    prefix = Path(os.environ["WINEPREFIX"]).resolve(strict=True)
    marker = prefix / ".overlay-trace-disposable"
    if prefix == lab / "coreapp-prefix" or not marker.is_file():
        parser.error("supply a fresh disposable prefix with an .overlay-trace-disposable marker")
    tray_dir = prefix / "drive_c/Program Files/LGHUB/system_tray"
    dwm = tray_dir / "dwmcorei.dll"
    if hashlib.sha256(dwm.read_bytes()).hexdigest() != DWM_SHA:
        parser.error("dwmcorei.dll hash does not match the reviewed breakpoint addresses")
    if hashlib.sha256((tray_dir / "lghub_system_tray.exe").read_bytes()).hexdigest() != TRAY_SHA:
        parser.error("tray executable hash does not match the notification breakpoints")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if shutil.disk_usage(prefix).free < 24 * 2**30:
        parser.error("less than 24 GiB available on the working disk")
    env = os.environ.copy()
    env.update(
        WINEDEBUG="-all,err+all,fixme+all,trace+loaddll,trace+ui,warn+ui,trace+composition,trace+storage,trace+d3d_perf,trace+threadpool,trace+dxgi_adapters,trace+systray",
        WINEDLLOVERRIDES="winemenubuilder.exe=d;winebus=d;wineusb=d;lghub_software_manager.exe=d;mfsensorgroup=n;coremessaging=n;d2d1=n;dcomp=n",
    )
    for name in (
        "WINE_GHUB_INPUT_SITE", "GHUB_DXGI_ENQUEUE_EVENT", "GHUB_D3D11_SHARED_TEXTURES",
        "GHUB_DISPATCHER_TRACE", "GHUB_COREMSG_PORT_ADAPTER", "GHUB_COREMSG_TRACE",
        "GHUB_COREMSG_FRAMEWORK_ADAPTER", "GHUB_D3D11_PARTNER_PROBE", "GHUB_DCOMP_TRACE",
        "GHUB_DCOMP_CHILD_TARGET", "WINE_GHUB_CHILD_COMPOSITION", "WINE_GHUB_INPUT_SINK",
        "WINE_GHUB_INPUT_DELEGATION", "WINE_GHUB_INPUT_ROUTING",
    ):
        env[name] = "1"
    for name in ("DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY", "GHUB_DCOMP_POPUP_TARGET"):
        env.pop(name, None)
    sources = {
        lab / "dispatcher-queue/ghub_dispatcher_queue.dll": tray_dir / "ghub_dispatcher_queue.dll",
        lab / "dispatcher-queue/coremessaging.dll": tray_dir / "CoreMessaging.dll",
        (args.dcomp_build_dir.resolve(strict=True) if args.dcomp_build_dir else lab / "dcomp-experiment") / "dcomp.dll": tray_dir / "dcomp.dll",
        args.native_d2d.resolve(strict=True): tray_dir / "d2d1.dll",
        lab / "coreapp/core_application_experiment.dll": tray_dir.parent / "core_application_experiment.dll",
    }
    if args.native_d3dcompiler:
        source = args.native_d3dcompiler.resolve(strict=True)
        if source.name.lower() != "d3dcompiler_47.dll":
            parser.error("--native-d3dcompiler requires D3DCompiler_47.dll")
        sources[source] = tray_dir / "D3DCompiler_47.dll"
        env["WINEDLLOVERRIDES"] += ";d3dcompiler_47=n"
    if args.exercise_tray:
        env["GHUB_TRAY_PRIVATE_LAB"] = "1"
    else:
        env.pop("GHUB_TRAY_PRIVATE_LAB", None)
    if args.exercise_pointer:
        env["GHUB_TRAY_ANCHOR_TRACE"] = "1"
        env["WINEDEBUG"] += ",trace+win"
        if args.anchor_menu: env["GHUB_EXPECT_TRAY_ANCHOR"] = "1"
    if args.dispatcher_build_dir:
        del sources[lab / "dispatcher-queue/ghub_dispatcher_queue.dll"]
        sources[args.dispatcher_build_dir.resolve(strict=True) / "ghub_dispatcher_queue.dll"] = tray_dir / "ghub_dispatcher_queue.dll"
    if args.anchor_menu: env["GHUB_TRAY_ANCHOR"] = "1"
    manifest = {"prefix": str(prefix), "dwm_sha256": DWM_SHA, "tray_sha256": TRAY_SHA,
                "exercise_tray": args.exercise_tray, "exercise_resize": args.exercise_resize, "files": []}
    for source, target in sources.items():
        shutil.copy2(source, target)
        manifest["files"].append({"source": str(source), "target": str(target),
                                  "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    manifest["probe_sha256"] = hashlib.sha256(args.probe.read_bytes()).hexdigest()
    manifest["wine_version"] = subprocess.check_output(["wine", "--version"], text=True).strip()
    manifest["environment"] = {key: value for key, value in env.items()
                               if key.startswith(("WINE", "GHUB"))}
    manifest["source_sha256"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), Path(__file__).with_name("tray_overlay_trace.cpp"))}
    components = [Path("/usr/lib/wine/x86_64-windows") / name for name in (
        "setupapi.dll", "ntoskrnl.exe", "kernelbase.dll", "ntdll.dll", "win32u.dll",
        "user32.dll", "d3d11.dll", "dxgi.dll", "wined3d.dll", "dwrite.dll", "windows.ui.dll",
        "windows.storage.dll", "threadpoolwinrt.dll", "shcore.dll", "winex11.drv")]
    components += [Path("/usr/lib/wine/x86_64-unix/winex11.so"),
                   Path("/usr/lib/wine/x86_64-unix/ntdll.so"),
                   Path("/usr/lib/wine/x86_64-unix/win32u.so"), Path("/usr/bin/wineserver")]
    manifest["private_components_sha256"] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in components}
    (output / "inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    xvfb = subprocess.Popen(["Xvfb", "-displayfd", "1", "-screen", "0", "1024x768x24", "-nolisten", "tcp"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(xvfb.stdout, selectors.EVENT_READ)
            if not selector.select(10):
                raise RuntimeError("Xvfb did not report a private display")
            display = xvfb.stdout.readline().decode().strip()
            if not display.isdecimal():
                raise RuntimeError("Invalid private Xvfb display")
        env["DISPLAY"] = ":" + display
        registrations = {
            "Windows.System.Threading.ThreadPoolTimer": r"C:\windows\system32\threadpoolwinrt.dll",
            "Windows.System.DispatcherQueue": r"C:\Program Files\LGHUB\system_tray\ghub_dispatcher_queue.dll",
            "Windows.ApplicationModel.Core.CoreApplication": r"C:\Program Files\LGHUB\core_application_experiment.dll",
            "Windows.Storage.Streams.InMemoryRandomAccessStream": r"C:\windows\system32\windows.storage.dll",
            "Windows.Storage.Streams.DataWriter": r"C:\windows\system32\windows.storage.dll",
            "Windows.UI.Internal.Input.InputSite": r"C:\windows\system32\windows.ui.dll",
            "Windows.UI.Internal.Input.ActivationConfigurationInputObject": r"C:\windows\system32\windows.ui.dll",
        }
        with (output / "setup.log").open("w") as log:
            for name, dll in registrations.items():
                key = "HKLM\\Software\\Microsoft\\WindowsRuntime\\ActivatableClassId\\" + name
                for values in (["/v", "DllPath", "/d", dll],
                               ["/v", "ThreadingModel", "/t", "REG_DWORD", "/d", "3"]):
                    subprocess.run(["wine", "reg", "add", key, *values, "/f"], env=env,
                                   stdout=log, stderr=subprocess.STDOUT, timeout=45, check=True)
        for name, executable in [("wnf", lab / "wnf_unavailable_test.exe"),
                                 ("coreapp", lab / "coreapp/core_application_test.exe")]:
            with (output / (name + ".log")).open("w") as log:
                subprocess.run(["wine", str(executable)], env=env, stdout=log,
                               stderr=subprocess.STDOUT, timeout=90, check=True)
        tray = "C:\\Program Files\\LGHUB\\system_tray\\lghub_system_tray.exe"
        with (output / "runtime.log").open("w") as log:
            trace_log = "Z:" + str(output / "probe.log").replace("/", "\\")
            command = ["wine", str(args.probe.resolve()), tray, trace_log]
            if args.exercise_pointer:
                command.append("--exercise-pointer")
            elif args.exercise_resize:
                command.append("--exercise-resize")
            elif args.exercise_tray:
                command.append("--exercise-tray")
            result = subprocess.run(command, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=100)
        (output / "result.json").write_text(json.dumps({"probe_exit": result.returncode,
            "meaning": "Debugger completion only; does not assert tray health"}, indent=2) + "\n")
        print("Overlay trace:", output, "probe exit:", result.returncode, flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
    finally:
        try:
            subprocess.run(["wineserver", "-k"], env=env, timeout=20, check=False)
            subprocess.run(["wineserver", "-w"], env=env, timeout=20, check=False)
        finally:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=5)
            except subprocess.TimeoutExpired:
                xvfb.kill()
                xvfb.wait()


if __name__ == "__main__":
    main()
