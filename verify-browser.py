#!/usr/bin/env python3
"""Run an isolated, sandboxed CDP probe. GPU discovery alone is not video proof."""
import argparse
from datetime import datetime, timezone
import http.server
import json
import mimetypes
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit


def classify_hardware(events, device_fds, advancing_frames, session=None):
    players = {}
    for event in events:
        if session is not None and event.get("sessionId") != session:
            continue
        if event.get("method") != "Media.playerPropertiesChanged":
            continue
        data = event.get("params", {})
        player = players.setdefault(data.get("playerId", "unknown"), {})
        player.update({item["name"]: item.get("value") for item in data.get("properties", [])})
    evidence = []
    # The controlled page contains one video. Multiple players leave frame-to-player
    # attribution ambiguous; fail closed rather than inherit another player's proof.
    candidates = players.items() if len(players) == 1 else []
    for player_id, properties in candidates:
        decoder = str(properties.get("kVideoDecoderName", ""))
        platform = str(properties.get("kIsPlatformVideoDecoder", "")).lower()
        hardware_names = ("v4l2", "vaapi", "d3d11", "videotoolbox", "rkmpp", "rockchip")
        if platform == "true" and any(name in decoder.lower() for name in hardware_names):
            evidence.append(f"Media player {player_id}: {decoder}, platform=true")
    return {"hardware_verified": advancing_frames >= 25 and bool(evidence),
            "advancing_frames": advancing_frames, "hardware_evidence": evidence,
            "mpp_device_corroboration": "/dev/mpp_service" in device_fds,
            "media_session": session, "ambiguous_players": len(players) > 1,
            "media_players": players, "device_fds": sorted(set(device_fds))}


def video_route(url, video):
    # A single explicit route, never join untrusted URL paths to a filesystem path.
    if url.startswith("//") or urlsplit(url).path != "/video":
        raise FileNotFoundError(url)
    return video


