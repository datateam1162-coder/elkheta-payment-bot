FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PORT=5055
ENV HEADLESS=1
EXPOSE 5055

CMD ["gunicorn", "--bind", "0.0.0.0:5055", "--workers", "1", "--threads", "4", "--timeout", "180", "app:app"]
