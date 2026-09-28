const NS = "http://www.w3.org/2000/svg";
const svgElement = (name, attrs = {}, text) => {
  const element = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs))
    element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
};

export const isProposal = (edge) =>
  edge.edge_type === "relates_to" &&
  edge.metadata?.relation === "alias" &&
  String(edge.metadata?.identity_verified).toLowerCase() === "false";

function labelLines(content, limit) {
  const lines = [];
  let remaining = content.replace(/\s+/g, " ").trim() || "(Empty content)";
  while (remaining && lines.length < 2) {
    if (remaining.length <= limit) {
      lines.push(remaining);
      break;
    }
    const space = remaining.lastIndexOf(" ", limit);
    const end = space > limit / 2 ? space : limit;
    lines.push(
      remaining.slice(0, end).trim() + (lines.length === 1 ? "…" : ""),
    );
    remaining = remaining.slice(end).trim();
  }
  return lines;
}

export class MemoryGraph {
  constructor(svg, onSelect) {
    this.svg = svg;
    this.onSelect = onSelect;
    this.reset();
    let drag = null;
    svg.addEventListener("pointerdown", (event) => {
      if (event.target.closest('[role="button"]') || event.button !== 0) return;
      drag = { x: event.clientX, y: event.clientY, ox: this.x, oy: this.y };
      svg.setPointerCapture(event.pointerId);
    });
    svg.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const rect = svg.getBoundingClientRect();
      const scale = Math.min(
        rect.width / this.width,
        rect.height / this.height,
      );
      this.x = drag.ox + (event.clientX - drag.x) / scale;
      this.y = drag.oy + (event.clientY - drag.y) / scale;
      this.transform();
    });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
      svg.addEventListener(name, () => {
        drag = null;
      });
    }
  }

  reset() {
    this.scale = 1;
    this.x = 0;
    this.y = 0;
    this.transform();
  }
  zoom(factor) {
    this.scale = Math.max(0.5, Math.min(3, this.scale * factor));
    this.transform();
  }
  transform() {
    const cx = this.width / 2,
      cy = this.height / 2;
    this.layer?.setAttribute(
      "transform",
      `translate(${cx + this.x} ${cy + this.y}) scale(${this.scale}) translate(${-cx} ${-cy})`,
    );
  }

  render(root, neighbors = [], edges = []) {
    this.svg.replaceChildren();
    if (!root) {
      this.layer = null;
      return;
    }
    const compact =
      window.matchMedia("(max-width: 650px)").matches && neighbors.length <= 6;
    this.width = compact ? 480 : 760;
    this.height = compact ? 580 : 490;
    const cx = this.width / 2,
      cy = this.height / 2;
    this.svg.setAttribute("viewBox", `0 0 ${this.width} ${this.height}`);
    this.svg.classList.toggle("graph-compact", compact);
    const defs = svgElement("defs");
    const marker = svgElement("marker", {
      id: "arrow",
      viewBox: "0 0 10 10",
      refX: 9,
      refY: 5,
      markerWidth: 6,
      markerHeight: 6,
      orient: "auto-start-reverse",
    });
    marker.append(
      svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#a5aec0" }),
    );
    defs.append(marker);
    this.layer = svgElement("g");
    this.svg.append(defs, this.layer);
    const positions = new Map([[root.id, { x: cx, y: cy }]]);
    const sorted = [...neighbors].sort((a, b) => a.id.localeCompare(b.id));
    sorted.forEach((node, index) => {
      const angle =
        -Math.PI / 2 + ((index + 0.5) * 2 * Math.PI) / sorted.length;
      positions.set(node.id, {
        x: cx + Math.cos(angle) * (compact ? 164 : 270),
        y: cy + Math.sin(angle) * (compact ? 225 : 195),
      });
    });
    const pairCounts = new Map();
    edges.forEach((edge) => {
      const source = positions.get(edge.source_id),
        target = positions.get(edge.target_id);
      if (!source || !target) return;
      const pair = [edge.source_id, edge.target_id].sort().join(":");
      const index = pairCounts.get(pair) || 0;
      pairCounts.set(pair, index + 1);
      let path, lx, ly;
      if (edge.source_id === edge.target_id) {
        path = `M ${source.x - 35} ${source.y - 38} C ${source.x - 120} ${source.y - 130 - index * 25}, ${source.x + 120} ${source.y - 130 - index * 25}, ${source.x + 35} ${source.y - 38}`;
        lx = source.x;
        ly = source.y - 107 - index * 18;
      } else {
        const dx = target.x - source.x,
          dy = target.y - source.y;
        const clip = (id) =>
          Math.min(
            (id === root.id ? (compact ? 76 : 91) : 70) /
              Math.max(Math.abs(dx), 0.01),
            (id === root.id ? 39 : 30) / Math.max(Math.abs(dy), 0.01),
          );
        const a = clip(edge.source_id),
          b = clip(edge.target_id);
        const sx = source.x + dx * a,
          sy = source.y + dy * a;
        const tx = target.x - dx * b,
          ty = target.y - dy * b;
        const bend =
          index === 0 ? 0 : Math.ceil(index / 2) * (index % 2 ? 23 : -23);
        const length = Math.hypot(dx, dy);
        const cx = (sx + tx) / 2 - (dy / length) * bend;
        const cy = (sy + ty) / 2 + (dx / length) * bend;
        path = `M ${sx} ${sy} Q ${cx} ${cy} ${tx} ${ty}`;
        lx = (sx + 2 * cx + tx) / 4;
        ly = (sy + 2 * cy + ty) / 4 - 5;
      }
      const line = svgElement("path", {
        d: path,
        class: `graph-edge${isProposal(edge) ? " proposal" : ""}`,
        "marker-end": "url(#arrow)",
      });
      line.append(
        svgElement(
          "title",
          {},
          `${edge.edge_type.replaceAll("_", " ")}${isProposal(edge) ? " · unverified alias" : ""}`,
        ),
      );
      this.layer.append(
        line,
        svgElement(
          "text",
          { x: lx, y: ly, "text-anchor": "middle", class: "edge-label" },
          edge.edge_type.replaceAll("_", " "),
        ),
      );
    });
    for (const node of [...sorted, root]) {
      const { x, y } = positions.get(node.id);
      const selected = node.id === root.id,
        width = selected ? (compact ? 150 : 180) : 138,
        height = selected ? 76 : 58;
      const group = svgElement("g", {
        class: `graph-node${selected ? " selected" : ""}`,
        transform: `translate(${x} ${y})`,
        role: "button",
        tabindex: 0,
        "aria-label": `Explore ${node.node_type}: ${node.content}`,
      });
      group.append(
        svgElement("title", {}, node.content),
        svgElement("rect", {
          x: -width / 2,
          y: -height / 2,
          width,
          height,
          rx: 6,
        }),
      );
      group.append(
        svgElement(
          "text",
          {
            x: 0,
            y: selected ? -17 : -10,
            "text-anchor": "middle",
            class: "node-type",
          },
          node.node_type,
        ),
      );
      const lines = labelLines(node.content, compact ? 17 : selected ? 24 : 18);
      group.append(
        svgElement(
          "text",
          {
            x: 0,
            y: selected ? 3 : 7,
            "text-anchor": "middle",
            class: "node-title",
          },
          lines[0],
        ),
      );
      if (lines[1])
        group.append(
          svgElement(
            "text",
            {
              x: 0,
              y: selected ? 20 : 22,
              "text-anchor": "middle",
              class: "node-title",
            },
            lines[1],
          ),
        );
      group.addEventListener("click", () => this.onSelect(node));
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          this.onSelect(node);
        }
      });
      this.layer.append(group);
    }
    this.reset();
  }
}
