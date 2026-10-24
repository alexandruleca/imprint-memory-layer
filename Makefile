VERSION    ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
UV_VERSION ?= 0.5.11
LDFLAGS    := -s -w -X main.version=$(VERSION)
PLATFORMS  := linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64

UI_DIR     := ui-electron

.PHONY: build all package package-local installer-macos installer-windows installer-local \
        clean fetch-uv \
        ui-build ui-build-linux ui-build-mac ui-build-win ui-build-all \
        ui-dist ui-dist-linux ui-dist-mac ui-dist-win \
        site-build dashboard-build chrome-extension-zip \
        $(PLATFORMS)

# Build for current OS/arch (local dev)
build:
	go build -ldflags "$(LDFLAGS)" -o build/imprint .

# Extract the ZIP, then Load unpacked in chrome://extensions.
chrome-extension-zip:
	python3 scripts/package_chrome_extension.py

# Cross-compile all platforms into bin/. UI and site have dedicated targets.
all: $(PLATFORMS)

$(PLATFORMS):
	$(eval OS := $(word 1,$(subst /, ,$@)))
	$(eval ARCH := $(word 2,$(subst /, ,$@)))
	$(eval EXT := $(if $(filter windows,$(OS)),.exe,))
	@mkdir -p bin
	GOOS=$(OS) GOARCH=$(ARCH) CGO_ENABLED=0 \
		go build -ldflags "$(LDFLAGS)" -o bin/imprint-$(OS)-$(ARCH)$(EXT) .
	@echo "  bin/imprint-$(OS)-$(ARCH)$(EXT)"

# Stage and validate archives without deleting unrelated build outputs.
# Staged trees remain available to native installer targets.
package:
	@set -e; for plat in $(PLATFORMS); do \
		UV_VERSION=$(UV_VERSION) bash scripts/package-platform.sh "$$plat"; \
	done

