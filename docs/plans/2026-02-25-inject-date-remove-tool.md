# Inject Current Date, Remove get_current_date Tool

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the `get_current_date` MCP tool by injecting the current date into the system prompt per-turn, saving an LLM round-trip.

**Architecture:** Both `agent.py` and `api_server.py` already rebuild the system prompt each turn (preferences reload). We add the date in the same place. Then remove the tool from utility.py, mcp_server.py, __init__.py, tests, and docs.

**Closes:** #125

---

### Task 1: Inject date into agent.py system prompt

**Files:**
- Modify: `src/agent.py:603-608`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

In `tests/test_agent.py`, add a test that verifies the date appears in the system prompt. Add near the existing `test_load_preferences_reloaded_each_turn`:

```python
def test_system_prompt_includes_current_date(tmp_path):
    """System prompt should include current date after per-turn rebuild."""
    from datetime import datetime
    from unittest.mock import patch

    import agent as agent_module
    from agent import SYSTEM_PROMPT, load_preferences

    # Simulate the per-turn prompt rebuild logic from chat_loop
    with patch("agent.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 15)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        updated_prompt = SYSTEM_PROMPT
        preferences = load_preferences()
        if preferences:
            updated_prompt += preferences
        updated_prompt += f"\n\nCurrent date: {mock_dt.now().strftime('%Y-%m-%d')}"

    assert "Current date: 2026-03-15" in updated_prompt
```

Actually — this tests our own test code, not the production code. The real test is that `chat_loop` injects the date. But `chat_loop` is an interactive async loop that's hard to unit test. A better approach: extract the prompt-building into a function and test that.

**Step 1 (revised): Extract `_build_system_prompt` helper in agent.py and write a test**

Add to `tests/test_agent.py`:

```python
def test_build_system_prompt_includes_date(tmp_path):
    """_build_system_prompt should append the current date."""
    import agent as agent_module
    from agent import _build_system_prompt

    original_prefs_file = agent_module.PREFERENCES_FILE
    try:
        agent_module.PREFERENCES_FILE = tmp_path / "Preferences.md"
        prompt = _build_system_prompt()
        # Should end with current date line
        today = datetime.now().strftime("%Y-%m-%d")
        assert f"Current date: {today}" in prompt
    finally:
        agent_module.PREFERENCES_FILE = original_prefs_file
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent.py::test_build_system_prompt_includes_date -v`
Expected: FAIL — `_build_system_prompt` doesn't exist yet in agent.py

**Step 3: Implement `_build_system_prompt` in agent.py and use it in `chat_loop`**

Add function after `load_preferences()` (~line 97):

```python
def _build_system_prompt() -> str:
    """Build system prompt with preferences and current date."""
    from datetime import datetime

    prompt = SYSTEM_PROMPT
    preferences = load_preferences()
    if preferences:
        prompt += preferences
    prompt += f"\n\nCurrent date: {datetime.now().strftime('%Y-%m-%d')}"
    return prompt
```

Then replace lines 603-608 in `chat_loop`:

```python
            # Before (remove):
            updated_prompt = SYSTEM_PROMPT
            preferences = load_preferences()
            if preferences:
                updated_prompt += preferences
            messages[0]["content"] = updated_prompt

            # After:
            messages[0]["content"] = _build_system_prompt()
```

Note: `datetime` is already imported at the top of agent.py, so use it directly (no local import needed). Just add the date line to the function.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent.py::test_build_system_prompt_includes_date -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agent.py tests/test_agent.py
git commit -m "feat: inject current date into agent system prompt"
```

---

### Task 2: Inject date into api_server.py system prompt

**Files:**
- Modify: `src/api_server.py:124-130`
- Test: `tests/test_session_management.py`

**Step 1: Write the failing test**

In `tests/test_session_management.py`, add a test near `test_preferences_reloaded_per_request`:

```python
@patch("api_server.agent_turn", new_callable=AsyncMock)
@patch("api_server.load_preferences")
def test_system_prompt_includes_current_date(self, mock_load_prefs, mock_agent_turn):
    """System prompt should include current date each request."""
    mock_agent_turn.return_value = "response"
    mock_load_prefs.return_value = None

    with self.client as client:
        client.post("/chat", json={"message": "hello"})

    # Check the system message passed to agent_turn
    call_kwargs = mock_agent_turn.call_args
    messages = call_kwargs[0][2]  # 3rd positional arg
    system_content = messages[0]["content"]
    today = datetime.now().strftime("%Y-%m-%d")
    assert f"Current date: {today}" in system_content
