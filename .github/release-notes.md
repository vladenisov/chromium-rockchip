Chromium for RK3588 with Rockchip MPP video decoding through V4L2: native ARM64, static build with the patches in `patches/chromium/`.

Built on a GitHub runner without Rockchip hardware; the build itself only checks that the browser starts and reports its version. See the [verification status](https://github.com/vladenisov/chromium-rockchip#status) for what has been tested on a board.

Install on the board, from the folder with these files:

```sh
sha256sum --check chromium-rockchip-*-arm64.tar.xz.sha256
mkdir stage && tar -xJf chromium-rockchip-*-arm64.tar.xz -C stage
python3 install.py --stage stage            # dry run
sudo python3 install.py --stage stage --apply
```

It installs alongside the distribution Chromium and keeps previous versions for rollback. See [INSTALL.md](https://github.com/vladenisov/chromium-rockchip/blob/main/INSTALL.md).
