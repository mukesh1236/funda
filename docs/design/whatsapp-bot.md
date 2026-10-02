# WhatsApp assistant

Lets a signed-in user ask the same grounded questions from WhatsApp that they ask
on the website, and receive a personalised morning brief. Built on Twilio's
WhatsApp sandbox.

## Linking a phone (code-proven, never self-claimed)

A phone number is bound to an account only by proving control of both, so nobody
can claim someone else's number or account.

1. The signed-in user calls `POST /api/whatsapp/link-code`
   (`whatsapp/linking.generate_link_code`): a fresh **6-digit code**, valid for
   **10 minutes**, single use, stored in `whatsapp_link_codes`. The response also
   carries a `wa.me` deep link pre-filled with the sandbox join phrase and the
   code.
2. The user sends the code to the bot from their phone.
3. `try_link` matches only a message that is *exactly* a code (kept strict so a
   normal question containing digits is not misread as one) and writes the binding
   to `whatsapp_links`, keyed by phone.

The mechanism mirrors the password-reset-token pattern.

## Inbound routing (`whatsapp/webhook.py`)

```
POST /api/whatsapp/webhook   (Twilio, form-encoded)
  0. verify X-Twilio-Signature     bad ─► 403
  1. rate limit                    over limit ─► polite "too fast" reply
  2. 6-digit code                  ─► link phone ─► confirm
  3. STOP / UNSUBSCRIBE / CANCEL / END / QUIT   ─► opt out
  4. phone not linked              ─► tell them how to link
  5. otherwise                     ─► chat.answer_question(...) ─► reply + disclaimer
```

Replies are TwiML (synchronous, no extra API call). WhatsApp answers always use the
US market and no pinned symbol, and always carry the disclaimer.

## Morning brief

`jobs.send_whatsapp_briefs`, run as the last step of the daily job: for every
**opted-in** user, their watchlist and fund picks, or the day's top calls if they
have pinned nothing. A failure for one user never affects the others or the rest of
the daily run. Skipped entirely if Twilio credentials are not configured.

## Rules that must not change

- **`opted_in` gates all outbound messages**, and `STOP` flips it off. Compliance,
  not a preference.
- **Signature verification.** Twilio signs the URL plus the sorted POST params with
  the auth token (HMAC-SHA1, base64), compared in constant time. The URL used is
  `APP_BASE_URL + /api/whatsapp/webhook`, not the request URL, to avoid
  scheme/host mismatches behind Railway's proxy, so **`APP_BASE_URL` must be the
  public URL**.
- **Verification is skipped when `TWILIO_AUTH_TOKEN` is empty** (a dev and test
  convenience). It is therefore a **required production setting**; see
  [`security/policy.md`](../security/policy.md).
- **A phone maps to exactly one account** (`whatsapp_links` is keyed by phone).
- **Same brain as the website**, so the advice and scope guards apply here too
  ([`ask-ai.md`](ask-ai.md)).

## Limits

- Per phone: 20 messages per 60 seconds, in memory (resets on restart; per process).
- The Twilio **sandbox** requires each user to send a join phrase first
  (`WHATSAPP_SANDBOX_JOIN`); a production sender needs Twilio approval.

## Configuration

`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`,
`WHATSAPP_SANDBOX_JOIN`, `APP_BASE_URL`.

## Code and tests

`app/whatsapp/` (`webhook.py`, `linking.py`, `client.py`), `app/jobs.py`;
`tests/test_whatsapp.py`.
