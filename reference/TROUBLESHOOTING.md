# Troubleshooting Guide

常見問題排解。

## Quick Diagnostics

Run the doctor script:

```bash
cd <path-to-han-agents>
python scripts/doctor.py
```

---

## Common Issues

### 1. "HAN system not initialized"

**Symptom:**
```
NotInitializedError: HAN system not initialized.
```

**Solution:**
```python
from servers.facade import init
init('/path/to/project', 'project-name')
```

---

### 2. "Project path not found"

**Symptom:**
```
ProjectNotFoundError: Project path not found: /path/to/project
```

**Solution:**
1. Verify path exists: `ls /path/to/project`
2. Check permissions: `ls -la /path/to/project`
3. Use absolute path, not relative

---

### 3. "Code Graph is empty"

**Symptom:**
```
CodeGraphEmptyError: Code Graph is empty for project 'my-project'.
```

**Solution:**
```python
from servers.facade import sync
sync('/path/to/project', 'my-project')
```

---

### 4. Import Errors

**Symptom:**
```
ModuleNotFoundError: No module named 'servers'
```

**Solution:**
Add path before import:
```python
import sys, os
from servers import HAN_BASE_DIR
sys.path.insert(0, HAN_BASE_DIR)
from servers.facade import sync
```

---

### 5. Database Locked

**Symptom:**
```
sqlite3.OperationalError: database is locked
```

**Cause:** Multiple processes accessing brain.db

**Solution:**
1. Close other Claude Code instances
2. Wait a moment and retry
3. If persists, restart Claude Code

---

### 6. Task Not Found

**Symptom:**
```
Task 'xxx-001' not found
```

**Solution:**
```python
from servers.memory import get_project_context

# Find active tasks
context = get_project_context('my-project')
print(context['active_tasks'])
```

---

### 7. Checkpoint Not Loading

**Symptom:**
```python
checkpoint = load_checkpoint('xxx-001')
# Returns None
```

**Possible causes:**
1. Wrong task ID
2. Checkpoint never saved
3. Task from different project

**Solution:**
```python
# List all checkpoints for project
from servers.memory import list_checkpoints
checkpoints = list_checkpoints('my-project')
```

---

### 8. SSOT Not Loading

**Symptom:**
```
SSOT files not found
```

**Solution:**
1. Check INDEX.md exists:
   ```bash
   ls <han-agents>/brain/ssot/PROJECT_INDEX.md
   # or for project-level
   ls .claude/pfc/INDEX.md
   ```

2. Verify format:
   ```yaml
   flows:
     - id: flow.auth
       name: Auth Flow
       ref: docs/flows/auth.md
   ```

3. Sync SSOT graph:
   ```python
   from servers.facade import sync_ssot_graph
   sync_ssot_graph('my-project')
   ```

---

### 9. Agent Not Responding Correctly

**Symptom:** Agent doesn't follow expected behavior

**Checklist:**
1. Using correct subagent_type?
2. TASK_ID included in prompt?
3. For Critic: ORIGINAL_TASK_ID included?

**Correct formats:**

```python
# Executor
Agent(
    subagent_type='executor',
    prompt=f'''TASK_ID = "{task_id}"
...'''
)

# Critic
Agent(
    subagent_type='critic',
    prompt=f'''TASK_ID = "{critic_id}"
ORIGINAL_TASK_ID = "{original_task_id}"
...'''
)
```

---

### 10. Drift Detection False Positives

**Symptom:** Drift reported but code is correct

**Possible causes:**
1. SSOT out of date
2. Code Graph not synced
3. Different naming conventions

**Solution:**
1. Sync Code Graph: `sync('/path/to/project')`
2. Sync SSOT Graph: `sync_ssot_graph('project')`
3. Update SSOT if code is the source of truth

---

## Database Issues

### Reset Database

**Warning:** This deletes all data!

```bash
rm <han-agents>/brain/brain.db
python <han-agents>/scripts/install.py
```

### Backup Database

```bash
cp <han-agents>/brain/brain.db \
   <han-agents>/brain/brain.db.backup.$(date +%Y%m%d)
```

### Restore Backup

```bash
cp <han-agents>/brain/brain.db.backup.20250105 \
   <han-agents>/brain/brain.db
```

---

## Performance Issues

### Slow Sync

**Symptom:** Code Graph sync takes too long

**Solutions:**
1. Use incremental sync (default)
2. Add ignores to `.gitignore`:
   ```
   node_modules/
   .git/
   dist/
   build/
   ```
3. Sync only changed files

### Large Memory Usage

**Symptom:** High memory consumption

**Solutions:**
1. Close unused IDE instances
2. Use `search_memory_semantic` with `limit` parameter
3. Clean up old working memory

---

## Hook Issues

### Hook Not Triggering

**Symptom:** PostToolUse hook not running

**Check:**
1. Hook file exists: `<han-agents>/hooks/post_task.py`
2. Hook registered in Claude Code settings
3. Check hook logs: `<han-agents>/hooks/hook.log`

### Hook Errors

**Check hook log:**
```bash
tail -50 <han-agents>/hooks/hook.log
```

---

## Getting Help

### Collect Debug Info

```python
from servers.facade import status

# Get system status
s = status('my-project')
print(s)

# Get version
import servers
print(servers.__version__ if hasattr(servers, '__version__') else 'unknown')
```

### Check File Locations

```bash
# Database
ls -la <han-agents>/brain/brain.db

# SSOT
ls -la <han-agents>/brain/ssot/

# Agents
ls -la ~/.claude/agents/

# Skills
ls -la <han-agents>/
```

### Report Issues

Include:
1. Error message
2. Steps to reproduce
3. Output of `doctor.py`
4. Python version: `python --version`
5. OS version
