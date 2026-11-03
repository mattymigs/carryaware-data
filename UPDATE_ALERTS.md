# CarryAwareNJ typed update feed

`nj_legal_updates.json` remains the single source of truth for remotely managed
CarryAwareNJ alerts and archive entries. The filename is retained so installed
app versions continue working.

## Update types

| `type` value | App label | Firebase topic |
| --- | --- | --- |
| `legal` | Legal & Legislative | `carryaware-legal` |
| `community` | Community News | `carryaware-community` |
| `safety` | Safety Notices | `carryaware-safety` |
| `appAnnouncement` | App Announcements | `carryaware-app` |

Every update ID must be globally unique. Adding a new item with
`"isImportant": true` makes it eligible for a one-time popup and, after push
delivery is configured, sends it to the matching Firebase topic when the change
lands on `main`.

Editing an existing ID updates the archive and popup feed without sending a
second push. Use the workflow's manual `update_id` input only when an intentional
resend is required.

## Publishing checklist

1. Add the complete item to `nj_legal_updates.json`.
2. Use one of the four exact `type` values above.
3. Use a new, descriptive ID.
4. Link directly to the original source.
5. Run the feed validator or open a pull request.
6. Merge to `main` only after the summary and destination link are verified.

## One-time push configuration

The iOS app must first ship with Firebase Messaging and Push Notifications
enabled. Upload an Apple Push Notification authentication key for the
CarryAwareNJ app in Firebase Console.

Use a dedicated Google service account that can send Firebase Cloud Messaging
messages. Allow this repository to impersonate it through the existing GitHub
Actions Workload Identity provider, then define these repository variables:

- `GCP_WIF_PROVIDER`: the provider's complete resource name
- `GCP_FCM_SERVICE_ACCOUNT`: the dedicated sender service-account email

The workflow uses only short-lived credentials. Do not add an Apple key,
service-account JSON file, access token, or other credential to this repository.
