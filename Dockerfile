# The Go binary is static, so it is cross-compiled on the build machine: Go's
# compiler hangs under the qemu emulation an arm64 image would otherwise use.
FROM --platform=$BUILDPLATFORM golang:1.25-alpine AS builder
ARG VERSION=dev
ARG TARGETOS TARGETARCH
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -ldflags "-s -w -X main.version=${VERSION}" -o /out/imprint .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
COPY --from=builder /out/imprint /usr/local/bin/imprint
RUN chmod +x /usr/local/bin/imprint && /usr/local/bin/imprint version
RUN addgroup -S imprint && adduser -S -G imprint imprint
USER imprint
EXPOSE 8430
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8430/health || exit 1
ENTRYPOINT ["/usr/local/bin/imprint"]
CMD ["relay", "--port", "8430"]
