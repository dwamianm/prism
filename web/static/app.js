import { MemoryGraph, isProposal } from "./graph.js";

const $ = (id) => document.getElementById(id);
const human = (value) =>
  value == null ? "—" : String(value).replaceAll("_", " ");
const date = (value) =>
  value
    ? new Date(value).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "—";
const shortDate = (value) =>
  value
    ? new Date(value).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : "—";
const percent = (value) =>
  Number.isFinite(value) ? `${Math.round(value * 100)}%` : "—";
const allStates = [
  "tentative",
  "stable",
  "contested",
  "superseded",
  "deprecated",
  "archived",
];

// All stored text goes through textContent; memory content is never HTML.
function el(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}
function tag(node) {
  const result = el("span", "tag", node.node_type);
  result.dataset.type = node.node_type;
  return result;
}
function fields(pairs) {
  const list = el("dl");
  for (const [key, value] of pairs)
    list.append(el("dt", "", key), el("dd", "", value ?? "—"));
  return list;
}
function section(title, content) {
  const block = el("section", "detail-section");
  block.append(el("h3", "", title), content);
  return block;
}
function jsonDetails(title, value) {
  const details = el("details", "detail-section");
  details.append(
    el("summary", "", title),
    el("pre", "", JSON.stringify(value, null, 2)),
  );
  return details;
}

let credentials = null;
let nodes = [],
  selected = null,
  nodeCursor = null,
  edgeCursor = null;
let scanController, graphController, evidenceController;
let scanBusy = false;
const graph = new MemoryGraph($("graph"), (node) => selectNode(node));

function error(message = "") {
  $("error").textContent = message;
  $("error").hidden = !message;
}
function showConnection(show) {
  $("connection-panel").hidden = !show;
  $("connection-toggle").setAttribute("aria-expanded", String(show));
}
function setControls(enabled) {
  for (const id of ["refresh", "search", "type", "state", "scope", "proposals"])
    $(id).disabled = !enabled;
  $("disconnect").hidden = !enabled;
}
function clearSelection() {
  graphController?.abort();
  evidenceController?.abort();
  selected = null;
  edgeCursor = null;
  graph.render(null);
  $("graph-empty").hidden = false;
  $("graph-count").textContent = "";
  $("connections-status").textContent = "No entry selected";
  $("connections").replaceChildren(
    el("p", "empty small", "Select an entry to inspect its relationships."),
  );
  $("details").replaceChildren(
    el("p", "empty", "Select an entry to see its full content and provenance."),
  );
  $("more-connections").hidden = true;
  $("first-connections").hidden = true;
}
function disconnect() {
  scanController?.abort();
  graphController?.abort();
  evidenceController?.abort();
  credentials = null;
  nodes = [];
  nodeCursor = null;
  scanBusy = false;
  $("token").value = "";
  $("owner").value = "";
  $("search").value = "";
  $("connect").disabled = false;
  $("connect").textContent = "Open memory →";
  $("connection-status").textContent = "No memory connected";
  $("status-dot").classList.remove("connected");
  setControls(false);
  clearSelection();
  renderEntries();
  showConnection(true);
  error();
}

async function api(path, params, signal) {
  const url = new URL(path, window.location.origin);
  if (credentials?.owner) url.searchParams.set("user_id", credentials.owner);
  for (const [key, value] of Object.entries(params || {})) {
    if (value === null || value === undefined || value === "") continue;
    for (const item of Array.isArray(value) ? value : [value])
      url.searchParams.append(key, item);
  }
  const headers = credentials?.token
    ? { Authorization: `Bearer ${credentials.token}` }
    : {};
  const response = await fetch(url, {
    headers,
    signal,
    cache: "no-store",
    credentials: "omit",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401)
      throw new Error(
        "The API key is missing or invalid. Update your connection and try again.",
      );
    if (response.status === 403)
      throw new Error(
        "This API key belongs to a different owner. Use its owner ID or leave Owner ID empty.",
      );
    if (response.status === 404)
      throw new Error(
        "This memory is no longer available. Refresh the entries to continue.",
      );
    if (response.status >= 500)
      throw new Error(
        "The memory server could not complete this request. Check the server and try again.",
      );
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `Request failed (${response.status}). Check the connection settings.`,
    );
  }
  return response.json();
}

