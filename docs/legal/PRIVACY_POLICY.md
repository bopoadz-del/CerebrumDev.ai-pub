# Privacy Policy

> **DRAFT — NOT YET IN FORCE. REQUIRES REVIEW BY A QUALIFIED LAWYER BEFORE
> PUBLICATION.**
>
> The factual sections below (what is collected, where it goes, who processes
> it) were derived from the production code and are accurate as at the date
> noted. The legal characterisation — lawful bases, transfer mechanisms,
> retention periods — needs counsel. Items marked `[[NEEDS INPUT]]` are
> decisions only the operator or their lawyer can make.

**Effective date:** `[[NEEDS INPUT: date of publication]]`
**Controller:** `[[NEEDS INPUT: registered legal entity, address]]`
**Privacy contact:** support@cerebrum-dev.com
**Facts verified against production code on:** 2026-11-11

---

## 1. What we collect

**Account data.** Your email address, a salted PBKDF2 hash of your password
(we never store the password itself), whether your email is verified, and the
date your account was created.

**Authentication data.** Hashed login tokens and hashed API keys. Raw tokens
are shown to you once and are not recoverable from our records.

**Usage data.** Counters recording how many generations, chat messages and
exports you have used, for enforcing trial limits and billing.

**Your Content.** Anything you submit — briefs, chat messages, uploaded
documents — and the prototypes and artefacts the Service generates from it.

**Technical data.** Server logs and error reports generated when you use the
Service, which may include IP address, request paths, timestamps and diagnostic
context.

**Payment data.** Handled by Stripe. We never see or store card numbers; payment
is taken by Stripe Checkout on Stripe pages, and card data goes directly to
Stripe and does not touch our servers.

## 2. Third parties who process your data

Using the Service necessarily sends data to the following processors. This list
is derived from the production code.

| Processor | What it receives | Purpose | Location |
|---|---|---|---|
| AWS | Everything hosted — the application, database and disks (ECS + managed Postgres) | Hosting | Oregon, USA (us-west-2) |
| DeepSeek | **Your Content** — briefs, chat messages, and document text sent for inference | Generating output (primary model provider, active) | PRC (China) |
| OpenRouter | **Your Content** — only if the fallback model leg is armed (it is not armed today: a fail-closed guard refuses it unless an operator explicitly enables it) | Fallback model routing | USA |
| Stripe | Email, billing identifiers, payment details | Subscriptions | Global |
| Sentry | Error reports, which may incidentally include request context | Error tracking | USA |

Moonshot AI (Kimi) is **not** in the production data path: no Moonshot
credentials are configured, and the legacy compatibility resolver in code
cannot send content anywhere without them. It is listed here only so this
policy is honest about what used to be true.

**The most important disclosure on this page: to generate output, the content
you submit is transmitted to a third-party AI model provider outside our
infrastructure.** If you submit confidential or personal information belonging
to someone else, it leaves our systems. Do not submit anything you are not
permitted to share with a third-party processor.

`[[NEEDS INPUT: a signed data-processing agreement is required with each
processor above before onboarding EU/UK users. DeepSeek is the active
primary provider and the one to check first — confirm what they do with
submitted content, in particular whether they retain it or train on it,
and whether that can be disabled. If they train on submitted content by
default, that must be stated here in plain language, and
it may be commercially unacceptable to business customers.]]`

## 3. Why we process it, and on what basis

| Purpose | Data used | `[[NEEDS INPUT: lawful basis if GDPR applies]]` |
|---|---|---|
| Providing the Service | Account, Your Content | Performance of a contract |
| Enforcing trial limits and preventing abuse | Usage counters, technical data | Legitimate interests |
| Billing | Account, payment data | Performance of a contract |
| Security, debugging, keeping the Service working | Technical data, error reports | Legitimate interests |
| Service-related email | Account data | Performance of a contract |

We do **not** sell your data, and we do not use Your Content to train our own
models. `[[NEEDS INPUT: this statement is only true end-to-end if the upstream
model providers (DeepSeek; OpenRouter if armed) also do not train on
submitted content — verify before
publishing, because publishing it while untrue would be a misrepresentation.]]`

## 4. How long we keep it

Account data is kept for the life of the account plus 30 days after closure.
Your Content is kept until you delete it or close your account. Server logs and
error reports are kept for 90 days. Backups are kept for 14 days. Billing
records are kept for the statutory period (typically 6–7 years). Automatic
purge is being implemented; until it ships, deletion is on request.

## 5. Your rights

Depending on where you live you may have the right to access, correct, delete,
export, or restrict processing of your personal data, to object to processing
based on legitimate interests, and to complain to a supervisory authority.

To exercise any of these, contact support@cerebrum-dev.com. We will
respond within `[[NEEDS INPUT: 30 days is the GDPR default]]`.

Self-service data export and account deletion are not yet surfaced in the
product's interface (the backend exposes export and delete endpoints, but the
web interface does not yet offer them). Until the interface ships, deletion and
export are handled by email: contact support@cerebrum-dev.com and we will
complete your request within 30 days.

## 6. Security

Passwords are stored as salted PBKDF2 hashes. Login tokens and API keys are
stored hashed. Traffic is served over HTTPS. Access to the production
environment is limited to the operator.

Our personal-data incident process is documented in
`docs/runbooks/INCIDENT_PERSONAL_DATA.md`: detect, contain, assess within
24 hours, notify affected customers without undue delay and any supervisory
authority within 72 hours where GDPR applies, then run a post-mortem.
`[[NEEDS INPUT: counsel to review and finalise the breach-notification wording.]]`

## 7. Cookies and local storage

The Service stores your login token in your browser's local storage so you stay
signed in. Only strictly necessary cookies (session sign-in) are used. No
analytics or advertising cookies are used, so no cookie banner is required.

## 8. Children

The Service is not intended for children under
`[[NEEDS INPUT: age, matching the Terms]]` and we do not knowingly collect
their data.

## 9. International transfers

Your data is processed in the United States and potentially elsewhere by the
processors listed in section 2. `[[NEEDS INPUT: if EU/UK users are in scope,
identify the transfer mechanism — Standard Contractual Clauses, adequacy, or
otherwise — for each processor.]]`

## 10. Changes

We will post changes here and, where material, notify you by email.

---

### Engineering work this policy implies

These are product gaps this document exposes, listed so they are not lost:

1. **Self-service data export and account deletion.** Neither exists today.
2. **Defined retention, and a job that enforces it.** Nothing expires now.
3. **A monitored privacy inbox** for data-subject requests.
4. **Signed DPAs** with AWS, DeepSeek, Stripe and Sentry (and OpenRouter,
   if the fallback leg is armed).
5. **A documented breach-notification process** meeting the 72-hour rule.
6. **Confirmation of what DeepSeek does with submitted content** — this is
   the single highest-impact unknown on this page.
