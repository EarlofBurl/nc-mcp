# nc-mcp

Ein [Model Context Protocol](https://modelcontextprotocol.io)-Server für Nextcloud.
Verbindet LLM-Agenten (z.B. Mistral Le Chat, Claude, pi) mit deiner Nextcloud:

| Modul | Lesen | Schreiben |
|---|---|---|
| **Files** | Ordner listen, Dateien lesen, suchen (WebDAV + Unified Search) | Schreiben, Ordner anlegen, Verschieben/Kopieren, Löschen (→ Papierkorb) |
| **Notes** | Notizen listen/lesen | Erstellen, Ändern, Löschen |
| **Calendar** | Kalender listen, Termine im Zeitraum | Termine erstellen, ändern, löschen |
| **Tasks** | Aufgaben listen | Erstellen, abschließen, löschen |
| **Contacts** | Adressbücher & Kontakte listen/suchen (CardDAV) | Kontakte erstellen, löschen |
| **Talk** | Räume + letzte Nachrichten lesen | Nachrichten senden |

## Architektur

```
LLM-Agent (Mistral Le Chat, ...)          dein Rechner (z.B. pi/Claude Desktop, stdio-fähig)
        │                                          │
        └──────────── HTTPS + Bearer-Token ────────┘
                        │
              Apache-Reverse-Proxy (Subdomain, TLS)
                        │
              nc-mcp (Docker, Streamable HTTP)
                        │
              Nextcloud APIs (OCS / WebDAV / CalDAV / CardDAV)
                        │
                Nextcloud (App-Passwort)
```

- **Transport:** Streamable HTTP (`/mcp`) — funktioniert mit Mistral Le Chat
  („MCP Connectors") und jedem modernen MCP-Client.
- **Auth (Client → MCP-Server):** statischer Bearer-Token (`MCP_TOKEN`).
- **Auth (MCP-Server → Nextcloud):** App-Passwort (`NC_APP_PASSWORD`) — keine
  Weitergabe des echten Passworts, jederzeit widerrufbar.

## Setup

### 1. App-Passwort erstellen

In Nextcloud: **Einstellungen → Sicherheit → Geräte & Sitzungen →
„Neues App-Passwort"** (Name z.B. `nc-mcp`). Berechtigungen sind API-weit;
das Passwort lässt sich dort jederzeit widerrufen.

### 2. Konfiguration

```bash
cp .env.example .env
# Werte eintragen:
#   NC_URL, NC_USERNAME, NC_APP_PASSWORD
#   MCP_TOKEN=$(openssl rand -hex 32)
```

### 3. Docker

```bash
docker compose up -d --build
docker compose logs -f
```

Der Server lauscht auf `127.0.0.1:8010` (nur lokal!). Nach außen wird er über
einen Reverse-Proxy mit TLS freigegeben.

### 4. Reverse-Proxy (Apache-Beispiel)

```apache
<VirtualHost *:443>
    ServerName nc-mcp.example.com
    SSLEngine on
    SSLCertificateFile /etc/letsencrypt/live/nc-mcp.example.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/nc-mcp.example.com/privkey.pem
    AllowEncodedSlashes NoDecode
    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:8010/ timeout=300
    ProxyPassReverse / http://127.0.0.1:8010/
    RequestHeader set X-Forwarded-Proto "https"
</VirtualHost>
```

Caddy-Beispiel:

```
nc-mcp.example.com {
    reverse_proxy 127.0.0.1:8010
}
```

### 5. Mistral Le Chat verbinden

In Le Chat: **Einstellungen → Connectors → MCP-Server hinzufügen**

- URL: `https://nc-mcp.example.com/mcp`
- Bearer-Token: dein `MCP_TOKEN`

### 6. Prüfen

```bash
curl -s https://nc-mcp.example.com/mcp \
  -H "Authorization: Bearer $MCP_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# → sollte die Tool-Liste zurückgeben; ohne Token: 401
```

## Lokale Entwicklung (ohne Docker)

```bash
uv venv && uv pip install -e . pytest pytest-asyncio
pytest                    # Unit-Tests (offline)
NC_URL=... NC_USERNAME=... NC_APP_PASSWORD=... python scripts/smoke_test.py
uvicorn nc_mcp.app:app --port 8010
```

## Tools im Detail

Alle Tools sind bewusst klein und LLM-freundlich (JSON in, JSON out):

- **Files:** `files_list`, `files_read`, `files_write`, `files_mkdir`,
  `files_move`, `files_copy`, `files_delete`, `files_search`
- **Notes:** `notes_list`, `notes_get`, `notes_create`, `notes_update`, `notes_delete`
- **Calendar:** `calendar_list`, `calendar_events_list`, `calendar_event_create`,
  `calendar_event_update`, `calendar_event_delete`
- **Tasks:** `tasks_lists`, `tasks_list`, `task_create`, `task_complete`, `task_delete`
- **Contacts:** `contacts_addressbooks_list`, `contacts_list`, `contact_create`, `contact_delete`
- **Talk:** `talk_rooms_list`, `talk_send_message`, `talk_read_messages`

Datumsangaben sind ISO-Format (`2025-12-24T18:00:00+01:00`); ohne Offset gilt
`NC_DEFAULT_TZ` (Default: `Europe/Berlin`), reine Datumsangaben erzeugen
Ganztages-Termine.

## Sicherheit

- **Kein** Nextcloud-Passwort im Spiel — nur ein widerrufbares App-Passwort.
- Der MCP-Server bindet lokal; TLS/Bearer kommen vom Reverse-Proxy-Setup.
- `.env` ist in `.gitignore` — niemals committen.
- `files_delete` verschiebt nur in den Nextcloud-Papierkorb (wiederherstellbar).
- Einschränkungen des App-Passworts (z.B. nur Lesen) werden von Nextcloud
  erzwungen — der Server hat keine eigenen Rechte darüber hinaus.

## Bekannte Eigenheiten

- **Talk-Räume:** Manche Apache-Setups (Rewrite-Regeln) brechen den Plural-
  Endpunkt `/ocs/.../api/v4/rooms` mit OCS-Code 998. nc-mcp erkennt das und
  fällt automatisch auf `/room` (Singular) zurück. Übersteuern via
  `NC_TALK_ROOMS_PATH`.
- **CardDAV-Pfad:** Nextcloud liegt Adressbücher unter
  `/remote.php/dav/addressbooks/users/<user>/` (mit `users`-Segment).

## License

MIT — siehe [LICENSE](LICENSE).
