airflow:
  docker compose -f docker-compose.postgres.yml -f docker-compose.airflow.yml up --build

project:
  docker compose -f docker-compose.postgres.yml -f docker-compose.airflow.yml -f docker-compose.chat.yml -f docker-compose.ui.yml up --build

project_no_airflow:
  docker compose -f docker-compose.postgres.yml -f docker-compose.chat.yml -f docker-compose.ui.yml up --build

build_mcp_image:
  docker build -f app/mcp_servers/Dockerfile app/mcp_servers -t hipposysai/airflow-mcp:latest
  docker push hipposysai/airflow-mcp:latest

build_mcp_image_local:
  docker build -f app/mcp_servers/Dockerfile app/mcp_servers -t hipposysai/airflow-mcp:local

open_web_tabs:
  open -a "Google Chrome" "http://localhost:8501" "http://localhost:8088"
