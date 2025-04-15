import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.server.llm import LLMAgent

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools


chat_router = APIRouter()


class ChatRequest(BaseModel):
    message: str


def get_user_chat_config(session_id: str) -> dict:
    return {'configurable': {'thread_id': session_id}}


@chat_router.post("/new")
async def new_chat(request: Request):
    """Create a new chat session."""
    request.session['chat_session_id'] = f'user_{uuid.uuid4()}'
    return {'results': 'ok'}


@chat_router.post("/ask")
async def chat(
    request: Request,
    chat_request: ChatRequest,
):
    if 'chat_session_id' not in request.session:
        await new_chat(request)
    session_id = request.session['chat_session_id']
    print('sldnsfnlksdf')
    print(session_id)
    # Get the user chat configuration and the LLM agent.
    user_config = get_user_chat_config(session_id)

    async def stream_agent_response():
        """Stream the agent's response to the client."""
        server_params = StdioServerParameters(
            command="python",
            args=["/code/app/mcp_servers/mcp_airflow.py"],
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                # Initialize the connection
                await session.initialize()

                # Get tools
                tools = await load_mcp_tools(session)
                async with LLMAgent(tools=tools) as llm_agent:
                    async for chat_msg in llm_agent.astream_events(
                       chat_request.message, user_config):
                        yield chat_msg.content

    # Return the agent's response as a stream of JSON objects.
    return StreamingResponse(stream_agent_response(),
                             media_type='application/json')
