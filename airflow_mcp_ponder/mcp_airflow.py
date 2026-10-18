from mcp.server.fastmcp import FastMCP
import httpx
import re
import time
import base64
import json as _json
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from typing import Any, Callable
import os
from starlette.status import HTTP_401_UNAUTHORIZED
from starlette.types import ASGIApp, Receive, Scope, Send
import uvicorn

_jwt_cache: dict = {}  # base_url -> {token, expires_at}

mcp = FastMCP("Airflow", port='8000')
app = FastAPI(title="MCP Auth Test Server")

POST_MODE = os.environ.get('POST_MODE', 'false').lower() == 'true'
AIRFLOW_INSIGHTS_MODE = os.environ.get('AIRFLOW_INSIGHTS_MODE',
                                       'false').lower() == 'true'
AIRFLOW_VERSION = int(os.environ.get('airflow_version', '2'))
_API_PREFIX = '/api/v2' if AIRFLOW_VERSION >= 3 else '/api/v1'

_EXPECTED_TOKEN = os.environ.get('TOKEN')


class AuthMiddleware:
    """Pure ASGI auth middleware — safe for SSE streaming responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            auth = headers.get(b"authorization", b"").decode()
            token = auth.removeprefix("Bearer ").strip()
            if not auth.startswith("Bearer ") or token != _EXPECTED_TOKEN:
                response = JSONResponse(
                    status_code=HTTP_401_UNAUTHORIZED,
                    content={"detail": "Missing or invalid authorization header"},
                )
                await response(scope, receive, send)
                return
            print("[SERVER] Authentication successful")
        await self.app(scope, receive, send)


app.add_middleware(AuthMiddleware)


def filtered_tool(func: Callable) -> Callable:
    """
    Conditionally registers tools based on POST_MODE:
    - POST_MODE=false: only register functions starting with 'get'
    - POST_MODE=true: register all tools
    """
    function_name = func.__name__
    if AIRFLOW_INSIGHTS_MODE and function_name.startswith('insights'):
        return mcp.tool()(func)
    if POST_MODE and not function_name.startswith('insights'):
        return mcp.tool()(func)
    if function_name.startswith('get'):
        return mcp.tool()(func)
    return func


def v3_only_tool(func: Callable) -> Callable:
    """Register only when connected to Airflow 3+."""
    if AIRFLOW_VERSION >= 3:
        return filtered_tool(func)
    return func


async def _get_jwt_token(base_url: str, username: str, password: str) -> str:
    """Fetch and cache a JWT token for Airflow 3+ authentication."""
    cached = _jwt_cache.get(base_url, {})
    if cached.get('token') and cached.get('expires_at', 0) > time.time() + 60:
        return cached['token']

    async with httpx.AsyncClient() as client:
        response = await client.post(
            base_url.rstrip('/') + '/auth/token',
            json={'username': username, 'password': password},
            headers={'Content-Type': 'application/json'},
            timeout=30.0,
        )
        response.raise_for_status()
        token = response.json()['access_token']

    # Decode JWT expiry without verifying signature
    try:
        payload_b64 = token.split('.')[1]
        payload_b64 += '=' * (4 - len(payload_b64) % 4)
        payload = _json.loads(base64.b64decode(payload_b64))
        expires_at = payload.get('exp', time.time() + 86400)
    except Exception:
        expires_at = time.time() + 86400

    _jwt_cache[base_url] = {'token': token, 'expires_at': expires_at}
    return token


async def make_airflow_request(url: str, method: str = 'get',
                               return_text: bool = False,
                               api_prefix: str = None,
                               **kwargs) -> dict[str, Any] | None:
    if api_prefix is None:
        api_prefix = _API_PREFIX
    headers = {'Content-Type': 'application/json'}
    airflow_assistent_ai_conn = os.environ.get('AIRFLOW_ASSISTENT_AI_CONN')
    if airflow_assistent_ai_conn is not None:
        authentication, base_api = airflow_assistent_ai_conn.split('@')
        username, password = authentication.split(':', 1)
    else:
        base_api = os.environ.get('airflow_api_url',
                                  os.environ.get('airflow_base_url',
                                                 'http://localhost:8080'))
        username = os.environ.get(
            "airflow_username",
            os.environ.get('_AIRFLOW_WWW_USER_USERNAME', "airflow"))
        password = os.environ.get(
            "airflow_password",
            os.environ.get('_AIRFLOW_WWW_USER_PASSWORD', "airflow"))
    # Strip any existing /api/vN suffix before appending the correct one
    clean_base = re.sub(r'/api/v\d+/?$', '', base_api.rstrip('/'))
    full_base = clean_base + api_prefix

    if AIRFLOW_VERSION >= 3:
        try:
            token = await _get_jwt_token(clean_base, username, password)
            headers['Authorization'] = f'Bearer {token}'
        except Exception as e:
            return f"JWTAuthError: {e}"
        auth = None
    else:
        auth = httpx.BasicAuth(username=username, password=password)

    async with httpx.AsyncClient(auth=auth) as client:
        try:
            req_method = getattr(client, method)
            response = await req_method(full_base + url,
                                        headers=headers, timeout=120.0,
                                        **kwargs)
            response.raise_for_status()
            return response.json() if not return_text else response.text
        except Exception as e:
            return f"{type(e).__name__}: {e}"


@filtered_tool
async def get_connections():
    """Fetch all available Airflow connections via the Airflow REST API"""
    return await make_airflow_request(url='/connections',
                                      params={'limit': 1000})


@filtered_tool
async def get_dags():
    """Fetch all available Airflow DAGs and return the list of them"""
    return await make_airflow_request(url='/dags')


@filtered_tool
async def get_dag(dag_id: str):
    """Get a simplified view of the DAG that retains all essential details"""
    # Airflow 3 removed /details; GET /dags/{dag_id} returns full details directly
    endpoint = f'/dags/{dag_id}' if AIRFLOW_VERSION >= 3 else f'/dags/{dag_id}/details'
    return await make_airflow_request(url=endpoint)


@filtered_tool
async def get_dags_tasks(dag_id: str):
    """Get all tasks for a DAG."""
    return await make_airflow_request(url=f'/dags/{dag_id}/tasks')


@filtered_tool
async def get_dags_task(dag_id: str, task_id: str):
    """Get a simplified view of the task that retains all essential details"""
    return await make_airflow_request(url=f'/dags/{dag_id}/tasks/{task_id}')


@filtered_tool
async def get_all_the_runs_for_dag(dag_id: str):
    """Get the most recent runs for a specific DAG, newest first"""
    # Airflow 3 renamed execution_date to logical_date
    order_field = '-logical_date' if AIRFLOW_VERSION >= 3 else '-execution_date'
    return await make_airflow_request(
        url=f'/dags/{dag_id}/dagRuns',
        params={'order_by': order_field, 'limit': 25},
    )


@filtered_tool
async def trigger_dag(dag_id: str):
    """Trigger specific dag"""
    # Airflow 3 requires logical_date (nullable); Airflow 2 accepts an empty body
    body = {'logical_date': None} if AIRFLOW_VERSION >= 3 else {}
    return await make_airflow_request(url=f'/dags/{dag_id}/dagRuns',
                                      method='post', json=body)


@filtered_tool
async def get_task_instances(dag_id: str, dag_run_id: str):
    """
    Get all task instances for a specific DAG run.
    Args:
        - dag_id: The DAG identifier
        - dag_run_id: The DAG run identifier
          (e.g., 'scheduled__2025-04-15T00:00:00+00:00')
    Returns:
        List of task instances with state, duration, and timing information
    """
    return await make_airflow_request(
        url=f'/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances'
    )


@filtered_tool
async def get_variables():
    """Fetch all Airflow variables"""
    return await make_airflow_request(url='/variables', params={'limit': 1000})


@filtered_tool
async def get_variable(variable_key: str):
    """Get a specific Airflow variable by key"""
    return await make_airflow_request(url=f'/variables/{variable_key}')


@filtered_tool
async def get_pools():
    """Fetch all Airflow resource pools and their slot usage"""
    return await make_airflow_request(url='/pools', params={'limit': 1000})


@filtered_tool
async def get_all_the_runs(dag_ids: str = None, start_date_gte: str = None,
                           start_date_lte: str = None, states: str = None):
    """
   Retrieve filtered DAG runs across multiple DAG IDs with various criteria.
   Args:
       - dag_ids: Comma-separated list of DAG identifiers
        (e.g., 'load_ticket_sales,transform_sales_aggregator').
                Leave this parameter blank '' to include all DAGs.
       - start_date_gte: Filter runs that started on or after
        specified timestamp (ISO 8601 format, e.g., '2025-04-15T13:23:49.079Z')
                       Leave this parameter blank '' to omit this filter.
       - start_date_lte: Filter runs that started on or before
        specified timestamp (ISO 8601 format, e.g., '2025-04-15T13:23:49.079Z')
                       Leave this parameter blank '' to omit this filter.
       - states: Comma-separated list of execution states to include
        ('failed', 'success', 'running').
               Leave this parameter blank '' to include all states.
   Returns:
       Dictionary containing the filtered DAG runs data
   """
    json_params = {}
    if dag_ids:
        json_params['dag_ids'] = dag_ids.split(',')
    if states:
        json_params['states'] = states.split(',')
    if start_date_gte:
        json_params['start_date_gte'] = start_date_gte
    if start_date_lte:
        json_params['start_date_lte'] = start_date_lte
    return await make_airflow_request(url='/dags/~/dagRuns/list',
                                      method='post', json={
                                          'page_limit': 10000,
                                          **json_params
                                      })


@filtered_tool
async def change_dags_pause_status(dag_id: str = '~',
                                   paused_status: bool = True):
    """
   Change the pause status for one or more DAGs via the Airflow API.
   Args:
       - dag_id: The identifier of the DAG to pause/unpause
        (e.g., 'extract_sales_data').
                Use '~' to apply the change to all DAGs in the system.
       - paused_status: Boolean flag to control the pause state
        Set to True to pause the DAG(s).
        Set to False to unpause the DAG(s).
   Returns:
       Dictionary containing the result of the pause/unpause operation
       with status and details of the affected DAG(s)
    """
    if dag_id == '~':
        return await make_airflow_request(url='/dags',
                                          method='patch',
                                          params={'dag_id_pattern': dag_id},
                                          json={'is_paused': paused_status})
    else:
        return await make_airflow_request(url=f'/dags/{dag_id}',
                                          method='patch',
                                          json={'is_paused': paused_status})


@filtered_tool
async def get_dags_script(file_token: str):
    """
    Retrieve the source code of a specific DAG via the Airflow API.

    Args:
        - file_token: A unique identifier token for the DAG script file,
                     obtained from the get_dag(dag_id) method response.
                    This token provides secure access to the DAG's source code.
                    Before using this function, call get_dag(dag_id) method.

    Returns:
        Text containing the DAG's source code and related metadata.
        The response typically includes the full script that defines the DAG.
    """
    return await make_airflow_request(url=f'/dagSources/{file_token}',
                                      return_text=True)


# ---------------------------------------------------------------------------
# Airflow 3+ only tools
# ---------------------------------------------------------------------------

@v3_only_tool
async def get_dag_versions(dag_id: str):
    """
    Get all versions of a specific DAG. Airflow records a new version each time
    the DAG file changes, letting you track schema evolution over time.
    Airflow 3+ only.
    Args:
        - dag_id: The DAG identifier
    Returns:
        List of DAG versions with version number, bundle version, and creation time
    """
    return await make_airflow_request(url=f'/dags/{dag_id}/dagVersions')


@v3_only_tool
async def get_dag_version(dag_id: str, version_number: int):
    """
    Get a specific version of a DAG. Airflow 3+ only.
    Args:
        - dag_id: The DAG identifier
        - version_number: The version number to retrieve
    Returns:
        DAG version details including bundle version and creation time
    """
    return await make_airflow_request(
        url=f'/dags/{dag_id}/dagVersions/{version_number}'
    )


@v3_only_tool
async def get_assets():
    """
    Fetch all Assets (formerly Datasets) registered in Airflow. Assets represent
    logical data dependencies between DAGs — producing DAGs emit assets, consuming
    DAGs are triggered when those assets are updated. Airflow 3+ only.
    Returns:
        List of assets with URIs, names, and consuming/producing DAG information
    """
    return await make_airflow_request(url='/assets', params={'limit': 1000})


# ---------------------------------------------------------------------------
# Schedule Insights tools (requires AIRFLOW_INSIGHTS_MODE=true)
# ---------------------------------------------------------------------------

@filtered_tool
async def insights_get_dags_next_run(dag_id: str):
    """
    Retrieve the next scheduled or triggered run metadata of a specific DAG via the Airflow API.

    Args:
        - dag_id: A unique identifier string for the DAG whose upcoming run details are being requested.
                  This DAG must already exist in the Airflow environment and be available for execution.

    Returns:
        A JSON-compatible dictionary containing the DAG's upcoming run metadata:
            - start_time: The expected start time of the next DAG run.
            - end_time: The expected end time of the next DAG run.
            - duration: Estimated duration between the start and end of the run.
            - description: A textual description of the DAG's triggering mechanism,
                           which may describe a schedule, asset dependency chain,
                           or custom trigger configuration.
            - run_type: The type of trigger for the next DAG run.
                        This can be either 'scheduled' or 'dataset/trigger'.

        Important Note: If the predicted run is not scheduled (i.e., it is triggered by an asset
        or custom trigger), the prediction may be inaccurate as it is based solely on metadata
        from previous runs.
    """
    return await make_airflow_request(url='/schedule_insights/get_next_future_run_json',
                                      params={'dag_id': dag_id},
                                      return_text=True,
                                      api_prefix='')


def main():
    if os.environ.get('TOKEN') and \
       os.environ.get('TRANSPORT_TYPE', 'stdio') == 'sse':
        app.mount("/", mcp.sse_app())
        uvicorn.run(app, host="0.0.0.0", port=8000)
    else:
        transport_type = os.environ.get('TRANSPORT_TYPE')
        if transport_type == 'sse':
            mcp.run(transport="sse")
        else:
            mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
