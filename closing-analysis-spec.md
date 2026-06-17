# Closing Analysis Spec

This document describes a future optimization step: compare classifier scores with real sales outcomes from a hot-lead follow-up sequence.

## Outcome Dataset

Recommended export:

```csv
contact_email,hot_score,tagged_hot_lead_at,entered_hot_sequence_at,purchased_at,purchase_amount,closed
email@example.com,42.5,2026-06-01T09:00:00Z,2026-06-01T09:05:00Z,2026-06-05T18:10:00Z,247,true
```

## Questions to Answer

- Which score band converts best?
- Is the `35` threshold too low or too high?
- Do clicks predict purchases better than opens?
- Is recency weighted strongly enough?
- How much time passes between the `hot-lead` tag and purchase?
- Which leads are false positives?
- Which buyers were false negatives and were not tagged?

## Initial Score Bands

```text
25-34 = warm
35-49 = light hot
50-74 = hot
75+ = very hot
```

## Metrics

- leads per score band;
- buyers per score band;
- conversion rate per score band;
- revenue per score band;
- revenue per lead;
- average time from tag to purchase;
- conversion within 7, 14, and 30 days.

## Final Decision

Update `config.json` only when the data shows a clear pattern.

Examples:

- If `35-49` converts poorly and overloads the follow-up sequence, raise the threshold to `50`.
- If many buyers are in `25-34`, lower the threshold or increase the weight of recent clicks.
- If high opens do not convert, reduce `open_points`.
- If recent clicks convert strongly, increase `click_points` or `days_0_14`.
