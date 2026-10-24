# S0 — operational notes

## Security note (S0-1, SEC-1)

- The three provider API keys were committed to `.env.example` in this public
  repository and are therefore exposed in git history.
- They are intentionally kept live for now.
- They will be rotated before any real users use the platform.

## Server review (S0-4, SEC-3)

Server check performed by the operator:

- Ports **5433** and **6379** do **not** listen on the server.
- PostgreSQL runs **natively**, bound only to `127.0.0.1:5432`.
- Redis is **not running** on the server.

Conclusion: the SEC-3 exposed-port attack surface does not exist on the
current server. (The compose hardening in S0-3 applies to local/CI containers.)
