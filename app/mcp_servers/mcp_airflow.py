from mcp.server.fastmcp import FastMCP
import httpx
from typing import Any
import os

mcp = FastMCP("Airflow")


async def make_airflow_request(url: str, **kwargs) -> dict[str, Any] | None:
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
            response = await client.get(base_api + url,
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


# @mcp.tool()
# async def get_dags():
#     """Fetch all available Airflow DAGs and return the list of them"""
#     return await make_airflow_request(url='/dags',
#                                       params={'limit': 1000})


@mcp.tool()
async def get_dag(dag_id: str):
    """Get a simplified view of the DAG that retains all essential details"""
    return await make_airflow_request(url=f'/dags/{dag_id}/details')


if __name__ == "__main__":
    mcp.run(transport="stdio")