def start_video_server(video):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.server.request_paths.append(self.path)
            if urlsplit(self.path).path == "/":
                page = b'<html><video id="v" src="/video" autoplay muted loop playsinline></video></html>'
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            try:
                path = video_route(self.path, video)
            except FileNotFoundError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            try:
                with path.open("rb") as handle:
                    while chunk := handle.read(128 * 1024):
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.request_paths = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class CDP:
    def __init__(self, url):
        import websocket
        self.connection = websocket.create_connection(url, origin="http://localhost", timeout=5)
        self.sequence = 0
        self.events = []

    def command(self, method, params=None, session=None, timeout=12):
        self.sequence += 1
        command_id = self.sequence
        message = {"id": command_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self.connection.send(json.dumps(message))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.connection.settimeout(max(0.1, deadline - time.monotonic()))
            try:
                message = json.loads(self.connection.recv())
            except Exception as error:
                raise RuntimeError(f"CDP {method} receive failed: {error}") from error
            if "method" in message:
                self.events.append(message)
            if message.get("id") == command_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
        raise TimeoutError(f"CDP timeout: {method}")

    def close(self):
        self.connection.close()


def own_device_fds(process_group):
    devices = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if os.getpgid(int(entry.name)) != process_group:
                continue
            for fd in (entry / "fd").iterdir():
                target = os.readlink(fd)
                if target.startswith(("/dev/mpp", "/dev/rga", "/dev/dri/", "/dev/dma_heap/")):
                    devices.add(target)
        except (PermissionError, ProcessLookupError, FileNotFoundError):
            continue
    return devices


def stop_owned_process(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def probe(args):
    binary = Path(args.binary).resolve(strict=True)
    video = Path(args.video).resolve(strict=True) if args.video else None
    if video is not None and not video.is_file():
        raise ValueError("--video must be a regular file")
    result = {"binary": str(binary), "hardware_verified": False,
              "timestamp": datetime.now(timezone.utc).isoformat(),
              "video": str(video) if video else None, "sandbox_requested": True}
    process, cdp, server = None, None, None
    with tempfile.TemporaryDirectory(prefix="chromium-rk-verify-") as directory:
        profile = Path(directory) / "profile"
        log = Path(directory) / "chromium.log"
        try:
            flags = [str(binary), f"--user-data-dir={profile}", "--headless=new", "--enable-gpu",
                     "--ozone-platform=headless", "--use-gl=angle", "--use-angle=gl-egl",
                     "--ignore-gpu-blocklist", "--enable-features=AcceleratedVideoDecoder,AcceleratedVideoEncoder",
                     "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                     "--remote-allow-origins=http://localhost", "--disable-background-networking",
                     "--disable-component-update", "--disable-sync", "--no-first-run",
                     "--no-proxy-server", "--disable-extensions", "--autoplay-policy=no-user-gesture-required",
                     "--no-default-browser-check", "--metrics-recording-only", "about:blank"]
            with log.open("w") as handle:
                process = subprocess.Popen(flags, stdout=handle, stderr=handle, start_new_session=True)
            deadline = time.monotonic() + 25
            port_file = profile / "DevToolsActivePort"
            while not port_file.exists():
                if process.poll() is not None:
                    raise RuntimeError(f"Chromium exited before CDP startup: {process.returncode}")
                if time.monotonic() > deadline:
                    raise TimeoutError("No DevToolsActivePort within 25 seconds")
                time.sleep(0.2)
            port, endpoint = port_file.read_text().splitlines()[:2]
            cdp = CDP(f"ws://127.0.0.1:{int(port)}{endpoint}")
            result["browser"] = cdp.command("Browser.getVersion")
            result["gpu"] = cdp.command("SystemInfo.getInfo").get("gpu", {})
            if args.expected_version and result["browser"].get("product", "").split("/")[-1] != args.expected_version:
                raise ValueError(f"Expected browser {args.expected_version}, got {result['browser']}")
            if video:
                server = start_video_server(video)
                target = cdp.command("Target.createTarget", {"url": "about:blank"})["targetId"]
                session = cdp.command("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
                cdp.command("Media.enable", session=session)
                cdp.command("Page.enable", session=session)
                result["renderer_probe"] = cdp.command("Runtime.evaluate", {"expression": "1+1", "returnByValue": True}, session=session)
                result["navigation"] = cdp.command("Page.navigate", {"url": f"http://127.0.0.1:{server.server_port}/"}, session=session, timeout=60)
                if result["navigation"].get("errorText"):
                    raise RuntimeError(f"Video navigation failed: {result['navigation']['errorText']}")
                samples, devices = [], set()
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    expression = "(() => {const v=document.querySelector('video');return v?{time:v.currentTime,frames:v.getVideoPlaybackQuality().totalVideoFrames,ready:v.readyState,error:v.error?{code:v.error.code,message:v.error.message}:null}:null})()"
                    value = cdp.command("Runtime.evaluate", {"expression": expression, "returnByValue": True}, session=session)
                    sample = value.get("result", {}).get("value")
                    devices.update(own_device_fds(process.pid))
                    if sample:
                        samples.append(sample)
                        if sample.get("error"):
                            break
                    time.sleep(0.5)
                frames = max(0, samples[-1]["frames"] - samples[0]["frames"]) if samples else 0
                result.update(classify_hardware(cdp.events, devices, frames, session=session))
                result["video_samples"] = samples
                result["media_events"] = [item for item in cdp.events if item.get("method", "").startswith("Media.")]
            result["probe_completed"] = True
        except Exception as error:
            result["error"] = f"{type(error).__name__}: {error}"
            result["probe_completed"] = False
            result["hardware_verified"] = False
        finally:
            if cdp:
                try:
                    cdp.close()
                except Exception as error:
                    result["cdp_close_error"] = str(error)
            stop_owned_process(process)
            if server:
                result["http_requests"] = server.request_paths
                server.shutdown()
                server.server_close()
            result["chromium_log_tail"] = log.read_text(errors="replace")[-18000:] if log.exists() else ""
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--video")
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    result = probe(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(output)
    print(json.dumps({"output": str(output), "probe_completed": result["probe_completed"],
                      "hardware_verified": result["hardware_verified"], "error": result.get("error")}))
    return 0 if result["probe_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
