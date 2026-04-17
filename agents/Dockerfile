# Build from repository root (so `agents` is importable):
#   docker build -f agents/Dockerfile -t social-army-gate .
#
# Override module per service, e.g.:
#   docker run -e AGENT_MODULE=agents.orchestrator.orchestrator_agent ...
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

COPY agents/requirements.txt /tmp/agents-requirements.txt
RUN pip install --no-cache-dir -r /tmp/agents-requirements.txt

COPY agents /app/agents

ARG AGENT_MODULE=agents.gate.gate_agent
ENV AGENT_MODULE=${AGENT_MODULE}

EXPOSE 8001 8002 8003 8004 8005 8010

CMD ["sh", "-c", "exec python -m \"${AGENT_MODULE}\""]
