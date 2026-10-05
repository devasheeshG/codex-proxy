# Context-window policy

The proxy exposes `allow_extended_context` in preset and user policy records. It is false by default. The Codex `/models` response is generated per API-key owner: standard users receive `context_window` and `max_context_window` of 272,000; opted-in users receive 1,000,000 plus an automatic-compaction limit of 900,000.

The migration enables the field for the existing user named `Devasheesh` and leaves all other users disabled.

Explicitly selecting a user override persists that choice even when the value matches the preset. Only clearing the override restores inheritance. Devasheesh's protected extended-context setting remains enabled and marked as an override when he has a preset.
