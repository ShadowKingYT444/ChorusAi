"""
Local tester UI: FastAPI routes that delegate to `agent_backend.agent_invoke.complete_chorus`.

Run from repo root:

  uvicorn agent_backend.test_invoke.app:app --reload --port 8765

Then: open http://127.0.0.1:8765/chat for a browser UI, or POST http://127.0.0.1:8765/invoke with JSON.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from agent_backend.agent_invoke import CHAT_COMPLETIONS_URL, DEFAULT_MODEL, ChorusInvokeError, complete_chorus

app = FastAPI(title="Chorus chat invoke tester", version="0.1.0")


class InvokeBody(BaseModel):
    persona: str = Field(..., description="System persona (orchestrator-chosen in prod).")
    context: str = Field(..., description="### Context body.")
    prompt: str = Field(..., description="### Prompt body.")
    data: str | None = Field(None, description="Optional ### Data section; omitted if empty.")
    policy: str = Field(
        default="Answer concisely in plain UTF-8 text only.",
        description="Appended after persona in system message.",
    )
    model: str = Field(default=DEFAULT_MODEL)
    max_tokens: int = Field(default=256, ge=1, le=128_000)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    job_id: str = Field(default="test-job")
    slot_id: str = Field(default="test-slot")
    round_no: int = Field(default=1, ge=1, alias="round")

    model_config = {"populate_by_name": True}


@app.get("/chat", response_class=HTMLResponse)
def chat_page() -> str:
    model_js = DEFAULT_MODEL.replace("\\", "\\\\").replace('"', '\\"')
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Chorus tester - chat</title>
  <style>
    :root {{
      font-family: system-ui, sans-serif;
      --bg: #0f1115;
      --panel: #1a1d24;
      --text: #e8eaed;
      --muted: #9aa0a6;
      --accent: #8ab4f8;
      --border: #2d323c;
    }}
    body {{ margin: 0; background: var(--bg); color: var(--text); min-height: 100vh; display: flex; flex-direction: column; }}
    header {{ padding: 0.75rem 1rem; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }}
    header code {{ color: var(--accent); font-size: 0.85rem; }}
    main {{ flex: 1; display: grid; grid-template-columns: minmax(200px, 280px) 1fr; gap: 0; min-height: 0; }}
    @media (max-width: 720px) {{ main {{ grid-template-columns: 1fr; }} }}
    aside {{ background: var(--panel); border-right: 1px solid var(--border); padding: 1rem; display: flex; flex-direction: column; gap: 0.75rem; overflow: auto; }}
    aside label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }}
    aside textarea, aside input {{ width: 100%; box-sizing: border-box; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 0.5rem; font: inherit; }}
    aside textarea {{ min-height: 4.5rem; resize: vertical; }}
    #log {{ flex: 1; overflow: auto; padding: 1rem; display: flex; flex-direction: column; gap: 0.75rem; }}
    .bubble {{ max-width: 85%; padding: 0.65rem 0.85rem; border-radius: 12px; line-height: 1.45; white-space: pre-wrap; }}
    .bubble.user {{ align-self: flex-end; background: #394867; }}
    .bubble.assistant {{ align-self: flex-start; background: var(--panel); border: 1px solid var(--border); }}
    .bubble.err {{ align-self: center; background: #5c2b29; border: 1px solid #8a3a36; }}
    .bubble.meta {{ align-self: center; font-size: 0.8rem; color: var(--muted); background: transparent; border: 1px dashed var(--border); }}
    footer {{ padding: 0.75rem 1rem; border-top: 1px solid var(--border); display: flex; gap: 0.5rem; align-items: flex-end; background: var(--panel); }}
    footer textarea {{ flex: 1; min-height: 2.5rem; max-height: 8rem; resize: vertical; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 0.6rem; font: inherit; }}
    button {{ padding: 0.55rem 1rem; border-radius: 8px; border: none; background: var(--accent); color: #0f1115; font-weight: 600; cursor: pointer; }}
    button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    button.secondary {{ background: transparent; color: var(--muted); border: 1px solid var(--border); }}
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <header>
    <strong>Chorus tester</strong>
    <span style="color:var(--muted)">→</span>
    <code>{CHAT_COMPLETIONS_URL}</code>
    <span style="flex:1"></span>
    <a href="/">JSON example</a>
  </header>
  <main>
    <aside>
      <div>
        <label for="persona">Persona (system)</label>
        <textarea id="persona">You are a helpful assistant. Answer clearly and concisely.</textarea>
      </div>
      <div>
        <label for="context">Context</label>
        <textarea id="context">Local test chat. The user types below; you see prior turns in the prompt.</textarea>
      </div>
      <div>
        <label for="model">Model id</label>
        <input id="model" type="text" value="{model_js}"/>
      </div>
      <div>
        <label for="max_tokens">max_tokens</label>
        <input id="max_tokens" type="number" value="512" min="1"/>
      </div>
      <div>
        <label for="temperature">temperature</label>
        <input id="temperature" type="number" value="0.7" min="0" max="2" step="0.1"/>
      </div>
      <button type="button" class="secondary" id="clear">Clear chat</button>
    </aside>
    <section style="display:flex;flex-direction:column;min-height:0;">
      <div id="log" aria-live="polite"></div>
      <footer>
        <textarea id="msg" rows="2" placeholder="Message… (Enter to send, Shift+Enter newline)"></textarea>
        <button type="button" id="send">Send</button>
      </footer>
    </section>
  </main>
  <script>
    const log = document.getElementById("log");
    const msg = document.getElementById("msg");
    const sendBtn = document.getElementById("send");
    const history = [];

    function addBubble(role, text) {{
      const el = document.createElement("div");
      el.className = "bubble " + role;
      el.textContent = text;
      log.appendChild(el);
      log.scrollTop = log.scrollHeight;
    }}

    function buildPrompt(userText) {{
      if (!history.length) {{
        return "User: " + userText + "\\n\\nRespond as Assistant.";
      }}
      const lines = history.map(m => (m.role === "user" ? "User: " : "Assistant: ") + m.text);
      lines.push("User: " + userText);
      return lines.join("\\n") + "\\n\\nRespond as Assistant to the last user message only.";
    }}

    async function sendMessage() {{
      const text = msg.value.trim();
      if (!text) return;
      msg.value = "";
      addBubble("user", text);
      history.push({{ role: "user", text }});
      sendBtn.disabled = true;
      const round = history.length;
      const body = {{
        persona: document.getElementById("persona").value.trim(),
        context: document.getElementById("context").value.trim(),
        prompt: buildPrompt(text),
        data: null,
        model: document.getElementById("model").value.trim() || "{model_js}",
        max_tokens: parseInt(document.getElementById("max_tokens").value, 10) || 512,
        temperature: parseFloat(document.getElementById("temperature").value) || 0.7,
        job_id: "ui-chat",
        slot_id: "local",
        round: round
      }};
      try {{
        const r = await fetch("/invoke", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(body)
        }});
        const j = await r.json().catch(() => ({{}}));
        if (!r.ok) {{
          const detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j);
          addBubble("err", "Error " + r.status + ": " + detail);
          history.pop();
          return;
        }}
        const reply = (j.content != null && j.content !== "") ? String(j.content) : "(empty content)";
        addBubble("assistant", reply);
        history.push({{ role: "assistant", text: reply }});
      }} catch (e) {{
        addBubble("err", String(e));
        history.pop();
      }} finally {{
        sendBtn.disabled = false;
        msg.focus();
      }}
    }}

    document.getElementById("send").addEventListener("click", sendMessage);
    msg.addEventListener("keydown", (e) => {{
      if (e.key === "Enter" && !e.shiftKey) {{ e.preventDefault(); sendMessage(); }}
    }});
    document.getElementById("clear").addEventListener("click", () => {{
      history.length = 0;
      log.innerHTML = "";
      addBubble("meta", "Chat cleared.");
    }});
    addBubble("meta", "Type a message and press Send or Enter. Ensure your LLM server is running (" + {repr(CHAT_COMPLETIONS_URL)} + ").");
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/><title>Chorus invoke tester</title></head>
<body>
  <h1>Chorus invoke tester</h1>
  <p><a href="/chat"><strong>Open chat UI</strong></a> - talk to your hardcoded LLM in the browser.</p>
  <p>Upstream: <code>{CHAT_COMPLETIONS_URL}</code></p>
  <p>Use <code>POST /invoke</code> with JSON, e.g.:</p>
  <pre id="ex"></pre>
  <script>
    const ex = {{
      "persona": "You are a careful assistant.",
      "context": "The user is testing the Chorus contract.",
      "prompt": "Reply with one short sentence confirming you received the prompt.",
      "data": null,
      "model": "{DEFAULT_MODEL}",
      "max_tokens": 128,
      "temperature": 0.3,
      "job_id": "test-job",
      "slot_id": "slot-0",
      "round": 1
    }};
    document.getElementById("ex").textContent = JSON.stringify(ex, null, 2);
  </script>
</body>
</html>"""


@app.post("/invoke")
async def invoke(body: InvokeBody) -> dict[str, Any]:
    try:
        text, raw = await complete_chorus(
            persona=body.persona,
            context=body.context,
            prompt=body.prompt,
            data=body.data,
            policy=body.policy,
            model=body.model,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
            job_id=body.job_id,
            slot_id=body.slot_id,
            round_no=body.round_no,
            return_raw=True,
        )
    except ChorusInvokeError as e:
        status = e.http_status if e.http_status is not None else 502
        raise HTTPException(
            status_code=status,
            detail=e.upstream_body if e.upstream_body else str(e),
        ) from e

    return {
        "upstream_url": CHAT_COMPLETIONS_URL,
        "content": text,
        "raw": raw,
    }
