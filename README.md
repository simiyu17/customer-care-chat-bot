# Customer Care Chat Bot

A customer-care assistant that answers two kinds of questions:

1. **Sales policy questions**, answered from the company's policy documents (for example, *"What does the policy say about protecting customer data?"*).
2. **Client loan questions**, answered live from Fineract (for example, *"Give me details of loans for a client with Id : 345544?"*, then *"Show me the repayment schedule for loan 139"*).

It refuses anything outside those two areas, such as general knowledge, politics or trivia.

## How it works

```
 Browser                                                               
 ┌───────────────────────┐                                             
 │ React chat UI         │  POST /api/chat/stream {message, thread_id} 
 │ chat_ui/  (:3000)     │ ──────────────────────────────────────────┐ 
 │                       │ ◄── stream of status / text / error events │ 
 └───────────────────────┘                                          │ 
                                                                    ▼ 
 ┌──────────────────────────────────────────────────────────────────────┐
 │ FastAPI gateway  app/main.py  (:8000)                                │
 │   runs the LangGraph agent and streams its tokens back to the UI     │
 │                                                                      │
 │   LangGraph agent  app/agent.py                                      │
 │   ┌──────────────────────────────┐                                   │
 │   │ agent node: GPT-4o           │  decides: answer, or call a tool  │
 │   │ + guard prompt (scope rules) │                                   │
 │   └──────┬───────────────┬───────┘                                   │
 │          │               │        tool result goes back to agent     │
 │          ▼               ▼                                           │
 │   standard_tools    mcp_executor                                     │
 │   policy search     loan tools ──────────────────────────────┐       │
 │          │                                                   │       │
 │   app/rag_engine.py                                          │       │
 │   LlamaIndex vector index over app/data/policies/*.pdf       │       │
 └──────────────────────────────────────────────────────────────┼───────┘
                                                                │ MCP over HTTP
                                                                ▼ (:5000/mcp)
 ┌──────────────────────────────────────────────────────────────────────┐
 │ MCP server  mcp_server/server.py                                     │
 │   get_client_loans(client_id)  → GET /clients/{id}/accounts          │
 │   get_loan_details(loan_id)    → GET /loans/{id}?associations=all    │
 │   Calls Fineract with a bearer token + tenant header and shrinks the │
 │   response to the useful fields before returning it                  │
 └──────────────────────────────────────────────────────────────────────┘
                                   │ HTTPS
                                   ▼
                     Fineract (https://localhost:8443/fineract-provider/api/v1)
```
<img width="865" height="856" alt="Screenshot 2026-10-07 at 23 52 36" src="https://github.com/user-attachments/assets/4fdca5a7-6749-493a-9e73-f5f58832ef8c" />

<img width="775" height="685" alt="Screenshot 2026-10-07 at 23 53 00" src="https://github.com/user-attachments/assets/54bc7318-9b5a-43c9-af5b-e7a677ee28c7" />

<img width="782" height="664" alt="Screenshot 2026-10-07 at 23 53 15" src="https://github.com/user-attachments/assets/3202d09e-c28e-4fd0-af54-0e8e8087c77f" />




### What happens to one question

1. The UI sends the message and a `thread_id` to `POST /api/chat/stream`. The `thread_id` is created once per page load, so the bot remembers the conversation until you refresh.
2. The **agent node** sends the conversation to GPT-4o along with a guard prompt and three tool definitions. GPT-4o either answers directly or asks for one tool.
3. The **router** sends the tool call to the right place:
   - `query_sales_policy_rag` goes to **standard_tools**. That node searches the vector index and returns the top 3 matching policy excerpts, each with its file name and page.
   - `get_client_loans` and `get_loan_details` go to **mcp_executor**. That node calls the MCP server, which calls Fineract.
4. The tool result goes back to the agent node, and GPT-4o writes the answer from it. Steps 2 to 4 repeat if more lookups are needed.
5. While this runs, the gateway streams events back to the UI. `status` events produce the "Running lookups…" lines, `text` events carry the answer as it's written, and `error` events report failures.

### Components

| Folder | What it is | Key libraries |
|---|---|---|
| `app/` | FastAPI gateway, LangGraph agent, policy search (RAG) | FastAPI, LangGraph, LangChain OpenAI, LlamaIndex, MCP client |
| `mcp_server/` | MCP server that wraps the Fineract loans API | MCP Python SDK (`FastMCP`), httpx |
| `chat_ui/` | Chat front end | React 19, Vite 8, Tailwind 4, react-markdown |
| `app/data/policies/` | Source policy documents (`.pdf`, `.md`, `.txt`) | |
| `app/storage/vector_store/` | Saved vector index, generated automatically | |

### Policy search (RAG)

- At startup, `rag_engine.py` splits every document in `app/data/policies/` into chunks of 512 tokens (50-token overlap) and embeds them with OpenAI `text-embedding-3-small`.
- The index is saved to `app/storage/vector_store/` along with a record of each document's name, size and modified time. On the next start it's loaded from disk. If any document was added, changed or removed, the index is rebuilt automatically.
- To add a policy, drop the file into `app/data/policies/` and restart the API.

### Loan lookups (MCP)

The MCP server doesn't pass Fineract's raw JSON to the model. Each tool returns two parts:

- a `<display>` block of ready-made markdown tables, built in Python so the layout and number formatting are always the same. The agent shows it unchanged, and the UI renders it as tables.
- a `<data>` block with a compact JSON summary, which the agent uses to answer follow-up questions.

The display tables are:

- **`get_client_loans`**: one table with Loan ID, Product, Status, Principal, Paid and Maturity.
- **`get_loan_details`**: three tables: loan details, repayment schedule, and transactions.

