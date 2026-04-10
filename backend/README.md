# Web Agent Backend

FastAPI backend for a web version of `deepagents-cli`.

## Features

- Reads runtime model provider/model from `config.yaml -> models.providers`
- Supports backend response i18n (`en`/`zh`) via `config.yaml -> app.language`
- PostgreSQL persistence for users, conversations, messages, tool events, sandbox metadata, and audit logs
- JWT auth + refresh tokens + forgot/reset password flow
- Optional message debug logging via `config.yaml -> app.debug.llm_message`
- Optional external prompt directory via `config.yaml -> app.prompts`
- Supports Docker sandbox runtime via `config.yaml -> docker`
- Uses installed `deepagents` / `deepagents-cli` packages by default
- Streams model output over WebSocket with session thread persistence

## Docker Sandbox Mode

The backend creates one container per session and routes built-in DeepAgents tools
(`execute`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`) into that
container via a `SandboxBackendProtocol` implementation.

In this mode, host-side utility tools are intentionally not attached, so tool
execution remains fully inside the Docker sandbox.

When `docker.daemon_hosts` is configured, each new sandbox container is started
on a randomly selected remote Docker daemon from that list. If
`docker.daemon_hosts` is empty or omitted, sandbox containers are started on the
local Docker daemon.

Built-in skills mount behavior:

- Local Docker mode: mounts `docker.skills_builtin_dir` (for example `./skills-builtin`)
  into sandbox path `/workspace/skills-builtin` as read-only.
- Remote Docker daemon mode: mounts `docker.daemon_skills_builtin_dir`
  (for example `/skills-builtin` on the remote daemon host) into sandbox path
  `/workspace/skills-builtin` as read-only.
- The docker chat system prompt includes `/workspace/skills-builtin` as the
  built-in skills root for the conversation.

### Chat Attachments

- `POST /api/v1/conversations/{id}/attachments` uploads one or more files into
  that conversation workspace under `uploads/`.
- `GET /api/v1/conversations/{id}/attachments/{path}` returns an uploaded file
  for inline preview or download (`inline=1` for image preview).
- In docker mode this maps to sandbox path `/workspace/uploads/...` (or the
  configured `docker.workdir` equivalent).
- Uploaded images are sent as multimodal content blocks; other files are either
  embedded as UTF-8 text (small files) or referenced by path for `read_file`.

## Message Debug Logs

When `config.yaml` has:

```yaml
app:
  debug:
    llm_message: true
```

the backend writes streamed LLM messages to:

- `logs/<Session ID>/stream-<timestamp>.ndjson`

## Database

Configure database in `config.yaml`:

```yaml
database:
  host: "127.0.0.1"
  port: 5432
  username: "teamclaw"
  password: "teamclaw_dev_password"
  dbname: "teamclaw"
  echo: false
```

Environment variables `TEAMCLAW_DB_HOST/PORT/USER/PASSWORD/NAME/ECHO` have higher priority than config file.

## Backend Language

Configure backend API response language:

```yaml
app:
  language: "en" # en | zh
```

Optional env override: `TEAMCLAW_LANGUAGE=en|zh`.

## SMTP (Forgot Password Email)

Configure SMTP in `config.yaml`:

```yaml
smtp:
  enabled: true
  host: "smtp.example.com"
  port: 587
  username: "mailer"
  password: "secret"
  from_email: "no-reply@example.com"
  from_name: "TeamClaw"
  use_tls: true
  use_ssl: false
  timeout: 15
  reset_code_ttl_seconds: 600
  reset_subject: "TeamClaw Password Reset"
  reset_url_template: "http://localhost:8080/forgot-password?code={code}&email={email}"
```

- If SMTP is enabled and configured, forgot-password sends email.
- If SMTP is not configured, backend returns a 6-digit `reset_code` (also `reset_token` for compatibility) for local development.

## External Prompt Directory

You can move system prompt / behavior rules out of Python code and manage them
as files:

```yaml
app:
  prompts:
    enabled: true
    dir: "./prompts"
    system_file: "system.md"
    behavior_file: "behavior.md"
    system_mode: "append" # append | override
```

- `append`: `deepagents` default system prompt + `system.md` + `behavior.md`
- `override`: `system.md` (as full system prompt) + `behavior.md`

## Install

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

By default, backend imports DeepAgents from your Python environment
(`deepagents` + `deepagents-cli`, already included in `requirements.txt`).

## Run

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## API

- `GET /api/health`
- `GET /api/models`
- `GET /api/config-path`
- `GET /api/settings`
- `POST /api/v1/auth/signup`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/forgot-password`
- `POST /api/v1/auth/reset-password`
- `GET /api/v1/me`
- `PATCH /api/v1/me`
- `POST /api/v1/conversations`
- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{id}`
- `PATCH /api/v1/conversations/{id}`
- `DELETE /api/v1/conversations/{id}`
- `GET /api/v1/conversations/{id}/messages`
- `GET /api/v1/conversations/{id}/tool-events`
- `POST /api/v1/conversations/{id}/attachments`
- `GET /api/v1/conversations/{id}/attachments/{path}`
- `GET /api/v1/conversations/{id}/sandbox`
- `POST /api/v1/conversations/{id}/sandbox/restart`
- `GET /api/v1/admin/users`
- `PATCH /api/v1/admin/users/{id}`
- `GET /api/v1/admin/audit-logs`
- `GET /api/v1/admin/audit-logs/{id}`
- `WS /ws/chat?token=<ACCESS_TOKEN>`

### WebSocket request payload

```json
{
  "type": "chat",
  "session_id": "<conversation_id>",
  "message": "Build a deployment checklist",
  "provider": "openai",
  "model": "qwen3.5-plus"
}
```

`provider` and `model` are optional. If omitted, the backend picks:

- first configured provider in `models.providers`
- first model in that provider's `models` list
