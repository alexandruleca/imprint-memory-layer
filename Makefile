VERSION  := $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
LDFLAGS  := -s -w -X main.version=$(VERSION)
PLATFORMS := linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64

.PHONY: build all clean $(PLATFORMS)

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

clean:
	rm -rf build/
