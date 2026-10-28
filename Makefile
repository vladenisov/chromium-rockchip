IMAGE ?= chromium-rockchip-builder:155-bookworm
WORK_DIR ?= $(CURDIR)/work
MEMORY ?= 3g
MEMORY_SWAP ?= 8g
CPUS ?= 2
PYTHON ?= python3
BROWSER ?= /opt/chromium-rockchip/chrome
WORK_ABS := $(abspath $(WORK_DIR))
RUN = docker run --rm --init --user $(shell id -u):$(shell id -g) \
      --cpus=$(CPUS) --memory=$(MEMORY) --memory-swap=$(MEMORY_SWAP) \
      --cap-drop=ALL --security-opt=no-new-privileges:true \
      -v "$(CURDIR):/project:ro" -v "$(WORK_ABS):/work" -e TMPDIR=/work/tmp \
      -e CHROMIUM_JOBS -e CHROMIUM_COMPONENT_BUILD -e CHROMIUM_BUILD_DEADLINE $(IMAGE)

.PHONY: image fetch build test fixture verify status latest
image:
	docker build -t $(IMAGE) .

fetch:
	mkdir -p "$(WORK_ABS)/tmp"
	$(RUN) python3 /project/fetch-sources.py --work /work

build:
	mkdir -p "$(WORK_ABS)/tmp"
	$(RUN) python3 /project/build-browser.py

test:
	$(PYTHON) -m unittest discover -s . -p 'test_*.py' -v
	sh -n build-libs.sh

fixture:
	mkdir -p "$(WORK_ABS)/fixtures"
	ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=1280x720:rate=25 \
	  -t 6 -c:v libx264 -threads 2 -preset ultrafast -pix_fmt yuv420p -movflags +faststart \
	  "$(WORK_ABS)/fixtures/h264-720p.mp4"

verify:
	$(PYTHON) verify-browser.py --binary "$(BROWSER)" \
	  --video "$(WORK_ABS)/fixtures/h264-720p.mp4" --output "$(WORK_ABS)/verification.json" \
	  --expected-version 155.0.8059.84

status:
	$(PYTHON) -m json.tool "$(WORK_ABS)/build-status.json"

latest:
	$(PYTHON) fetch-sources.py --check-latest
