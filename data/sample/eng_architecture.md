# SYNTHETIC DEMO DATA — Platform Architecture (Engineering)

## Payment Service
The payment service publishes events to the Kafka cluster and retries failed charges three times with exponential backoff.

## Deployments
Services are deployed to Kubernetes using blue green releases. Rollbacks complete within five minutes.
