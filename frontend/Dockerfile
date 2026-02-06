FROM python:3.12-slim

RUN useradd -m -s /bin/bash frontend

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

USER frontend

EXPOSE 5000

CMD ["python3", "app.py"]
