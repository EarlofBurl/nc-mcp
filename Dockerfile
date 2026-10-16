FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8010

EXPOSE 8010

CMD ["python", "-m", "nc_mcp"]