function renderEntries() {
  const query = $("search").value.trim().toLowerCase();
  const filtered = nodes.filter((node) =>
    `${node.content} ${node.id}`.toLowerCase().includes(query),
  );
  const container = $("entries");
  container.replaceChildren();
  $("list-count").textContent = credentials
    ? `${filtered.length} shown / ${nodes.length} loaded${nodeCursor ? " · more available" : ""}`
    : "Connect to browse your entries";
  for (const node of filtered) {
    const button = el("button", "entry");
    button.setAttribute("aria-current", String(selected?.id === node.id));
    const top = el("span", "entry-top");
    top.append(tag(node), el("span", "", human(node.lifecycle_state)));
    button.append(
      top,
      el("span", "entry-content", node.content || "(Empty content)"),
      el(
        "span",
        "entry-bottom",
        `${human(node.scope)} · ${shortDate(node.created_at)}`,
      ),
    );
    button.addEventListener("click", () => selectNode(node));
    container.append(button);
  }
  if (!filtered.length)
    container.append(
      el(
        "p",
        "empty small",
        query
          ? "No loaded entries match. Try another term or load more entries."
          : credentials
            ? "No entries match these filters. Try another scope, type, or lifecycle."
            : "Your stored memories will appear here.",
      ),
    );
  $("load-more").hidden = !nodeCursor;
  $("load-more").disabled = scanBusy;
}

async function loadEntries(append = false) {
  scanController?.abort();
  const controller = new AbortController();
  scanController = controller;
  scanBusy = true;
  error();
  if (!append) {
    nodes = [];
    nodeCursor = null;
    clearSelection();
  }
  renderEntries();
  $("list-count").textContent = "Loading entries…";
  $("entries").setAttribute("aria-busy", "true");
  try {
    const page = await api(
      "/v1/nodes/scan",
      {
        limit: 100,
        after_id: append ? nodeCursor : null,
        type: $("type").value,
        scope: $("scope").value,
        state: $("state").value || allStates,
      },
      controller.signal,
    );
    if (controller.signal.aborted) return false;
    const merged = new Map(nodes.map((node) => [node.id, node]));
    page.nodes.forEach((node) => merged.set(node.id, node));
    nodes = [...merged.values()];
    nodeCursor = page.has_more ? page.next_cursor : null;
    renderEntries();
    if (!append && nodes.length) selectNode(nodes[0]);
    return true;
  } catch (err) {
    if (err.name !== "AbortError") {
      error(err.message || "Cannot reach the memory server.");
      renderEntries();
    }
    return false;
  } finally {
    if (!controller.signal.aborted) {
      scanBusy = false;
      $("load-more").disabled = false;
      $("entries").setAttribute("aria-busy", "false");
    }
  }
}

