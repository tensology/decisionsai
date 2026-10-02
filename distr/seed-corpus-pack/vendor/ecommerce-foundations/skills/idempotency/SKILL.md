---
name: idempotency
description: Use when designing any operation that may be retried — HTTP POST endpoints, payment requests, webhook consumers, message handlers, async job enqueues, integration calls. Load before deciding whether an operation needs an idempotency key, how to generate the key, how long to keep replay state, or what to return on replay. Vocabulary (idempotency key, idempotent operation, replay, CIT, MIT) is established here; cross-references domain-vocabulary, payment-flows, webhooks, order-lifecycle.
---

# Idempotency

The property that makes retries safe. Without it, every distributed system slowly accumulates double charges, duplicate orders, repeated emails, and rebuilt state — because every layer (clients, load balancers, queues, processors) retries on transient failure, and most operations are not naturally safe to repeat.

This skill distinguishes the two things people call "idempotency," frames the implementation choices, and pins down the failure modes that look like correct behavior until they aren't.

Read the `domain-vocabulary` skill first. This skill cross-references `payment-flows`, `webhooks`, `order-lifecycle`, and `cart-lifecycle`.

## Vocabulary

**Idempotent operation** — An operation whose final effect is the same whether it executes once or many times. `PUT /resource/123` with the same body is naturally idempotent — the resource ends in the same state after one call or ten. ([RFC 7231 §4.2](https://datatracker.ietf.org/doc/html/rfc7231#section-4.2))

**Idempotency key** — A client-supplied token that lets the server detect a retry of an operation that is *not* naturally idempotent, and return the original response instead of executing the operation again. The mechanism that turns a non-idempotent operation into a safely-retryable one. ([Stripe](https://docs.stripe.com/api/idempotent_requests))

**Replay** — A second arrival of the same request, identified by the idempotency key. The server's job is to *not* re-execute the operation but to return the *original* outcome.

**Replay window** — The time during which the server retains the original outcome for a given key. After the window, the key is forgotten and a new request with the same key is treated as a fresh operation.

**Effect** — The observable consequences of an operation: state changes, charges, emails, events emitted. Idempotency is about ensuring effects happen *at most once* per logical request, not about HTTP response equality.

**Natural idempotency** — Idempotency provided by the operation's semantics, not by a key. `DELETE`, `PUT`, read-only `GET`, and `SET state = X` (vs. `+= 1`) are naturally idempotent.

**Idempotency by domain key** — Replacing the client's idempotency key with a domain identifier the operation already needs (order id, line item id, event id). Eliminates the separate key; tightly couples idempotency to domain modeling.

**At-least-once delivery** — A messaging guarantee that a message will arrive one or more times. Default for most message buses, webhook senders, and retry-capable HTTP clients. Consumer-side idempotency is the only defense.

**Exactly-once processing** — The property the consumer achieves by combining at-least-once delivery with idempotent processing. The system *delivers* at least once and *acts* exactly once.

**Concurrent replay** — The case where two retries arrive in flight simultaneously. The idempotency mechanism must handle this without racing — either by serializing on the key or by ensuring the operation itself is concurrency-safe.

## Canonical model

The structure most production systems converge on:

```
1. Client generates a unique key per logical operation
       (UUIDv4 typical; deterministic key for clear retry semantics)

2. Client sends the operation with the key
       (HTTP: `Idempotency-Key: <key>` header; message: in metadata)

3. Server, on receiving:
       a. Look up the key in a replay store.
          - If found and complete → return the stored response.
          - If found and in-flight → block until complete or return 409.
          - If not found → claim the key (insert with status=in_progress).
       b. Execute the operation.
       c. Persist the response next to the key (status=complete).
       d. Return the response.

4. On replay, step (3a) short-circuits the work.

5. After the replay window, the key entry is reaped.
```

Storage shape:

```
IdempotencyKey
  ├── key (the client-supplied or domain-derived token)
  ├── scope (tenant, customer, endpoint — see decision framework)
  ├── status (in_progress | complete | failed)
  ├── request_fingerprint (hash of the request body; see below)
  ├── response (serialized HTTP response or domain outcome)
  ├── created_at
  └── expires_at
```

Invariants:

1. **The key uniquely identifies a logical operation.** Two different operations must never share a key; the same operation across retries must always have the same key.
2. **The first response is the response.** Once the first execution completes, every replay returns the same response — even if circumstances would now produce a different result.
3. **Request fingerprint check on replay.** If the replay's request body differs from the original, return an error. The same key with a different body indicates a client bug, not a retry.
4. **Concurrent replays do not race.** The mechanism handles two-arrivals-at-once deterministically (block, return-in-progress, or return-stale).
5. **Failures store outcomes too.** If the operation failed deterministically (validation error), the failure response is stored and replayed. If it failed transiently (network), the key may need to allow re-execution — design this explicitly.

## Variation dimensions

- **Operation type** — HTTP request, message handler, webhook consumer, async job, scheduled task. Each has different retry sources and key plumbing.
- **Retry source** — Client retry (browser, mobile app, server), proxy/load-balancer retry, processor retry, message broker retry. The system may face all four simultaneously.
- **Effect surface** — Local (DB write only), single-external (one payment processor call), multi-external (DB + payment + email + webhook publish). More effects means harder atomicity guarantees.
- **Operation duration** — Sub-second (simple write), seconds (external API), minutes (workflow with steps). Replay window and concurrent-replay handling depend.
- **Trust boundary** — Internal calls (system-controlled) vs external (third-party clients). External demands stricter key scoping (per-tenant or per-customer).

## Decision frameworks

### Identify which operations need idempotency

Apply this order:

1. **Can the operation be made naturally idempotent?** Reshape the operation: `PUT` over `POST`; `SET status = paid` over `IF status = pending THEN status = paid`; create-or-update over create. Natural idempotency beats key-based idempotency at every level.
2. **Is there a domain identifier the operation already needs?** Reuse it as the idempotency key. Order placement keyed on a client-generated `order_attempt_id`; webhook consumer keyed on the event id; queue consumer keyed on the message id.
3. **Otherwise, require a client-supplied idempotency key.** For external APIs, document the header; for internal calls, make the key parameter mandatory.

Naturally non-idempotent operations that almost always need keys:

- Payment intent creation / capture / refund.
- Order placement (cart → order conversion).
- Outbound webhook publication.
- Email and SMS send.
- Money transfer / payout.
- Inventory adjustment (when it's `+= delta`, not `= absolute`).

### Key source

| Source | Description | When to choose |
|:---|:---|:---|
| Client-supplied (header) | The caller generates and sends `Idempotency-Key` | External APIs where the caller knows the retry boundary; payment APIs |
| Domain-derived | The key is a domain id the operation already requires (event id, order attempt id) | Internal systems with explicit domain identity at retry boundary |
| Server-derived from request hash | Hash of the request body becomes the key | Only when no domain id is available and clients cannot be educated to send a header — risky, because hash collisions or legitimate identical re-requests confuse the mechanism |

Prefer domain-derived when feasible. It collapses two concerns (operation identity, retry identity) into one. Client-supplied is the right default for external API surfaces.

### Replay window (TTL)

| Window | Behavior | When to choose |
|:---|:---|:---|
| Short (minutes to 1 hour) | Replays beyond the window re-execute | Frequent operations where stale-replay risk is low |
| Medium (24 hours) | Captures most retry storms | Industry default (Stripe uses ~24 hours) |
| Long (days to weeks) | Strict at-most-once even under prolonged retry chains | Money-moving operations; legal/audit requirements |
| Indefinite | Keys never expire | Rare; only when the key *is* the domain identifier (e.g., the order id itself) |

Default to 24 hours. Extend for money-moving operations to match the retry behavior of the consuming system (e.g., processor webhook retry windows).

### Where to apply

| Layer | Pattern | Notes |
|:---|:---|:---|
| HTTP endpoint | `Idempotency-Key` header, replay store per endpoint | Most common; well-supported by frameworks |
| Application layer | Internal calls take an idempotency parameter | Required when calls cross service boundaries within the system |
| Database write | `INSERT ... ON CONFLICT DO NOTHING` on a deduplicating column | Cheapest layer for last-resort dedup |
| Message consumer | Track processed message ids in a dedup table | Required when broker delivers at-least-once |

Layered defense: a single layer is insufficient. The HTTP endpoint catches client retries; the DB unique constraint catches what slipped through; the message consumer catches re-delivery. Each layer has a different failure mode.

### Concurrent replay handling

Three positions:

1. **Block second arrival until the first completes.** The replay waits; once the first finishes, the stored response is returned. Best UX; requires lock or queue.
2. **Return `409 Conflict` (in-progress) immediately.** The replay sees the in-progress entry and is told to retry later. Lowest server cost; the client must handle the conflict.
3. **Allow both to execute, dedupe at the effect layer.** Both run to completion; the second one's effects are detected and undone at the side-effect boundary (payment processor's own idempotency, DB unique constraint). Tolerates concurrency but requires deep effect-layer support.

Stripe's API uses 2 for payment-intent creation (returns the existing intent if in-progress). Many internal systems use 1. Use 3 only when the underlying effects naturally dedupe (the payment processor itself rejects duplicate idempotency keys).

### Failure handling

A failed operation is still an outcome. Distinguish:

| Failure type | Behavior | Example |
|:---|:---|:---|
| Deterministic (validation) | Store the failure response; replay returns same failure | Invalid email format |
| Transient (timeout, 5xx) | Do not store; allow replay to retry | Network blip downstream |
| Partial success | Store the partial outcome; replay returns same partial state | DB committed but email failed |

The middle case is the most subtle. If the failure was after the operation's effects were committed, the key should be stored so replay sees the (partially-successful) outcome — otherwise the second retry double-executes the already-committed effects.

A robust pattern: write the key + in-progress marker *first*; on completion, update with the response; on failure, branch by failure type. The cost is two writes per request; the benefit is correctness under interrupt.

### Scope of the key

Three positions:

1. **Global.** Keys are unique system-wide. Risk: a malicious or buggy tenant can poison another tenant's keys.
2. **Per-tenant.** Keys scoped to a tenant; same key on two tenants is two different operations. Standard for SaaS multi-tenant.
3. **Per-customer / per-endpoint.** Finer scoping. Same key on `/orders` and `/refunds` is two different operations. Reduces collision risk further.

Default to per-tenant for external APIs; per-customer + per-endpoint for high-security operations.

## Pattern catalog (cross-references)

Idempotency intersects with several named patterns from cloud-architecture catalogs. Each addresses a specific facet of the same problem; the patterns compose.

| Pattern | Where it appears | What it adds beyond the basic key |
|:---|:---|:---|
| Idempotent receiver | [microservices.io](https://microservices.io/patterns/communication-style/idempotent-consumer.html), [Hohpe & Woolf](https://www.enterpriseintegrationpatterns.com/patterns/messaging/IdempotentReceiver.html) | Consumer-side dedup for at-least-once messaging |
| Idempotent consumer | [AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/idempotent-consumer.html) | Cloud-native version: explicit storage, TTL, fingerprint check |
| Transactional outbox | [microservices.io](https://microservices.io/patterns/data/transactional-outbox.html) | Pairs with idempotent receiver: writes events atomically with state change |
| Inbox table | Various | Receiver writes incoming event ids to an inbox before processing; dedup by inbox primary key |
| Sequential convoy | [Azure pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/sequential-convoy) | Serializes processing of messages with the same key — orthogonal to idempotency but solves related ordering problems |
| Compensating transaction | [Azure pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction) | Reverses prior committed work; itself must be idempotent |

The Stripe Engineering team has documented how the pieces fit together in their own infrastructure — idempotency keys, exactly-once effects, and the database-level fingerprinting that catches mid-execution duplicates. ([Stripe — designing robust and predictable APIs with idempotency](https://stripe.com/blog/idempotency))

The Alibaba / Ant Group engineering teams document a similar approach in their distributed-transaction frameworks (Seata, Hmily), where the idempotency key is part of the transaction context and persists across compensation. ([Alibaba Seata](https://www.alibabacloud.com/help/en/seata/))

## Anti-patterns

- **Retrying without an idempotency key.** Every retry is a fresh operation; effects multiply. Pattern: client wraps any non-2xx in a retry loop with no key; payment is captured twice. Fix: idempotency-key the operation before adding any retry.
- **Idempotency at the wrong layer.** The HTTP handler dedupes, but the worker that processes the queued event has no dedup. Payment captured once, email sent twice. Apply idempotency at every layer where retry is possible.
- **Different request, same key.** Client mistakenly reuses a key for a new operation. Without fingerprint check, the new request returns the old response — undetected. Always fingerprint-check on replay; return error on mismatch.
- **Storing only the success response.** First attempt fails after committing effects; second attempt has no key entry and re-commits. Store the in-progress marker before any effect; update on completion.
- **TTL too short for the retry window.** Payment processor retries webhooks for up to 3 days; replay store keeps keys for 1 hour. Day-2 retries re-execute. Match the TTL to the longest retry chain the system faces.
- **Forgetting that DB transactions retry too.** A `SERIALIZABLE` transaction that fails with a serialization conflict and retries — without an idempotency check on the operation it just performed — may double-execute side effects. Idempotency lives outside the transaction boundary as well as inside.
- **Treating message ids as transparent and unique.** Some brokers redeliver with the same id (Kafka offset, SQS message id); some redeliver with new ids on each delivery (rare but real). Verify the broker's behavior and key accordingly.
- **Using a hash of the request body as the key, when the body legitimately repeats.** Two genuinely different "$10 charge" operations have the same body hash; the second is silently swallowed. Body-hash idempotency is only safe when the body carries a request-unique element (timestamp, nonce).
- **Idempotency without a fingerprint check.** A key-only check accepts a key-replay even if the body changed; the original response is returned for a different request. The client thinks the new request succeeded; it never executed.
- **Idempotency that makes long-running operations stateful in memory.** Replay store in process memory; instance crash loses the in-progress markers; replays after restart re-execute. Persist the keys to durable storage.
- **Idempotency on read endpoints.** Reads are naturally idempotent (they have no effect); adding key plumbing adds complexity for nothing. Reserve the mechanism for state-changing operations.
- **Storing PII or full request bodies in the replay store indefinitely.** Replay store becomes a compliance liability (GDPR, CCPA). Store the minimum needed (status, response, fingerprint hash) and expire aggressively.
- **No monitoring on key collisions or replays.** A spike in replays is a signal — could be a client bug, a network issue, or an attack. Emit metrics on replay rate and fingerprint mismatches.

## Sources

- RFC 7231 §4.2.2 (idempotent methods) — https://datatracker.ietf.org/doc/html/rfc7231#section-4.2.2
- RFC 9110 (HTTP semantics; supersedes 7231) — https://datatracker.ietf.org/doc/html/rfc9110
- Stripe idempotent requests — https://docs.stripe.com/api/idempotent_requests
- AWS Builders' Library — Making retries safe with idempotent APIs — https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/
- Fowler — Patterns of Distributed Systems: Idempotent Receiver — https://martinfowler.com/articles/patterns-of-distributed-systems/idempotent-receiver.html
- Standard Webhooks (consumer idempotency guidance) — https://www.standardwebhooks.com/
- Adyen — idempotency — https://docs.adyen.com/development-resources/api-idempotency/
- Square — idempotency keys — https://developer.squareup.com/docs/working-with-apis/idempotency
- AWS Prescriptive Guidance — idempotent consumer — https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/idempotent-consumer.html
- Azure Architecture Center — idempotency in API design — https://learn.microsoft.com/en-us/azure/architecture/microservices/design/api-design#idempotent-operations
- Azure Sequential Convoy pattern — https://learn.microsoft.com/en-us/azure/architecture/patterns/sequential-convoy
- microservices.io — idempotent consumer — https://microservices.io/patterns/communication-style/idempotent-consumer.html
- microservices.io — transactional outbox — https://microservices.io/patterns/data/transactional-outbox.html
- Stripe Engineering — designing robust APIs with idempotency — https://stripe.com/blog/idempotency
- Alibaba Cloud — Seata distributed transactions — https://www.alibabacloud.com/help/en/seata/
- Hohpe & Woolf — Idempotent Receiver — https://www.enterpriseintegrationpatterns.com/patterns/messaging/IdempotentReceiver.html
- Werner Vogels — "Eventually Consistent" (ACM Queue, 2008) — https://queue.acm.org/detail.cfm?id=1466448
- Shopify Engineering — idempotency in checkout — https://shopify.engineering/
- DoorDash Engineering — idempotent order operations — https://doordash.engineering/
- Mercado Libre Engineering — idempotency in payment pipelines — https://medium.com/mercadolibre-tech
- Kleppmann, *Designing Data-Intensive Applications* (2017), Chapter 8 — fault-tolerance and at-least-once semantics
