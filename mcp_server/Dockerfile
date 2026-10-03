FROM python:3.11-slim

WORKDIR /workspace

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# MCP server listens over streamable HTTP on this port (endpoint: /mcp)
EXPOSE 5000

CMD ["python", "server.py"]
