---
name: webhooks
description: Use when designing webhook receivers or senders — signature verification, replay protection, retry policy, at-least-once handling, dead-letter strategy, and event payload contracts. Load before wiring an inbound webhook endpoint (payment processor, shipping carrier, marketplace), publishing outbound events to merchant integrations, or debugging missing/duplicate events. Cross-references idempotency, payment-flows, order-lifecycle.
---

# Webhooks

The HTTP callback is how asynchronous truth crosses system boundaries: payments confirm, shipments update, marketplaces notify, integrations sync. Webhooks are unreliable by default — the network drops them, the receiver crashes, the sender retries indefinitely, the same event arrives three times. Reliability is engineered on both sides.

This skill frames the design of webhook senders and receivers. It does not pick a transport library.

Read the `domain-vocabulary` skill first. This skill cross-references `idempotency`, `payment-flows`, and `order-lifecycle`.

## Vocabulary

**Webhook** — An HTTP request the sender makes to a URL the receiver registered, carrying an event payload, to notify the receiver of something that happened. The inverse of polling. ([Standard Webhooks](https://www.standardwebhooks.com/))

**Sender (producer)** — The system that emits webhook requests. Owns the event source, the signing secret, and the retry policy. Examples: Stripe, Shopify, GitHub, any system the merchant integrates with.

**Receiver (consumer)** — The system that exposes a URL to receive webhook events. Owns verification, deduplication, and processing. Most commerce systems are both senders and receivers.

**Event** — A discrete record of something that happened in the sender's domain (`payment_intent.succeeded`, `order.shipped`, `dispute.created`). Typically immutable, identifiable by an event id, and named in past tense.

**Event payload** — The JSON body of the webhook request, carrying the event id, type, occurrence timestamp, and a snapshot of the affected resource.

**Signature** — A cryptographic value (typically HMAC-SHA256) over the request body and timestamp, computed by the sender using a shared secret. Verifies that the request came from the sender and was not modified in transit.

**Replay attack** — An attacker captures a valid webhook request and re-sends it to the receiver, hoping to trigger the operation again. Defended against by including a timestamp in the signature and rejecting old requests.

**At-least-once delivery** — The sender's guarantee: every event will be delivered one or more times. The receiver must handle duplicates. Default behavior for all production webhook senders.

**At-most-once delivery** — A weaker guarantee that some sender configurations offer (give up after N failures). Risky: losing webhook events silently is worse than handling duplicates.

**Exactly-once processing** — The receiver's goal: act on each event exactly once. Achieved by combining at-least-once delivery with consumer-side deduplication.

**Retry policy** — The sender's rule for re-attempting failed deliveries: how often, with what backoff, for how long, with what failure criteria.

**Dead letter** — An event the sender (or receiver) gives up on after retries exhausted. Typically routed to a dead-letter queue or a manual review surface.

**Event ordering** — Whether events arrive in the same order as they occurred. Most webhook systems do *not* guarantee order; assuming order is a common cause of bugs.

**Subscription** — The receiver's declaration of which event types it wants to receive at which URL. Most webhook senders expose a subscription API or dashboard.

**Versioned event schema** — The sender's commitment that a given event type's payload shape is stable, or evolves under a versioning scheme. Consumers depend on this to upgrade safely.

## Canonical model

The webhook lifecycle, end-to-end:

```
1. Event occurs in sender (payment succeeds, order ships)
        ↓
2. Sender enqueues a delivery to each subscribed receiver
        ↓
3. Sender computes signature over body + timestamp using receiver's secret
        ↓
4. Sender POSTs to receiver URL with headers:
       - Webhook-Id (unique per event)
       - Webhook-Timestamp
       - Webhook-Signature
        ↓
5. Receiver:
   a. Reads raw body (before any parsing or JSON re-serialization)
   b. Verifies signature against secret
   c. Verifies timestamp is within tolerance
   d. Looks up the event id in dedup store — if seen, ACK and skip
   e. Persists the event (with id) to a queue or table
   f. Returns 2xx promptly (typically <5 seconds)
        ↓
6. Receiver processes the event asynchronously
        ↓
7. If receiver returned non-2xx or timed out, sender retries per its policy
```

Sender-side retry shape (typical):

```
Attempt 1:     immediate
Attempt 2:     after 5 seconds
Attempt 3:     after 30 seconds
Attempt 4:     after 5 minutes
...
Final:         after up to 72 hours (varies by sender)
After last failure:  dead-letter
```

Invariants:

1. **The receiver verifies the signature on the raw bytes.** Re-serializing the body or parsing then re-encoding will fail signature verification.
2. **The receiver returns 2xx fast.** Slow handlers cause sender retries; the receiver does *not* process synchronously. Acknowledge fast; process async.
3. **The receiver deduplicates by event id.** Same event id appearing twice is a re-delivery, not a new event.
4. **Order is not guaranteed.** The receiver handles each event based on the current state, not the assumed sequence.
5. **The receiver tolerates payload schema additions.** Senders add fields over time; consumers ignore unknown fields rather than failing.

## Standard Webhooks

[Standard Webhooks](https://www.standardwebhooks.com/) is an industry specification adopted by OpenAI, Anthropic, Vercel, Kong, Svix, Supabase, Resend, and others. It standardizes:

- **Header names** — `Webhook-Id`, `Webhook-Timestamp`, `Webhook-Signature`.
- **Signature scheme** — HMAC-SHA256 over `{id}.{timestamp}.{body}`, base64-encoded, with optional key rotation via a `v1,<sig> v1,<sig2>` format.
- **Replay protection** — 5-minute timestamp window.
- **Event payload** — JSON with `type`, `data`, and optional `version`.
- **Retry behavior** — Exponential backoff over 72 hours.

If building a new webhook sender, adopt Standard Webhooks. If consuming a sender, recognize when they have adopted it; many haven't (Stripe, Shopify, GitHub each have their own conventions that predate the standard).

## Variation dimensions

- **Direction** — Receiving from third parties (payment processor, carrier, marketplace), sending to third parties (merchant integrations, ERP), or both.
- **Volume** — Low (a few events per minute) vs high (thousands per second). High-volume demands queueing and worker pools; low-volume can use in-line processing.
- **Latency tolerance** — Real-time (UI updates depend on event arrival) vs eventual (batch jobs, reporting). Latency-sensitive consumers need backup polling.
- **Authority** — The webhook is informational (the receiver could find the truth elsewhere) vs authoritative (the webhook is the only signal). Authoritative webhooks demand stricter loss prevention.
- **Subscriber count** — Single subscriber (merchant's own integration) vs many subscribers (SaaS platform fanning out to many tenant endpoints). The latter introduces per-subscriber retry isolation needs.

## Decision frameworks

### Receiver: synchronous ack vs synchronous processing

Two positions:

| Position | Behavior | Trade-off |
|:---|:---|:---|
| Sync ack only | Receive → verify → enqueue to internal queue → return 200 | Robust under retries; eventual processing |
| Sync processing | Receive → verify → process inline → return 200 | Simpler; risks timeout retries under load |

Default to sync-ack. The sender's retry policy (Stripe: 3 days) makes synchronous processing under any load a recipe for repeated re-delivery storms. Process asynchronously; the 2xx is the receipt-confirmation, not the processing-confirmation.

Exceptions: very small systems with predictable webhook volumes and sub-second processing time. Even then, the cost of switching to sync-ack later is small; the cost of moving from sync-processing under load to sync-ack is much higher.

### Receiver: deduplication granularity

Three positions:

| Position | Key | Storage |
|:---|:---|:---|
| Event-id-only | `event.id` | Dedup table or cache |
| Event-id + type | `(event.id, event.type)` | Same |
| Domain-derived | An entity id from the payload (`order.id` + status) | Domain table itself (`WHERE NOT EXISTS`) |

Default to event-id-only. The sender's id is the authoritative event identity; combining it with type adds nothing. Domain-derived is the highest-correctness pattern when feasible — the dedup *is* the domain check (does this order already exist?) rather than a separate concern.

### Receiver: signature verification strictness

Verify everything; reject anything off:

- **Constant-time comparison** of the signature, not string-equal (timing-attack mitigation).
- **Timestamp within tolerance** (5 minutes is the Standard Webhooks default).
- **Secret rotation** — accept signatures from both the old and new secret during a rotation window.
- **Raw body for verification** — never re-parse then re-serialize before verifying. Use the framework's raw-body access (Express: `express.raw()`; Django: `request.body`; etc.).

### Receiver: handling sender retries

Two patterns:

1. **Receiver dedup table.** Persist seen event ids; reject duplicates. Required for at-least-once delivery from any sender.
2. **Domain-level dedup.** The handler itself uses an idempotent operation (`UPDATE ... WHERE state = 'pending'` or order placement keyed on event id). Eliminates the separate dedup store.

Layered defense: keep both. The dedup table catches early, before any work; the domain idempotency catches what slipped through (e.g., during dedup-table outage). See `idempotency`.

### Receiver: ordering

Senders almost never guarantee ordering. The receiver must:

- **Treat each event as a snapshot.** Process based on the event payload's data, not on inferred sequence.
- **Reconcile with the sender's state when needed.** For state-machine transitions where order matters (auth → capture → refund), fetch the current state from the sender's API rather than trusting webhook order.
- **Tolerate out-of-order events.** A `dispute.closed` arriving before `dispute.created` should not 500; either process them order-agnostically or stash the out-of-order event for retry.

### Receiver: timeout and retry behavior

Senders retry on non-2xx and on timeout. The receiver should:

- **Set its own timeout shorter than the sender's expected response time.** Stripe waits 30 seconds; receiver should return within 5–10 seconds and offload to a queue.
- **Never 5xx as a control-flow signal.** A bug that 500s causes massive retry traffic. Wrap handler exceptions; log them; return 2xx (or a controlled 4xx if the event is genuinely invalid).
- **Return 4xx only for unrecoverable issues.** Signature failure (the sender should not retry), event the receiver doesn't recognize (silently 2xx and ignore, or 410 to ask the sender to stop sending it).

### Sender: signing and rotation

If building a sender:

- **HMAC-SHA256 over body + timestamp.** Standard. Avoid plain hash (timing attacks) or asymmetric signing (overkill).
- **Per-endpoint secret.** Each subscriber gets its own secret; revoking one does not affect others.
- **Rotation.** Provide a way to rotate secrets; sign with both old and new during transition; let subscribers fetch the active set.
- **Document the verification.** Many integration failures come from senders documenting signing poorly. Show a verified example in two or three languages.

### Sender: retry policy

A solid default:

- **Exponential backoff** with caps (1s, 30s, 5m, 30m, 2h, 12h).
- **Total window** of 24–72 hours.
- **Dead-letter** after the window; expose to the subscriber via dashboard or API.
- **Pause delivery** to an endpoint that returns 4xx persistently — the subscriber likely deleted or misconfigured the URL.

### Sender: event delivery and ordering

- **Each event has a stable id.** Don't reuse ids; subscribers depend on uniqueness.
- **Don't guarantee order; document the lack of guarantee.** Subscribers who need order will then build for it.
- **Include enough payload to act without a fetch.** If the consumer must always call the sender's API to act on a webhook, the webhook is a useless ping. Include the resource snapshot.
- **Version the schema explicitly.** Either via an event version field or a per-endpoint schema version setting.

### Receiver: polling backstop

When webhooks are critical and the sender's delivery is imperfect (or could be), pair webhooks with a periodic poll for recent events. The webhook is the fast path; the poll catches what was missed. The dedup mechanism ensures duplicates are ignored.

## Webhook delivery and fan-out at scale

The basic model (sender retries until 2xx; receiver acks fast and processes async) holds for any volume. The implementation changes once a sender must deliver thousands of events per second across many subscribers, or once a receiver must process millions of events daily.

Sender-side patterns ([AWS Architecture Blog](https://aws.amazon.com/blogs/architecture/), [Cloudflare blog](https://blog.cloudflare.com/), [Standard Webhooks](https://www.standardwebhooks.com/)):

- **Per-endpoint queues with isolation.** Each subscriber endpoint gets its own queue; one slow consumer does not back up the others. AWS-flavored: SQS per endpoint; Azure-flavored: Service Bus topic + subscription per receiver.
- **Per-endpoint circuit breaker.** When an endpoint persistently fails, pause delivery to it and surface the state to the subscriber via a dashboard or status API. Pairs naturally with the [Azure Circuit Breaker pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker).
- **Fan-out via pub/sub.** Internally, the event source publishes once; a dispatcher service materializes per-subscriber deliveries. Amazon EventBridge, Azure Event Grid, and Google Cloud Pub/Sub all support this shape. The merchant rarely builds the fan-out from scratch.

Receiver-side patterns ([Azure Async Request-Reply pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/async-request-reply), [AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/modernization-integrating-microservices/)):

- **Two-stage receiver.** Stage 1 (the HTTP handler): verify, dedup, enqueue, return 2xx. Stage 2 (the worker): process the event with retries and dead-letter routing. The split lets stage 1 run on serverless / autoscaling infrastructure tuned for fast acknowledgment.
- **Inbox table.** Receiver writes the event id to an inbox before any processing. The inbox's unique constraint becomes the dedup mechanism. Pairs with the workflow described in `idempotency`.
- **Backstop polling.** For events that drive critical state, schedule a periodic reconciliation against the sender's API. Catches the lost events that webhook retry exhaustion misses.

The Stripe Engineering team and Cloudflare have both written about the operational reality of running webhook infrastructure at scale: the bug surface is almost entirely in retries, signature drift during secret rotation, and pathological subscriber endpoints. The receiver's defense is observability: per-event-type counters, per-endpoint latency, dedup-rate gauges, dead-letter alerts.

## Anti-patterns

- **Verifying the parsed body instead of the raw body.** Reformatting JSON (key order, whitespace, escaping) breaks the signature. Capture the raw bytes before any parsing. Symptom: signature verification fails for valid requests.
- **String comparison of signature.** Vulnerable to timing attacks. Use a constant-time comparison from a vetted library (`crypto.timingSafeEqual` in Node, `hmac.compare_digest` in Python).
- **Skipping signature verification in development and forgetting to add it in production.** Pattern: `if (process.env.NODE_ENV === 'production') verify()`. Adversary triggers production with `NODE_ENV=development`. Always verify; mock the secret in tests if needed.
- **No timestamp validation.** Captured webhooks can be replayed indefinitely. Reject anything older than the tolerance window.
- **Processing webhooks synchronously and slowly.** Sender's retry policy retries every timeout; receiver sees a multiplier on every slow handler. Enqueue and return; process out-of-band.
- **No deduplication.** Same event processed twice; double inventory decrement, duplicate emails, double captures. Always dedupe by event id.
- **Assuming event order.** Code expects `payment_intent.succeeded` to arrive before `charge.refunded`; reality has them swapped under retry storms. Process each event against current state, not against assumed prior state.
- **Treating webhook payload as the authoritative resource.** The webhook is a notification; the sender's API is the authoritative source. For high-stakes transitions (payment capture, dispute), refetch the resource to confirm rather than acting on the payload alone.
- **Returning 5xx for known-bad payloads.** Causes retry storms for events that will never succeed. Return 2xx (log and skip) or 4xx (ask sender to stop).
- **Logging the full payload including secrets.** Webhooks often carry sensitive data (customer info, partial card data, internal ids). Log identifiers and types, not the full body. Mask anything sensitive.
- **No dead-letter visibility.** Sender retries exhaust; the event is silently dropped on the receiver's end. Operations are blind. Surface DLQ contents in dashboards and alerts.
- **One endpoint for everything.** Receiver mixes payment, fulfillment, customer events on one URL with one secret. Rotation, monitoring, and isolation are harder. Use distinct endpoints (or distinct event-type-based routes) per producer or per concern.
- **Forgetting backstop polling for critical flows.** Lost webhooks silently leave orders stuck. Pair webhook-driven flows with a daily reconciliation poll for any state that must be eventually consistent.
- **No idempotency in the handler beyond the dedup table.** Dedup table read-then-write race: two replicas process the same event because both read "not seen" before either inserted. Use insert-or-fail (`INSERT ... ON CONFLICT DO NOTHING`) as the gate, then proceed only if insert succeeded.
- **Outbound webhook sender that blocks the domain operation on delivery.** Order placement waits for the webhook POST to a customer's integration to return. The customer's URL is slow; order placement is slow. Decouple: domain operation commits; outbound webhook queued.
- **Sending plaintext secrets to subscribers via email or the dashboard with no reveal-once.** Secrets shown in plaintext indefinitely accumulate over engineers' tenures. Display once at creation; allow rotation.

## Sources

- Standard Webhooks specification — https://www.standardwebhooks.com/
- Standard Webhooks GitHub — https://github.com/standard-webhooks/standard-webhooks
- Stripe webhooks — https://docs.stripe.com/webhooks
- Stripe webhook signatures — https://docs.stripe.com/webhooks#verify-official-libraries
- Shopify webhooks — https://shopify.dev/docs/apps/build/webhooks
- GitHub webhooks — https://docs.github.com/en/webhooks
- AWS — receiving webhooks at scale — https://aws.amazon.com/blogs/architecture/serverless-architecture-for-a-web-scraping-solution/
- OWASP — Webhook security cheat sheet — https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html
- Adyen webhooks — https://docs.adyen.com/development-resources/webhooks/
- AWS Prescriptive Guidance — integrating microservices (webhook patterns) — https://docs.aws.amazon.com/prescriptive-guidance/latest/modernization-integrating-microservices/
- AWS EventBridge — https://docs.aws.amazon.com/eventbridge/
- Azure Architecture Center — Async Request-Reply pattern — https://learn.microsoft.com/en-us/azure/architecture/patterns/async-request-reply
- Azure Architecture Center — Circuit Breaker pattern — https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker
- Azure Functions — HTTP/webhook bindings — https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-http-webhook
- Alibaba Cloud — EventBridge — https://www.alibabacloud.com/help/en/eventbridge
- Cloudflare blog — webhook delivery at scale — https://blog.cloudflare.com/
- Stripe Engineering — webhook design — https://stripe.com/blog/engineering
- Shopify Engineering — webhook delivery infrastructure — https://shopify.engineering/
- Werner Vogels — at-least-once delivery essays — https://www.allthingsdistributed.com/
- Mercado Libre Engineering — webhook fan-out across LATAM markets — https://medium.com/mercadolibre-tech
- Kleppmann, *Designing Data-Intensive Applications* (2017), Chapter 11 — stream processing and message delivery semantics
- Nygard, *Release It!* — circuit breakers and bulkheads for webhook receivers
