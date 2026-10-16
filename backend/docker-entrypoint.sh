#!/bin/sh
# Safe startup sequence: apply migrations, make sure built-in roles exist, then serve.
# One worker + threads on purpose: the embedded Chroma store and in-memory rate limiter are
# per-process (see docs/DEPLOYMENT.md before scaling out).
set -eu
flask db upgrade
flask seed-roles
exec gunicorn --bind 0.0.0.0:5000 --workers 1 --threads 8 --timeout 300 --access-logfile - wsgi:app
