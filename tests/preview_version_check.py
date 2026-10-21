"""Safe browser fixture for the actual version UI. Run: python tests/preview_version_check.py
Open http://127.0.0.1:8765/?state=current|update|unknown|partial
No portal server, system commands, or network version lookups are started.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portal_assets import SCRIPT

# Execute the actual production functions against a minimal DOM.
version_js = SCRIPT[SCRIPT.index('// 版本检测与一键更新交互'):SCRIPT.index('async function waitForCoreUpgrade')]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        state = parse_qs(urlsplit(self.path).query).get('state', ['current'])[0]
        if urlsplit(self.path).path == '/check-version':
            payload = dict(ok=True, core_current='2.6.4', core_latest='2.6.4', core_has_update=False,
                           portal_current='aaaaaaa', portal_latest='bbbbbbb' if state == 'update' else 'aaaaaaa',
                           portal_has_update=state in ('update', 'partial'),
                           portal_update_status={'current': 'up_to_date', 'unknown': 'unknown'}.get(state, 'update_available'),
                           awg_installed=False)
            if state in ('unknown', 'partial'):
                payload['portal_latest'] = ''
            content = json.dumps(payload).encode()
            kind = 'application/json'
        else:
            content = ('''<!doctype html><meta charset="utf-8"><title>Version check fixture</title>
<p><a href="?state=current">全部一致</a> | <a href="?state=update">有差异</a> |
<a href="?state=unknown">检测失败</a> | <a href="?state=partial">部分检测发现差异</a></p>
<p id="core-ver-display"></p><p id="portal-ver-display"></p><p id="awg-ver-display"></p>
<button id="btn-update-portal">更新面板</button><button id="btn-recheck-update">重新检测</button>
<script>const doUpgrade = () => {}; const realFetch = window.fetch.bind(window);
window.fetch = url => realFetch(url + (url.includes('?') ? '&' : '?') + location.search.slice(1));
''' + version_js + '\ncheckVersions();</script>').encode()
            kind = 'text/html; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.end_headers()
        self.wfile.write(content)


if __name__ == '__main__':
    print('Version UI fixture: http://127.0.0.1:8765/', flush=True)
    HTTPServer(('127.0.0.1', 8765), Handler).serve_forever()
