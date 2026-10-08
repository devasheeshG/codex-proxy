# Automatic pool reset policy

Normal available accounts are always tried before spending a reset credit.
When none can serve the requested model, recovery selects an opted-in account
with exhausted weekly quota and known available credits. The lowest-numbered
priority group with usable credits wins first. Within that group, the latest
natural weekly reset date wins; unknown dates sort last. If a provider check
finds no usable credits in a group, recovery proceeds to the next priority.
Within the chosen account, the earliest-expiring valid credit wins.

Preflight, request failover, post-response handling, and the quota refresher
share this policy. Busy-but-otherwise-available accounts still count as pool
capacity. The refresher probes the entire pool before making a decision.
A PostgreSQL transaction advisory lock serializes reset decisions across all
workers, and fresh provider usage is checked before consumption. After a
successful reset, provider-confirmed usable quota clears the quota cooldown.
Recovery stops after the first successful redemption or restored capacity.
Manual reset actions retain their explicit administrator safeguards.
