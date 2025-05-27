FROM python:3.12-slim

COPY ./requirements.txt requirements.txt

RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY ./mcp_airflow.py mcp_airflow.py

EXPOSE 8000

CMD ["python", "mcp_airflow.py"]