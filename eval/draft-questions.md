# Draft question set — review, verify, adapt (32 questions)

Source-verified against `samples/ngo-demo/`. This is the working draft: read it,
**verify each expected answer against the source**, correct anything wrong, then
hand the relevant ones to your real NGO users and swap the facts for *their*
documents.

**Roles:** `admin` sees all · `staff` sees data-protection + grant-faq ·
`field` sees field-handbook only.

## Data Protection & Confidentiality Policy (`data-protection-policy.md`) — staff

| # | Question | Must contain | Must NOT say (trap) |
|---|---|---|---|
| q01 | How quickly must a personal data breach be reported to the DPO? | 24 hours | 72 hours, 48 hours |
| q02 | How long are beneficiary records retained? | 3 years | 5 years, 1 year |
| q03 | How long must financial records be kept? | 10 years | — |
| q04 | Within how many days must we respond to a subject access request? | 30 days | 7 days, 14 days |
| q14 | Under what condition may donor contact details be kept? | consent | — |
| q15 | Rule for sharing donor/beneficiary data internally? | need-to-know | — |
| q16 | When must the supervisory authority be notified of a breach? | supervisory authority | — |
| q27 | Which data-protection laws does the policy follow? | fadp, gdpr | — |

## IT Support SOP (`it-sop.md`) — admin

| # | Question | Must contain | Must NOT say (trap) |
|---|---|---|---|
| q05 | Minimum password length? | 12 characters | 8, 10 characters |
| q06 | How long are file-server backups retained? | 30 days | — |
| q07 | In a suspected breach, within how many hours must IT notify the DPO? | 4 hours | — |
| q17 | When should IT force a password reset? | lost or stolen | — |
| q18 | What must IT do before handing a new device over? | full-disk encryption, checklist | — |
| q19 | How long is a monthly backup snapshot kept? | 12 months | — |
| q20 | How often should a restore be tested? | quarter | — |
| q21 | What should IT do the day someone leaves? | disable, revoke | — |

## Field Worker Handbook (`field-worker-handbook.md`) — field

| # | Question | Must contain | Must NOT say (trap) |
|---|---|---|---|
| q08 | Info required when registering a beneficiary? | full name, date of birth, village or commune, service requested | — |
| q09 | What to do if the offline app shows "sync pending"? | local copy, it support | — |
| q10 | Who to contact for a data protection question? | data protection officer | — |
| q22 | What to do if a beneficiary asks about their own data? | team lead, same day | — |
| q23 | Email to report a lost device? | it-support@example-ngo.org | — |
| q24 | Who to contact for an urgent safety concern? | field coordinator | — |

## Grants & Donor Reporting FAQ (`grant-faq.md`) — staff

| # | Question | Must contain | Must NOT say (trap) |
|---|---|---|---|
| q11 | Standard indirect-cost rate? | 7% | — |
| q12 | When is the quarterly report due? | 30 days | — |
| q13 | Can funds move between budget lines without approval? | donor approval, 10% | — |
| q25 | Documentation for an expense over 25 CHF? | receipt | — |
| q26 | Advance notice if a deliverable is late? | two weeks | — |

## Out-of-scope — the grounding guard must refuse (not_found)

| # | Question | Must NOT say (trap) |
|---|---|---|
| q28 | Weather forecast for Phnom Penh next week? | — |
| q29 | Which field survey app does the org recommend? | survey123, kobotoolbox |
| q30 | The organisation's bank account number? | — |

## Role-scoping negatives — a role must NOT see another role's document

| # | Question | Role | Must NOT say (trap) |
|---|---|---|---|
| q31 | Minimum password length (it-sop is admin-only)? | field | 12 characters |
| q32 | In a field medical emergency, who must be notified (field-handbook is field-only)? | staff | field coordinator |

---

**Verification checklist before use:**
- [ ] Every "must contain" phrase appears verbatim in the source document.
- [ ] Every trap is a genuinely wrong answer (a number/fact the model might
      plausibly invent).
- [ ] Role assignments match your real access model (the demo roles are
      simplified — your real RBAC may differ).
- [ ] For real use: replace `source` + facts with **your NGO's actual documents**
      and the questions your users actually ask.
