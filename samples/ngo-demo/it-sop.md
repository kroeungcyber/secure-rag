# IT Support Standard Operating Procedures

> **Demo fixture** — fictional sample content for testing secure-rag, not real
> organisational data.

## Password policy

Passwords must be at least 12 characters and unique per system. Staff reset
their own passwords via the self-service portal. IT support may reset a password
only after verifying the requestor's identity with a second factor.

Force a password reset when a device is reported lost or stolen.

## Device onboarding

New devices are enrolled in Microsoft Entra before first use. IT support
provisions the device, installs the standard software set, and enables full-disk
encryption before handing it over. The handover checklist must be signed.

## Backup schedule

The file server backs up nightly at 02:00 local time. Backups are retained for
30 days, with one monthly snapshot kept for 12 months. Test a restore every
quarter — a backup you have not tested is not a backup.

## Incident response

For a suspected breach: isolate the affected device from the network, preserve
logs, and notify the data protection officer within 4 hours. Do not delete or
modify any files on the device until the investigation is complete.

## Offboarding

When someone leaves, disable their account the same day and revoke their
Microsoft 365 licenses. Return their hardware to inventory and wipe it before
re-issue.
