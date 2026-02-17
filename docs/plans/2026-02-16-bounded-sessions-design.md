# Bounded Session Management

## Problem
`api_server.py` has two unbounded growth vectors:
1. `file_sessions` dict grows indefinitely (one session per unique `active_file`)
2. Each session's `messages` list grows without limit

## Design

### Session eviction (LRU)
- Replace `file_sessions: dict` with `OrderedDict`
- On access, move session to end (`move_to_end`)
- On insert, if `len > MAX_SESSIONS` (default 20), `popitem(last=False)` to evict oldest
- `MAX_SESSIONS` configurable in `config.py`

### Message cap (sliding window)
- After compaction in `/chat`, if `len(messages) > MAX_SESSION_MESSAGES` (default 50), trim
- Keep `messages[0]` (system prompt) + last `MAX_SESSION_MESSAGES - 1` messages
- Trim point safety: if trim lands inside a tool call group (assistant+tool messages), scan forward to the next `user` message to avoid orphaning tool results
- `MAX_SESSION_MESSAGES` configurable in `config.py`

### Testing
- LRU: oldest session evicted, recently-accessed session survives
- Message trim: system prompt preserved, old messages dropped
- Tool call group integrity: trim doesn't split assistant+tool sequences
- Configurable limits respected
