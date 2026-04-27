# OpsLens AI Node.js SDK

Push log events into the [OpsLens AI](https://opslensai.com) alert pipeline from Node.js or any TypeScript/JavaScript service.

## Installation

```bash
npm install opslens
# or
yarn add opslens
# or
pnpm add opslens
```

## Quickstart

```ts
import { OpsLens } from 'opslens';

const client = new OpsLens({ apiKey: 'opsl_...' });

const result = await client.ingest({
  service: 'payment-service',
  error: 'PaymentError: Stripe timeout after 30s',
  severity: 'p1',
  count: 47,
});

console.log(result.status);         // "queued"
console.log(result.errorSignature); // "a3f9c2..."
```

## Configuration

```ts
const client = new OpsLens({
  apiKey: 'opsl_...',              // or set OPSLENS_API_KEY env var
  baseUrl: 'https://opslensai.com', // override for self-hosted
  timeoutMs: 10_000,               // default: 10s
});
```

## Severity levels

| Value | Meaning            |
|-------|--------------------|
| `p0`  | Critical           |
| `p1`  | High               |
| `p2`  | Medium *(default)* |
| `p3`  | Low                |

## With metadata

```ts
await client.ingest({
  service: 'checkout-service',
  error: 'CardDeclinedError: card_id=card_abc123',
  severity: 'p2',
  count: 3,
  metadata: {
    userId: 'usr_789',
    region: 'us-east-1',
    deploy: 'v2.4.1',
  },
});
```

## Express error handler integration

```ts
import express from 'express';
import { OpsLens } from 'opslens';

const app = express();
const opslens = new OpsLens({ apiKey: process.env.OPSLENS_API_KEY });

// Add as last middleware
app.use((err: Error, req: express.Request, res: express.Response, next: express.NextFunction) => {
  opslens.ingest({
    service: 'my-api',
    error: `${err.name}: ${err.message}`,
    severity: 'p1',
    metadata: {
      path: req.path,
      method: req.method,
    },
  }).catch(console.error); // fire and forget

  res.status(500).json({ error: 'Internal server error' });
});
```

## Error handling

```ts
import { OpsLens, AuthenticationError, RateLimitError, OpsLensError } from 'opslens';

const client = new OpsLens({ apiKey: 'opsl_...' });

try {
  await client.ingest({ service: 'api', error: 'Something went wrong' });
} catch (err) {
  if (err instanceof AuthenticationError) {
    console.error('Check your API key');
  } else if (err instanceof RateLimitError) {
    console.error('Slow down or upgrade your plan');
  } else if (err instanceof OpsLensError) {
    console.error(`API error ${err.statusCode}: ${err.message}`);
  }
}
```

## CommonJS usage

```js
const { OpsLens } = require('opslens');

const client = new OpsLens({ apiKey: 'opsl_...' });
```

## Requirements

- Node.js 18+ (uses native `fetch`)
- No runtime dependencies
