# Volunteer dispatch and SMS adapters

`services/notifications/` turns a coordinator-approved assignment into a short pickup alert
over SMS. Two transports implement the same port: Twilio Programmable SMS and Amazon SNS.

## Why SMS content is deliberately thin

A text message leaves the application's control: carriers store it, lock screens display it,
and it cannot be recalled. `SEC-HG-007` forbids address leakage "in notifications/logs", so a
message contains only:

- crop label, coarse area label (e.g. `Delft, coarse cell u173z`), and the UTC time window;
- short operational constraints (`street parking only`, `bring a 3m ladder`);
- the instruction that the exact address unlocks in the app two hours before the window;
- the template reference and a coordinator contact *role*, never a personal number.

`PickupAlert` has no address, access-code, coordinate, owner, or recipient field. The renderer
additionally scans for known restricted strings passed in by the caller, obvious street
addresses, and phone numbers, and refuses to send if it finds any. `restricted_markers` are
used only for that comparison; they are never included in the message.

## Dispatch rules

| Rule | Behaviour |
| --- | --- |
| Coordinator approval | `approved_by` must equal the coordinator; otherwise `dispatch_not_authorized` |
| Owner approval | Must be current at send time |
| Glean state | `invited` or `confirmed` for invitations/reminders; `cancelled` for cancellations |
| SMS opt-in | Required per volunteer, with an opt-in policy version (`SMS only with separate opt-in`) |
| Quiet hours | 21:00–07:00 local by default; messages are deferred, never sent |
| Daily cap | 3 messages per volunteer per 24 h; per tick cap 50 |
| Idempotency | `glean/state/recipient/template/version`; a repeat dispatch is suppressed |
| Suppression list | Honoured before any other check |

There is no scheduled or automatic dispatch path: the only entry point requires an
authorization object carrying coordinator and owner approval (`SEC-HG-006`).

## Transports

| Adapter | Protocol | Notes |
| --- | --- | --- |
| `TwilioSmsTransport` | HTTPS `POST /2010-04-01/Accounts/{sid}/Messages.json`, HTTP Basic | 401/403 → `provider_unauthorized`, 429 → `provider_rate_limited` (`Retry-After` preserved), 5xx → `provider_unavailable`, 4xx → `provider_rejected` |
| `SnsSmsTransport` | `sns:publish` with `PhoneNumber` | Transactional SMS type, `MaxPrice` guard, optional sender ID; requires `boto3`, raises `BackendUnavailable` without it |
| `RecordingSmsTransport` | in-memory | Labelled fixture; records messages and sends nothing |

Error messages carry provider codes and statuses only. Phone numbers, tokens, and message
text are excluded from every exception surface and from the ledger, which stores a SHA-256
body digest and the provider receipt id instead.

## Cost and region notes

SNS direct SMS is priced per message and per destination country; `MaxPrice = 0.05 USD` caps
the per-message spend. Sender IDs are not supported in every country, and Twilio numbers are
long-code/messaging-service specific. Neither behaviour has been exercised against a live
account, so cost and deliverability remain unverified.

## Evidence limits

Both adapters are tested against injected HTTP/SNS fakes that assert the exact request shape,
error mapping, and redaction. No Twilio account, SNS topic, real phone number, or message
delivery exists in this repository, and `docs/validation.md` records the same limitation.
