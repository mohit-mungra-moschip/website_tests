# conftest.py — project-level pytest configuration
import json
import pathlib
import time
from datetime import datetime
import pytest

def parse_test_docstring(doc: str) -> dict:
    if not doc:
        return {}
    lines = doc.splitlines()
    parsed = {}
    current_key = None
    current_val = []
    
    # Standard keys to match
    key_mapping = {
        "test case name": "test_case_name",
        "module": "module",
        "test case id": "test_case_id",
        "description": "description",
        "steps": "steps",
        "expected output": "expected_output",
        "test case description": "description",
        "expected": "expected_output"
    }
    
    for line in lines:
        line_stripped = line.strip()
        matched_key = None
        for k in key_mapping:
            if line_stripped.lower().startswith(k + ":"):
                matched_key = key_mapping[k]
                val = line_stripped[len(k) + 1:].strip()
                break
        
        if matched_key:
            if current_key:
                parsed[current_key] = "\n".join(current_val).strip()
            current_key = matched_key
            current_val = [val] if val else []
        else:
            if current_key:
                current_val.append(line.rstrip())
            else:
                if "description" not in parsed:
                    parsed["description"] = ""
                parsed["description"] += line_stripped + "\n"
                
    if current_key:
        parsed[current_key] = "\n".join(current_val).strip()
        
    if "description" in parsed and isinstance(parsed["description"], str):
        parsed["description"] = parsed["description"].strip()
        
    return parsed

def write_ui_dashboard_reports(ui_results: list[dict], session_start_time: float) -> None:
    if not ui_results:
        return

    json_dir_name = "reports/json"
    html_dir_name = "reports/html"
    try:
        config_path = pathlib.Path("files/ai_evaluation/test_config.json")
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
                paths = cfg.get("paths", {})
                json_dir_name = paths.get("json_reports_dir", json_dir_name)
                html_dir_name = paths.get("html_reports_dir", html_dir_name)
    except Exception:
        pass

    json_dir = pathlib.Path(json_dir_name)
    html_dir = pathlib.Path(html_dir_name)
    json_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)

    elapsed  = time.time() - session_start_time if session_start_time else 0.0
    passed   = sum(1 for r in ui_results if r.get("status") == "PASS")
    failed   = sum(1 for r in ui_results if r.get("status") == "FAIL")
    skipped  = sum(1 for r in ui_results if r.get("status") == "SKIPPED")
    total    = len(ui_results)
    success_rate = (passed / total * 100) if total > 0 else 0.0

    import os
    run_id = os.environ.get("REGRESSION_RUN_ID")
    stamp = os.environ.get("REGRESSION_RUN_STAMP") or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    json_path = json_dir / f"test_results_{stamp}.json"
    html_path = html_dir / f"test_results_{stamp}.html"

    payload = {
        "run_id":            run_id,
        "generated_at":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "execution_seconds": round(elapsed, 2),
        "summary": {
            "total": total, "passed": passed,
            "failed": failed, "skipped": skipped,
            "success_rate": round(success_rate, 2),
        },
        "results": ui_results
    }

    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  JSON saved  → {json_path}")

    html_content = _build_html(payload, json_path.name)
    html_path.write_text(html_content, encoding="utf-8")
    print(f"  Visual HTML → {html_path}")


