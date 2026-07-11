FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY README.md ./
COPY consciousness ./consciousness
RUN pip install --no-cache-dir -e .

EXPOSE 8770
CMD ["consciousness-api"]
