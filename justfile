airflow:
  docker compose -f docker-compose.postgres.yml -f docker-compose.airflow.yml up --build

mcp_image:
  docker compose -f docker-compose.mcp.yml up --build
