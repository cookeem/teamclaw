# TeamClaw - OpenClaw for Multi-tenant Secure Isolation 🦞

![](images/teamclaw_logo1.png)

- [English Documentation](README.md)
- [Chinese Documentation](README-zh.md)

- Project Links:
  - [github.com/cookeem/teamclaw](https://github.com/cookeem/teamclaw)
  - [gitee.com/cookeem/teamclaw](https://gitee.com/cookeem/teamclaw)

## What Is TeamClaw?
- TeamClaw is an intelligent task agent designed for team-based multi-tenant scenarios. Through chat, it helps generate executable task checklists automatically, then runs them in isolated container environments, making complex tasks safer, controllable, and traceable.
- The core agent is built on DeepAgents from LangChain, with a FastAPI backend and a static frontend (Vue + Vuetify). It supports one-command Docker deployment.

![](images/screenshot.png)

## TeamClaw Task Execution Flow

![](images/teamclaw.png)

- Users create conversations in TeamClaw and chat with an LLM. The LLM understands user requirements and generates a task checklist (todo list) through TeamClaw.
- TeamClaw reads the checklist and automatically creates a dedicated Docker container for each user conversation, mounting the conversation workspace as persistent data storage.
- TeamClaw executes tasks inside the Docker container sandbox. Tasks may call tools and skills.
- Execution progress and results are reported back to users through TeamClaw.

### TeamClaw Components

- postgres: TeamClaw database for users, conversations, and related data.
- docker in docker (dind): TeamClaw sandbox runtime for conversation tasks, isolating workloads and data per user conversation.

## Key Features
- Supports multiple LLM providers: openai / ollama / anthropic / google_gemini / google_vertexai / azure_openai / xai / together / mistralai / cohere / bedrock
- Supports automatic tool/skill invocation to execute tasks in sandbox containers
- Supports multi-tenancy with isolated per-conversation containers
- Supports creating and executing skills through conversation
- Supports creating and managing scheduled tasks through conversation
- Supports uploading Office/PDF files into conversation workspaces for task-based processing

## Quick Install (Docker)
1. Clone the repository
```bash
git clone https://github.com/cookeem/teamclaw.git
cd teamclaw
```

2. Update configuration
  - For Docker deployment, use `docker-compose-docker.yaml` to start services and `config-docker.yaml` as the config file.
  - Edit `config-docker.yaml` and update:
    - `models.providers`: model provider settings
    - `smtp`: email settings

3. Start services
```bash
# Copy files to data/teamclaw and generate Docker daemon certificates.
# Certificates will be stored in data/teamclaw/certs.
mkdir -p data/teamclaw
cp -rp config-docker.yaml docker_certs.sh prompts skills-builtin data/teamclaw
cd data/teamclaw
sh docker_certs.sh
cd ../..

# Start TeamClaw services, including postgres / docker-0.docker / docker-1.docker
docker compose -f docker-compose-docker.yaml up -d
```
4. Access endpoints
  - Frontend: http://localhost:8080/
  - Backend: http://localhost:8000/docs

## Local Install (Development)

1. Clone the repository
```bash
git clone https://github.com/cookeem/teamclaw.git
cd teamclaw
```

2. Update configuration
  - For local deployment, use `docker-compose.yaml` to start services and `config.yaml` as the config file.
  - Edit `config.yaml` and update:
    - `models.providers`: model provider settings
    - `smtp`: email settings

3. Start services
```bash
# Generate Docker daemon certificates. Certificates will be stored in ./certs
sh docker_certs.sh

# Start postgres / docker-0.docker / docker-1.docker
docker compose up -d

# Start backend service
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Start frontend service
cd frontend
npm install
npm run dev
```

4. Access endpoints
  - Frontend: http://localhost:8080/frontend/
  - Backend: http://localhost:8000/docs

## Build Docker Image

```bash
# Clone code
git clone https://github.com/cookeem/teamclaw.git
cd teamclaw

# Build assets and image
cd frontend
npm install
npm run build
cd ..

export DOCKERHUB_USER=username
export VERSION=version
docker build \
  --platform linux/amd64 \
  -f docker/teamclaw/Dockerfile \
  --build-arg APT_MIRROR=mirrors.aliyun.com \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
  --build-arg PIP_TRUSTED_HOST=mirrors.aliyun.com \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
  -t ${DOCKERHUB_USER}/teamclaw:${VERSION} .
```
