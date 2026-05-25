# Vibe Coding Rules (Avoiding Common Pitfalls)

### 1. The "Iterate First" Principle (Avoid Reinventing the Wheel)
* **Iterate before creating:** Always look for existing code to build upon instead of writing from scratch.
* **Respect established patterns:** Do not drastically change architectures unless absolutely necessary.
* **Clean up replacements:** If introducing a new pattern, remove the old one immediately.

---

### 2. Laser Focus & Scope Discipline (Avoid Scope Creep)
* **Stay in your lane:** Only touch code relevant to the task.
* **Zero assumptions:** Only implement what is clearly required.
* **Think downstream:** Always consider what your changes might break.

---

### 3. Simplicity & Readability (Avoid Overengineering)
* **Keep it simple:** Prefer simple, readable solutions over clever ones.
* **Be concise:** Avoid unnecessary verbosity.
* **Clarity over magic:** If it’s hard to understand, it’s wrong.

---

### 4. Cleanliness & Structure (Avoid the Spaghetti Trap)
* **Consistent naming conventions:** Follow existing patterns.
* **Avoid duplication (DRY):** Reuse existing logic where possible.
* **Watch file size:** Keep files under ~200–300 lines.
* **Organized codebase:** Modular, predictable structure only.
* **No rogue scripts:** No one-off logic inside core files.

---

### 5. Comments & Documentation (Avoid Noise)
* **Comment only when necessary:** Don’t over-comment clean code.
* **Explain *why*, not *what*.**
* **Keep comments short and clear.**
* **No emojis in comments.**
* **Update or delete outdated comments immediately.**

---

### 6. Server & Environment Hygiene (Avoid Ghost Bugs)
* **Restart cleanly:** Kill old processes before running new ones.
* **Test fresh builds:** Always validate with a clean run.
* **Environment awareness:** Account for `dev`, `test`, `prod`.
* **Protect secrets:** Never modify `.env` without confirmation.

---

### 7. Data Integrity (Avoid Fake Data Leaks)
* **No hardcoding:** Use configs or environment variables.
* **Mocks are for tests only:** Never leak fake data into real environments.
* **Single source of truth:** Avoid duplicated logic/data.

---

### 8. Testing & Reliability (Avoid “It Works on My Machine”)
* **Test core flows:** Cover main functionality.
* **Handle edge cases:** Nulls, failures, unexpected input.
* **Fix broken tests immediately.**

---

### 9. Change Management (Avoid Breaking Working Systems)
* **Don’t rewrite blindly:** Work within existing systems first.
* **Avoid unnecessary refactors.**
* **Incremental changes > big rewrites.**

---

### 10. AI Awareness (Core Vibe Coding Discipline)
* **Don’t trust blindly:** Always review AI-generated code.
* **Understand before shipping.**
* **Use AI iteratively, not destructively.**

---

### 11. Security & Sensitive Data (CRITICAL 🚨)
* **Never commit sensitive files:** Files like `.env`, API keys, private configs must NEVER enter version control. :contentReference[oaicite:0]{index=0}  
* **Always use `.gitignore`:** Ensure sensitive files are explicitly ignored (`.env`, `.env.local`, `secrets.*`). :contentReference[oaicite:1]{index=1}  
* **Assume leaks are permanent:** Once secrets are committed, they remain in Git history even after deletion. :contentReference[oaicite:2]{index=2}  
* **Use environment variables:** Never hardcode credentials in code — inject them at runtime. :contentReference[oaicite:3]{index=3}  
* **Commit templates, not secrets:** Use `.env.example` with placeholders only. :contentReference[oaicite:4]{index=4}  
* **Audit `.gitignore` regularly:** Missing one file can expose the entire system. :contentReference[oaicite:5]{index=5}  
* **Never log secrets:** Avoid printing tokens, keys, or credentials anywhere.  
* **Separate config vs secrets:** Not everything in `.env` should be sensitive — know the difference. :contentReference[oaicite:6]{index=6}  

---

# Golden Rules (Pin This)

* **Iterate > Reinvent**  
* **Simple > Clever**  
* **Scoped > Scattered**  
* **Clean > Hacks**  
* **Understand > Blind Trust**  
* **Secure > Convenient**  