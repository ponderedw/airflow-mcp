from mcp.server.fastmcp import FastMCP
import httpx
from fastapi import FastAPI, HTTPException, Request
from typing import Any, Callable
import os
from starlette.status import HTTP_401_UNAUTHORIZED
import uvicorn

mcp = FastMCP("Airflow", port='8000', description='Airflow MCP')
app = FastAPI(title="MCP Auth Test Server")


POST_MODE = os.environ.get('POST_MODE', 'false').lower() == 'true'


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """
    Authentication middleware to verify JWT tokens.

    Args:
        request: FastAPI request object
        call_next: Next middleware or endpoint function

    Returns:
        Response from the next middleware or endpoint

    Raises:
        HTTPException: If authentication fails
    """
    # Check for Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header"
        )

    # Extract and verify token
    token = auth_header.split(" ")[1]
    if token == os.environ.get('TOKEN'):
        print("[SERVER] Authentication successful")
        response = await call_next(request)
    else:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    return response


def filtered_tool(func: Callable) -> Callable:
    """
    Custom decorator that conditionally registers tools based on POST_MODE:
    - If POST_MODE is False/None: Only register functions starting with 'get'
    - If POST_MODE is True: Register all tools
    """
    function_name = func.__name__
    # If POST_MODE is True, register all tools
    if POST_MODE:
        return mcp.tool()(func)
    # If POST_MODE is False/None, only register functions starting with 'get'
    if function_name.startswith('get'):
        return mcp.tool()(func)
    # Return the original function without registering it as a tool
    return func


async def make_airflow_request(url: str, method: str = 'get',
                               return_text: bool = False,
                               **kwargs) -> dict[str, Any] | None:
    headers = {
        'Content-Type': 'application/json'
    }
    base_api = os.environ.get('airflow_api_url',
                              'http://airflow-webserver:8080/api/v1')
    auth = httpx.BasicAuth(username=os.environ.get("airflow_username",
                                                   "airflow"),
                           password=os.environ.get("airflow_password",
                                                   "airflow"))
    async with httpx.AsyncClient(auth=auth) as client:
        try:
            req_method = getattr(client, method)
            response = await req_method(base_api + url,
                                        headers=headers, timeout=30.0,
                                        **kwargs)
            response.raise_for_status()
            return response.json() if not return_text else response.text
        except Exception as e:
            return e


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
    return await make_airflow_request(url=f'/dags/{dag_id}/details')


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
    """Get all the runs for a specific run"""
    return await make_airflow_request(url=f'/dags/{dag_id}/dagRuns')


@filtered_tool
async def trigger_dag(dag_id: str):
    """Trigger specific dag"""
    return await make_airflow_request(url=f'/dags/{dag_id}/dagRuns',
                                      method='post', json={})


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
        dag_ids = dag_ids.split(',')
        json_params['dag_ids'] = dag_ids
    if states:
        states = states.split(',')
        json_params['states'] = states
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
                                          json={
                                                'is_paused': paused_status
                                            })
    else:
        return await make_airflow_request(url=f'/dags/{dag_id}',
                                          method='patch', json={
                                            'is_paused': paused_status
                                          })


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

if os.environ.get('TOKEN'):
    app.mount("/", mcp.sse_app())


if __name__ == "__main__":
    if os.environ.get('TOKEN'):
        uvicorn.run(app, host="0.0.0.0", port=8000)
    else:
        transport_type = os.environ.get('TRANSPORT_TYPE')
        if transport_type == 'sse':
            mcp.run(transport="sse")
        else:
            mcp.run(transport="stdio")
