#!/usr/bin/env python3
"""
m5x2 Automations Dashboard — tracks auto-signed leases and other automation events.
Run: python3 m5x2-automations-dashboard.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, render_template_string
import automations_db as db
from config import DB_PATH, DASHBOARD_PORT
from datetime import datetime

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>m5x2 Automations</title>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="60">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, sans-serif; background: #0d0d0d; color: #e8e8e8; padding: 32px; }
    h1 { font-size: 22px; font-weight: 600; margin-bottom: 24px; color: #fff; }
    h2 { font-size: 14px; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 12px; }
    .cards { display: flex; gap: 16px; margin-bottom: 32px; flex-wrap: wrap; }
    .card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 10px; padding: 20px 24px; min-width: 140px; }
    .card .num  { font-size: 36px; font-weight: 700; color: #4ade80; }
    .card .label { font-size: 12px; color: #666; margin-top: 4px; }
    .card.warn .num { color: #f97316; }
    table { width: 100%; border-collapse: collapse; background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 10px; overflow: hidden; }
    th { text-align: left; padding: 10px 14px; font-size: 11px; color: #666; text-transform: uppercase; border-bottom: 1px solid #2a2a2a; }
    td { padding: 10px 14px; font-size: 13px; border-bottom: 1px solid #1f1f1f; }
    tr:last-child td { border-bottom: none; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
    .badge.success { background: #14532d; color: #4ade80; }
    .badge.failed  { background: #431407; color: #f97316; }
    .badge.renewal { background: #1e3a5f; color: #60a5fa; }
    .badge.new     { background: #2d1b4e; color: #a78bfa; }
    .ts { color: #555; font-size: 11px; }
  </style>
</head>
<body>
  <h1>m5x2 Automations</h1>

  <div class="cards">
    <div class="card">
      <div class="num">{{ summary.ytd }}</div>
      <div class="label">Signed YTD</div>
    </div>
    <div class="card">
      <div class="num">{{ summary.renewals }}</div>
      <div class="label">Renewals (all time)</div>
    </div>
    <div class="card">
      <div class="num">{{ summary.new }}</div>
      <div class="label">New leases (all time)</div>
    </div>
    {% if summary.failed > 0 %}
    <div class="card warn">
      <div class="num">{{ summary.failed }}</div>
      <div class="label">Failed / needs review</div>
    </div>
    {% endif %}
  </div>

  <h2>Recent signings</h2>
  <table>
    <thead>
      <tr>
        <th>Date</th>
        <th>Unit</th>
        <th>Tenants</th>
        <th>Type</th>
        <th>Status</th>
        <th>Sender</th>
      </tr>
    </thead>
    <tbody>
      {% for row in signings %}
      <tr>
        <td class="ts">{{ row.signed_at[:16] }}</td>
        <td>{{ row.unit or '—' }}</td>
        <td>{{ row.tenants or '—' }}</td>
        <td><span class="badge {{ row.lease_type }}">{{ row.lease_type }}</span></td>
        <td><span class="badge {{ row.status }}">{{ row.status }}</span></td>
        <td class="ts">{{ row.source_sender }}</td>
      </tr>
      {% endfor %}
      {% if not signings %}
      <tr><td colspan="6" style="color:#555; text-align:center; padding:24px;">No signings yet</td></tr>
      {% endif %}
    </tbody>
  </table>
</body>
</html>
"""

@app.route("/")
def index():
    summary  = db.get_summary(DB_PATH)
    signings = db.get_signings(DB_PATH)
    return render_template_string(HTML, summary=summary, signings=signings)

@app.route("/api/signings")
def api_signings():
    return jsonify({"summary": db.get_summary(DB_PATH), "signings": db.get_signings(DB_PATH)})

if __name__ == "__main__":
    db.init_db(DB_PATH)
    print(f"m5x2 Automations dashboard → http://localhost:{DASHBOARD_PORT}")
    app.run(port=DASHBOARD_PORT, debug=False)
