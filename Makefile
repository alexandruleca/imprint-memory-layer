VERSION  ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
LDFLAGS  := -s -w -X main.version=$(VERSION)
PLATFORMS := linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64

.PHONY: build all package clean $(PLATFORMS)

# Build for current OS/arch (local dev)
build:
	go build -ldflags "$(LDFLAGS)" -o build/imprint .

# Cross-compile all platforms directly into bin/
all: $(PLATFORMS)

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
		if [ "$$OS" = "windows" ]; then \
			python3 -c "import shutil; shutil.make_archive('dist/$$NAME', 'zip', 'dist', '$$NAME')"; \
			echo "  dist/$$NAME.zip"; \
		else \
			tar -czf "dist/$$NAME.tar.gz" -C dist "$$NAME"; \
			echo "  dist/$$NAME.tar.gz"; \
		fi; \
		rm -rf "$$STAGE"; \
	done

clean:
	rm -rf build/ dist/
