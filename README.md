# TeamClaw - OpenClaw for Multi-tenant Secure Isolation 🦞

![](images/teamclaw_logo1.png)

- [English Doc](README.md)
- [中文文档](README-zh.md)

- Project:
  - [github.com/cookeem/teamclaw](https://github.com/cookeem/teamclaw)
  - [gitee.com/cookeem/teamclaw](https://gitee.com/cookeem/teamclaw)

## What is TeamClaw?
- The name TeamClaw is inspired by the openclaw 🦞 — a team-oriented, multi-tenant task agent that turns chat requests into a task checklist and executes them inside isolated containers, making complex tasks safer, controllable, and traceable.
- The agent layer is based on LangChain DeepAgents. The backend is FastAPI, and the frontend is a static site (Vue + Vuetify). You can deploy it with Docker in one step.

![](images/screenshot.png)

## TeamClaw Task Execution Flow

![](images/teamclaw.png)

- Users create a conversation in TeamClaw and chat with the LLM. The LLM understands the request and generates a task checklist (todo list).
- TeamClaw reads the todo list, creates a Docker container for each conversation, and mounts the conversation workspace as data storage.
- The user confirms whether to execute the task checklist.
- TeamClaw executes tasks inside the container; tasks may call tools or skills.
- TeamClaw feeds back the execution process and results to the user.

### TeamClaw Components

- Postgres: TeamClaw database for users/conversations/skills and more.
- Docker in Docker (dind): sandbox for conversation task execution, isolating each user's tasks and data.

## Key Features
- Multiple LLM providers: openai / ollama / anthropic / google_gemini / google_vertexai / azure_openai / xai / together / mistralai / cohere / bedrock
- Generate a todo list via chat
- Auto-call tools/skills to execute tasks
- Run tools/skills in isolated containers per conversation
- Multi-tenant isolation with per-conversation containers
- Create and execute skills through chat
- Publish and share skills
- Manual approval before execution for safer control
- Upload Office/PDF files into the conversation workspace and process them with tasks

## Quick Install (Docker)
1. Clone the repo
```bash
git clone https://github.com/cookeem/teamclaw.git
cd teamclaw
```

2. Update config
  - Docker deployment uses `docker-compose-docker.yaml` and `config-docker.yaml`.
  - Edit `config-docker.yaml`:
    - `models.providers`: model configuration
    - `api_keys.tavily`: Tavily internet_search API key
    - `smtp`: email settings

3. Start services
```bash
# Generate docker daemon certificates; output in ./certs
sh docker_certs.sh

# Start TeamClaw service (also starts postgres / docker-0.docker / docker-1.docker)
docker compose -f docker-compose-docker.yaml up -d
```
4. Access
  - Frontend: http://localhost:8080/frontend/
  - Backend: http://localhost:8000/docs

## Local Install (Development)

1. Clone the repo
```bash
git clone https://github.com/cookeem/teamclaw.git
cd teamclaw
```

2. Update config
  - Local deployment uses `docker-compose.yaml` and `config.yaml`.
  - Edit `config.yaml`:
    - `models.providers`: model configuration
    - `api_keys.tavily`: Tavily internet_search API key
    - `smtp`: email settings

3. Start services
```bash
# Generate docker daemon certificates; output in ./certs
sh docker_certs.sh

# Start postgres / docker-0.docker / docker-1.docker
docker compose up -d

# Create venv and install dependencies
# If you need models beyond openai / ollama,
# update requirements-models.txt
python3 -m venv venv
source venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-models.txt

# Start backend
python3 -m backend.main

# Start frontend
python3 -m http.server 8080
```
4. Access
  - Frontend: http://localhost:8080/frontend/
  - Backend: http://localhost:8000/docs

## Build Docker Image

```bash
# Clone
git clone https://github.com/cookeem/teamclaw.git
cd teamclaw

# If you need models beyond openai / ollama,
# update requirements-models.txt
vi requirements-models.txt

# Build image
docker build -t yourname/teamclaw:dev .
```
