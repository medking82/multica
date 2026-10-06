// Workspace Skill picker. This single file runs in Multica's opaque plugin
// iframe; it receives no draft text or credentials. The host owns insertion.
const port = globalThis.__multicaPluginBridgePortV2;
if (!(port instanceof MessagePort)) throw new Error("Multica surface bridge is unavailable");
delete globalThis.__multicaPluginBridgePortV2;

const pending = new Map();
let sequence = 0;
port.onmessage = ({ data }) => {
  if (data?.kind === "theme") {
    for (const [name, value] of Object.entries(data.theme ?? {})) {
      document.documentElement.style.setProperty(name, value);
    }
    return;
  }
  const request = pending.get(data?.id);
  if (!request) return;
  pending.delete(data.id);
  if (data.ok) request.resolve(data.data);
  else request.reject(new Error(data.error ?? "Plugin call failed"));
};
port.start();

function request(payload) {
  const id = `r${++sequence}`;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    port.postMessage({ id, ...payload });
  });
}

function resize() {
  port.postMessage({ id: `size${++sequence}`, kind: "ui.resize", height: document.body.scrollHeight + 12 });
}

function escapeMarkdownLabel(label) {
  return label.replace(/[\[\]\\()]/g, "\\$&");
}

const root = document.getElementById("root");
root.innerHTML = `
  <main style="display:grid;gap:10px;padding:12px 14px;color:var(--foreground)">
    <label for="search" style="font-weight:600">Workspace Skills</label>
    <input id="search" type="search" autocomplete="off" placeholder="Find a Skill"
      style="padding:8px;border:1px solid var(--border);border-radius:var(--radius,6px);background:var(--background);color:var(--foreground)">
    <div id="skills" style="display:grid;gap:2px;max-height:340px;overflow:auto"></div>
    <div id="status" role="status" style="min-height:1.2em;color:var(--muted-foreground)">Loading…</div>
  </main>`;
const search = root.querySelector("#search");
const list = root.querySelector("#skills");
const status = root.querySelector("#status");
let skills = [];
let inserting = false;

function render() {
  const query = search.value.trim().toLowerCase();
  const matches = skills.filter((skill) =>
    skill.name.toLowerCase().includes(query) || (skill.description ?? "").toLowerCase().includes(query));
  list.replaceChildren();
  for (const skill of matches.slice(0, 40)) {
    const button = document.createElement("button");
    button.type = "button";
    button.style.cssText = "display:grid;gap:2px;width:100%;padding:8px;text-align:left;border-radius:var(--radius,6px);background:transparent;color:var(--foreground)";
    const name = document.createElement("strong");
    name.textContent = `/${skill.name}`;
    const description = document.createElement("span");
    description.style.color = "var(--muted-foreground)";
    description.textContent = skill.description ?? "";
    button.append(name, description);
    button.onclick = async () => {
      if (inserting) return;
      inserting = true;
      status.textContent = `Inserting /${skill.name}…`;
      try {
        await request({
          kind: "composer.insert",
          format: "markdown",
          text: `[/${escapeMarkdownLabel(skill.name)}](slash://skill/${skill.id}) `,
        });
        status.textContent = "Inserted into draft.";
      } catch (error) {
        status.textContent = error instanceof Error ? error.message : "Insertion failed";
      } finally {
        inserting = false;
        resize();
      }
    };
    list.append(button);
  }
  status.textContent = matches.length ? `${matches.length} Skills` : "No matching Skills";
  resize();
}

search.addEventListener("input", render);
request({ kind: "action", method: "GET", path: "/skills" })
  .then((result) => {
    skills = Array.isArray(result?.skills) ? result.skills : [];
    render();
    search.focus();
  })
  .catch((error) => {
    status.textContent = error instanceof Error ? error.message : "Could not load Skills";
    resize();
  });
