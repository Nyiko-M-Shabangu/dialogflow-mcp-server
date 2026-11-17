# ADR 0003: Constant-time comparison of the raw Authorization header against both tokens

- Status: Accepted
- Date: 2026-11-17

## Context

The remote server accepts a user token and, optionally, an admin token. Token checks should not leak, through timing, either the token content or which of the two tokens matched.

`BearerAuthASGIMiddleware` builds the expected header values (`b"Bearer <token>"`) once at startup. For each request it:

- takes the raw `Authorization` header bytes;
- runs `hmac.compare_digest(provided, expected_user)`;
- runs `hmac.compare_digest(provided, expected_admin)` whenever an admin token is configured, even if the user token already matched.

`compare_digest` is constant-time for equal-length inputs. On a length mismatch it returns early, which reveals only the length.

Alternatives considered:

- Hash both sides with SHA-256 first and compare the 32-byte digests. This removes the length signal. Tokens must be at least 16 characters, and `secrets.token_urlsafe(32)` gives 43, so knowing the length gives no practical advantage.
- Parse the scheme and the token separately and accept `bearer` in any case. That is a behaviour change, not hardening.

## Decision

Keep the current comparison: raw header bytes, `hmac.compare_digest`, both tokens compared on every request with no short-circuit.

## Consequences

- The scheme is case-sensitive. `bearer <token>` gets 401.
- In theory the length of the expected header is observable through timing. This is accepted.
- The same function is used at startup to reject an admin token equal to the user token.
- To change: hashing both sides is a few lines in the middleware's `__init__` and `__call__`, if a security review asks for it.

Implementation notes: in place in [`remote_server.py`](../../src/dialogflow_mcp/remote_server.py). `tests/test_unit.py` counts `compare_digest` calls per request (two with an admin token configured, one without, for matching, wrong, empty and oversized headers), and covers accept, reject and the lowercase-scheme case.
