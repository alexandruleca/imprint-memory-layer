VERSION    ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
UV_VERSION ?= 0.5.11
LDFLAGS    := -s -w -X main.version=$(VERSION)
PLATFORMS  := linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64

UI_DIR     := ui-electron

.PHONY: build all package package-local installer-macos installer-windows installer-local \
        clean fetch-uv \
        ui-build ui-build-linux ui-build-mac ui-build-win ui-build-all \
        ui-dist ui-dist-linux ui-dist-mac ui-dist-win \
        site-build \
        $(PLATFORMS)

# Build for current OS/arch (local dev)
build:
	go build -ldflags "$(LDFLAGS)" -o build/imprint .

# Cross-compile all platforms + build UI for host OS + build site.
# In CI (GitHub Actions sets CI=true) skip ui-dist — Electron requires
# display/FUSE tooling not available on headless runners.
ifeq ($(CI),true)
all: $(PLATFORMS) site-build
else
all: $(PLATFORMS) ui-dist site-build
endif

$(PLATFORMS):
	$(eval OS := $(word 1,$(subst /, ,$@)))
	$(eval ARCH := $(word 2,$(subst /, ,$@)))
	$(eval EXT := $(if $(filter windows,$(OS)),.exe,))
	@mkdir -p bin
	GOOS=$(OS) GOARCH=$(ARCH) CGO_ENABLED=0 \
		go build -ldflags "$(LDFLAGS)" -o bin/imprint-$(OS)-$(ARCH)$(EXT) .
	@echo "  bin/imprint-$(OS)-$(ARCH)$(EXT)"

# Package per-platform self-contained archives into dist/.
# Each archive contains the full repo source (filtered via .releaseignore)
# plus that platform's imprint binary at bin/imprint[.exe].
# Requires binaries in bin/ — run `make all` first.
# Includes Electron UI binary from build/electron/ if pre-built via ui-dist-*.
package:
	@command -v rsync >/dev/null || { echo "rsync required for packaging"; exit 1; }
	@command -v python3 >/dev/null || { echo "python3 required for zip packaging"; exit 1; }
	@rm -rf dist
	@mkdir -p dist
	@set -e; for plat in $(PLATFORMS); do \
		OS=$${plat%/*}; ARCH=$${plat#*/}; \
		EXT=""; [ "$$OS" = "windows" ] && EXT=".exe"; \
		NAME="imprint-$$OS-$$ARCH"; \
		STAGE="dist/$$NAME"; \
		SRCBIN="bin/imprint-$$OS-$$ARCH$$EXT"; \
		if [ ! -f "$$SRCBIN" ]; then \
			echo "[x] missing $$SRCBIN — run 'make all' first"; exit 1; \
		fi; \
		echo "[*] staging $$NAME"; \
		mkdir -p "$$STAGE"; \
		rsync -a --exclude-from=.releaseignore ./ "$$STAGE/"; \
		mkdir -p "$$STAGE/bin"; \
		cp "$$SRCBIN" "$$STAGE/bin/imprint$$EXT"; \
		chmod +x "$$STAGE/bin/imprint$$EXT"; \
		UV_VERSION=$(UV_VERSION) bash scripts/fetch-uv.sh $$OS $$ARCH "$$STAGE"; \
		if [ "$$OS" = "linux" ] && [ -f "build/electron/imprint-ui" ]; then \
			cp "build/electron/imprint-ui" "$$STAGE/bin/imprint-ui"; \
			chmod +x "$$STAGE/bin/imprint-ui"; \
			echo "  included Electron UI (AppImage)"; \
		elif [ "$$OS" = "darwin" ] && [ -d "build/electron/imprint-ui.app" ]; then \
			cp -r "build/electron/imprint-ui.app" "$$STAGE/bin/imprint-ui.app"; \
			echo "  included Electron UI (macOS .app)"; \
		elif [ "$$OS" = "windows" ] && [ -f "build/electron/imprint-ui.exe" ]; then \
			cp "build/electron/imprint-ui.exe" "$$STAGE/bin/imprint-ui.exe"; \
			echo "  included Electron UI (portable exe)"; \
		else \
			echo "  [!] no Electron binary for $$OS — run 'make ui-dist-$$OS' on that platform first"; \
		fi; \
		if [ "$$OS" = "windows" ]; then \
			python3 -c "import shutil; shutil.make_archive('dist/$$NAME', 'zip', 'dist', '$$NAME')"; \
			echo "  dist/$$NAME.zip"; \
		else \
			tar -czf "dist/$$NAME.tar.gz" -C dist "$$NAME"; \
			echo "  dist/$$NAME.tar.gz"; \
		fi; \
		rm -rf "$$STAGE"; \
	done

