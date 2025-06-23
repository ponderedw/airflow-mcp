FROM apache/airflow:2.10.2-python3.10
USER airflow
# RUN pip install airflow-chat==0.1.0a1
RUN pip install airflow-schedule-insights==0.1.2
COPY local_airflow/ $AIRFLOW_HOME
