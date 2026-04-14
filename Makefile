VERSION  := $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
LDFLAGS  := -s -w -X main.version=$(VERSION)
PLATFORMS := linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64
QDRANT_VERSION := v1.17.1

.PHONY: build all clean $(PLATFORMS) qdrant-update

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

# Download qdrant binaries for all platforms into bin/
# Run manually: make qdrant-update QDRANT_VERSION=v1.18.0
qdrant-update:
	@mkdir -p /tmp/qdrant-dl bin
	@echo "Downloading Qdrant $(QDRANT_VERSION) for all platforms..."
	curl -fsSL "https://github.com/qdrant/qdrant/releases/download/$(QDRANT_VERSION)/qdrant-x86_64-unknown-linux-gnu.tar.gz" | tar xz -C /tmp/qdrant-dl && mv /tmp/qdrant-dl/qdrant bin/qdrant-linux-amd64
	curl -fsSL "https://github.com/qdrant/qdrant/releases/download/$(QDRANT_VERSION)/qdrant-aarch64-unknown-linux-musl.tar.gz" | tar xz -C /tmp/qdrant-dl && mv /tmp/qdrant-dl/qdrant bin/qdrant-linux-arm64
	curl -fsSL "https://github.com/qdrant/qdrant/releases/download/$(QDRANT_VERSION)/qdrant-x86_64-apple-darwin.tar.gz" | tar xz -C /tmp/qdrant-dl && mv /tmp/qdrant-dl/qdrant bin/qdrant-darwin-amd64
	curl -fsSL "https://github.com/qdrant/qdrant/releases/download/$(QDRANT_VERSION)/qdrant-aarch64-apple-darwin.tar.gz" | tar xz -C /tmp/qdrant-dl && mv /tmp/qdrant-dl/qdrant bin/qdrant-darwin-arm64
	curl -fsSL "https://github.com/qdrant/qdrant/releases/download/$(QDRANT_VERSION)/qdrant-x86_64-pc-windows-msvc.zip" -o /tmp/qdrant-dl/qdrant-win.zip && python3 -c "import zipfile; zipfile.ZipFile('/tmp/qdrant-dl/qdrant-win.zip').extractall('/tmp/qdrant-dl')" && mv /tmp/qdrant-dl/qdrant.exe bin/qdrant-windows-amd64.exe
	@chmod +x bin/qdrant-*
	@rm -rf /tmp/qdrant-dl
	@echo "bin/ updated with Qdrant $(QDRANT_VERSION)"

clean:
	rm -rf build/
