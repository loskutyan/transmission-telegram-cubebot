# CubeBot

Private Telegram bot for a Transmission instance. It accepts magnet links and
`.torrent` documents from an allowlisted Telegram user, then manages the local
Transmission RPC endpoint.

The important design choice is that uploaded `.torrent` files are downloaded by
the bot and submitted to Transmission as Base64 `metainfo`. Transmission never
needs to download a temporary Telegram file URL itself.

## Features in the first version

- private access by numeric Telegram user ID;
- magnet links and `.torrent` documents;
- `.torrent` files stay in memory and are not written to disk;
- `/status`, `/list`, `/pause HASH`, `/resume HASH`;
- inline controls for start, pause, remove, and remove with data;
- explicit confirmation before deleting downloaded data;
- Transmission RPC session-ID handshake and retry after HTTP `409`;
- no token, password, or Telegram file URL in application logs.

Notifications and persistent state are deliberately outside this MVP.

## Configuration

Copy [`.env.example`](.env.example) to a private `.env` file and replace the
placeholder values. Do not commit that file.

| Variable | Required | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | yes | Token issued by BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | yes | Comma-separated numeric Telegram user IDs |
| `TRANSMISSION_RPC_URL` | no | Defaults to `http://transmission:9091/transmission/rpc` |
| `TRANSMISSION_RPC_USERNAME` | pair | RPC username, if authentication is enabled |
| `TRANSMISSION_RPC_PASSWORD` | pair | RPC password, if authentication is enabled |
| `MAX_TORRENT_FILE_BYTES` | no | Max uploaded `.torrent` size; default is 5 MiB |
| `RPC_TIMEOUT_SECONDS` | no | RPC request timeout; default is 15 |
| `LOG_LEVEL` | no | Python log level; default is `INFO` |

Both RPC credentials must be present together or both omitted.

## Local development

This project uses Python 3.13 and `uv`.

```bash
uv sync --all-extras
uv run pytest
```

Run the bot after exporting the required environment variables:

```bash
uv run python -m cubebot
```

## Docker

Build locally:

```bash
docker build -t transmission-telegram-cubebot:local .
```

Run the container with a private env file. If Transmission runs in another
container, attach both services to the same user-defined Docker network and set
`TRANSMISSION_RPC_URL` to its resolvable service name. Otherwise use any RPC
endpoint reachable from the container:

```bash
docker run -d \
  --name transmission-telegram-cubebot \
  --network YOUR_DOCKER_NETWORK \
  --env-file /path/to/private/.env \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --restart unless-stopped \
  transmission-telegram-cubebot:local
```

The application deliberately ignores inherited `ALL_PROXY`, `HTTP_PROXY`,
`HTTPS_PROXY`, and `NO_PROXY` variables. Configure network routing outside the
application and provide the intended Transmission endpoint explicitly through
`TRANSMISSION_RPC_URL`.

The Docker healthcheck verifies only the local Transmission RPC connection. It
does not make calls to Telegram, so it will not consume API quota or expose the
bot token.

## Security notes

- Keep the bot token in the deployment platform's secret or private environment
  configuration.
- Revoke a token immediately if it appears in a chat, log, or commit.
- The bot operates only in private chats and only for allowlisted numeric IDs.
- It intentionally rejects arbitrary HTTP/HTTPS torrent URLs. This prevents the
  bot from being used as an internal-network fetcher.
- `Удалить вместе с файлами` always requires an extra confirmation.