# Build the macOS .pkg installer for the current host arch.
# Usage: make installer-macos [ARCH=arm64|amd64]
# Requires: macOS host and a staged tree from make package[-local].
installer-macos:
	@[ "$$(uname)" = "Darwin" ] || { echo "installer-macos requires macOS (pkgbuild/productbuild)"; exit 1; }
	@ARCH=$${ARCH:-$$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')}; \
	VER=$$(echo "$(VERSION)" | sed 's/^v//'); \
	SRC="dist/imprint-darwin-$$ARCH"; \
	OUT="dist/imprint-darwin-$$ARCH.pkg"; \
	[ -d "$$SRC" ] || { echo "Missing $$SRC — run 'make all && make package' first"; exit 1; }; \
	./installers/macos/build-pkg.sh --version "$$VER" --arch "$$ARCH" --source "$$SRC" --out "$$OUT"

# Build the Windows .exe installer.
# Usage: make installer-windows (requires iscc / Inno Setup 6.3+ on PATH, Windows host)
installer-windows:
	@command -v iscc >/dev/null || { echo "iscc (Inno Setup 6.3+) not on PATH"; exit 1; }
	@VER=$$(echo "$(VERSION)" | sed 's/^v//'); \
	SRC="$$(pwd)/dist/imprint-windows-amd64"; \
	[ -d "$$SRC" ] || { echo "Missing $$SRC — run 'make all && make package' first"; exit 1; }; \
	iscc /DImprintVersion=$$VER /DImprintSource="$$SRC" /O"$$(pwd)/dist" installers/windows/imprint.iss

# Standalone target for pulling uv into an already-staged tree (used when
# re-packaging a single platform without re-running `make package`).
# Usage: make fetch-uv OS=linux ARCH=amd64 DEST=dist/imprint-linux-amd64
fetch-uv:
	@[ -n "$$OS" ] && [ -n "$$ARCH" ] && [ -n "$$DEST" ] || { echo "usage: make fetch-uv OS=<os> ARCH=<arch> DEST=<dir>"; exit 1; }
	UV_VERSION=$(UV_VERSION) bash scripts/fetch-uv.sh "$$OS" "$$ARCH" "$$DEST"

# Build + package one platform (defaults to the current Go target).
PLAT ?= $(shell go env GOOS)/$(shell go env GOARCH)
package-local:
	$(MAKE) $(PLAT)
	UV_VERSION=$(UV_VERSION) bash scripts/package-platform.sh "$(PLAT)"

# Build the Windows installer from an already-staged tree (runs iscc directly).
# Usage: make installer-local [PLAT=windows/amd64]
# Requires: iscc (Inno Setup 6.3+) on PATH.
installer-local:
	$(eval _PLAT := $(or $(PLAT),windows/amd64))
	$(eval _OS   := $(word 1,$(subst /, ,$(_PLAT))))
	$(eval _ARCH := $(word 2,$(subst /, ,$(_PLAT))))
	$(eval _NAME := imprint-$(_OS)-$(_ARCH))
	@command -v iscc >/dev/null || { echo "iscc (Inno Setup 6.3+) not on PATH"; exit 1; }
	@[ -d "dist/$(_NAME)" ] || { echo "Missing dist/$(_NAME) — run 'make package-local' first"; exit 1; }
	@VER=$$(echo "$(VERSION)" | sed 's/^v//'); \
	SRC="$$(pwd)/dist/$(_NAME)"; \
	iscc /DImprintVersion=$$VER /DImprintSource="$$SRC" /O"$$(pwd)/dist" installers/windows/imprint.iss
	@echo "[+] dist/imprint-setup.exe"

# ── Electron UI builds ────────────────────────────────────────────────────────
# ui-build*    : raw electron-builder invocations (outputs to dist/electron/)
# ui-dist-*    : build + normalize to build/electron/imprint-ui[.exe/.app]
#                These are what make package/package-local consume.
#
# Cross-platform limits: macOS .app requires macOS; AppImage/portable can build on Linux.
# CI workflow: run `make ui-dist-<os>` on each platform runner before `make package`.

# Detect host OS for the no-arg ui-build target
_UI_HOST_OS := $(shell uname -s | tr '[:upper:]' '[:lower:]')
_UI_EB_FLAG  := $(if $(filter darwin,$(_UI_HOST_OS)),--mac,$(if $(filter mingw% msys%,$(_UI_HOST_OS)),--win,--linux))

ui-build: $(UI_DIR)/node_modules
	cd $(UI_DIR) && npx electron-builder $(_UI_EB_FLAG)

ui-build-linux: $(UI_DIR)/node_modules
	cd $(UI_DIR) && npx electron-builder --linux

ui-build-mac: $(UI_DIR)/node_modules
	cd $(UI_DIR) && npx electron-builder --mac

ui-build-win: $(UI_DIR)/node_modules
	cd $(UI_DIR) && npx electron-builder --win

ui-build-all: $(UI_DIR)/node_modules
	cd $(UI_DIR) && npx electron-builder --linux --mac --win

# Normalized outputs live in build/electron; Linux keeps a separate directory
# per architecture. Packaging retains staged release trees under dist/.

ARCH ?= $(shell go env GOARCH)
_ELECTRON_ARCH = $(if $(filter amd64,$(ARCH)),x64,$(ARCH))

ui-dist-linux: $(UI_DIR)/node_modules
	cd $(UI_DIR) && npx electron-builder --linux AppImage --$(_ELECTRON_ARCH) --publish never
	@mkdir -p build/electron/linux-$(ARCH)
	@cp dist/electron/imprint-ui.AppImage build/electron/linux-$(ARCH)/imprint-ui
	@chmod +x build/electron/linux-$(ARCH)/imprint-ui

ui-dist-mac: $(UI_DIR)/node_modules
	cd $(UI_DIR) && npx electron-builder --mac dir --universal --publish never
	@mkdir -p build/electron
	@rm -rf build/electron/imprint-ui.app
	@test -d dist/electron/mac-universal/Imprint.app
	@cp -R dist/electron/mac-universal/Imprint.app build/electron/imprint-ui.app
	@echo "[+] build/electron/imprint-ui.app"

ui-dist-win: $(UI_DIR)/node_modules
	cd $(UI_DIR) && npx electron-builder --win portable --x64 --publish never
	@mkdir -p build/electron
	@cp dist/electron/imprint-ui.exe build/electron/imprint-ui.exe
	@echo "[+] build/electron/imprint-ui.exe"

# Build Electron UI for the current host OS
ui-dist: $(if $(filter darwin,$(_UI_HOST_OS)),ui-dist-mac,$(if $(filter mingw% msys% windows%,$(_UI_HOST_OS)),ui-dist-win,ui-dist-linux))

# Install npm deps only when package.json changes
$(UI_DIR)/node_modules: $(UI_DIR)/package.json $(UI_DIR)/package-lock.json
	cd $(UI_DIR) && npm ci --prefer-offline
	@touch $(UI_DIR)/node_modules

SITE_DIR := site

# Build Astro static site
site-build: $(SITE_DIR)/node_modules
	cd $(SITE_DIR) && npm run build
	@echo "[+] site/dist/ built"

$(SITE_DIR)/node_modules: $(SITE_DIR)/package.json $(SITE_DIR)/package-lock.json
	cd $(SITE_DIR) && npm ci --prefer-offline
	@touch $(SITE_DIR)/node_modules

clean:
	rm -rf build/ dist/
	rm -rf $(UI_DIR)/node_modules
	rm -rf $(SITE_DIR)/node_modules $(SITE_DIR)/dist

# Always regenerate the static dashboard before a release.
dashboard-build:
	cd imprint/ui && npm ci --prefer-offline && npm run build
