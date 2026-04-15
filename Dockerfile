FROM golang:1.25-alpine AS builder
ARG VERSION=dev
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -ldflags "-s -w -X main.version=${VERSION}" -o /out/imprint .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
COPY --from=builder /out/imprint /usr/local/bin/imprint
RUN chmod +x /usr/local/bin/imprint && /usr/local/bin/imprint version
EXPOSE 8430
ENTRYPOINT ["/usr/local/bin/imprint"]
CMD ["relay", "--port", "8430"]
