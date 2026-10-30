# 0009. Discord delivery through a Postgres outbox

Date: 2026-10-30

## Status

Accepted

## Context

Every message this service sends to Discord goes through the shared
transport in `mini_app_polis.discord`, wrapped by `services/discord.py`.
The transport waits out Discord's limits: it follows each webhook's bucket
headers, retries a 429 after its cooldown, and treats a Cloudflare 1015
(an HTML refusal applied to this service's outbound address) as a global
cooldown of up to a minute. Waiting is bounded by `max_wait`: a message
that would wait longer is dropped and logged. The queue, buckets and
cooldowns are process memory, so a restart forgets them.

Two routes send. `POST /v1/notify` carries the fleet's run reports and
other first-party messages to the `runs` channel, copying WARN and ERROR
reports to `errors`. It answers 502 when the send is dropped. The GitHub
webhook route forwards repository events and already answers GitHub 200
whatever happens, giving up after `GITHUB_MAX_WAIT_SECS`.

Bursts are normal: a fleet-wide evaluation pass posts one report per
repository, and a round of releases produces a run of GitHub events and
run reports within seconds. When a burst meets a 1015 hold, everything
sent during the hold is lost. `/v1/notify` turns each loss into a 502,
which Cloudflare shows as an outage and every caller reports to Sentry as
an error, though the API itself is healthy. Run signals are recorded before
delivery, so failure detection does not depend on Discord; the record in
the channels does.

## Decision

**1. A held message is answered 200, never 502.** `POST /v1/notify` keeps
its `NotificationResult` shape. A message sent straight away answers
`forwarded=true`, reason `forwarded`. A message that cannot go out within
a short wait answers `forwarded=false`, reason `queued`: it is in the
outbox and will be sent. A message Discord refuses outright (a 4xx other
than 429, such as a malformed embed) answers `forwarded=false`, reason
`delivery_failed`, is logged and reported to Sentry, and is not retried.
No outcome of a Discord send is a 5xx. Outside production, nothing is
posted except the contract suite's marked result, as before.

**2. Messages that cannot go out are kept in a Postgres outbox and sent
later; errors and run failures are never dropped.** Each route first tries
the send with a short wait. If the message is held, it is written to an
outbox table with its channel, payload, severity, caller and creation
time, and a background drainer in this service sends it when the channel
is free. Rows are leased with `FOR UPDATE SKIP LOCKED`, so more than one
process can drain safely, and a restart loses nothing. The `errors` copy
of a WARN or ERROR report and GitHub events held past their wait go through
the same outbox. Messages at ERROR or WARN, and run reports for a failed
run, never expire. Other messages expire after a configured age (a
setting with a default of hours, not minutes) and are counted, not sent.

**3. No caller depends on Discord delivery.** Callers read the answer, not
the channel. The contract suite checks that `/v1/notify` answers as
designed (`forwarded=false` with the development reason for its unmarked
check) and posts its own result best-effort; CI does not check delivery.
Run reports treat `queued` as accepted, not as a failure.

**4. Sends are spaced to stay under the limits, not just to recover from
them, and packed when a backlog builds.** Every send, straight or from the
outbox, first takes a token from a send budget held below Discord's
limits: one budget per webhook, under its bucket, and one across all
webhooks, under the rate at which Cloudflare starts refusing this
service's address (the 1015 holds, which no bucket header warns of). Both
rates are settings, set with headroom. With no token free, a route does
not wait: the message goes to the outbox, and the drainer sends it when
the budget allows. The bucket headers still apply on top, as a second
guard. The drainer sends one channel's messages in order. When more than
one embed-only message is waiting for a channel, it packs up to Discord's
limit of ten embeds (within Discord's total size limit) into one message,
under a neutral username, with each embed keeping its own title and
footer so the sender stays visible. A message with text content is sent
on its own.

**5. A late message says so, and expired messages are summarized.** A
message sent from the outbox after more than a configured delay carries
its original time. When messages expire, the drainer posts one message
to `runs` saying how many were not delivered and the time range they
covered, and the episode is reported to Sentry once, as the transport
already does for drops. Webhook URLs are never logged.

## Consequences

- Bursts are spread out before Discord or Cloudflare refuses them, so a
  1015 hold should be rare; when one happens anyway, it no longer looks
  like an API outage, and what was sent during it arrives late instead of
  not at all.
- The send budget is process state, like the transport's buckets. It is
  correct while one process sends; if this service ever runs more than one
  replica, the across-webhooks budget has to move somewhere shared.
- The budget rates need tuning against what Discord and Cloudflare actually
  allow; too low delays messages for no reason, too high lets holds through.
- `/v1/notify` gains a database write on the held path, and this service
  gains a migration (the outbox table) and a long-running drainer task.
- Callers see two new reasons, `queued` and `delivery_failed`.
  common-python-utils' client, run reports and contract models are updated
  to treat `queued` as accepted.
- Packing changes how a burst reads in Discord: several reports in one
  message, under a neutral name, instead of one message each.
- Expired messages are only counted. Their content is not kept past
  expiry.
