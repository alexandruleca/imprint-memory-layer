FROM golang:1.25-alpine AS builder
ARG VERSION=dev
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -ldflags "-s -w -X main.version=${VERSION}" -o imprint .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/imprint /usr/local/bin/imprint
EXPOSE 8430
ENTRYPOINT ["imprint"]
CMD ["relay", "--port", "8430"]
