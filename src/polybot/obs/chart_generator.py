"""Phase 9 — Interactive HTML chart generator.

Reads a per-market NDJSON positions log (produced by PositionRecorder) and
generates a self-contained HTML file with an equity curve chart using
Chart.js (CDN, no server required).

Output: a single .html file openable in any browser.
"""

from __future__ import annotations

import json
from pathlib import Path


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>polybot equity curve</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body {{ font-family: sans-serif; background: #1a1a2e; color: #eee; margin: 0; padding: 20px; }}
  h2   {{ color: #e0e0ff; }}
  .stats {{ display: flex; gap: 2rem; margin-bottom: 1rem; }}
  .stat  {{ background: #16213e; border-radius: 8px; padding: 12px 20px; }}
  .stat span {{ display: block; font-size: 0.75rem; color: #aaa; }}
  .stat b    {{ font-size: 1.4rem; }}
  canvas {{ max-height: 400px; }}
</style>
</head>
<body>
<h2>polybot — equity curve</h2>
<div class="stats">
  <div class="stat"><span>Trades</span><b>{n_trades}</b></div>
  <div class="stat"><span>Win rate</span><b>{win_rate:.1%}</b></div>
  <div class="stat"><span>Net PnL</span><b>${net_pnl:.2f}</b></div>
  <div class="stat"><span>Max drawdown</span><b>${max_dd:.2f}</b></div>
</div>
<canvas id="chart"></canvas>
<script>
const labels = {labels};
const equity = {equity};
const colors = {colors};
new Chart(document.getElementById('chart'), {{
  type: 'line',
  data: {{
    labels: labels,
    datasets: [{{
      label: 'Cumulative PnL (USD)',
      data: equity,
      borderColor: '#7c83fd',
      backgroundColor: 'rgba(124,131,253,0.1)',
      fill: true,
      tension: 0.3,
      pointBackgroundColor: colors,
      pointRadius: 5,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#eee' }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#aaa', maxTicksLimit: 20 }} }},
      y: {{ ticks: {{ color: '#aaa' }}, grid: {{ color: '#333' }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""


def generate_chart(ndjson_path: Path, output_path: Path) -> Path:
    """Read positions NDJSON, produce equity-curve HTML chart."""
    records = []
    with ndjson_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        raise ValueError(f"No records found in {ndjson_path}")

    # Build equity curve
    labels, equity, colors = [], [], []
    cumulative = 0.0
    wins = 0
    peak = 0.0
    max_dd = 0.0

    for r in records:
        net = float(r.get("net_pnl_usd", 0))
        cumulative += net
        ts = r.get("ts_utc", "")[:19].replace("T", " ")
        labels.append(ts)
        equity.append(round(cumulative, 4))
        colors.append("#4caf50" if net >= 0 else "#f44336")
        if r.get("won"):
            wins += 1
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    n = len(records)
    html = _HTML_TEMPLATE.format(
        n_trades=n,
        win_rate=wins / n if n else 0,
        net_pnl=cumulative,
        max_dd=max_dd,
        labels=json.dumps(labels),
        equity=json.dumps(equity),
        colors=json.dumps(colors),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
