# WhatsApp Bridge

Small REST service for the WhatsApp Web session used by the application. It stores the linked-device session in SQLite and keeps the database under `DATA_DIR` (`/data/whatsapp.db` in Compose).

The bridge uses the `github.com/yermek-coder/whatsmeow-fork` fork through its upstream-compatible import path, `go.mau.fi/whatsmeow`.

## Run locally

Requires Go 1.26 or newer and a C compiler for the SQLite driver.

```sh
cd services/whatsapp-bridge
go run ./cmd/server
```

By default, the service listens on port `8080` and stores data in `./data`. Set `PORT` or `DATA_DIR` to override those values.

## HTTP API

| Method and path | Behavior |
| --- | --- |
| `GET /health` | Liveness check; returns `{"status":"ok"}`. |
| `GET /api/status` | Returns `connected`, `logged_in`, and `phone`. `logged_in` remains true while the saved device reconnects. |
| `GET /api/qr` | Returns the current QR code as `image/png`; responds with `409` while there is no active code. |
| `POST /api/send` | Sends a text message. Request: `{"phone":"77010000000","message":"Hello"}`. |
| `POST /api/logout` | Unlinks the device when WhatsApp is reachable, clears the local session, and starts a fresh QR pairing flow. |

Phone numbers must include their country calling code. Spaces and common punctuation are accepted and removed before sending.
If WhatsApp unlinks the device remotely, the bridge also replaces the invalid client and starts a fresh QR pairing flow.

The service is intended to be reachable only on the private Compose network. The API has no authentication layer, so do not publish its port directly to the internet.
