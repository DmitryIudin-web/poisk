FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN mkdir -p /app/data
ENV SEARCH_DB=/app/data/searches.db
EXPOSE 8000
CMD ["uvicorn","universal_search.app:app","--host","0.0.0.0","--port","8000"]