function renderDetails(node) {
  const container = $("details");
  container.replaceChildren();
  const top = el("div", "detail-top");
  top.append(
    tag(node),
    el(
      "span",
      `state-badge ${node.lifecycle_state}`,
      human(node.lifecycle_state),
    ),
  );
  const id = el("div", "detail-id");
  const copy = el("button", "", "Copy");
  copy.setAttribute("aria-label", "Copy memory ID");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(node.id);
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Select ID to copy";
    }
  });
  id.append(el("span", "", node.id), copy);
  const scores = el("div", "scores");
  for (const name of ["confidence", "salience"]) {
    const metric = el("div");
    const track = el("div", "score-track"),
      bar = el("span");
    bar.style.width = `${Math.max(0, Math.min(100, (node[name] || 0) * 100))}%`;
    track.append(bar);
    metric.append(
      el("span", "score-label", human(name)),
      el("span", "score-value", percent(node[name])),
      track,
    );
    scores.append(metric);
  }
  container.append(
    top,
    el("p", "detail-content", node.content || "(Empty content)"),
    id,
    scores,
  );
  container.append(
    section(
      "Classification",
      fields([
        ["Owner", node.user_id],
        ["Scope", human(node.scope)],
        ["Epistemic", human(node.epistemic_type)],
        ["Source", human(node.source_type)],
        ["Session", node.session_id],
        ["Pinned", node.pinned ? "Yes" : "No"],
      ]),
    ),
  );
  container.append(
    section(
      "Timeline · local time",
      fields([
        ["Event", date(node.event_time)],
        ["Created", date(node.created_at)],
        ["Updated", date(node.updated_at)],
        ["Valid from", date(node.valid_from)],
        ["Valid to", node.valid_to ? date(node.valid_to) : "Open interval"],
        ["TTL", node.ttl_days == null ? "No TTL" : `${node.ttl_days} days`],
        ["Superseded by", node.superseded_by],
      ]),
    ),
  );
  const sources = el("div");
  sources.append(
    el(
      "p",
      "",
      `${node.evidence_refs.length} source reference${node.evidence_refs.length === 1 ? "" : "s"}`,
    ),
  );
  if (node.evidence_refs.length) {
    const button = el("button", "text-button", "Read source evidence");
    button.addEventListener("click", async () => {
      evidenceController?.abort();
      evidenceController = new AbortController();
      const controller = evidenceController;
      button.disabled = true;
      button.textContent = "Loading evidence…";
      try {
        const provenance = await api(
          `/v1/nodes/${node.id}/provenance`,
          { operation_limit: 1 },
          controller.signal,
        );
        if (controller.signal.aborted || selected?.id !== node.id) return;
        sources.replaceChildren();
        for (const event of provenance.evidence_events)
          sources.append(
            el("blockquote", "", event.content),
            el(
              "p",
              "source-id",
              `${event.id} · ${date(event.timestamp ?? event.created_at)}`,
            ),
          );
        for (const ref of provenance.missing_evidence_refs)
          sources.append(el("p", "source-id", `Unavailable source: ${ref}`));
        if (
          !provenance.evidence_events.length &&
          !provenance.missing_evidence_refs.length
        )
          sources.append(el("p", "", "No available source evidence."));
      } catch (err) {
        if (err.name !== "AbortError") {
          button.disabled = false;
          button.textContent = "Retry source evidence";
          error(err.message);
        }
      }
    });
    sources.append(button);
  }
  container.append(
    section("Source evidence", sources),
    jsonDetails("Metadata", node.metadata || {}),
    jsonDetails("Complete record", node),
  );
}

async function selectNode(node, after = null) {
  if (!credentials) return;
  graphController?.abort();
  evidenceController?.abort();
  const controller = new AbortController();
  graphController = controller;
  selected = node;
  edgeCursor = null;
  renderEntries();
  renderDetails(node);
  error();
  graph.render(node);
  $("graph-empty").hidden = true;
  $("graph-count").textContent = "Loading…";
  $("connections-status").textContent = "Loading connections…";
  $("connections").replaceChildren();
  $("more-connections").hidden = true;
  $("first-connections").hidden = true;
  try {
    const page = await api(
      `/v1/explorer/nodes/${node.id}/connections`,
      {
        limit: window.matchMedia("(max-width: 650px)").matches ? 6 : 12,
        after_id: after,
        include_proposals: $("proposals").checked,
      },
      controller.signal,
    );
    if (controller.signal.aborted) return;
    selected = page.node;
    renderDetails(page.node);
    graph.render(page.node, page.nodes, page.edges);
    edgeCursor = page.has_more ? page.next_cursor : null;
    $("graph-count").textContent = `${page.nodes.length + 1} nodes`;
    $("connections-status").textContent =
      `${page.edges.length} links on this page${page.has_more ? " · more available" : ""}`;
    $("more-connections").hidden = !edgeCursor;
    $("first-connections").hidden = !after;
    renderConnections(page);
  } catch (err) {
    if (err.name !== "AbortError") {
      error(err.message);
      $("graph-count").textContent = "";
      $("connections-status").textContent = "Could not load connections";
      const retry = el("button", "load-more", "Retry connections");
      retry.addEventListener("click", () => selectNode(node, after));
      $("connections").replaceChildren(retry);
    }
  }
}

