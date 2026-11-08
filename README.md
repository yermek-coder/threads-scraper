# Threads Monitor

A self-hosted dashboard for monitoring Threads posts by keyword.

## Features

- Scheduled and manual searches across Recent and Top results.
- Keyword and stop-word filtering, duplicate detection, and optional AI relevance filtering through OpenRouter.
- A searchable feed with conversation context and engagement counts.
- WhatsApp and email notifications.
- Threads web-session collection or Graph API integration.

Built with React, FastAPI, PostgreSQL, and a Go WhatsApp bridge.

## Getting started

Requires Docker with Docker Compose and a domain pointing to your server. Ports 80 and 443 must be available for Caddy.

1. Copy the example configuration:

   ```sh
   cp .env.example .env
   ```

2. Edit `.env`: set your domain and public URLs, replace the placeholder secrets, and choose your admin credentials. Use a random `SECRET_KEY` of at least 32 characters and an admin password of at least 12 bytes.

3. Build and start the services:

   ```sh
   docker compose up -d --build
   ```

4. Open your configured domain and sign in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`. Add keywords and configure a Threads web session or Graph API credentials in Settings. Configure notifications and OpenRouter only if needed.

## Useful commands

```sh
# View service status
docker compose ps

# Follow backend logs
docker compose logs -f backend

# Stop services while keeping stored data
docker compose down
```

Keep `.env`, exported browser sessions, and database files private. Keep the WhatsApp bridge on the internal network.

See [backend documentation](backend/README.md) for collection details, browser-session setup, and tests, or [WhatsApp bridge documentation](services/whatsapp-bridge/README.md) for its API.
