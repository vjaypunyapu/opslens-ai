# OpsLens AI Python SDK

Push log events into the [OpsLens AI](https://opslensai.com) alert pipeline directly from your Python services.

## Installation

```bash
pip install opslens
```

## Quickstart

```python
from opslens import OpsLens

client = OpsLens(api_key="opsl_...")

result = client.ingest(
    service="payment-service",
    error="PaymentError: Stripe timeout after 30s",
    severity="p1",
    count=47,
)

print(result.status)          # "queued"
print(result.error_signature) # "a3f9c2..."
```

## Configuration

| Parameter  | Description                                           | Default                     |
|------------|-------------------------------------------------------|-----------------------------|
| `api_key`  | Your OpsLens API key. Also reads `OPSLENS_API_KEY`    | —                           |
| `base_url` | Override for self-hosted deployments                  | `https://opslensai.com`     |
| `timeout`  | Request timeout in seconds                            | `10`                        |

## Severity levels

| Value | Meaning   |
|-------|-----------|
| `p0`  | Critical  |
| `p1`  | High      |
| `p2`  | Medium (default) |
| `p3`  | Low       |

## Async usage

```python
import asyncio
from opslens import OpsLens

client = OpsLens(api_key="opsl_...")

async def main():
    result = await client.ingest_async(
        service="auth-service",
        error="TokenExpiredError: JWT expired 120s ago",
        severity="p2",
        count=8,
    )
    print(result.status)

asyncio.run(main())
```

## Exception hook integration

Automatically capture unhandled exceptions:

```python
import sys
from opslens import OpsLens

client = OpsLens(api_key="opsl_...")

def handle_exception(exc_type, exc_value, exc_tb):
    client.ingest(
        service="my-service",
        error=f"{exc_type.__name__}: {exc_value}",
        severity="p1",
        metadata={"unhandled": True},
    )
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = handle_exception
```

## With metadata

```python
client.ingest(
    service="checkout-service",
    error="CardDeclinedError: card_id=card_abc123",
    severity="p2",
    count=3,
    metadata={
        "user_id": "usr_789",
        "region": "us-east-1",
        "deploy": "v2.4.1",
    },
)
```

## Error handling

```python
from opslens import OpsLens, AuthenticationError, RateLimitError, OpsLensError

client = OpsLens(api_key="opsl_...")

try:
    client.ingest(service="api", error="Something went wrong")
except AuthenticationError:
    print("Check your API key")
except RateLimitError:
    print("Slow down or upgrade your plan")
except OpsLensError as e:
    print(f"API error {e.status_code}: {e}")
```
