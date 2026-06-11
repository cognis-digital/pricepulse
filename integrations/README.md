# Integrations

## webhook.py

A minimal, dependency-free forwarder. Pipe any pricepulse JSON to a webhook
(Slack/Teams incoming webhook, a SIEM HTTP collector, a Jira automation rule):

```bash
pricepulse diff https://acme.example/pricing --format json \
  | python integrations/webhook.py --url "$SLACK_WEBHOOK_URL"
```

Or let the tool POST directly on change:

```bash
pricepulse diff  https://acme.example/pricing --webhook "$SLACK_WEBHOOK_URL"
pricepulse watch https://acme.example/pricing --interval 3600 --webhook "$SLACK_WEBHOOK_URL"
```

The webhook receives the full diff record (`changed`, `changes[]`, `current`)
as JSON. Add auth headers with `--header "Authorization: Bearer EXAMPLE_NOT_A_REAL_KEY"`.