def _build_html(payload: dict, json_filename: str) -> str:
    payload_js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>agentic_solution Automation Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --white:#fff; --bg:#f8f9fb; --bg2:#f1f4f8;
      --border:#cbd5e1; --border2:#d0d7e3;
      --ink:#0f172a; --ink2:#334155; --ink3:#64748b;
      --brand:#2563eb; --brand-soft:#eff6ff;
      --green:#16a34a; --green-soft:#f0fdf4; --green-ring:#bbf7d0;
      --yellow:#d97706; --yellow-soft:#fffbeb;
      --red:#dc2626; --red-soft:#fef2f2; --red-ring:#fecaca;
      --violet:#7c3aed; --violet-soft:#f5f3ff;
      --shadow-sm:0 1px 3px rgba(13,17,23,.06),0 1px 2px rgba(13,17,23,.04);
      --shadow-md:0 4px 16px rgba(13,17,23,.08),0 2px 6px rgba(13,17,23,.04);
      --r-sm:8px; --r-md:12px; --r-lg:16px; --r-xl:20px;
    }}
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
    body{{font-family:'Plus Jakarta Sans',sans-serif;background:var(--bg);color:var(--ink);font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased;}}
    .mono{{font-family:'JetBrains Mono',monospace;}}
    .wrap{{max-width:100%;margin:0 auto;padding:0 24px 80px;}}

    /* ── HEADER ─────────────────────────────────────── */
    .page-header{{margin:0 -24px 28px;padding:0 24px;background:var(--white);border-bottom:1px solid var(--border);box-shadow:var(--shadow-sm);}}
    .page-header-inner{{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:18px 0;min-height:72px;}}
    .page-header-brand{{display:flex;align-items:center;gap:14px;min-width:0;}}
    .brand-logo{{width:44px;height:44px;border-radius:10px;flex-shrink:0;background:linear-gradient(145deg,#1d4ed8,#2563eb);display:flex;align-items:center;justify-content:center;font-size:.72rem;font-weight:800;color:#fff;letter-spacing:.04em;box-shadow:0 4px 12px rgba(37,99,235,.25);}}
    .brand-title{{font-size:1.35rem;font-weight:800;color:var(--ink);letter-spacing:-.03em;line-height:1.2;margin:0;}}
    .brand-title em{{font-style:normal;color:var(--brand);}}
    .brand-sub{{font-size:.75rem;color:var(--ink3);margin-top:2px;font-weight:500;}}
    .page-header-meta{{display:flex;align-items:center;flex-wrap:wrap;gap:10px;flex-shrink:0;}}
    .meta-item{{display:flex;flex-direction:column;gap:2px;padding:8px 14px;background:var(--bg);border:1px solid var(--border);border-radius:var(--r-sm);}}
    .meta-k{{font-size:.58rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--ink3);}}
    .meta-v{{font-family:'JetBrains Mono',monospace;font-size:.78rem;font-weight:600;color:var(--ink);}}

    /* ── STAT CARDS ──────────────────────────────────── */
    .stats-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px;}}
    .stat-card{{background:var(--white);border:1px solid var(--border);border-radius:var(--r-lg);padding:18px 16px;box-shadow:var(--shadow-sm);display:flex;flex-direction:column;gap:6px;position:relative;overflow:hidden;transition:transform .15s,box-shadow .15s;}}
    .stat-card:hover{{transform:translateY(-2px);box-shadow:var(--shadow-md);}}
    .stat-card::after{{content:'';position:absolute;top:0;left:0;right:0;height:3px;border-radius:var(--r-lg) var(--r-lg) 0 0;}}
    .stat-card.c-blue::after{{background:linear-gradient(90deg,#2563eb,#60a5fa);}}
    .stat-card.c-green::after{{background:linear-gradient(90deg,#16a34a,#4ade80);}}
    .stat-card.c-red::after{{background:linear-gradient(90deg,#dc2626,#f87171);}}
    .stat-card.c-amber::after{{background:linear-gradient(90deg,#d97706,#fbbf24);}}
    .stat-card.c-purple::after{{background:linear-gradient(90deg,#7c3aed,#a78bfa);}}
    .stat-card.c-slate::after{{background:linear-gradient(90deg,#475569,#94a3b8);}}
    .stat-lbl{{font-size:.65rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--ink3);}}
    .stat-val{{font-family:'JetBrains Mono',monospace;font-size:1.65rem;font-weight:700;letter-spacing:-.03em;line-height:1;color:var(--ink);}}
    .stat-val.green{{color:#16a34a;}} .stat-val.red{{color:#dc2626;}} .stat-val.amber{{color:#d97706;}} .stat-val.purple{{color:#7c3aed;}}
    .stat-sub{{font-size:.7rem;color:var(--ink3);}}

    /* ── PASS BAR ────────────────────────────────────── */
    .pass-bar-wrap{{background:var(--white);border:1px solid var(--border);border-radius:var(--r-xl);padding:20px 24px;box-shadow:var(--shadow-sm);margin-bottom:0;}}
    .pass-bar-row{{display:flex;align-items:center;gap:14px;margin-bottom:10px;}}
    .pass-bar-lbl{{font-size:.78rem;font-weight:600;color:var(--ink2);}}
    .pass-bar-nums{{margin-left:auto;font-size:.78rem;font-family:'JetBrains Mono',monospace;color:var(--ink3);}}
    .pass-bar-nums strong{{color:var(--ink);}}
    .pass-bar-track{{height:10px;background:#fef2f2;border:1px solid #fecaca;border-radius:10px;overflow:hidden;}}
    .pass-bar-fill{{height:100%;background:linear-gradient(90deg,#16a34a,#4ade80);border-radius:10px;}}
    .pct-gradient{{background-clip:text;-webkit-background-clip:text;-webkit-text-fill-color:transparent;color:transparent;}}
    .pct-gradient.green{{background-image:linear-gradient(135deg,#16a34a,#4ade80);}}
    .pct-gradient.red{{background-image:linear-gradient(135deg,#dc2626,#f87171);}}
    .pct-gradient.blue{{background-image:linear-gradient(135deg,#2563eb,#60a5fa);}}

    /* ── SECTION HEADER ──────────────────────────────── */
    .section-hdr{{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;margin-top:28px;}}
    .section-hdr-left{{display:flex;align-items:center;gap:10px;}}
    .section-icon{{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:15px;}}
    .section-icon.blue{{background:#eff6ff;}}
    .section-title{{font-size:.9rem;font-weight:700;letter-spacing:-.01em;}}
    .section-chip{{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:2px 8px;font-size:.72rem;font-family:'JetBrains Mono',monospace;font-weight:600;color:var(--ink3);}}
    .section-filter{{display:flex;gap:6px;}}
    .filter-btn{{padding:5px 12px;border-radius:7px;border:1px solid var(--border);background:var(--white);font-family:'Plus Jakarta Sans',sans-serif;font-size:.72rem;font-weight:600;color:var(--ink3);cursor:pointer;transition:all .12s;}}
    .filter-btn:hover,.filter-btn.active{{background:#2563eb;border-color:#2563eb;color:#fff;}}
    .filter-btn.active-green{{background:#16a34a;border-color:#16a34a;color:#fff;}}
    .filter-btn.active-red{{background:#dc2626;border-color:#dc2626;color:#fff;}}
    .filter-btn.active-amber{{background:#d97706;border-color:#d97706;color:#fff;}}
    .filter-btn.active-purple{{background:#7c3aed;border-color:#7c3aed;color:#fff;}}

    /* ── RESULT CARD ─────────────────────────────────── */
    .result-list{{display:flex;flex-direction:column;gap:8px;}}
    .rc{{background:var(--white);border:1px solid var(--border);border-radius:var(--r-lg);box-shadow:var(--shadow-sm);overflow:hidden;transition:border-color .15s,box-shadow .15s;}}
    .rc:hover{{border-color:var(--border2);box-shadow:var(--shadow-md);}}
    .rc.open{{border-color:#93c5fd;box-shadow:0 0 0 3px rgba(37,99,235,.07),var(--shadow-md);}}
    .rc.fail-row{{border-left:3px solid #dc2626;}}
    .rc.pass-row{{border-left:3px solid #16a34a;}}
    .rc.skip-row{{border-left:3px solid #d97706;}}
    .rc.has-traceability{{border-left:5px solid var(--violet) !important;background:var(--violet-soft) !important;box-shadow:0 0 8px rgba(124,58,237,0.15),var(--shadow-sm);}}
    .rc.has-traceability:hover{{box-shadow:0 0 12px rgba(124,58,237,0.25),var(--shadow-md);}}
    .rc-row{{display:grid;grid-template-columns:140px 72px minmax(0,1fr) 64px 24px;gap:12px;align-items:center;padding:14px 18px;cursor:pointer;user-select:none;}}
    .rc-row:hover{{background:#fafbfc;}}
    .tc-badge{{font-family:'JetBrains Mono',monospace;font-size:.7rem;font-weight:600;background:var(--bg2);color:var(--ink2);border:1px solid var(--border);border-radius:5px;padding:3px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:center;display:block;width:100%;box-sizing:border-box;}}
    .status-badge{{font-size:.65rem;font-weight:700;font-family:'JetBrains Mono',monospace;letter-spacing:.06em;padding:3px 9px;border-radius:5px;white-space:nowrap;border:1px solid transparent;display:inline-flex;justify-content:center;align-items:center;width:100%;box-sizing:border-box;}}
    .status-badge.pass{{background:#f0fdf4;color:#16a34a;border-color:#bbf7d0;}}
    .status-badge.fail{{background:#fef2f2;color:#dc2626;border-color:#fecaca;}}
    .status-badge.skipped{{background:#fffbeb;color:#d97706;border-color:#fde68a;}}
    .status-badge.healed{{background:#f5f3ff;color:#7c3aed;border-color:#e9d5ff;}}
    .rc-question{{font-size:.84rem;color:var(--ink2);overflow:hidden;font-weight:500;display:flex;align-items:center;gap:6px;min-width:0;}}
    .rc-question .rc-name{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;}}
    .score-ring{{position:relative;width:56px;height:56px;flex-shrink:0;}}
    .score-ring svg{{width:56px;height:56px;}}
    .sr-text{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-family:'JetBrains Mono',monospace;font-size:.58rem;font-weight:800;z-index:1;letter-spacing:-.02em;}}
    .rc-chevron{{color:var(--ink3);transition:transform .22s ease;display:flex;align-items:center;flex-shrink:0;}}
    .rc.open .rc-chevron{{transform:rotate(180deg);}}
    .rc-chevron svg{{width:16px;height:16px;stroke:currentColor;stroke-width:2;fill:none;}}

    /* ── DETAIL PANEL ────────────────────────────────── */
    .rc-detail{{display:none;border-top:1px solid var(--border);background:#fafbfc;}}
    .rc.open .rc-detail{{display:block;}}
    .detail-inner{{padding:20px;display:flex;flex-direction:column;gap:10px;}}

    /* row of small meta chips */
    .meta-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}}
    .dm{{background:var(--white);border:1px solid var(--border);border-radius:8px;padding:12px 14px;}}
    .dm-k{{font-size:.62rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--ink3);margin-bottom:4px;}}
    .dm-v{{font-size:.82rem;font-weight:600;color:var(--ink);}}
    .dm-v.mono{{font-family:'JetBrains Mono',monospace;}}

    /* doc-info row: 2 equal columns, spanning 100% total width */
    .doc-row{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;}}
    .doc-row .t-box:only-child{{grid-column:span 2;}}

    /* full-width box for description / steps / expected output */
    .doc-row-full{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}}

    .t-box{{background:var(--white);border:1px solid var(--border);border-radius:8px;padding:14px;}}
    .t-box-k{{font-size:.62rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--ink3);margin-bottom:6px;}}
    .t-box-v{{font-size:.82rem;color:var(--ink2);line-height:1.65;white-space:pre-wrap;word-break:break-word;}}
    .t-box-v.red{{color:#dc2626;font-weight:500;}}

    /* ── AI INSIGHT ROW ──────────────────────────────── */
    .insight-row{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;}}
    .insight-box{{background:var(--white);border:1px solid var(--border);border-radius:8px;padding:14px;min-height:140px;display:flex;flex-direction:column;border-top:3px solid var(--border);}}
    .insight-box.fail-box{{border-top-color:#dc2626;background:var(--red-soft);}}
    .insight-box.summary-box{{border-top-color:#7c3aed;background:var(--violet-soft);}}
    .insight-box.fix-box{{border-top-color:#2563eb;background:var(--brand-soft);}}
    .insight-hdr{{display:flex;align-items:center;gap:8px;margin-bottom:8px;}}
    .insight-k{{font-size:.62rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;}}
    .insight-k.fail{{color:#b91c1c;}}
    .insight-k.summary{{color:#6d28d9;}}
    .insight-k.fix{{color:#1d4ed8;}}
    .insight-pill{{font-size:.58rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;padding:2px 7px;border-radius:999px;background:linear-gradient(135deg,#7c3aed,#2563eb);color:#fff;}}
    .insight-body{{font-size:.8rem;color:var(--ink2);line-height:1.65;white-space:pre-wrap;word-break:break-word;flex:1;}}
    .insight-body.fail{{color:#991b1b;font-weight:500;font-family:'JetBrains Mono',monospace;font-size:.74rem;}}

    .empty-state{{text-align:center;padding:40px;color:var(--ink3);font-size:.85rem;background:var(--white);border:2px dashed var(--border);border-radius:var(--r-xl);}}

    @media(max-width:900px){{
      .stats-row{{grid-template-columns:repeat(3,1fr);}}
      .meta-row{{grid-template-columns:repeat(2,1fr);}}
      .doc-row,.doc-row-full,.insight-row{{grid-template-columns:1fr;}}
    }}
    @media(max-width:600px){{
      .stats-row{{grid-template-columns:repeat(2,1fr);}}
      .rc-row{{grid-template-columns:100px 60px minmax(0,1fr) 52px 20px;gap:8px;padding:10px 12px;}}
    }}
  </style>
</head>
<body>
<div class="wrap">

  <header class="page-header">
    <div class="page-header-inner">
      <div class="page-header-brand">
        <div class="brand-logo" id="brandLogo"></div>
        <div>
          <h1 class="brand-title" id="brandTitle"></h1>
          <p class="brand-sub">Automation Test Report</p>
        </div>
      </div>
      <div class="page-header-meta" id="headerMeta"></div>
    </div>
  </header>

  <div class="stats-row" id="statsRow"></div>
  <div class="pass-bar-wrap" id="passBarWrap"></div>

  <div class="section-hdr">
    <div class="section-hdr-left">
      <div class="section-icon blue">&#128187;</div>
      <span class="section-title">Test Results</span>
      <span class="section-chip" id="uiCount">0</span>
    </div>
    <div class="section-filter">
      <button class="filter-btn active" onclick="filterResults('all',this)">All</button>
      <button class="filter-btn" onclick="filterResults('healed',this)">Healed</button>
      <button class="filter-btn" onclick="filterResults('pass',this)">Passed</button>
      <button class="filter-btn" onclick="filterResults('fail',this)">Failed</button>
      <button class="filter-btn" onclick="filterResults('skipped',this)">Skipped</button>
    </div>
  </div>
  <div class="result-list" id="uiResults"></div>

</div>
<script>
  const REPORT_DATA = {payload_js};
  const byId = id => document.getElementById(id);
  const esc  = s  => String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const isPass = v => ['PASS','PASSED'].includes(String(v||'').toUpperCase());
  const isSkip = v => ['SKIP','SKIPPED'].includes(String(v||'').toUpperCase());
  const BRAND = {{ main:'Result ', accent:'Dashboard' }};

  /* ── helpers ── */
  function formatTime(s) {{
    let secs = Math.round(Number(s)||0);
    const h=Math.floor(secs/3600), m=Math.floor((secs%3600)/60), r=secs%60;
    const p=[];
    if(h) p.push(h+'h'); if(m) p.push(m+'m');
    if(r||!p.length) p.push(r+'s');
    return p.join(' ');
  }}
  function pctClass(v) {{
    const n=Number(String(v).replace('%',''))||0;
    return n>=80?'green':n>=50?'blue':'red';
  }}

  /* ── ring ── */
  function lerpC(a,b,t){{return[Math.round(a[0]+(b[0]-a[0])*t),Math.round(a[1]+(b[1]-a[1])*t),Math.round(a[2]+(b[2]-a[2])*t)];}}
  function ringColors(p,kind){{
    if(kind==='skip') return{{c1:'#fde68a',c2:'#f59e0b',c3:'#d97706',tx:'#92400e'}};
    if(p>=100) return{{c1:'#bbf7d0',c2:'#22c55e',c3:'#15803d',tx:'#14532d'}};
    const R=[239,68,68],O=[249,115,22],G=[34,197,94];
    const rgb=p<=50?lerpC(R,O,p/50):lerpC(O,G,(p-50)/50);
    const hex=c=>'#'+c.map(v=>v.toString(16).padStart(2,'0')).join('');
    return{{c1:hex(lerpC(rgb,[255,255,255],.45)),c2:hex(rgb),c3:hex(lerpC(rgb,[0,0,0],.25)),tx:hex(lerpC(rgb,[0,0,0],.40))}};
  }}
  function ringsvg(uid,pct,kind){{
    const p=Math.max(0,Math.min(100,Number(pct)||0));
    const col=ringColors(p,kind), gid='g-'+uid;
    const r=20,sw=6,circ=2*Math.PI*r;
    const off=p<=0?0:circ*(1-p/100);
    return {{
      tx:col.tx,
      svg:`<svg viewBox="0 0 56 56" xmlns="http://www.w3.org/2000/svg">
  <defs><linearGradient id="${{gid}}" x1="0%" y1="0%" x2="100%" y2="100%">
    <stop offset="0%"   stop-color="${{col.c1}}"/>
    <stop offset="50%"  stop-color="${{col.c2}}"/>
    <stop offset="100%" stop-color="${{col.c3}}"/>
  </linearGradient></defs>
  <circle cx="28" cy="28" r="${{r}}" fill="none" stroke="#e2e8f0" stroke-width="${{sw}}"/>
  <circle cx="28" cy="28" r="${{r}}" fill="none" stroke="url(#${{gid}})" stroke-width="${{sw}}"
    stroke-linecap="round" stroke-dasharray="${{circ}}" stroke-dashoffset="${{off}}"
    transform="rotate(-90 28 28)" style="transition:stroke-dashoffset .5s"/>
  <circle cx="28" cy="28" r="${{r-sw/2-1}}" fill="white"/>
</svg>`,
      lbl:Math.round(p)+'%'
    }};
  }}

  function statusColor(status){{
    const s=String(status||'').toUpperCase();
    if(s==='PASS'||s==='PASSED') return{{kind:'pass',text:'#16a34a'}};
    if(s==='SKIPPED'||s==='SKIP') return{{kind:'skip',text:'#d97706'}};
    return{{kind:'fail',text:'#dc2626'}};
  }}

  /* ── tbox helper ── */
  function tbox(label, value){{
    if(!value||!String(value).trim()) return '';
    return `<div class="t-box">
      <div class="t-box-k">${{esc(label)}}</div>
      <div class="t-box-v">${{esc(value)}}</div>
    </div>`;
  }}

  /* ── doc sections: rows of 3 ── */
  function docSectionsHtml(item){{
    const module = item.doc_module          || '';
    const tcId   = item.doc_test_case_id    || '';
    const desc   = item.doc_description     || item.description || '';
    const steps  = item.doc_steps           || '';
    const expOut = item.doc_expected_output || '';

    const hasStructured = module||tcId||steps||expOut;
    let html = '';

    /* Row 1: Module | Test Case ID */
    if(module || tcId){{
      const r1 = [];
      if(module) r1.push(tbox('Module', module));
      if(tcId) r1.push(tbox('Test Case ID', tcId));
      html += `<div class="doc-row">${{r1.join('')}}</div>`;
    }}

    /* Row 2: Description | Steps | Expected Output  (same 3-col grid) */
    const r2 = [
      desc   ? tbox('Description',     desc)   : '<div></div>',
      steps  ? tbox('Steps',           steps)  : '<div></div>',
      expOut ? tbox('Expected Output',  expOut) : '<div></div>',
    ];
    /* Only render row 2 if at least one cell is non-empty */
    if(desc||steps||expOut){{
      html += `<div class="doc-row-full">${{r2.join('')}}</div>`;
    }}

    return html;
  }}

  /* ── AI insight row (3 cols) ── */
  function insightRowHtml(item, hasFailure){{
    if(!hasFailure) return '';

    const summary = (item.ai_short_summary||'').trim()
      || 'AI summary unavailable – see failure_reason for details.';
    const fix     = (item.ai_suggested_fix||'').trim()
      || 'No fix suggestion available. Review the failure reason.';

    return `<div class="insight-row">
      <div class="insight-box fail-box">
        <div class="insight-hdr"><span class="insight-k fail">&#128308; Failure Reason</span></div>
        <div class="insight-body fail">${{esc(item.failure_reason)}}</div>
      </div>
      <div class="insight-box summary-box">
        <div class="insight-hdr">
          <span class="insight-pill">AI</span>
          <span class="insight-k summary">Short Summary</span>
        </div>
        <div class="insight-body">${{esc(summary)}}</div>
      </div>
      <div class="insight-box fix-box">
        <div class="insight-hdr">
          <span class="insight-pill">AI</span>
          <span class="insight-k fix">Suggested Fix</span>
        </div>
        <div class="insight-body">${{esc(fix)}}</div>
      </div>
    </div>`;
  }}

  /* ── card ── */
  function uiCard(item){{
    const pass = isPass(item.status);
    const skip = isSkip(item.status);
    const isHealed = !!item.pr_url;
    const rowCls   = pass?'pass-row':skip?'skip-row':'fail-row';
    const badgeCls = isHealed?'healed':(pass?'pass':skip?'skipped':'fail');
    const badgeText = isHealed?'HEALED':item.status;
    const uid = 'rc-'+Math.random().toString(36).slice(2,8);
    const col = statusColor(item.status);
    const pct = Number(item.percentage??(pass?100:skip?50:0));
    const ring= ringsvg(uid,pct,col.kind);

    const hasFailure = item.status==='FAIL'
      && item.failure_reason
      && String(item.failure_reason).trim();

    let jiraHtml = '';
    if (item.jira_id && item.jira_url) {{
      jiraHtml = `<a href="${{esc(item.jira_url)}}" target="_blank" rel="noopener noreferrer" style="flex-shrink:0;font-size:.62rem;font-weight:700;font-family:'JetBrains Mono',monospace;letter-spacing:.04em;padding:2px 7px;border-radius:4px;white-space:nowrap;border:1px solid #bfdbfe;background:#eff6ff;color:#2563eb;text-decoration:none;display:inline-flex;align-items:center;" onclick="event.stopPropagation();">${{esc(item.jira_id)}}</a>`;
    }}

    let prHtml = '';
    if (item.pr_url) {{
      const urls = typeof item.pr_url === 'string' ? item.pr_url.split(',') : (Array.isArray(item.pr_url) ? item.pr_url : [item.pr_url]);
      urls.forEach(url => {{
        const trimmedUrl = url.trim();
        if (!trimmedUrl) return;
        let prText = 'PR Link';
        let prUrlClean = trimmedUrl.replace(/\/+$/, "");
        if (prUrlClean.includes('/pull/')) {{
          const parts = prUrlClean.split('/');
          const prNum = parts[parts.length - 1];
          if (prNum && !isNaN(prNum)) {{
            let repoLabel = '';
            if (prUrlClean.toLowerCase().includes('agentic_pipeline_tests') || prUrlClean.toLowerCase().includes('test')) {{
              repoLabel = ' (QA)';
            }} else if (prUrlClean.toLowerCase().includes('agentic_pipeline') || prUrlClean.toLowerCase().includes('app')) {{
              repoLabel = ' (Dev)';
            }}
            prText = `PR #${{prNum}}${{repoLabel}}`;
          }}
        }}
        prHtml += `<a href="${{esc(trimmedUrl)}}" target="_blank" rel="noopener noreferrer" style="flex-shrink:0;font-size:.62rem;font-weight:700;font-family:'JetBrains Mono',monospace;letter-spacing:.04em;padding:2px 7px;border-radius:4px;white-space:nowrap;border:1px solid #e9d5ff;background:#f5f3ff;color:#7c3aed;text-decoration:none;display:inline-flex;align-items:center;margin-left:5px;" onclick="event.stopPropagation();">${{esc(prText)}}</a>`;
      }});
    }}

    const highlightCls = (item.jira_id || item.pr_url) ? 'has-traceability' : '';

    return `<div class="rc ${{rowCls}} ${{highlightCls}}" id="${{uid}}">
      <div class="rc-row" onclick="toggleRC('${{uid}}')">
        <span class="tc-badge" title="${{esc(item.test_id)}}">${{esc(item.doc_test_case_id || item.test_id)}}</span>
        <span class="status-badge ${{badgeCls}}">${{esc(badgeText)}}</span>
        <span class="rc-question"><span class="rc-name">${{esc(item.test_name)}}</span>${{jiraHtml}}${{prHtml}}</span>
        <div class="score-ring">
          ${{ring.svg}}
          <div class="sr-text" style="color:${{ring.tx}}">${{ring.lbl}}</div>
        </div>
        <span class="rc-chevron"><svg viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"/></svg></span>
      </div>
      <div class="rc-detail">
        <div class="detail-inner">

          <!-- Row 1: Test Name / Duration / Status -->
          <div class="meta-row">
            <div class="dm"><div class="dm-k">Test Name</div><div class="dm-v">${{esc(item.doc_test_case_name||item.test_name||'—')}}</div></div>
            <div class="dm"><div class="dm-k">Duration</div><div class="dm-v mono">${{item.duration!=null?Number(item.duration).toFixed(3)+'s':'—'}}</div></div>
            <div class="dm"><div class="dm-k">Status</div><div class="dm-v" style="color:${{col.text}}">${{esc(item.status)}}</div></div>
          </div>

          <!-- Docstring rows -->
          ${{docSectionsHtml(item)}}

          <!-- AI insight (only for FAIL) -->
          ${{insightRowHtml(item, hasFailure)}}

        </div>
      </div>
    </div>`;
  }}

  function toggleRC(id){{
    const el=byId(id);
    if(el) el.classList.toggle('open');
  }}

  let _all=[];

  function filterResults(mode,btn){{
    btn.closest('.section-filter').querySelectorAll('.filter-btn')
      .forEach(b=>b.classList.remove('active','active-green','active-red','active-amber','active-purple'));
    if(mode==='pass') btn.classList.add('active-green');
    else if(mode==='fail') btn.classList.add('active-red');
    else if(mode==='skipped') btn.classList.add('active-amber');
    else if(mode==='healed') btn.classList.add('active-purple');
    else btn.classList.add('active');
    let f=_all;
    if(mode==='pass')    f=_all.filter(r=>isPass(r.status));
    else if(mode==='fail')    f=_all.filter(r=>!isPass(r.status)&&!isSkip(r.status));
    else if(mode==='skipped') f=_all.filter(r=>isSkip(r.status));
    else if(mode==='healed')  f=_all.filter(r=>!!r.pr_url);
    byId('uiResults').innerHTML=f.length?f.map(uiCard).join(''):`<div class="empty-state">No ${{mode}} results found.</div>`;
    byId('uiCount').textContent=f.length;
  }}

  function renderHeader(){{
    const d=REPORT_DATA;
    byId('brandLogo').textContent=(BRAND.main.charAt(0)+BRAND.accent.charAt(0)).toUpperCase();
    byId('brandTitle').innerHTML=`${{esc(BRAND.main)}} <em>${{esc(BRAND.accent)}}</em>`;
    byId('headerMeta').innerHTML=`
      <div class="meta-item"><span class="meta-k">Generated</span><span class="meta-v">${{esc(d.generated_at||'—')}}</span></div>
      <div class="meta-item"><span class="meta-k">Execution</span><span class="meta-v">${{esc(formatTime(d.execution_seconds||0))}}</span></div>`;
  }}

  function renderStats(){{
    const s=REPORT_DATA.summary||{{}};
    const total=s.total||0,passed=s.passed||0,failed=s.failed||0,skipped=s.skipped||0,healed=s.healed||0;
    const pr=s.success_rate!=null?Number(s.success_rate).toFixed(1)+'%':(total?(((passed+healed)/total)*100).toFixed(1)+'%':'0.0%');
    const items=[
      {{lbl:'Total Tests',val:total,   cls:'',                   accent:'c-blue', sub:'cases run'}},
      {{lbl:'Passed',        val:passed,  cls:'green',              accent:'c-green',sub:'tests passed'}},
      {{lbl:'Failed',        val:failed,  cls:failed>0?'red':'',   accent:'c-red',  sub:'tests failed'}},
      {{lbl:'Healed',        val:healed,  cls:healed>0?'purple':'',accent:'c-purple',sub:'tests healed'}},
      {{lbl:'Skipped',       val:skipped, cls:skipped>0?'amber':'',accent:'c-amber',sub:'tests skipped'}},
      {{lbl:'Pass Rate',     val:pr, isPct:true, pCls:pctClass(pr), accent:'c-slate',sub:(passed+healed)+' / '+total+' passed'}},
    ];
    byId('statsRow').innerHTML=items.map(i=>`<div class="stat-card ${{i.accent}}">
      <div class="stat-lbl">${{esc(i.lbl)}}</div>
      <div class="stat-val ${{i.isPct?'pct-gradient '+i.pCls:(i.cls||'')}}">${{i.val}}</div>
      <div class="stat-sub">${{esc(i.sub)}}</div>
    </div>`).join('');
  }}

  function renderPassBar(){{
    const s=REPORT_DATA.summary||{{}};
    const total=s.total||1,passed=s.passed||0,failed=s.failed||0,healed=s.healed||0;
    const pct=((passed+healed)/total*100).toFixed(1);
    byId('passBarWrap').innerHTML=`
      <div class="pass-bar-row">
        <span class="pass-bar-lbl">Overall Pass Rate</span>
        <span class="pass-bar-nums"><strong>${{passed+healed}}</strong> / ${{total}} tests passed</span>
      </div>
      <div class="pass-bar-track"><div class="pass-bar-fill" style="width:${{pct}}%"></div></div>
      <div style="display:flex;justify-content:space-between;margin-top:6px">
        <span style="font-size:.7rem;color:#16a34a;font-weight:700">&#10003; ${{passed}} Passed${{healed?` (+${{healed}} Healed)`:''}}</span>
        <span class="pct-gradient ${{pctClass(pct)}}" style="font-size:.75rem;font-weight:800;font-family:'JetBrains Mono',monospace">${{pct}}%</span>
        <span style="font-size:.7rem;color:#dc2626;font-weight:700">${{failed}} Failed &#10007;</span>
      </div>`;
  }}

  function render(){{
    renderHeader(); renderStats(); renderPassBar();
    _all=REPORT_DATA.results||[];
    byId('uiCount').textContent=_all.length;
    byId('uiResults').innerHTML=_all.length
      ?_all.map(uiCard).join('')
      :`<div class="empty-state"><div style="font-size:2.5rem;margin-bottom:10px">&#128187;</div>No UI results in this run.</div>`;
  }}

  render();
</script>
</body>
</html>"""


# pytest hooks to integrate the reporter
@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    config._ui_results = []
    config._session_start_time = time.time()

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    
    if report.when == "call" or (report.when == "setup" and report.failed) or (report.when == "teardown" and report.failed):
        if report.passed:
            status = "PASS"
        elif report.skipped:
            status = "SKIPPED"
        else:
            status = "FAIL"
            

        doc = item.obj.__doc__ if hasattr(item, "obj") and item.obj.__doc__ else ""
        parsed_doc = parse_test_docstring(doc)

        fallback_doc = ""
        if hasattr(item, "cls") and item.cls and item.cls.__doc__:
            fallback_doc = item.cls.__doc__
        elif hasattr(item, "module") and item.module and item.module.__doc__:
            fallback_doc = item.module.__doc__

        if fallback_doc:
            parsed_fallback = parse_test_docstring(fallback_doc)
            for key in ["module", "description", "steps", "expected_output", "test_case_name", "test_case_id"]:
                val = parsed_doc.get(key)
                if not val or str(val).strip().upper() in ("N/A", "NONE", ""):
                    fallback_val = parsed_fallback.get(key)
                    if fallback_val and str(fallback_val).strip().upper() not in ("N/A", "NONE", ""):
                        parsed_doc[key] = fallback_val

        def clean_val(v, default=""):
            if not v or str(v).strip().upper() in ("N/A", "NONE", ""):
                return default
            return str(v).strip()

        doc_tc_name = clean_val(parsed_doc.get("test_case_name"), default=item.name)
        doc_mod = clean_val(parsed_doc.get("module"), default=item.module.__name__.split(".")[-1] if hasattr(item, "module") and item.module else "agentic_solution")
        doc_tc_id = ""
        testid_marker = item.get_closest_marker("testid")
        if testid_marker and testid_marker.args:
            doc_tc_id = str(testid_marker.args[0]).strip()
        if not doc_tc_id:
            doc_tc_id = clean_val(parsed_doc.get("test_case_id"), default="")
            if "@testcase" in doc_tc_id:
                doc_tc_id = doc_tc_id.replace("@testcase", "").replace("ID:", "").replace("id:", "").strip("- ").strip()
        if not doc_tc_id:
            import hashlib
            norm_id = item.nodeid.replace("/", ".").replace(".py", "").replace("::", ".")
            norm_id = norm_id.split("[")[0]
            h = hashlib.md5(norm_id.encode()).hexdigest()[:6].upper()
            doc_tc_id = f"TC-{h}"

        def clean_multiline(text):
            if not text:
                return ""
            return "\n".join(line.strip() for line in str(text).splitlines()).strip()

        doc_desc = clean_multiline(clean_val(parsed_doc.get("description"), default=doc.strip() if doc else ""))
        doc_steps = clean_multiline(clean_val(parsed_doc.get("steps"), default=""))
        doc_exp = clean_multiline(clean_val(parsed_doc.get("expected_output"), default=""))

        failure_reason = ""
        if status == "FAIL":
            failure_reason = str(report.longrepr)

        result_entry = {
            "test_id": item.nodeid,
            "status": status,
            "test_name": item.name,
            "duration": report.duration,
            "doc_test_case_name": doc_tc_name,
            "doc_module": doc_mod,
            "doc_test_case_id": doc_tc_id,
            "doc_description": doc_desc,
            "doc_steps": doc_steps,
            "doc_expected_output": doc_exp,
            "failure_reason": failure_reason,
            "ai_short_summary": "",
            "ai_suggested_fix": ""
        }
        item.config._ui_results.append(result_entry)

def pytest_sessionfinish(session, exitstatus):
    ui_results = getattr(session.config, "_ui_results", [])
    session_start_time = getattr(session.config, "_session_start_time", None)
    write_ui_dashboard_reports(ui_results, session_start_time)