function renderConnections(page) {
  const container = $("connections");
  container.replaceChildren();
  const byId = new Map(
    [page.node, ...page.nodes].map((node) => [node.id, node]),
  );
  for (const edge of page.edges) {
    const outgoing = edge.source_id === selected.id;
    const neighbor = byId.get(outgoing ? edge.target_id : edge.source_id);
    const row = el("article", "connection");
    const main = el("div", "connection-main"),
      body = el("div", "connection-body");
    const closed = edge.valid_to && new Date(edge.valid_to) <= new Date();
    const future = new Date(edge.valid_from) > new Date();
    const state = closed ? "closed" : future ? "future" : "current";
    body.append(
      el(
        "p",
        "edge-kind",
        `${outgoing ? "Outgoing" : "Incoming"} · ${human(edge.edge_type)}`,
      ),
    );
    const link = el(
      "button",
      "connection-link",
      neighbor.content || "(Empty content)",
    );
    link.addEventListener("click", () => selectNode(neighbor));
    body.append(link);
    if (isProposal(edge))
      body.append(el("p", "proposal-label", "Unverified alias proposal"));
    main.append(el("span", "direction", outgoing ? "↗" : "↙"), body);
    const details = el("details");
    details.append(
      el(
        "summary",
        "",
        `${percent(edge.confidence)} confidence · ${state} · Inspect relationship`,
      ),
      fields([
        ["Edge ID", edge.id],
        ["Source", edge.source_id],
        ["Target", edge.target_id],
        ["Valid from", date(edge.valid_from)],
        ["Valid to", edge.valid_to ? date(edge.valid_to) : "Open interval"],
        ["Created", date(edge.created_at)],
        ["Evidence", edge.provenance_event_id],
      ]),
      el("pre", "", JSON.stringify(edge.metadata || {}, null, 2)),
    );
    row.append(main, details);
    container.append(row);
  }
  if (!page.edges.length)
    container.append(
      el(
        "p",
        "empty small",
        "No visible relationships in this owner and scope. Unverified aliases are hidden unless enabled above.",
      ),
    );
}

$("connection-toggle").addEventListener("click", () =>
  showConnection($("connection-panel").hidden),
);
$("connection-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const owner = $("owner").value.trim(),
    token = $("token").value.trim();
  if (!owner && !token) {
    error("Enter an owner ID or a per-user API key to open memory.");
    return;
  }
  scanController?.abort();
  clearSelection();
  credentials = { owner, token };
  nodes = [];
  nodeCursor = null;
  $("search").value = "";
  $("token").value = "";
  $("connection-status").textContent = "Connecting…";
  $("status-dot").classList.remove("connected");
  setControls(false);
  $("connect").disabled = true;
  $("connect").textContent = "Opening…";
  const connection = credentials;
  const success = await loadEntries();
  if (credentials !== connection) return;
  $("connect").disabled = false;
  $("connect").textContent = "Open memory →";
  if (success) {
    setControls(true);
    showConnection(false);
    $("connection-status").textContent =
      `Connected · ${owner || nodes[0]?.user_id || "authenticated owner"}`;
    $("status-dot").classList.add("connected");
  } else {
    credentials = null;
    nodes = [];
    clearSelection();
    renderEntries();
    $("connection-status").textContent = "Connection failed";
  }
});
$("disconnect").addEventListener("click", disconnect);
$("search").addEventListener("input", renderEntries);
for (const id of ["type", "state", "scope"])
  $(id).addEventListener("change", () => loadEntries());
$("refresh").addEventListener("click", () => loadEntries());
$("load-more").addEventListener("click", () => loadEntries(true));
$("proposals").addEventListener("change", () => {
  if (selected) selectNode(selected);
});
$("more-connections").addEventListener("click", () => {
  if (selected && edgeCursor) selectNode(selected, edgeCursor);
});
$("first-connections").addEventListener("click", () => {
  if (selected) selectNode(selected);
});
$("zoom-in").addEventListener("click", () => graph.zoom(1.25));
$("zoom-out").addEventListener("click", () => graph.zoom(0.8));
$("zoom-reset").addEventListener("click", () => graph.reset());
