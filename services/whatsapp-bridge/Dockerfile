FROM golang:1.26-alpine3.22 AS builder

RUN apk add --no-cache build-base git
WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY cmd ./cmd
RUN CGO_ENABLED=1 GOOS=linux go build \
    -trimpath \
    -ldflags="-s -w" \
    -o /out/whatsapp-bridge \
    ./cmd/server

FROM alpine:3.22

RUN apk add --no-cache ca-certificates \
    && addgroup -S bridge \
    && adduser -S -G bridge bridge \
    && mkdir -p /data \
    && chown bridge:bridge /data

COPY --from=builder /out/whatsapp-bridge /usr/local/bin/whatsapp-bridge

ENV PORT=8080 \
    DATA_DIR=/data
EXPOSE 8080
VOLUME ["/data"]
USER bridge

ENTRYPOINT ["/usr/local/bin/whatsapp-bridge"]