The JSON summaries contain:

- **`get_client_loans`**: for each loan, the loan ID, account number, product, loan type, status, arrears flag, principal, amount paid, and the main dates (submitted, approved, disbursed, maturity, closed).
- **`get_loan_details`**: totals (expected, repaid, outstanding, paid in advance or late), delinquency, delivery, the repayment schedule, and transactions with payment type and receipt number.

Accrual entries, staff usernames and payers' phone numbers are dropped. IDs must be numeric. "Not found" and expired-token errors are returned as plain messages that the bot passes on to the user.

## Getting it running

### Prerequisites

- An **OpenAI API key** with access to `gpt-4o` and `text-embedding-3-small`
- A running **Fineract** instance and a **bearer token** for it (a Keycloak access token)
- For Docker: **Docker Desktop** (or Docker Engine with Compose v2)
- For running without Docker: **Python 3.11+** and **Node.js 24 LTS** (pinned in `.nvmrc`, so run `nvm use` in the project folder)

### 1. Configure secrets

Copy the example file and fill it in:

```bash
cp .env.example .env
```

| Variable | Used by | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | app | OpenAI key for GPT-4o and embeddings |
| `BEARER_TOKEN` | mcp_server | Fineract access token. Keycloak tokens expire, so refresh it when the bot reports rejected credentials |
| `API_URL` | mcp_server (local run) | Fineract API base, e.g. `https://localhost:8443/fineract-provider/api/v1/` |
| `FINERACT_DOCKER_API_URL` | mcp_server (Docker) | The same API as seen from inside a container. Default: `https://host.docker.internal:8443/fineract-provider/api/v1/` |
| `FINERACT_TENANT_ID` | mcp_server | Sent as the `Fineract-Platform-TenantId` header. Default: `default` |
| `FINERACT_VERIFY_SSL` | mcp_server | Set to `false` for a local Fineract with a self-signed certificate |

`.env` is listed in `.gitignore`. Never commit it, and never put keys directly in `docker-compose.yml`.

### 2a. Run with Docker (recommended)

```bash
docker compose up --build
```

Then open **http://localhost:3000**.

| Service | URL | Notes |
|---|---|---|
| `frontend-ui` | http://localhost:3000 | nginx serving the built React app |
| `agent-api` | http://localhost:8000 | API docs at http://localhost:8000/docs |
| `mcp-server` | `http://mcp-server:5000/mcp` | Only reachable from other containers |

The first start builds the vector index, which makes a few embedding calls. Later starts reuse it, because `app/storage` is mounted as a volume.

### 2b. Run locally without Docker

Use three terminals, all starting in the project root.

**Terminal 1: MCP server**

```bash
cd mcp_server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
set -a; source ../.env; set +a
python server.py                      # listens on http://localhost:5000/mcp
```

**Terminal 2: API and agent.** Run it from inside `app/`, because the policy and index paths are relative to that folder.

```bash
cd app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
set -a; source ../.env; set +a
uvicorn main:app --reload --port 8000
```

**Terminal 3: UI**

```bash
cd chat_ui
npm install
npm run dev                           # http://localhost:3000
```

When run locally, the API finds the MCP server at `http://localhost:5000/mcp` and the UI calls the API at `http://localhost:8000`. You only need to change these if you move things:

| Variable | Where | Default |
|---|---|---|
| `MCP_SERVER_URL` | app | `http://localhost:5000/mcp` |
| `ALLOWED_ORIGINS` | app (comma-separated CORS origins) | `http://localhost:3000` |
| `VITE_API_URL` | chat_ui (build time) | `http://localhost:8000` |

### 3. Try it

- *What does the responsible marketing and sales policy say about data protection?*
- *Give me details of loans for a client with Id : 2?*
- *Show me the repayment schedule and transactions for loan 139.*
- *Who won the World Cup?* (should be politely refused)

You can also call the API directly:

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message": "Give me details of loans for a client with Id : 2?", "thread_id": "demo-1"}'
```

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| *"Loan lookup service is unavailable"* | The MCP server isn't running, or `MCP_SERVER_URL` is wrong. Start `mcp_server/server.py` and check port 5000. |
| *"Failed to connect to the loans service … certificate verify failed"* | Fineract uses a self-signed certificate. Set `FINERACT_VERIFY_SSL=false`. |
| *"Failed to connect to the loans service"* (in Docker) | Inside the container, `localhost` means the container itself. Point `FINERACT_DOCKER_API_URL` at `host.docker.internal` or at Fineract's real host. |
| *"rejected our credentials (HTTP 401/403)"* | `BEARER_TOKEN` has expired or lacks permission. Get a new token and restart the MCP server. |
| *"No client was found with ID …"* | Fineract returned 404. Check the ID, and check `FINERACT_TENANT_ID`. |
| The bot doesn't know about a new policy | Make sure the file is a `.pdf`, `.md` or `.txt` file in `app/data/policies/`, then restart the API. |
| The browser shows CORS errors | Add the UI's origin to `ALLOWED_ORIGINS`. |
| The bot forgets earlier messages | Memory lasts until the page is refreshed, and is lost when the API restarts (it's held in memory only). |

## Limitations and security notes

- **The chat has no login.** Anyone who can reach the UI can look up any client's loans, using whatever permissions `BEARER_TOKEN` grants. Add authentication, and use a read-only Fineract account, before letting anyone else use it.
- Conversation memory is held in the API process only (LangGraph `MemorySaver`). Use a persistent checkpointer if you need history to survive restarts.
- The agent makes one tool call per step (`parallel_tool_calls=False`), so a question that needs several lookups takes several round trips.
- The policy index is loaded (or built) when the API starts, so the API won't start without `OPENAI_API_KEY` set.
