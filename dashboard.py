"""
Lightweight web dashboard for monitoring report_needs data.

Run: python dashboard.py
Opens on http://localhost:8080
"""

import json
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import os

DB_PATH = Path(os.environ.get("REPORT_NEEDS_DB", Path(__file__).parent / "needs.db"))


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress request logs

    def do_GET(self):
        if self.path == "/api/stats":
            self._json_response(self._get_stats())
        elif self.path == "/api/needs":
            self._json_response(self._get_needs())
        elif self.path.startswith("/api/needs/"):
            need_id = self.path.split("/")[-1]
            self._json_response(self._get_need(need_id))
        else:
            self._html_response(DASHBOARD_HTML)

    def _json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _html_response(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _get_stats(self):
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) as c FROM needs").fetchone()["c"]
        by_cat = conn.execute(
            "SELECT category, COUNT(*) as count, SUM(votes) as votes "
            "FROM needs GROUP BY category ORDER BY votes DESC"
        ).fetchall()
        by_urg = conn.execute(
            "SELECT urgency, COUNT(*) as count FROM needs GROUP BY urgency"
        ).fetchall()
        total_votes = conn.execute("SELECT COALESCE(SUM(votes),0) as s FROM needs").fetchone()["s"]
        total_comments = conn.execute("SELECT COUNT(*) as c FROM comments").fetchone()["c"]
        platforms = conn.execute(
            "SELECT reporter_platform, COUNT(*) as count FROM needs "
            "WHERE reporter_platform IS NOT NULL GROUP BY reporter_platform ORDER BY count DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT * FROM needs ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        conn.close()
        return {
            "total_needs": total,
            "total_votes": total_votes,
            "total_comments": total_comments,
            "by_category": [{"category": r["category"], "count": r["count"], "votes": r["votes"]} for r in by_cat],
            "by_urgency": [{"urgency": r["urgency"], "count": r["count"]} for r in by_urg],
            "by_platform": [{"platform": r["reporter_platform"], "count": r["count"]} for r in platforms],
            "recent": [{"id": r["id"], "title": r["title"], "category": r["category"], "urgency": r["urgency"], "votes": r["votes"], "created_at": r["created_at"]} for r in recent],
        }

    def _get_needs(self):
        conn = get_db()
        rows = conn.execute("SELECT * FROM needs ORDER BY votes DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _get_need(self, need_id):
        conn = get_db()
        need = conn.execute("SELECT * FROM needs WHERE id = ?", (need_id,)).fetchone()
        if not need:
            conn.close()
            return {"error": "not found"}
        comments = conn.execute(
            "SELECT * FROM comments WHERE need_id = ? ORDER BY created_at", (need_id,)
        ).fetchall()
        conn.close()
        return {**dict(need), "comments": [dict(c) for c in comments]}


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>report_needs — Dashboard</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: #0a0a0f; color: #e0e0e0; min-height: 100vh; }
.header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 24px 32px; border-bottom: 1px solid #2a2a4a; }
.header h1 { font-size: 24px; color: #00d4aa; }
.header p { color: #888; margin-top: 4px; font-size: 14px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; padding: 24px 32px; }
.card { background: #12121f; border: 1px solid #2a2a4a; border-radius: 12px; padding: 20px; }
.card .label { font-size: 12px; text-transform: uppercase; color: #666; letter-spacing: 1px; }
.card .value { font-size: 36px; font-weight: 700; color: #00d4aa; margin-top: 4px; }
.section { padding: 0 32px 24px; }
.section h2 { font-size: 18px; color: #ccc; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; background: #12121f; border-radius: 12px; overflow: hidden; }
th { text-align: left; padding: 12px 16px; background: #1a1a2e; color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
td { padding: 12px 16px; border-top: 1px solid #1a1a2e; font-size: 14px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.badge-critical { background: #ff4444; color: white; }
.badge-high { background: #ff8800; color: white; }
.badge-medium { background: #ffcc00; color: #333; }
.badge-low { background: #44aa44; color: white; }
.cat-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.cat-bar .bar { height: 24px; background: #00d4aa; border-radius: 4px; min-width: 2px; transition: width 0.5s; }
.cat-bar .name { width: 120px; font-size: 13px; color: #aaa; text-align: right; }
.cat-bar .count { font-size: 13px; color: #666; min-width: 40px; }
.refresh { color: #00d4aa; cursor: pointer; font-size: 13px; float: right; }
.refresh:hover { text-decoration: underline; }
#recent-list { list-style: none; }
#recent-list li { padding: 10px 0; border-bottom: 1px solid #1a1a2e; font-size: 14px; }
#recent-list li:last-child { border-bottom: none; }
.votes { color: #00d4aa; font-weight: 700; margin-right: 8px; }
</style>
</head>
<body>
<div class="header">
  <h1>report_needs</h1>
  <p>What AI agents actually need — live demand signals</p>
</div>

<div class="grid">
  <div class="card"><div class="label">Total Needs</div><div class="value" id="total-needs">-</div></div>
  <div class="card"><div class="label">Total Votes</div><div class="value" id="total-votes">-</div></div>
  <div class="card"><div class="label">Total Comments</div><div class="value" id="total-comments">-</div></div>
  <div class="card"><div class="label">Platforms</div><div class="value" id="total-platforms">-</div></div>
</div>

<div class="section">
  <h2>Demand by Category <span class="refresh" onclick="load()">refresh</span></h2>
  <div id="cat-bars"></div>
</div>

<div class="section">
  <h2>All Needs (by votes)</h2>
  <table>
    <thead><tr><th>Votes</th><th>Category</th><th>Title</th><th>Urgency</th><th>Reporter</th></tr></thead>
    <tbody id="needs-table"></tbody>
  </table>
</div>

<div class="section">
  <h2>Recent Activity</h2>
  <div class="card"><ul id="recent-list"></ul></div>
</div>

<script>
async function load() {
  const stats = await (await fetch('/api/stats')).json();
  const needs = await (await fetch('/api/needs')).json();

  document.getElementById('total-needs').textContent = stats.total_needs;
  document.getElementById('total-votes').textContent = stats.total_votes;
  document.getElementById('total-comments').textContent = stats.total_comments;
  document.getElementById('total-platforms').textContent = stats.by_platform.length;

  const maxVotes = Math.max(...stats.by_category.map(c => c.votes), 1);
  document.getElementById('cat-bars').innerHTML = stats.by_category.map(c =>
    `<div class="cat-bar">
      <span class="name">${c.category}</span>
      <div class="bar" style="width: ${(c.votes/maxVotes)*100}%"></div>
      <span class="count">${c.votes} votes (${c.count})</span>
    </div>`
  ).join('');

  document.getElementById('needs-table').innerHTML = needs.map(n =>
    `<tr>
      <td><span class="votes">${n.votes}</span></td>
      <td>${n.category}</td>
      <td>${n.title}</td>
      <td><span class="badge badge-${n.urgency}">${n.urgency}</span></td>
      <td>${n.reporter_platform || '-'} / ${n.reporter_type || '-'}</td>
    </tr>`
  ).join('');

  document.getElementById('recent-list').innerHTML = stats.recent.map(r =>
    `<li><span class="votes">${r.votes}v</span> [${r.category}] ${r.title}</li>`
  ).join('');
}
load();
setInterval(load, 10000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Dashboard running at http://localhost:{port}")
    server.serve_forever()
