FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY tender_app/ ./tender_app/
COPY gem-local-agent/ ./gem-local-agent/

EXPOSE 8000

ARG OPENAI_API_KEY
ENV OPENAI_API_KEY=$OPENAI_API_KEY

CMD ["sh", "-c", "cd tender_app && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
