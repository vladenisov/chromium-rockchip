# Install alongside the system browser

`install.py` accepts the staging root containing `opt/chromium-rockchip`, or
that prefix directory itself. Its default is a dry run. It checks Chromium's
exact version, required runtime files, source symlinks, and destination
collisions before changing anything.

```sh
python3 install.py --stage work/stage
sudo python3 install.py --stage work/stage --apply
```

Installation creates a root-owned, protected version directory under `/opt`,
named with the Chromium version and the first 12 characters of its runtime
artifact SHA256. This covers component libraries, resources, normalized
symlink targets, and effective executable permissions; timestamps and file
owners do not affect it. Updating a component library creates a new version
even when the small `chrome` entry point remains identical.
`/opt/chromium-rockchip` switches atomically to that directory. Old
version directories remain available; the system Chromium is untouched.
The Chromium setuid sandbox is installed as root-owned `chrome-sandbox` mode
4755 only inside this protected prefix. Launchers do not disable sandboxing.
Installation must target a filesystem permitting execution and the setuid
sandbox, or a system where Chromium's user namespace sandbox is available.

Run `chromium-rockchip`: it opens a window in a Wayland/X11 session and runs
headless with CDP otherwise. `chromium-rockchip-headless` always runs headless.
They use separate profiles under your config directory. The headless launcher
binds debugging to loopback, on port 9223 by default; `CDP_PORT` accepts only
an integer from 1 through 65535. Run both launchers as your regular user.
Additional arguments are passed through unchanged.

The installer creates only the known `dec`/`enc` regular configuration files
for the V4L2 MPP bridge, mode 0660 with group `video`. Matching existing files
are kept; unrelated files, symlinks, and real device nodes are refused. A
managed tmpfiles configuration recreates them at boot. Your playback user
also needs the existing host permissions for MPP, RGA, DMA heaps, and the GPU;
the installer does not change those device permissions or user memberships.

`--root /host` supports a scoped root helper container with installation
directories mounted below `/host`; the stage executable is checked inside
the helper without chroot. `--video-gid NUMBER` selects the host's numeric
video group when it differs from the helper's group database. Mount only the
required `/opt`, `/usr/local/bin`, `/usr/share/applications`,
`/etc/tmpfiles.d`, and `/dev` destinations into that helper.

## Desktop window on RK3588

Hardware video decoding in a window needs a Wayland session. On the tested
board GDM's own session is X11 and offers no Wayland choice, so GNOME Wayland
was started from the text console with the display manager stopped:

```sh
XDG_SESSION_TYPE=wayland dbus-run-session -- gnome-session
```

The first attempt on that board showed a black screen and the second did not;
the cause is unknown. X11 sessions, including xrdp, play video with the
software decoder.

## Make it the default browser

The desktop entry declares the web MIME types, so it can be chosen in the
desktop's default-application settings, or per user with:

```sh
xdg-settings set default-web-browser chromium-rockchip.desktop
```

For programs that call `x-www-browser` or `gnome-www-browser`:

```sh
sudo update-alternatives --install /usr/bin/x-www-browser x-www-browser /usr/local/bin/chromium-rockchip 210
sudo update-alternatives --install /usr/bin/gnome-www-browser gnome-www-browser /usr/local/bin/chromium-rockchip 210
sudo update-alternatives --set x-www-browser /usr/local/bin/chromium-rockchip
sudo update-alternatives --set gnome-www-browser /usr/local/bin/chromium-rockchip
```

The distribution `chromium` command keeps starting the stock browser.

## Roll back

List the protected `chromium-rockchip-*` version directories under `/opt`.
To return to a previous build, create a temporary relative symlink to the
chosen directory and atomically replace `/opt/chromium-rockchip` with it:

```sh
cd /opt
sudo ln -s chromium-rockchip-PREVIOUS-VERSION-HASH .chromium-rockchip-rollback
sudo mv -Tf .chromium-rockchip-rollback chromium-rockchip
```

Restart the custom browser afterward. Stock Chromium remains available
through its original executable throughout installation and rollback.
