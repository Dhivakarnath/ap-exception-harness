# Mock ERP ledger. Object model mirrors QuickBooks Online so that swapping in a
# real connector is a connector change, not an architecture change (ADR-014).
FROM python:3.12-slim

WORKDIR /srv

# Pinned to match the backend stack.
RUN pip install --no-cache-dir \
    "fastapi==0.141.1" \
    "uvicorn[standard]==0.52.4" \
    "pydantic==2.13.5"

COPY app ./app

EXPOSE 8081

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
