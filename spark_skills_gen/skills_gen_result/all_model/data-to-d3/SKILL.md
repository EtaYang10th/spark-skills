---
id: data-to-d3-bubble-chart
title: D3.js v6 Bubble Chart with Force Simulation and Linked Table
category: data-to-d3
tags: [d3, visualization, bubble-chart, force-simulation, html, css, javascript]
version: 1.0.0
---

## Module 1: Project Setup and Data Inspection

### 1.1 Directory Structure

Always create the required output structure first, then copy input data before writing any code:

```bash
mkdir -p /root/output/js /root/output/css /root/output/data/indiv-stock
cp /root/data/stock-descriptions.csv /root/output/data/
cp /root/data/indiv-stock/* /root/output/data/indiv-stock/
curl -sL https://cdnjs.cloudflare.com/ajax/libs/d3/6.7.0/d3.min.js -o /root/output/js/d3.v6.min.js
```

### 1.2 Inspect Input Data First

Before writing any visualization code, understand the schema:

```bash
head -5 /root/data/stock-descriptions.csv
ls /root/data/indiv-stock/ | head -10
head -3 /root/data/indiv-stock/AAPL.csv
```

Key things to identify:
- Which columns exist (ticker, name, sector, marketCap, country, website, etc.)
- Which rows are ETFs (they typically lack marketCap, country, website)
- The range of marketCap values (to calibrate bubble size scale)
- How many distinct sectors exist (to plan color palette and cluster anchors)

---

## Module 2: Bubble Chart with Force Simulation

### 2.1 Sector Clustering — Critical Pattern

The most commonly failed test is sector clustering. You MUST use `forceX`/`forceY` with per-sector anchor points, not just `forceCenter` + `forceCollide`.

```javascript
// Precompute anchor positions for each sector
const sectors = [...new Set(data.map(d => d.sector))];
const sectorCount = sectors.length;
const cx = width / 2, cy = height / 2;
const clusterRadius = Math.min(width, height) * 0.28;

const sectorAnchors = {};
sectors.forEach((s, i) => {
  const angle = (2 * Math.PI * i) / sectorCount - Math.PI / 2;
  sectorAnchors[s] = {
    x: cx + clusterRadius * Math.cos(angle),
    y: cy + clusterRadius * Math.sin(angle)
  };
});

// Force simulation with sector-directed forces
const simulation = d3.forceSimulation(nodes)
  .force("x", d3.forceX(d => sectorAnchors[d.sector].x).strength(0.12))
  .force("y", d3.forceY(d => sectorAnchors[d.sector].y).strength(0.12))
  .force("collide", d3.forceCollide(d => d.r + 2).strength(0.9))
  .force("center", d3.forceCenter(cx, cy).strength(0.02));
```

### 2.2 Bubble Sizing

Use `d3.scaleSqrt` for area-proportional sizing. ETFs (no marketCap) get a fixed uniform radius:

```javascript
const ETF_RADIUS = 18;
const maxCap = d3.max(data.filter(d => d.marketCap), d => +d.marketCap);
const radiusScale = d3.scaleSqrt().domain([0, maxCap]).range([8, 55]);

nodes.forEach(d => {
  d.r = d.marketCap ? radiusScale(+d.marketCap) : ETF_RADIUS;
});
```

### 2.3 Tooltip Rules — ETF vs Non-ETF

ETFs must be handled separately. Non-ETF tooltips show ticker, name, sector. ETF bubbles show NO tooltip (or a minimal one — check task requirements):

```javascript
svg.on("mouseover", (event, d) => {
  if (!d.marketCap) return; // ETF — skip tooltip
  tooltip
    .style("display", "block")
    .html(`
      <div class="tt-ticker">${d.ticker}</div>
      <div class="tt-name">${d.name}</div>
      <div class="tt-sector">${d.sector}</div>
    `);
})
.on("mousemove", (event) => {
  tooltip
    .style("left", (event.pageX + 12) + "px")
    .style("top", (event.pageY - 28) + "px");
})
.on("mouseout", () => tooltip.style("display", "none"));
```

### 2.4 Legend

Render a legend for every sector with a colored swatch. Use `div.legend-item` elements (not SVG) for easier CSS styling and reliable DOM querying by tests:

```javascript
const legend = d3.select("#legend");
sectors.forEach(s => {
  const item = legend.append("div").attr("class", "legend-item");
  item.append("div")
    .attr("class", "legend-swatch")
    .style("background", colorScale(s));
  item.append("span").text(s);
});
```

---

## Module 3: Data Table and Bidirectional Linking

### 3.1 Market Cap Formatting

Format raw numbers into human-readable strings:

```javascript
function formatMarketCap(val) {
  if (!val || isNaN(+val)) return "—";
  const n = +val;
  if (n >= 1e12) return (n / 1e12).toFixed(2) + "T";
  if (n >= 1e9)  return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6)  return (n / 1e6).toFixed(2) + "M";
  return n.toString();
}
```

### 3.2 Bidirectional Selection

Use a shared `selectTicker(ticker)` function that both the bubble click handler and table row click handler call:

```javascript
let selectedTicker = null;

function selectTicker(ticker) {
  selectedTicker = (selectedTicker === ticker) ? null : ticker; // toggle

  // Update bubble highlight
  d3.selectAll("circle.bubble")
    .classed("selected", d => d.ticker === selectedTicker);

  // Update table row highlight + scroll into view
  d3.selectAll("tr.stock-row")
    .classed("highlighted", d => d.ticker === selectedTicker)
    .filter(d => d.ticker === selectedTicker)
    .node()?.scrollIntoView({ block: "nearest" });
}

// Bubble click
bubble.on("click", (event, d) => selectTicker(d.ticker));

// Table row click
row.on("click", (event, d) => selectTicker(d.ticker));
```

---

## Common Pitfalls

1. Sector clustering fails when using only `forceCenter` + `forceCollide`. You MUST add `forceX`/`forceY` with per-sector anchor coordinates. Without this, bubbles scatter randomly and the clustering test will fail.

2. CSS syntax errors (stray quotes, unclosed strings) can silently break legend rendering and cause Playwright selector timeouts. Always validate CSS before finalizing.

3. ETF tooltip handling: if you show a tooltip for ETF entries (which lack marketCap/country/website), the tooltip test will fail. Guard with `if (!d.marketCap) return;` before rendering tooltip content.

4. Legend DOM structure matters for tests. Use `div.legend-item` with a child `div.legend-swatch` (colored background) and a `span` for the label. Avoid SVG-only legends if the test queries the DOM.

5. Don't use `forceCenter` with high strength — it fights against `forceX`/`forceY` and collapses clusters toward the center. Keep `forceCenter` strength very low (≤ 0.02) or omit it.

6. When copying individual stock files, use a glob (`cp /root/data/indiv-stock/* ...`) and verify the destination directory exists first, or the copy silently fails.

7. Market cap values in CSV are often raw integers (e.g., `1640000000000`). Parse with `+d.marketCap` and always check for empty/null before formatting.