# Build the macOS .pkg installer for the current host arch.
# Usage: make installer-macos [ARCH=arm64|amd64]
# Requires: macOS host, `make package` has already produced dist/imprint-darwin-$ARCH/
installer-macos:
	@[ "$$(uname)" = "Darwin" ] || { echo "installer-macos requires macOS (pkgbuild/productbuild)"; exit 1; }
	@ARCH=$${ARCH:-$$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')}; \
	VER=$$(echo "$(VERSION)" | sed 's/^v//'); \
	SRC="dist/imprint-darwin-$$ARCH"; \
	OUT="dist/imprint-darwin-$$ARCH.pkg"; \
	[ -d "$$SRC" ] || { echo "Missing $$SRC — run 'make all && make package' first"; exit 1; }; \
	./installers/macos/build-pkg.sh --version "$$VER" --arch "$$ARCH" --source "$$SRC" --out "$$OUT"

# Build the Windows .exe installer.
# Usage: make installer-windows (requires iscc / Inno Setup 6 on PATH, Windows host)
installer-windows:
	@command -v iscc >/dev/null || { echo "iscc (Inno Setup 6) not on PATH"; exit 1; }
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

# Build + package a single platform for local iteration.
# Builds the Go binary inline, stages the tree, fetches uv, produces the archive.
# The staged tree stays in dist/<name>/ so you can inspect it without re-running.
# Includes Electron UI from build/electron/ if pre-built via ui-dist-*.
# Usage: make package-local [PLAT=windows/amd64]
package-local:
	$(eval _PLAT := $(or $(PLAT),windows/amd64))
	$(eval _OS   := $(word 1,$(subst /, ,$(_PLAT))))
	$(eval _ARCH := $(word 2,$(subst /, ,$(_PLAT))))
	$(eval _EXT  := $(if $(filter windows,$(_OS)),.exe,))
	$(eval _NAME := imprint-$(_OS)-$(_ARCH))
	@command -v rsync >/dev/null || { echo "rsync required"; exit 1; }
	@command -v python3 >/dev/null || { echo "python3 required"; exit 1; }
	@echo "[*] building $(_OS)/$(_ARCH)..."
	@mkdir -p bin
	GOOS=$(_OS) GOARCH=$(_ARCH) CGO_ENABLED=0 \
		go build -ldflags "$(LDFLAGS)" -o bin/imprint-$(_OS)-$(_ARCH)$(_EXT) .
	@echo "  bin/imprint-$(_OS)-$(_ARCH)$(_EXT)"
	@mkdir -p dist
	@rm -rf "dist/$(_NAME)"
	@echo "[*] staging $(_NAME)..."
	@rsync -a --exclude-from=.releaseignore ./ "dist/$(_NAME)/"
	@mkdir -p "dist/$(_NAME)/bin"
	@cp "bin/imprint-$(_OS)-$(_ARCH)$(_EXT)" "dist/$(_NAME)/bin/imprint$(_EXT)"
	@[ "$(_OS)" = "windows" ] || chmod +x "dist/$(_NAME)/bin/imprint"
	@UV_VERSION=$(UV_VERSION) bash scripts/fetch-uv.sh $(_OS) $(_ARCH) "dist/$(_NAME)"
	@if [ "$(_OS)" = "linux" ] && [ -f "build/electron/imprint-ui" ]; then \
		cp "build/electron/imprint-ui" "dist/$(_NAME)/bin/imprint-ui"; \
		chmod +x "dist/$(_NAME)/bin/imprint-ui"; \
		echo "  included Electron UI (AppImage)"; \
	elif [ "$(_OS)" = "darwin" ] && [ -d "build/electron/imprint-ui.app" ]; then \
		cp -r "build/electron/imprint-ui.app" "dist/$(_NAME)/bin/imprint-ui.app"; \
		echo "  included Electron UI (macOS .app)"; \
	elif [ "$(_OS)" = "windows" ] && [ -f "build/electron/imprint-ui.exe" ]; then \
		cp "build/electron/imprint-ui.exe" "dist/$(_NAME)/bin/imprint-ui.exe"; \
		echo "  included Electron UI (portable exe)"; \
	else \
		echo "  [!] no Electron binary for $(_OS) — run 'make ui-dist-$(_OS)' to include it"; \
	fi
	@rm -f "dist/$(_NAME).zip" "dist/$(_NAME).tar.gz"
	@if [ "$(_OS)" = "windows" ]; then \
		python3 -c "import shutil; shutil.make_archive('dist/$(_NAME)', 'zip', 'dist', '$(_NAME)')"; \
		echo "[+] dist/$(_NAME).zip (staged tree: dist/$(_NAME)/)"; \
	else \
		tar -czf "dist/$(_NAME).tar.gz" -C dist "$(_NAME)"; \
		echo "[+] dist/$(_NAME).tar.gz (staged tree: dist/$(_NAME)/)"; \
	fi

# Build the Windows installer from an already-staged tree (runs iscc directly).
# Usage: make installer-local [PLAT=windows/amd64]
# Requires: iscc (Inno Setup 6) on PATH.
installer-local:
	$(eval _PLAT := $(or $(PLAT),windows/amd64))
	$(eval _OS   := $(word 1,$(subst /, ,$(_PLAT))))
	$(eval _ARCH := $(word 2,$(subst /, ,$(_PLAT))))
	$(eval _NAME := imprint-$(_OS)-$(_ARCH))
	@command -v iscc >/dev/null || { echo "iscc (Inno Setup 6) not on PATH"; exit 1; }
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

# Normalized dist targets: build + copy to build/electron/imprint-ui[.exe/.app]
# so make package can include them without being stomped by `rm -rf dist`.

ui-dist-linux: $(UI_DIR)/node_modules
	cd $(UI_DIR) && npx electron-builder --linux AppImage
	@mkdir -p build/electron
	@cp $$(find dist/electron -maxdepth 1 -name '*.AppImage' | head -1) build/electron/imprint-ui
	@chmod +x build/electron/imprint-ui
	@echo "[+] build/electron/imprint-ui (AppImage)"

ui-dist-mac: $(UI_DIR)/node_modules
	cd $(UI_DIR) && npx electron-builder --mac dir --universal
	@mkdir -p build/electron
	@rm -rf build/electron/imprint-ui.app
	@APP=$$(find dist/electron -maxdepth 2 -name 'Imprint.app' -type d | head -1); \
	  [ -n "$$APP" ] || { echo "[x] Imprint.app not found under dist/electron"; exit 1; }; \
	  cp -r "$$APP" build/electron/imprint-ui.app
	@echo "[+] build/electron/imprint-ui.app"

ui-dist-win: $(UI_DIR)/node_modules
	cd $(UI_DIR) && npx electron-builder --win portable
	@mkdir -p build/electron
	@cp $$(find dist/electron -maxdepth 1 -name '*.exe' | head -1) build/electron/imprint-ui.exe
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
