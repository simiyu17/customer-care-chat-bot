import os
import re
import json
import uuid
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import app as graph_agent
from langchain_core.messages import HumanMessage

app = FastAPI(title="Agentic Chatbot Gateway (OpenAI Exclusive)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ready-made markdown tables returned by the loan tools (see mcp_server/server.py `_respond`)
DISPLAY_BLOCK = re.compile(r"<display>\s*(.*?)\s*</display>", re.S)

class ChatPayload(BaseModel):
    message: str
    thread_id: str | None = None  # Conversation id; reuse it to keep chat history across turns

async def agent_stream_generator(user_message: str, thread_id: str):
    input_state = {"messages": [HumanMessage(content=user_message)]}
    config = {"configurable": {"thread_id": thread_id}}
    
    try:
        # We listen to stream events across our LangGraph canvas execution steps
        async for event in graph_agent.astream_events(input_state, config=config, version="v2"):
            kind = event["event"]
            node_name = event.get("metadata", {}).get("langgraph_node", "")

            # 1. Capture system events to signal background activity to your React UI
            if kind == "on_chain_start" and node_name in ["standard_tools", "mcp_executor"]:
                status_msg = f"Running lookups: Fetching verified data from {node_name}..."
                yield f"data: {json.dumps({'type': 'status', 'content': status_msg})}\n\n"

            # 2. Send loan tool tables straight to the UI so their layout never depends on the model
            elif kind == "on_chain_end" and event.get("name") == "mcp_executor":
                for message in (event["data"].get("output") or {}).get("messages", []):
                    match = DISPLAY_BLOCK.search(str(message.content))
                    if match:
                        tables = match.group(1) + "\n\n"
                        yield f"data: {json.dumps({'type': 'text', 'content': tables})}\n\n"

            # 3. Capture and filter chat token generation layers
            elif kind == "on_chat_model_stream" and node_name == "agent":
                # Extract the incremental data chunk packet securely
                chunk_data = event["data"].get("chunk")
                if chunk_data and hasattr(chunk_data, "content"):
                    content = chunk_data.content
                    
                    # Skip only empty chunks (tool-call deltas). Whitespace-only chunks carry the
                    # spaces and line breaks that markdown tables and lists depend on.
                    if content:
                        yield f"data: {json.dumps({'type': 'text', 'content': content})}\n\n"
                    
            await asyncio.sleep(0.01)
            
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'content': f'Stream runtime error: {str(e)}'})}\n\n"

@app.post("/api/chat/stream")
async def chat_stream_endpoint(payload: ChatPayload):
    return StreamingResponse(
        agent_stream_generator(payload.message, payload.thread_id or str(uuid.uuid4())), 
        media_type="text/event-stream"
    )
