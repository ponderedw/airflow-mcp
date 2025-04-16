from mcp.server.fastmcp import FastMCP
import httpx
from typing import Any
import os

mcp = FastMCP("Airflow")


async def make_airflow_request(url: str, method: str = 'get',
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
            return response.json()
        except Exception as e:
            return e


@mcp.tool()
async def get_connections():
    """Fetch all available Airflow connections via the Airflow REST API"""
    return await make_airflow_request(url='/connections',
                                      params={'limit': 1000})


@mcp.tool()
async def get_dags():
    """Fetch all available Airflow DAGs and return the list of them"""
    return await make_airflow_request(url='/dags')


@mcp.tool()
async def get_dag(dag_id: str):
    """Get a simplified view of the DAG that retains all essential details"""
    return await make_airflow_request(url=f'/dags/{dag_id}/details')


@mcp.tool()
async def get_dags_tasks(dag_id: str):
    """Get all tasks for a DAG."""
    return await make_airflow_request(url=f'/dags/{dag_id}/tasks')


@mcp.tool()
async def get_dags_task(dag_id: str, task_id: str):
    """Get a simplified view of the task that retains all essential details"""
    return await make_airflow_request(url=f'/dags/{dag_id}/tasks/{task_id}')


@mcp.tool()
async def get_all_the_runs_for_dag(dag_id: str):
    """Get all the runs for a specific run"""
    return await make_airflow_request(url=f'/dags/{dag_id}/dagRuns')


@mcp.tool()
async def trigger_dag(dag_id: str):
    """Trigger specific dag"""
    return await make_airflow_request(url=f'/dags/{dag_id}/dagRuns',
                                      method='post', json={})


@mcp.tool()
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


if __name__ == "__main__":
    mcp.run(transport="stdio")
