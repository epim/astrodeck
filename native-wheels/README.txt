The native engine for the Docker image.

`docker compose build` reads the release's astrodeck_native wheel from this
directory (or from the directory named by ASTRODECK_NATIVE_DIR) and installs it
into the image, then runs the engine probe. With no wheel for the build machine's
architecture the build stops and says so: there is no silent image without the
engine.

Download the wheel for your architecture (x86_64 or aarch64) from the release
assets at https://github.com/epim/astrodeck/releases, its name starts with
astrodeck_native- and its platform tag contains linux, and put exactly one such
file here. Do not commit wheels; they are downloaded per build.

Details: docs/guide/install-docker.md.