```

Note: check existing test imports — `datetime` may need importing. Also check how `mock_agent_turn` is called (positional vs keyword) to get the messages arg correctly.

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_session_management.py::TestSessionManagement::test_system_prompt_includes_current_date -v`
Expected: FAIL — date not in system prompt

**Step 3: Update `_build_system_prompt` in api_server.py**

```python
def _build_system_prompt() -> str:
    """Build system prompt with current user preferences and date appended."""
    from datetime import datetime

    system_prompt = app.state.system_prompt
    preferences = load_preferences()
    if preferences:
        system_prompt += preferences
    system_prompt += f"\n\nCurrent date: {datetime.now().strftime('%Y-%m-%d')}"
    return system_prompt
```

Note: `datetime` may already be imported in api_server.py — check first and use module-level import if available.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_session_management.py::TestSessionManagement::test_system_prompt_includes_current_date -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/api_server.py tests/test_session_management.py
git commit -m "feat: inject current date into API server system prompt"
```

---

### Task 3: Remove get_current_date tool

**Files:**
- Modify: `src/tools/utility.py` — remove function
- Modify: `src/tools/__init__.py` — remove import and `__all__` entry
- Modify: `src/mcp_server.py` — remove import and registration
- Modify: `tests/test_tools_utility.py` — remove `TestGetCurrentDate` class and import

**Step 1: Remove the function from utility.py**

Delete `get_current_date` function (lines 36-42). Also remove `datetime` import if `log_interaction` doesn't use it (it doesn't — check). Update module docstring.

**Step 2: Remove from __init__.py**

Remove `get_current_date` from the import and from `__all__`.

**Step 3: Remove from mcp_server.py**

Remove `get_current_date` from the import line and the `mcp.tool()(get_current_date)` registration.

**Step 4: Remove test class**

Remove `TestGetCurrentDate` class from `tests/test_tools_utility.py`. Remove `get_current_date` from the import. Remove `re` and `datetime` imports if no longer used by remaining tests.

**Step 5: Run all tests**

Run: `.venv/bin/python -m pytest tests/test_tools_utility.py tests/test_agent.py tests/test_session_management.py -v`
Expected: All PASS, no references to `get_current_date`

**Step 6: Verify no remaining references**

Run: `grep -r "get_current_date" src/ tests/` — should return nothing.

**Step 7: Commit**

```bash
git add src/tools/utility.py src/tools/__init__.py src/mcp_server.py tests/test_tools_utility.py
git commit -m "refactor: remove get_current_date tool"
```

---

### Task 4: Update documentation

**Files:**
- Modify: `system_prompt.txt.example` — remove `get_current_date` from tool reference
- Modify: `CLAUDE.md` — remove from tree listing and tool table
- Modify: `README.md` — remove from tool table (if still referenced)

**Step 1: Update system_prompt.txt.example**

Remove line 199: `- get_current_date: Get today's date in YYYY-MM-DD format.`

**Step 2: Update CLAUDE.md**

- Line 28: Change `utility.py       # log_interaction, get_current_date` → `utility.py       # log_interaction`
- Line 79: Remove the `get_current_date` row from the MCP Tools table

**Step 3: Update README.md**

Remove line 217: the `get_current_date` row.

**Step 4: Commit**

```bash
git add system_prompt.txt.example CLAUDE.md README.md
git commit -m "docs: remove get_current_date references"
```

---

### Task 5: Full test suite verification and PR

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass, no regressions

**Step 2: Create PR**

```bash
gh pr create --title "Inject current date into system prompt, remove get_current_date tool" --body "..."
```

Closes #125.
