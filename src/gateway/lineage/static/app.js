/* ═══════════════════════════════════════════════════════════════════
   Walacor Lineage Dashboard — SPA
   Security: all dynamic content escaped via esc().
   Data source: gateway's own WAL database (not user input).
   ═══════════════════════════════════════════════════════════════════ */

'use strict';

const API = '/v1/lineage';
const HEALTH_URL = '/health';
const $content = document.getElementById('content');
const $statusDot = document.getElementById('status-dot');
const $statusLabel = document.getElementById('status-label');
const $statusMeta = document.getElementById('status-meta');

// ─── Safe DOM helper ─────────────────────────────────────────────

function setHTML(el, html) {
    el.textContent = '';
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    el.appendChild(tpl.content);
}

// ─── Status bar ──────────────────────────────────────────────────

async function refreshStatus() {
    try {
        const h = await fetchJSON(HEALTH_URL);
        $statusDot.className = 'status-dot ' + (h.status || '');
        $statusLabel.textContent = h.status || 'unknown';
        const parts = [];
        if (h.enforcement_mode) parts.push(h.enforcement_mode);
        if (h.uptime_seconds != null) parts.push(formatUptime(h.uptime_seconds));
        $statusMeta.textContent = parts.join(' · ');
    } catch (_) {
        $statusDot.className = 'status-dot';
        $statusLabel.textContent = 'offline';
        $statusMeta.textContent = '';
    }
}

refreshStatus();
setInterval(refreshStatus, 30000);

// ─── Navigation ──────────────────────────────────────────────────

function navigate(view, params) {
    params = params || {};
    document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
    var tab = document.querySelector('[data-view="' + view + '"]');
    if (tab) tab.classList.add('active');
    render(view, params);
}

document.querySelectorAll('.tab').forEach(function(t) {
    t.addEventListener('click', function(e) {
        e.preventDefault();
        navigate(t.dataset.view);
    });
});

async function render(view, params) {
    // Stop throughput chart when leaving overview
    if (_throughputChart) { _throughputChart.destroy(); _throughputChart = null; }
    $content.textContent = '';
    try {
        switch (view) {
            case 'overview': await renderOverview(); break;
            case 'sessions': await renderSessions(params); break;
            case 'timeline': await renderTimeline(params.sessionId); break;
            case 'execution': await renderExecution(params.executionId, params.sessionId); break;
            case 'attempts': await renderAttempts(params); break;
            case 'control': await renderControl(params); break;
            default: await renderOverview();
        }
    } catch (err) {
        setHTML($content, '<div class="error-card">Error: ' + esc(err.message) + '</div>');
    }
    bindClicks();
}

// ─── Throughput Chart (Mission Control Telemetry) ────────────────

function parsePrometheusMetrics(text) {
    var metrics = {};
    var lines = text.split('\n');
    for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (!line || line[0] === '#') continue;
        // Parse: metric_name{label="val",...} value
        var braceIdx = line.indexOf('{');
        var spaceIdx;
        var name, labels, value;
        if (braceIdx >= 0) {
            name = line.substring(0, braceIdx);
            var closeIdx = line.indexOf('}', braceIdx);
            labels = line.substring(braceIdx + 1, closeIdx);
            spaceIdx = line.indexOf(' ', closeIdx);
            value = parseFloat(line.substring(spaceIdx + 1));
        } else {
            spaceIdx = line.indexOf(' ');
            if (spaceIdx < 0) continue;
            name = line.substring(0, spaceIdx);
            labels = '';
            value = parseFloat(line.substring(spaceIdx + 1));
        }
        if (isNaN(value)) continue;
        if (!metrics[name]) metrics[name] = [];
        metrics[name].push({ labels: labels, value: value });
    }
    return metrics;
}

function sumMetric(metrics, name, filter) {
    var entries = metrics[name];
    if (!entries) return 0;
    var total = 0;
    for (var i = 0; i < entries.length; i++) {
        if (filter && entries[i].labels.indexOf(filter) < 0) continue;
        total += entries[i].value;
    }
    return total;
}

var _throughputChart = null;

function ThroughputChart(canvasId) {
    this.canvas = null;
    this.ctx = null;
    this.canvasId = canvasId;
    this.data = [];          // {ts, reqsPerSec, allowedPerSec, blockedPerSec, tokensPerSec}
    this.maxPoints = 60;
    this.pollInterval = 3000;
    this.timer = null;
    this.animFrame = null;
    this.prevMetrics = null;
    this.prevTime = null;
    this.hasData = false;
}

ThroughputChart.prototype.start = function() {
    this.canvas = document.getElementById(this.canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    this._resize();
    this._poll();
    var self = this;
    this.timer = setInterval(function() { self._poll(); }, this.pollInterval);
};

ThroughputChart.prototype.stop = function() {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    if (this.animFrame) { cancelAnimationFrame(this.animFrame); this.animFrame = null; }
    this.prevMetrics = null;
    this.prevTime = null;
};

ThroughputChart.prototype._resize = function() {
    if (!this.canvas) return;
    var rect = this.canvas.parentElement.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    this.ctx.scale(dpr, dpr);
    this.w = rect.width;
    this.h = rect.height;
};

ThroughputChart.prototype._poll = async function() {
    try {
        var resp = await fetch('/metrics');
        if (!resp.ok) return;
        var text = await resp.text();
        var m = parsePrometheusMetrics(text);
        var now = Date.now();

        var totalReqs = sumMetric(m, 'walacor_gateway_requests_total', '');
        var allowedReqs = sumMetric(m, 'walacor_gateway_requests_total', 'outcome="allowed"');
        var blockedReqs = totalReqs - allowedReqs;
        var totalTokens = sumMetric(m, 'walacor_gateway_token_usage_total', '');

        if (this.prevMetrics !== null && this.prevTime !== null) {
            var dt = (now - this.prevTime) / 1000;
            if (dt > 0) {
                var reqsPerSec = Math.max(0, (totalReqs - this.prevMetrics.totalReqs) / dt);
                var allowedPerSec = Math.max(0, (allowedReqs - this.prevMetrics.allowedReqs) / dt);
                var blockedPerSec = Math.max(0, (blockedReqs - this.prevMetrics.blockedReqs) / dt);
                var tokensPerSec = Math.max(0, (totalTokens - this.prevMetrics.totalTokens) / dt);
                this.data.push({
                    ts: now,
                    reqsPerSec: reqsPerSec,
                    allowedPerSec: allowedPerSec,
                    blockedPerSec: blockedPerSec,
                    tokensPerSec: tokensPerSec
                });
                if (this.data.length > this.maxPoints) this.data.shift();
                this.hasData = true;
            }
        } else {
            // First poll — push a zero point
            this.data.push({ ts: now, reqsPerSec: 0, allowedPerSec: 0, blockedPerSec: 0, tokensPerSec: 0 });
        }

        this.prevMetrics = { totalReqs: totalReqs, allowedReqs: allowedReqs, blockedReqs: blockedReqs, totalTokens: totalTokens };
        this.prevTime = now;

        this._updateCounters(totalReqs, allowedReqs, blockedReqs, totalTokens);
        this._draw();
    } catch (_) {
        // Silently handle fetch errors
    }
};

ThroughputChart.prototype._updateCounters = function(totalReqs, allowedReqs, blockedReqs, totalTokens) {
    var last = this.data.length > 0 ? this.data[this.data.length - 1] : null;
    var rps = last ? last.reqsPerSec : 0;
    var tps = last ? last.tokensPerSec : 0;
    var pctAllowed = totalReqs > 0 ? ((allowedReqs / totalReqs) * 100) : 100;

    var elRps = document.getElementById('tp-rps');
    var elTps = document.getElementById('tp-tps');
    var elPct = document.getElementById('tp-pct');
    var elTotal = document.getElementById('tp-total');

    if (elRps) elRps.textContent = rps < 0.1 && rps > 0 ? rps.toFixed(2) : rps.toFixed(1);
    if (elTps) elTps.textContent = tps < 1 ? tps.toFixed(1) : Math.round(tps);
    if (elPct) elPct.textContent = pctAllowed.toFixed(0) + '%';
    if (elTotal) elTotal.textContent = formatNumber(totalReqs);

    // Update status text
    var elStatus = document.getElementById('tp-status');
    if (elStatus) {
        elStatus.textContent = this.data.length + '/' + this.maxPoints + ' samples';
    }

    // Toggle waiting state
    var elWait = document.getElementById('tp-waiting');
    if (elWait) {
        elWait.style.display = this.hasData ? 'none' : 'block';
    }
};

ThroughputChart.prototype._draw = function() {
    if (!this.ctx || !this.canvas) return;
    var ctx = this.ctx;
    var w = this.w;
    var h = this.h;
    var pad = { top: 16, right: 16, bottom: 28, left: 48 };
    var chartW = w - pad.left - pad.right;
    var chartH = h - pad.top - pad.bottom;

    // Clear
    ctx.clearRect(0, 0, w, h);

    // Grid lines
    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 0.5;
    var gridRows = 4;
    for (var gi = 0; gi <= gridRows; gi++) {
        var gy = pad.top + (chartH / gridRows) * gi;
        ctx.beginPath();
        ctx.moveTo(pad.left, gy);
        ctx.lineTo(w - pad.right, gy);
        ctx.stroke();
    }
    var gridCols = 6;
    for (var gj = 0; gj <= gridCols; gj++) {
        var gx = pad.left + (chartW / gridCols) * gj;
        ctx.beginPath();
        ctx.moveTo(gx, pad.top);
        ctx.lineTo(gx, h - pad.bottom);
        ctx.stroke();
    }

    if (this.data.length < 2) return;

    // Find max value for Y scale
    var maxVal = 0.1;
    for (var di = 0; di < this.data.length; di++) {
        if (this.data[di].reqsPerSec > maxVal) maxVal = this.data[di].reqsPerSec;
    }
    maxVal = maxVal * 1.2; // headroom

    // Y-axis labels
    ctx.font = '10px "JetBrains Mono", monospace';
    ctx.fillStyle = '#4c4c60';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (var yi = 0; yi <= gridRows; yi++) {
        var yVal = maxVal - (maxVal / gridRows) * yi;
        var yPos = pad.top + (chartH / gridRows) * yi;
        ctx.fillText(yVal < 1 ? yVal.toFixed(2) : yVal.toFixed(1), pad.left - 6, yPos);
    }

    // X-axis time labels
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    var timeLabels = ['3m', '2m', '1m', 'now'];
    for (var xi = 0; xi < timeLabels.length; xi++) {
        var xp = pad.left + (chartW / (timeLabels.length - 1)) * xi;
        ctx.fillText(timeLabels[xi], xp, h - pad.bottom + 8);
    }

    // Helper: data index to X position
    var self = this;
    function dataX(idx) {
        return pad.left + (idx / (self.maxPoints - 1)) * chartW;
    }
    function dataY(val) {
        return pad.top + chartH - (val / maxVal) * chartH;
    }

    // Draw allowed fill (green, subtle)
    ctx.beginPath();
    ctx.moveTo(dataX(0), dataY(0));
    for (var ai = 0; ai < this.data.length; ai++) {
        ctx.lineTo(dataX(ai), dataY(this.data[ai].allowedPerSec));
    }
    ctx.lineTo(dataX(this.data.length - 1), dataY(0));
    ctx.closePath();
    var greenGrad = ctx.createLinearGradient(0, pad.top, 0, h - pad.bottom);
    greenGrad.addColorStop(0, 'rgba(52, 211, 153, 0.12)');
    greenGrad.addColorStop(1, 'rgba(52, 211, 153, 0.01)');
    ctx.fillStyle = greenGrad;
    ctx.fill();

    // Draw blocked fill (red, subtle) on top
    if (this.data.some(function(d) { return d.blockedPerSec > 0; })) {
        ctx.beginPath();
        ctx.moveTo(dataX(0), dataY(0));
        for (var bi = 0; bi < this.data.length; bi++) {
            ctx.lineTo(dataX(bi), dataY(this.data[bi].blockedPerSec));
        }
        ctx.lineTo(dataX(this.data.length - 1), dataY(0));
        ctx.closePath();
        var redGrad = ctx.createLinearGradient(0, pad.top, 0, h - pad.bottom);
        redGrad.addColorStop(0, 'rgba(239, 68, 68, 0.15)');
        redGrad.addColorStop(1, 'rgba(239, 68, 68, 0.01)');
        ctx.fillStyle = redGrad;
        ctx.fill();
    }

    // Draw gold fill (total requests)
    ctx.beginPath();
    ctx.moveTo(dataX(0), dataY(0));
    for (var fi = 0; fi < this.data.length; fi++) {
        ctx.lineTo(dataX(fi), dataY(this.data[fi].reqsPerSec));
    }
    ctx.lineTo(dataX(this.data.length - 1), dataY(0));
    ctx.closePath();
    var goldGrad = ctx.createLinearGradient(0, pad.top, 0, h - pad.bottom);
    goldGrad.addColorStop(0, 'rgba(201, 168, 76, 0.18)');
    goldGrad.addColorStop(1, 'rgba(201, 168, 76, 0.01)');
    ctx.fillStyle = goldGrad;
    ctx.fill();

    // Draw lines — allowed (green)
    ctx.beginPath();
    ctx.strokeStyle = 'rgba(52, 211, 153, 0.5)';
    ctx.lineWidth = 1.2;
    for (var gli = 0; gli < this.data.length; gli++) {
        var glx = dataX(gli), gly = dataY(this.data[gli].allowedPerSec);
        if (gli === 0) ctx.moveTo(glx, gly); else ctx.lineTo(glx, gly);
    }
    ctx.stroke();

    // Draw lines — blocked (red), only if present
    if (this.data.some(function(d) { return d.blockedPerSec > 0; })) {
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(239, 68, 68, 0.6)';
        ctx.lineWidth = 1.2;
        for (var rli = 0; rli < this.data.length; rli++) {
            var rlx = dataX(rli), rly = dataY(this.data[rli].blockedPerSec);
            if (rli === 0) ctx.moveTo(rlx, rly); else ctx.lineTo(rlx, rly);
        }
        ctx.stroke();
    }

    // Draw main gold line (total)
    ctx.beginPath();
    ctx.strokeStyle = '#c9a84c';
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    for (var li = 0; li < this.data.length; li++) {
        var lx = dataX(li), ly = dataY(this.data[li].reqsPerSec);
        if (li === 0) ctx.moveTo(lx, ly); else ctx.lineTo(lx, ly);
    }
    ctx.stroke();

    // Animated pulse dot on last point
    var lastPt = this.data[this.data.length - 1];
    var px = dataX(this.data.length - 1);
    var py = dataY(lastPt.reqsPerSec);
    var pulsePhase = (Date.now() % 1500) / 1500;
    var pulseRadius = 3 + Math.sin(pulsePhase * Math.PI * 2) * 2;
    var pulseAlpha = 0.6 + Math.sin(pulsePhase * Math.PI * 2) * 0.4;

    // Outer glow
    ctx.beginPath();
    ctx.arc(px, py, pulseRadius + 4, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(201, 168, 76, ' + (pulseAlpha * 0.2) + ')';
    ctx.fill();

    // Inner dot
    ctx.beginPath();
    ctx.arc(px, py, pulseRadius, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(201, 168, 76, ' + pulseAlpha + ')';
    ctx.fill();

    // Core
    ctx.beginPath();
    ctx.arc(px, py, 2, 0, Math.PI * 2);
    ctx.fillStyle = '#c9a84c';
    ctx.fill();

    // Request animation frame for pulse
    var chart = this;
    if (this.timer) {
        this.animFrame = requestAnimationFrame(function() { chart._draw(); });
    }
};

ThroughputChart.prototype.destroy = function() {
    this.stop();
    this.canvas = null;
    this.ctx = null;
    this.data = [];
};

// ─── View: Overview ──────────────────────────────────────────────

async function renderOverview() {
    var results = await Promise.all([
        fetchJSON(HEALTH_URL),
        fetchJSON(API + '/sessions?limit=6'),
        fetchJSON(API + '/attempts?limit=8')
    ]);
    var health = results[0];
    var sessData = results[1];
    var attData = results[2];

    var sessions = sessData.sessions || [];
    var attempts = attData.items || [];
    var stats = attData.stats || {};
    var total = attData.total || 0;
    var allowed = stats.allowed || 0;
    var denied = 0;
    var errors = 0;
    var keys = Object.keys(stats);
    for (var i = 0; i < keys.length; i++) {
        if (keys[i].indexOf('denied') === 0) denied += stats[keys[i]];
        if (keys[i].indexOf('error') === 0) errors += stats[keys[i]];
    }

    var html = '<div class="fade-in">';

    // Stat cards
    html += '<div class="stat-grid">' +
        statCard(sessions.length, 'Sessions', '') +
        statCard(total, 'Total Requests', '<span class="green">' + allowed + ' allowed</span>' + (denied ? ' · <span class="red">' + denied + ' denied</span>' : '')) +
        statCard(health.enforcement_mode || '-', 'Enforcement', health.storage ? esc(health.storage.backend) + ' storage' : '') +
    '</div>';

    // Throughput chart
    html += '<div class="throughput-card">' +
        '<div class="throughput-header">' +
            '<span class="throughput-title"><span class="throughput-live-dot"></span> Live Throughput</span>' +
            '<span class="throughput-status" id="tp-status">initializing</span>' +
        '</div>' +
        '<div class="throughput-canvas-wrap">' +
            '<canvas id="tp-canvas"></canvas>' +
            '<div class="throughput-waiting" id="tp-waiting">' +
                '<div class="throughput-waiting-text">AWAITING TELEMETRY</div>' +
                '<div class="throughput-waiting-bar"></div>' +
            '</div>' +
        '</div>' +
        '<div class="throughput-counters">' +
            '<div class="throughput-counter">' +
                '<div class="counter-value gold" id="tp-rps">0</div>' +
                '<div class="counter-label">req/s</div>' +
            '</div>' +
            '<div class="throughput-counter">' +
                '<div class="counter-value" id="tp-tps">0</div>' +
                '<div class="counter-label">tokens/s</div>' +
            '</div>' +
            '<div class="throughput-counter">' +
                '<div class="counter-value green" id="tp-pct">100%</div>' +
                '<div class="counter-label">allowed</div>' +
            '</div>' +
            '<div class="throughput-counter">' +
                '<div class="counter-value" id="tp-total">0</div>' +
                '<div class="counter-label">total</div>' +
            '</div>' +
        '</div>' +
    '</div>';

    // Recent Sessions
    html += '<div class="card"><div class="card-head">' +
        '<span class="card-title">Recent Sessions</span>' +
        '<button class="btn" data-nav="sessions">View All</button>' +
    '</div>';

    if (sessions.length === 0) {
        html += '<div class="empty-state"><p>No sessions yet. Send requests through the gateway.</p></div>';
    } else {
        html += '<div class="table-wrap"><table><thead><tr>' +
            '<th>Session</th><th>Records</th><th>Model</th><th>Last Active</th>' +
        '</tr></thead><tbody>';
        for (var s = 0; s < sessions.length; s++) {
            var sess = sessions[s];
            html += '<tr class="clickable" data-nav="timeline" data-sid="' + esc(sess.session_id) + '">' +
                '<td class="id">' + esc(formatSessionId(sess.session_id)) + '</td>' +
                '<td class="record-count">' + sess.record_count + '</td>' +
                '<td class="model-name">' + esc(displayModel(sess.model)) + '</td>' +
                '<td class="time-relative">' + timeAgo(sess.last_activity) + '</td>' +
            '</tr>';
        }
        html += '</tbody></table></div>';
    }
    html += '</div>';

    // Recent Activity
    html += '<div class="card"><div class="card-head">' +
        '<span class="card-title">Recent Activity</span>' +
        '<button class="btn" data-nav="attempts">View All</button>' +
    '</div>';

    if (attempts.length === 0) {
        html += '<div class="empty-state"><p>No activity recorded yet.</p></div>';
    } else {
        for (var a = 0; a < attempts.length; a++) {
            var att = attempts[a];
            html += '<div class="activity-row' + (att.execution_id ? ' clickable' : '') + '"' +
                (att.execution_id ? ' data-nav="execution" data-eid="' + esc(att.execution_id) + '"' : '') + '>' +
                dispBadge(att.disposition) +
                '<span class="activity-model">' + esc(displayModel(att.model_id) || '-') + '</span>' +
                '<span class="activity-path">' + esc(att.path) + '</span>' +
                '<span class="activity-time">' + timeAgo(att.timestamp) + '</span>' +
            '</div>';
        }
    }
    html += '</div></div>';

    setHTML($content, html);

    // Start throughput chart after DOM is ready
    if (_throughputChart) { _throughputChart.destroy(); _throughputChart = null; }
    _throughputChart = new ThroughputChart('tp-canvas');
    _throughputChart.start();
}

// ─── View: Sessions ──────────────────────────────────────────────

async function renderSessions(params) {
    params = params || {};
    var limit = params.limit || 50;
    var offset = params.offset || 0;
    var data = await fetchJSON(API + '/sessions?limit=' + limit + '&offset=' + offset);
    var sessions = data.sessions || [];

    var html = '<div class="fade-in">';

    if (!sessions.length) {
        html += '<div class="empty-state"><h3>No sessions found</h3>' +
            '<p>Send requests through the gateway to see audit records here.</p></div>';
        setHTML($content, html + '</div>');
        return;
    }

    html += '<div class="card"><div class="card-head">' +
        '<span class="card-title">All Sessions (' + sessions.length + ')</span>' +
    '</div>' +
    '<div class="table-wrap"><table><thead><tr>' +
        '<th>Session</th><th>Records</th><th>Model</th><th>Last Active</th>' +
    '</tr></thead><tbody>';

    for (var i = 0; i < sessions.length; i++) {
        var s = sessions[i];
        html += '<tr class="clickable" data-nav="timeline" data-sid="' + esc(s.session_id) + '">' +
            '<td class="id">' + esc(formatSessionId(s.session_id)) + '</td>' +
            '<td class="record-count">' + s.record_count + '</td>' +
            '<td class="model-name">' + esc(displayModel(s.model)) + '</td>' +
            '<td class="time-relative">' + timeAgo(s.last_activity) + '</td>' +
        '</tr>';
    }

    html += '</tbody></table></div></div>';

    if (sessions.length >= limit) {
        html += '<div class="pagination">';
        if (offset > 0) html += '<button class="btn" data-nav="sessions-page" data-offset="' + (offset - limit) + '">Previous</button>';
        html += '<button class="btn" data-nav="sessions-page" data-offset="' + (offset + limit) + '">Next</button>';
        html += '</div>';
    }

    html += '</div>';
    setHTML($content, html);
}

// ─── View: Timeline ──────────────────────────────────────────────

async function renderTimeline(sessionId) {
    var data = await fetchJSON(API + '/sessions/' + sessionId);
    var records = data.records || [];

    var model = records.length > 0 ? displayModel(records[0].model_id || records[0].model_attestation_id) : '-';
    var lastTime = records.length > 0 ? timeAgo(records[records.length - 1].timestamp) : '-';

    var html = '<div class="fade-in">';

    // Breadcrumb
    html += '<div class="breadcrumb">' +
        '<a href="#" data-nav="sessions">Sessions</a>' +
        '<span class="sep">&#9656;</span>' +
        '<span class="current">' + esc(formatSessionId(sessionId)) + '</span>' +
    '</div>';

    // Header with verify button
    html += '<div class="chain-header">' +
        '<div>' +
            '<div class="chain-title">' + esc(formatSessionId(sessionId)) + '</div>' +
            '<div class="chain-subtitle">' + records.length + ' record' + (records.length !== 1 ? 's' : '') +
                ' · ' + esc(model) + ' · ' + lastTime + '</div>' +
        '</div>' +
        '<button class="btn btn-gold" id="verify-btn" data-action="verify" data-sid="' + esc(sessionId) + '">' +
            '&#9670; Verify Chain</button>' +
    '</div>';

    // Verification result placeholder
    html += '<div id="verify-result"></div>';

    if (!records.length) {
        html += '<div class="empty-state"><h3>No records in this session</h3></div>';
        setHTML($content, html + '</div>');
        return;
    }

    // Chain visualization
    html += '<div class="chain" id="chain-container">';
    for (var i = 0; i < records.length; i++) {
        var r = records[i];
        var seq = r.sequence_number != null ? r.sequence_number : '?';
        var prompt = (r.prompt_text || '').substring(0, 100);
        var response = (r.response_content || '').substring(0, 80);
        var tokens = getTokenCount(r);
        var isLast = (i === records.length - 1);

        var toolInfo = r.metadata && r.metadata.tool_interactions ? r.metadata.tool_interactions : [];
        var hasTools = toolInfo.length > 0;
        var toolNames = toolInfo.map(function(t) { return t.tool_name || 'tool'; });

        html += '<div class="chain-node" data-idx="' + i + '">' +
            '<div class="chain-marker">' +
                '<div class="chain-seq">' + seq + '</div>' +
                (isLast ? '' : '<div class="chain-connector"></div>') +
            '</div>' +
            '<div class="chain-card" data-nav="execution" data-eid="' + esc(r.execution_id) + '" data-sid="' + esc(sessionId) + '">' +
                '<div class="chain-prompt-line">' + esc(prompt || '(empty prompt)') + '</div>' +
                '<div class="chain-response-line">' + (response ? '→ ' + esc(response) : '') + '</div>' +
                '<div class="chain-foot">' +
                    policyBadge(r.policy_result) +
                    (hasTools ? '<span class="badge badge-gold">&#9881; ' + toolNames.join(', ') + '</span>' : '') +
                    (tokens ? '<span class="chain-tokens">' + tokens + ' tokens</span>' : '') +
                    '<span class="chain-hash-preview">' + truncHash(r.record_hash, 20) + '</span>' +
                '</div>' +
            '</div>' +
        '</div>';
    }
    html += '</div></div>';

    setHTML($content, html);
}

// ─── View: Execution ─────────────────────────────────────────────

async function renderExecution(executionId, sessionId) {
    var data = await fetchJSON(API + '/executions/' + executionId);
    var r = data.record;
    var toolEvents = data.tool_events || [];

    // Resolve sessionId from record if not passed
    var sid = sessionId || r.session_id;

    var html = '<div class="fade-in">';

    // Breadcrumb
    html += '<div class="breadcrumb">' +
        '<a href="#" data-nav="sessions">Sessions</a>' +
        '<span class="sep">&#9656;</span>' +
        '<a href="#" data-nav="timeline" data-sid="' + esc(sid) + '">' + esc(formatSessionId(sid)) + '</a>' +
        '<span class="sep">&#9656;</span>' +
        '<span class="current">' + truncId(r.execution_id, 20) + '</span>' +
    '</div>';

    // Two-column: Metadata | Chain
    html += '<div class="exec-cols">';

    // Left column — metadata
    html += '<div class="card"><div class="detail-section">' +
        '<div class="detail-section-title">Execution Record</div>' +
        '<div class="detail-grid">' +
            detailRow('Execution ID', r.execution_id, 'mono') +
            detailRow('Model', displayModel(r.model_id || r.model_attestation_id)) +
            detailRow('Provider Request', r.provider_request_id, 'mono') +
            detailRow('Policy', r.policy_result, badgeClass(r.policy_result)) +
            detailRow('Policy Version', r.policy_version) +
            detailRow('Tenant', r.tenant_id) +
            detailRow('User', r.user || '-') +
            detailRow('Timestamp', formatTime(r.timestamp)) +
        '</div></div></div>';

    // Right column — chain integrity
    html += '<div class="card"><div class="detail-section">' +
        '<div class="detail-section-title">&#9670; Chain Integrity</div>' +
        '<div class="detail-grid">' +
            detailRow('Sequence', r.sequence_number, 'mono') +
            detailRow('Record Hash', r.record_hash, 'gold') +
            detailRow('Previous Hash', r.previous_record_hash, 'gold') +
        '</div></div></div>';

    html += '</div>'; // close exec-cols

    // Prompt
    html += '<div class="card"><div class="detail-section">' +
        '<div class="detail-section-title">Prompt</div>' +
        '<div class="text-block">' + esc(r.prompt_text || '(empty)') + '</div>' +
    '</div></div>';

    // Response
    html += '<div class="card"><div class="detail-section">' +
        '<div class="detail-section-title">Response</div>' +
        '<div class="text-block">' + esc(r.response_content || '(empty)') + '</div>' +
    '</div></div>';

    // Thinking content
    if (r.thinking_content) {
        html += '<div class="card"><div class="detail-section">' +
            '<div class="detail-section-title">&#9670; Reasoning / Thinking</div>' +
            '<div class="text-block thinking">' + esc(r.thinking_content) + '</div>' +
        '</div></div>';
    }

    // Tool events — combine tool_events (separate WAL records) and metadata.tool_interactions
    var tools = toolEvents.length > 0 ? toolEvents :
        (r.metadata && r.metadata.tool_interactions ? r.metadata.tool_interactions : []);
    var toolStrategy = r.metadata && r.metadata.tool_strategy ? r.metadata.tool_strategy : null;
    var toolIterations = r.metadata && r.metadata.tool_loop_iterations ? r.metadata.tool_loop_iterations : 0;

    if (tools.length > 0) {
        html += '<div class="card"><div class="detail-section">' +
            '<div class="detail-section-title">&#9881; Tool Calls (' + tools.length + ')' +
            (toolStrategy ? ' <span class="badge badge-muted" style="font-size:11px;vertical-align:middle">' + esc(toolStrategy) + ' strategy</span>' : '') +
            (toolIterations > 0 ? ' <span class="badge badge-muted" style="font-size:11px;vertical-align:middle">' + toolIterations + ' iteration' + (toolIterations > 1 ? 's' : '') + '</span>' : '') +
            '</div>';

        for (var t = 0; t < tools.length; t++) {
            var te = tools[t];
            var toolType = te.tool_type || 'function';
            var toolSource = te.source || '-';
            var isErr = te.is_error === true;

            html += '<div class="tool-event-card' + (isErr ? ' tool-error' : '') + '">';

            // Tool header
            html += '<div class="tool-event-header">' +
                '<span class="tool-event-name">' + esc(te.tool_name || 'unknown') + '</span>' +
                '<span class="badge ' + (toolType === 'web_search' ? 'badge-gold' : 'badge-muted') + '">' + esc(toolType) + '</span>' +
                '<span class="badge ' + (toolSource === 'gateway' ? 'badge-pass' : 'badge-muted') + '">' + esc(toolSource) + '</span>' +
                (isErr ? '<span class="badge badge-fail">error</span>' : '') +
                (te.duration_ms != null ? '<span class="tool-duration">' + te.duration_ms.toFixed(0) + 'ms</span>' : '') +
            '</div>';

            // Input data (search query, function args)
            if (te.input_data) {
                var inputDisplay = typeof te.input_data === 'string' ? te.input_data : JSON.stringify(te.input_data, null, 2);
                html += '<div class="tool-input-block">' +
                    '<div class="tool-block-label">Input</div>' +
                    '<div class="tool-block-content">' + esc(inputDisplay) + '</div>' +
                '</div>';
            }

            // Sources (web search results)
            var sources = te.sources || [];
            if (sources.length > 0) {
                html += '<div class="tool-sources">' +
                    '<div class="tool-block-label">Sources (' + sources.length + ')</div>';
                for (var si = 0; si < sources.length; si++) {
                    var src = sources[si];
                    html += '<div class="tool-source-item">' +
                        '<a href="' + esc(src.url || '#') + '" target="_blank" rel="noopener" class="tool-source-link">' +
                            esc(src.title || src.url || 'Link') +
                        '</a>' +
                        (src.snippet ? '<div class="tool-source-snippet">' + esc(src.snippet) + '</div>' : '') +
                    '</div>';
                }
                html += '</div>';
            }

            // Hashes
            html += '<div class="detail-grid" style="margin-top:8px">';
            if (te.input_hash) html += detailRow('Input Hash', te.input_hash, 'hash-gold');
            if (te.output_hash) html += detailRow('Output Hash', te.output_hash, 'hash-gold');
            html += detailRow('Timestamp', formatTime(te.timestamp));
            if (te.iteration) html += detailRow('Iteration', te.iteration);
            html += '</div>';

            // Content analysis on tool output (indirect prompt injection detection)
            var toolAnalysis = te.content_analysis || [];
            if (toolAnalysis.length > 0) {
                html += '<div class="tool-content-analysis">' +
                    '<div class="tool-block-label">Output Analysis</div>' +
                    '<div class="tool-analysis-badges">';
                for (var ai = 0; ai < toolAnalysis.length; ai++) {
                    var a = toolAnalysis[ai];
                    html += verdictBadge(a.verdict) + ' <span class="tool-analysis-name">' + esc(a.analyzer_id || '') + '</span> ';
                }
                html += '</div></div>';
            }

            html += '</div>'; // close tool-event-card
        }
        html += '</div></div>';
    }

    // Content analysis
    var decisions = r.metadata && r.metadata.analyzer_decisions;
    if (decisions && decisions.length > 0) {
        html += '<div class="card"><div class="detail-section">' +
            '<div class="detail-section-title">Content Analysis</div>' +
            '<div class="table-wrap"><table><thead><tr>' +
                '<th>Analyzer</th><th>Verdict</th><th>Confidence</th><th>Category</th><th>Reason</th>' +
            '</tr></thead><tbody>';
        for (var d = 0; d < decisions.length; d++) {
            var dec = decisions[d];
            html += '<tr>' +
                '<td class="mono">' + esc(dec.analyzer_id) + '</td>' +
                '<td>' + verdictBadge(dec.verdict) + '</td>' +
                '<td>' + (dec.confidence != null ? dec.confidence.toFixed(2) : '-') + '</td>' +
                '<td>' + esc(dec.category || '-') + '</td>' +
                '<td>' + esc(dec.reason || '-') + '</td>' +
            '</tr>';
        }
        html += '</tbody></table></div></div></div>';
    }

    // Token usage
    var usage = r.metadata && r.metadata.token_usage;
    if (usage) {
        html += '<div class="card"><div class="detail-section">' +
            '<div class="detail-section-title">Token Usage</div>' +
            '<div class="detail-grid">' +
                detailRow('Prompt Tokens', usage.prompt_tokens) +
                detailRow('Completion Tokens', usage.completion_tokens) +
                detailRow('Total', usage.total_tokens, 'mono') +
            '</div></div></div>';
    }

    // Raw metadata (collapsible)
    if (r.metadata) {
        html += '<div class="card"><div class="detail-section">' +
            '<div class="collapsible-trigger" id="meta-toggle">' +
                '<span class="arrow">&#9656;</span>' +
                '<span class="detail-section-title" style="margin-bottom:0;border:none;padding:0">Raw Metadata</span>' +
            '</div>' +
            '<div class="collapsible-content" id="meta-content">' +
                '<div class="text-block">' + esc(JSON.stringify(r.metadata, null, 2)) + '</div>' +
            '</div>' +
        '</div></div>';
    }

    html += '</div>'; // close fade-in

    setHTML($content, html);

    // Collapsible toggle
    var toggle = document.getElementById('meta-toggle');
    if (toggle) {
        toggle.addEventListener('click', function() {
            toggle.classList.toggle('open');
            document.getElementById('meta-content').classList.toggle('open');
        });
    }
}

// ─── View: Attempts ──────────────────────────────────────────────

async function renderAttempts(params) {
    params = params || {};
    var limit = params.limit || 100;
    var offset = params.offset || 0;
    var data = await fetchJSON(API + '/attempts?limit=' + limit + '&offset=' + offset);
    var items = data.items || [];
    var stats = data.stats || {};
    var total = data.total || 0;

    var html = '<div class="fade-in">';

    // Stats
    var statKeys = Object.keys(stats);
    html += '<div class="stat-grid">';
    html += statCard(total, 'Total Attempts', '');
    for (var i = 0; i < statKeys.length; i++) {
        var k = statKeys[i];
        var color = k === 'allowed' || k === 'forwarded' ? 'green' : k.indexOf('denied') === 0 ? 'red' : 'amber';
        html += statCard(stats[k], k.replace(/_/g, ' '), '<span class="' + color + '">' + (stats[k] / total * 100).toFixed(0) + '%</span>');
    }
    html += '</div>';

    if (!items.length) {
        html += '<div class="empty-state"><h3>No attempts recorded</h3></div>';
        setHTML($content, html + '</div>');
        return;
    }

    html += '<div class="card"><div class="card-head"><span class="card-title">Attempts</span></div>' +
        '<div class="table-wrap"><table><thead><tr>' +
            '<th>Disposition</th><th>Model</th><th>Path</th><th>Status</th><th>Time</th>' +
        '</tr></thead><tbody>';

    for (var j = 0; j < items.length; j++) {
        var a = items[j];
        var hasExec = !!a.execution_id;
        html += '<tr class="' + (hasExec ? 'clickable' : '') + '"' +
            (hasExec ? ' data-nav="execution" data-eid="' + esc(a.execution_id) + '"' : '') + '>' +
            '<td>' + dispBadge(a.disposition) + '</td>' +
            '<td class="model-name">' + esc(displayModel(a.model_id) || '-') + '</td>' +
            '<td class="mono" style="font-size:12px;color:var(--text-muted)">' + esc(a.path) + '</td>' +
            '<td><span class="badge ' + (a.status_code < 300 ? 'badge-pass' : a.status_code < 500 ? 'badge-warn' : 'badge-fail') + '">' + a.status_code + '</span></td>' +
            '<td class="time-relative">' + timeAgo(a.timestamp) + '</td>' +
        '</tr>';
    }

    html += '</tbody></table></div></div>';

    html += '<div class="pagination">';
    if (offset > 0) html += '<button class="btn" data-nav="attempts-page" data-offset="' + (offset - limit) + '">Previous</button>';
    if (items.length >= limit) html += '<button class="btn" data-nav="attempts-page" data-offset="' + (offset + limit) + '">Next</button>';
    html += '</div></div>';

    setHTML($content, html);
}

// ─── Chain Verification ──────────────────────────────────────────

async function verifyChain(sessionId) {
    var btn = document.getElementById('verify-btn');
    var resultEl = document.getElementById('verify-result');
    if (!resultEl) return;

    if (btn) { btn.disabled = true; btn.textContent = 'Verifying…'; }
    resultEl.textContent = '';

    try {
        var data = await fetchJSON(API + '/sessions/' + sessionId);
        var records = data.records || [];

        if (!records.length) {
            setHTML(resultEl, '<div class="verify-banner pass"><span class="verify-icon">&#10003;</span> No records to verify</div>');
            if (btn) { btn.disabled = false; btn.innerHTML = '&#9670; Verify Chain'; }
            return;
        }

        var errors = [];
        var GENESIS = new Array(129).join('0');
        var prevHash = GENESIS;
        var nodeResults = [];

        for (var i = 0; i < records.length; i++) {
            var r = records[i];
            var nodeOk = true;

            if (r.record_hash == null) {
                errors.push('Record #' + i + ': missing record_hash');
                nodeOk = false;
            } else {
                // Check linkage
                if (r.previous_record_hash != null && r.previous_record_hash !== prevHash) {
                    errors.push('Record #' + i + ': previous_record_hash mismatch');
                    nodeOk = false;
                }
                // Client-side recompute
                if (typeof sha3_512 !== 'undefined') {
                    var canonical = [
                        r.execution_id,
                        String(r.policy_version != null ? r.policy_version : ''),
                        String(r.policy_result != null ? r.policy_result : ''),
                        String(r.previous_record_hash != null ? r.previous_record_hash : ''),
                        String(r.sequence_number != null ? r.sequence_number : ''),
                        String(r.timestamp != null ? r.timestamp : '')
                    ].join('|');
                    var computed = sha3_512(canonical);
                    if (computed !== r.record_hash) {
                        errors.push('Record #' + i + ': hash mismatch (client recompute)');
                        nodeOk = false;
                    }
                }
                prevHash = r.record_hash;
            }
            nodeResults.push(nodeOk);
        }

        // Animate nodes one-by-one
        var nodes = document.querySelectorAll('.chain-node');
        for (var n = 0; n < nodes.length && n < nodeResults.length; n++) {
            (function(node, ok, delay) {
                setTimeout(function() {
                    node.setAttribute('data-verified', ok ? 'pass' : 'fail');
                }, delay);
            })(nodes[n], nodeResults[n], n * 180);
        }

        // Show result banner after animation completes
        var totalDelay = nodeResults.length * 180 + 100;
        setTimeout(function() {
            if (errors.length === 0) {
                setHTML(resultEl,
                    '<div class="verify-banner pass">' +
                        '<span class="verify-icon">&#10003;</span>' +
                        'Chain Valid — ' + records.length + ' record' + (records.length !== 1 ? 's' : '') +
                        ' verified, all hashes match' +
                    '</div>');
            } else {
                var errList = errors.map(function(e) { return '<li>' + esc(e) + '</li>'; }).join('');
                setHTML(resultEl,
                    '<div class="verify-banner fail">' +
                        '<span class="verify-icon">&#10007;</span>' +
                        'Chain Invalid — ' + errors.length + ' error' + (errors.length !== 1 ? 's' : '') +
                    '</div>' +
                    '<div class="card" style="margin-top:8px"><ul style="padding-left:20px;font-size:13px;color:var(--red)">' + errList + '</ul></div>');
            }
            if (btn) { btn.disabled = false; btn.innerHTML = '&#9670; Verify Chain'; }
        }, totalDelay);

    } catch (err) {
        // Fallback: server-side verification
        try {
            var result = await fetchJSON(API + '/verify/' + sessionId);
            if (result.valid) {
                setHTML(resultEl,
                    '<div class="verify-banner pass"><span class="verify-icon">&#10003;</span>' +
                    'Chain Valid (server-side) — ' + result.record_count + ' record(s)</div>');
            } else {
                var srvList = result.errors.map(function(e) { return '<li>' + esc(e) + '</li>'; }).join('');
                setHTML(resultEl,
                    '<div class="verify-banner fail"><span class="verify-icon">&#10007;</span>' +
                    'Chain Invalid (server-side)</div>' +
                    '<div class="card" style="margin-top:8px"><ul style="padding-left:20px;font-size:13px;color:var(--red)">' + srvList + '</ul></div>');
            }
        } catch (e2) {
            setHTML(resultEl, '<div class="verify-banner fail"><span class="verify-icon">&#10007;</span>Verification failed: ' + esc(e2.message) + '</div>');
        }
        if (btn) { btn.disabled = false; btn.innerHTML = '&#9670; Verify Chain'; }
    }
}

// ─── Click delegation ────────────────────────────────────────────

function bindClicks() {
    $content.querySelectorAll('[data-nav]').forEach(function(el) {
        el.addEventListener('click', function(e) {
            e.preventDefault();
            var nav = el.dataset.nav;
            if (nav === 'sessions') navigate('sessions');
            else if (nav === 'sessions-page') navigate('sessions', { offset: parseInt(el.dataset.offset || '0') });
            else if (nav === 'timeline') navigate('timeline', { sessionId: el.dataset.sid });
            else if (nav === 'execution') navigate('execution', { executionId: el.dataset.eid, sessionId: el.dataset.sid });
            else if (nav === 'attempts') navigate('attempts');
            else if (nav === 'attempts-page') navigate('attempts', { offset: parseInt(el.dataset.offset || '0') });
            else if (nav === 'overview') navigate('overview');
        });
    });
    $content.querySelectorAll('[data-action="verify"]').forEach(function(el) {
        el.addEventListener('click', function(e) {
            e.stopPropagation();
            verifyChain(el.dataset.sid);
        });
    });
}

// ─── Helpers ─────────────────────────────────────────────────────

async function fetchJSON(url) {
    var resp = await fetch(url);
    if (!resp.ok) {
        var body = '';
        try { body = await resp.text(); } catch(_) {}
        throw new Error('HTTP ' + resp.status + (body ? ': ' + body : ''));
    }
    return resp.json();
}

function esc(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function displayModel(m) {
    if (!m) return '';
    // Strip self-attested prefix (standalone governance mode)
    m = m.replace(/^self-attested:/, '');
    // If it's still a UUID (control plane attestation_id from old records), show truncated
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(m)) {
        return m.substring(0, 8) + '…';
    }
    return m;
}

function formatSessionId(id) {
    if (!id) return '-';
    // If it's a UUID, show first 12 chars
    if (/^[0-9a-f]{8}-/.test(id)) return id.substring(0, 13) + '…';
    // If it's a named session (chain-test-xxx), show it cleaner
    if (id.length > 28) return id.substring(0, 28) + '…';
    return id;
}

function truncId(id, len) {
    len = len || 16;
    if (!id) return '-';
    return id.length > len ? id.substring(0, len) + '…' : id;
}

function truncHash(h, len) {
    len = len || 16;
    if (!h) return '-';
    return h.substring(0, len) + '…';
}

function timeAgo(ts) {
    if (!ts) return '-';
    var diff = (Date.now() - new Date(ts).getTime()) / 1000;
    if (diff < 0) diff = 0;
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
}

function formatTime(ts) {
    if (!ts) return '-';
    try {
        return new Date(ts).toLocaleString();
    } catch (_) {
        return ts;
    }
}

function formatUptime(seconds) {
    if (seconds < 60) return Math.floor(seconds) + 's';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm';
    if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ' + Math.floor((seconds % 3600) / 60) + 'm';
    return Math.floor(seconds / 86400) + 'd ' + Math.floor((seconds % 86400) / 3600) + 'h';
}

function formatNumber(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
    return String(n);
}

function getTokenCount(record) {
    var m = record.metadata;
    if (m && m.token_usage && m.token_usage.total_tokens) return m.token_usage.total_tokens;
    return null;
}

function policyBadge(result) {
    if (!result) return '';
    var cls = result === 'pass' ? 'badge-pass' :
              result === 'denied' || result === 'blocked' ? 'badge-fail' :
              result.indexOf('flag') >= 0 ? 'badge-warn' : 'badge-muted';
    return '<span class="badge ' + cls + '">' + esc(result) + '</span>';
}

function verdictBadge(verdict) {
    if (!verdict) return '<span class="badge badge-muted">-</span>';
    var v = verdict.toLowerCase();
    var cls = v === 'pass' ? 'badge-pass' : v === 'block' ? 'badge-fail' : v === 'warn' ? 'badge-warn' : 'badge-muted';
    return '<span class="badge ' + cls + '">' + esc(verdict) + '</span>';
}

function dispBadge(disp) {
    if (!disp) return '<span class="badge badge-muted">-</span>';
    var cls = disp === 'allowed' || disp === 'forwarded' ? 'badge-pass' :
              disp.indexOf('denied') === 0 ? 'badge-fail' :
              disp.indexOf('error') === 0 ? 'badge-fail' : 'badge-muted';
    return '<span class="badge ' + cls + '">' + esc(disp.replace(/_/g, ' ')) + '</span>';
}

function badgeClass(result) {
    if (!result) return '';
    if (result === 'pass') return 'badge-inline-pass';
    if (result === 'denied' || result === 'blocked') return 'badge-inline-fail';
    return '';
}

function statCard(value, label, sub) {
    return '<div class="stat-card">' +
        '<div class="stat-value">' + esc(String(value)) + '</div>' +
        '<div class="stat-label">' + esc(label) + '</div>' +
        (sub ? '<div class="stat-sub">' + sub + '</div>' : '') +
    '</div>';
}

function detailRow(label, value, extraClass) {
    extraClass = extraClass || '';
    var cls = '';
    if (extraClass === 'mono') cls = 'mono';
    else if (extraClass === 'gold') cls = 'detail-value gold mono';
    else if (extraClass === 'hash-gold') cls = 'hash-gold';
    else cls = '';

    var valStr = value != null ? String(value) : '-';
    return '<div class="detail-label">' + esc(label) + '</div>' +
           '<div class="detail-value ' + cls + '">' + esc(valStr) + '</div>';
}

// ═══════════════════════════════════════════════════════════════════
// Phase 20: Control Plane Dashboard
// Security: all dynamic content escaped via esc(), same as lineage views.
// Data source: gateway's own control plane API (admin-authenticated).
// ═══════════════════════════════════════════════════════════════════

var _controlSub = 'models';
var CTRL_API = '/v1/control';

// ─── Control Auth + Fetch Helpers ─────────────────────────────────

function getControlKey() {
    return sessionStorage.getItem('cp_api_key') || '';
}

function setControlKey(key) {
    sessionStorage.setItem('cp_api_key', key);
}

function clearControlKey() {
    sessionStorage.removeItem('cp_api_key');
}

async function fetchControlJSON(url) {
    var key = getControlKey();
    var resp = await fetch(url, {
        headers: key ? { 'X-API-Key': key } : {}
    });
    if (resp.status === 401 || resp.status === 403) {
        clearControlKey();
        throw new Error('AUTH');
    }
    if (!resp.ok) {
        var body = '';
        try { body = await resp.text(); } catch(_) {}
        throw new Error('HTTP ' + resp.status + (body ? ': ' + body : ''));
    }
    return resp.json();
}

async function controlFetch(url, options) {
    var key = getControlKey();
    options = options || {};
    options.headers = Object.assign({
        'Content-Type': 'application/json'
    }, key ? { 'X-API-Key': key } : {}, options.headers || {});
    var resp = await fetch(url, options);
    if (resp.status === 401 || resp.status === 403) {
        clearControlKey();
        throw new Error('AUTH');
    }
    if (!resp.ok) {
        var body = '';
        try { body = await resp.text(); } catch(_) {}
        throw new Error('HTTP ' + resp.status + (body ? ': ' + body : ''));
    }
    return resp.json();
}

// ─── Control: Router ──────────────────────────────────────────────

async function renderControl(params) {
    params = params || {};
    if (params.sub) _controlSub = params.sub;

    if (!getControlKey()) {
        renderControlAuth();
        return;
    }

    var html = '<div class="fade-in">';

    // Sub-navigation
    html += '<div class="control-subnav">';
    var tabs = ['models', 'policies', 'budgets', 'status'];
    for (var i = 0; i < tabs.length; i++) {
        var t = tabs[i];
        html += '<button class="control-subtab' + (t === _controlSub ? ' active' : '') + '" data-csub="' + t + '">' +
            esc(t.charAt(0).toUpperCase() + t.slice(1)) + '</button>';
    }
    html += '</div>';
    html += '<div id="control-content"></div>';
    html += '</div>';

    setHTML($content, html);

    // Bind sub-tab clicks
    $content.querySelectorAll('[data-csub]').forEach(function(el) {
        el.addEventListener('click', function() {
            _controlSub = el.dataset.csub;
            navigate('control', { sub: _controlSub });
        });
    });

    var $cc = document.getElementById('control-content');
    try {
        switch (_controlSub) {
            case 'models': await renderControlModels($cc); break;
            case 'policies': await renderControlPolicies($cc); break;
            case 'budgets': await renderControlBudgets($cc); break;
            case 'status': await renderControlStatus($cc); break;
        }
    } catch (err) {
        if (err.message === 'AUTH') {
            renderControlAuth();
            return;
        }
        setHTML($cc, '<div class="error-card">Error: ' + esc(err.message) + '</div>');
    }
    bindClicks();
}

// ─── Control: Auth Gate ───────────────────────────────────────────

function renderControlAuth() {
    var html = '<div class="fade-in">' +
        '<div class="auth-card">' +
            '<div class="auth-icon">&#9670;</div>' +
            '<div class="auth-title">Control Plane Access</div>' +
            '<div class="auth-subtitle">Enter your gateway API key to manage models, policies, and budgets.</div>' +
            '<div class="form-group">' +
                '<input type="password" class="form-input" id="auth-key-input" placeholder="API key" autocomplete="off">' +
            '</div>' +
            '<div class="form-actions" style="justify-content:center">' +
                '<button class="btn-primary" id="auth-submit-btn">Authenticate</button>' +
            '</div>' +
            '<div class="auth-error" id="auth-error"></div>' +
        '</div>' +
    '</div>';

    setHTML($content, html);

    var $input = document.getElementById('auth-key-input');
    var $btn = document.getElementById('auth-submit-btn');
    var $err = document.getElementById('auth-error');

    async function tryAuth() {
        var key = $input.value.trim();
        if (!key) { $err.textContent = 'Please enter an API key'; return; }
        setControlKey(key);
        try {
            await fetchControlJSON(CTRL_API + '/status');
            navigate('control', { sub: _controlSub });
        } catch (e) {
            clearControlKey();
            $err.textContent = 'Invalid API key or gateway unreachable';
        }
    }

    $btn.addEventListener('click', tryAuth);
    $input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') tryAuth();
    });
    $input.focus();
}

// ─── Control: Models ──────────────────────────────────────────────

async function renderControlModels($el) {
    var results = await Promise.all([
        fetchControlJSON(CTRL_API + '/attestations'),
        fetchJSON(HEALTH_URL)
    ]);
    var attData = results[0];
    var health = results[1];
    var attestations = attData.attestations || [];
    var modelCaps = health.model_capabilities || {};

    var html = '';

    // Add model form toggle
    html += '<div class="card"><div class="card-head">' +
        '<span class="card-title">Model Attestations (' + attestations.length + ')</span>' +
        '<button class="btn-primary btn-sm" id="toggle-add-model">+ Add Model</button>' +
    '</div>';

    // Inline add form (hidden by default) — uses safe setHTML for rendering
    html += '<div id="add-model-form" style="display:none">' +
        '<div class="inline-form">' +
            '<div class="inline-form-title">&#9670; Register Model</div>' +
            '<div class="form-row">' +
                '<div class="form-group"><label class="form-label">Model ID</label>' +
                    '<input type="text" class="form-input" id="am-model-id" placeholder="e.g. qwen3:4b"></div>' +
                '<div class="form-group"><label class="form-label">Provider</label>' +
                    '<select class="form-select" id="am-provider">' +
                        '<option value="ollama">ollama</option>' +
                        '<option value="openai">openai</option>' +
                        '<option value="anthropic">anthropic</option>' +
                        '<option value="huggingface">huggingface</option>' +
                    '</select></div>' +
            '</div>' +
            '<div class="form-group"><label class="form-label">Notes</label>' +
                '<input type="text" class="form-input" id="am-notes" placeholder="Optional notes"></div>' +
            '<div class="form-actions">' +
                '<button class="btn-primary" id="am-submit">Register</button>' +
                '<button class="btn-ghost" id="am-cancel">Cancel</button>' +
            '</div>' +
        '</div></div>';

    if (!attestations.length && !Object.keys(modelCaps).length) {
        html += '<div class="empty-state"><p>No models registered. Add a model or send a request to auto-discover.</p></div>';
    } else {
        html += '<div class="table-wrap"><table><thead><tr>' +
            '<th>Model ID</th><th>Provider</th><th>Status</th><th>Verification</th><th>Notes</th><th style="text-align:right">Actions</th>' +
        '</tr></thead><tbody>';

        for (var i = 0; i < attestations.length; i++) {
            var a = attestations[i];
            var sCls = a.status === 'active' ? 'badge-pass' : a.status === 'revoked' ? 'badge-fail' : 'badge-warn';
            html += '<tr class="control-table-row">' +
                '<td class="id">' + esc(a.model_id) + '</td>' +
                '<td class="mono">' + esc(a.provider) + '</td>' +
                '<td><span class="badge ' + sCls + '">' + esc(a.status) + '</span></td>' +
                '<td><span class="badge badge-muted">' + esc(a.verification_level) + '</span></td>' +
                '<td style="font-size:12px;color:var(--text-muted)">' + esc(a.notes || '-') + '</td>' +
                '<td><div class="actions-cell">' +
                    (a.status === 'active'
                        ? '<button class="btn-ghost btn-sm" data-action="revoke-model" data-aid="' + esc(a.attestation_id) + '">Revoke</button>'
                        : '<button class="btn-ghost btn-sm" data-action="approve-model" data-aid="' + esc(a.attestation_id) + '" data-mid="' + esc(a.model_id) + '" data-prov="' + esc(a.provider) + '">Approve</button>') +
                    '<button class="btn-danger btn-sm" data-action="remove-model" data-aid="' + esc(a.attestation_id) + '">Remove</button>' +
                '</div></td>' +
            '</tr>';
        }

        // Auto-discovered models not in attestation list
        var attModels = {};
        for (var j = 0; j < attestations.length; j++) {
            attModels[attestations[j].model_id] = true;
        }
        var capKeys = Object.keys(modelCaps);
        for (var k = 0; k < capKeys.length; k++) {
            var mid = capKeys[k];
            if (!attModels[mid]) {
                var cap = modelCaps[mid];
                html += '<tr class="control-table-row">' +
                    '<td class="id">' + esc(mid) + '</td>' +
                    '<td class="mono">auto</td>' +
                    '<td><span class="badge badge-auto">discovered</span></td>' +
                    '<td><span class="badge badge-muted">tools: ' + (cap.supports_tools ? 'yes' : 'no') + '</span></td>' +
                    '<td style="font-size:12px;color:var(--text-muted)">Auto-discovered via capability probe</td>' +
                    '<td><div class="actions-cell">' +
                        '<button class="btn-primary btn-sm" data-action="register-discovered" data-mid="' + esc(mid) + '">Register</button>' +
                    '</div></td>' +
                '</tr>';
            }
        }

        html += '</tbody></table></div>';
    }
    html += '</div>';

    setHTML($el, html);

    // Form toggle
    var $form = document.getElementById('add-model-form');
    document.getElementById('toggle-add-model').addEventListener('click', function() {
        $form.style.display = $form.style.display === 'none' ? 'block' : 'none';
    });
    var cancelBtn = document.getElementById('am-cancel');
    if (cancelBtn) cancelBtn.addEventListener('click', function() { $form.style.display = 'none'; });

    // Submit
    document.getElementById('am-submit').addEventListener('click', async function() {
        var modelId = document.getElementById('am-model-id').value.trim();
        if (!modelId) return;
        try {
            await controlFetch(CTRL_API + '/attestations', {
                method: 'POST',
                body: JSON.stringify({
                    model_id: modelId,
                    provider: document.getElementById('am-provider').value,
                    status: 'active',
                    notes: document.getElementById('am-notes').value.trim()
                })
            });
            navigate('control', { sub: 'models' });
        } catch (e) {
            if (e.message === 'AUTH') { renderControlAuth(); return; }
        }
    });

    // Action buttons
    bindControlModelActions($el);
}

function bindControlModelActions($el) {
    $el.querySelectorAll('[data-action="revoke-model"]').forEach(function(el) {
        el.addEventListener('click', async function() {
            try {
                await controlFetch(CTRL_API + '/attestations', {
                    method: 'POST',
                    body: JSON.stringify({ attestation_id: el.dataset.aid, status: 'revoked' })
                });
                navigate('control', { sub: 'models' });
            } catch (e) { if (e.message === 'AUTH') renderControlAuth(); }
        });
    });
    $el.querySelectorAll('[data-action="approve-model"]').forEach(function(el) {
        el.addEventListener('click', async function() {
            try {
                await controlFetch(CTRL_API + '/attestations', {
                    method: 'POST',
                    body: JSON.stringify({
                        model_id: el.dataset.mid,
                        provider: el.dataset.prov,
                        status: 'active'
                    })
                });
                navigate('control', { sub: 'models' });
            } catch (e) { if (e.message === 'AUTH') renderControlAuth(); }
        });
    });
    $el.querySelectorAll('[data-action="remove-model"]').forEach(function(el) {
        el.addEventListener('click', async function() {
            try {
                await controlFetch(CTRL_API + '/attestations/' + el.dataset.aid, { method: 'DELETE' });
                navigate('control', { sub: 'models' });
            } catch (e) { if (e.message === 'AUTH') renderControlAuth(); }
        });
    });
    $el.querySelectorAll('[data-action="register-discovered"]').forEach(function(el) {
        el.addEventListener('click', async function() {
            try {
                await controlFetch(CTRL_API + '/attestations', {
                    method: 'POST',
                    body: JSON.stringify({
                        model_id: el.dataset.mid,
                        provider: 'ollama',
                        status: 'active',
                        notes: 'Registered from auto-discovery'
                    })
                });
                navigate('control', { sub: 'models' });
            } catch (e) { if (e.message === 'AUTH') renderControlAuth(); }
        });
    });
}

// ─── Control: Policies ────────────────────────────────────────────

async function renderControlPolicies($el) {
    var data = await fetchControlJSON(CTRL_API + '/policies');
    var policies = data.policies || [];

    var html = '<div class="card"><div class="card-head">' +
        '<span class="card-title">Policies (' + policies.length + ')</span>' +
        '<button class="btn-primary btn-sm" id="toggle-add-policy">+ Add Policy</button>' +
    '</div>';

    // Inline add form
    html += '<div id="add-policy-form" style="display:none">' +
        '<div class="inline-form">' +
            '<div class="inline-form-title">&#9670; Create Policy</div>' +
            '<div class="form-row">' +
                '<div class="form-group"><label class="form-label">Policy Name</label>' +
                    '<input type="text" class="form-input" id="ap-name" placeholder="e.g. content-safety"></div>' +
                '<div class="form-group"><label class="form-label">Enforcement Level</label>' +
                    '<select class="form-select" id="ap-enforcement">' +
                        '<option value="blocking">blocking</option>' +
                        '<option value="audit_only">audit_only</option>' +
                    '</select></div>' +
            '</div>' +
            '<div class="form-group"><label class="form-label">Description</label>' +
                '<textarea class="form-textarea" id="ap-desc" placeholder="What does this policy enforce?"></textarea></div>' +
            '<div class="form-group"><label class="form-label">Rules</label>' +
                '<div id="ap-rules"></div>' +
                '<button class="btn-ghost" id="ap-add-rule" style="margin-top:4px">+ Add Rule</button>' +
            '</div>' +
            '<div class="form-actions">' +
                '<button class="btn-primary" id="ap-submit">Create Policy</button>' +
                '<button class="btn-ghost" id="ap-cancel">Cancel</button>' +
            '</div>' +
        '</div></div>';

    if (!policies.length) {
        html += '<div class="empty-state"><p>No policies defined. Create a policy to enforce governance rules.</p></div>';
    } else {
        html += '<div class="table-wrap"><table><thead><tr>' +
            '<th>Name</th><th>Enforcement</th><th>Rules</th><th>Status</th><th style="text-align:right">Actions</th>' +
        '</tr></thead><tbody>';
        for (var i = 0; i < policies.length; i++) {
            var p = policies[i];
            var eCls = p.enforcement_level === 'blocking' ? 'badge-enforced' : 'badge-muted';
            var sCls = p.status === 'active' ? 'badge-pass' : 'badge-muted';
            var ruleCount = (p.rules || []).length + (p.prompt_rules || []).length + (p.rag_rules || []).length;
            html += '<tr class="control-table-row">' +
                '<td style="font-weight:500">' + esc(p.policy_name) + '</td>' +
                '<td><span class="badge ' + eCls + '">' + esc(p.enforcement_level) + '</span></td>' +
                '<td class="mono">' + ruleCount + '</td>' +
                '<td><span class="badge ' + sCls + '">' + esc(p.status) + '</span></td>' +
                '<td><div class="actions-cell">' +
                    '<button class="btn-danger btn-sm" data-action="delete-policy" data-pid="' + esc(p.policy_id) + '">Delete</button>' +
                '</div></td>' +
            '</tr>';
        }
        html += '</tbody></table></div>';
    }
    html += '</div>';

    setHTML($el, html);

    // Form toggle
    var $form = document.getElementById('add-policy-form');
    document.getElementById('toggle-add-policy').addEventListener('click', function() {
        $form.style.display = $form.style.display === 'none' ? 'block' : 'none';
    });
    var cancelBtn = document.getElementById('ap-cancel');
    if (cancelBtn) cancelBtn.addEventListener('click', function() { $form.style.display = 'none'; });

    // Rule builder — builds DOM elements directly (no innerHTML on user data)
    var $rules = document.getElementById('ap-rules');
    document.getElementById('ap-add-rule').addEventListener('click', function() {
        var row = document.createElement('div');
        row.className = 'rule-row';
        var fieldInput = document.createElement('input');
        fieldInput.type = 'text';
        fieldInput.className = 'form-input';
        fieldInput.placeholder = 'field';
        fieldInput.setAttribute('data-rf', 'field');
        var opSelect = document.createElement('select');
        opSelect.className = 'form-select';
        opSelect.setAttribute('data-rf', 'operator');
        ['equals','contains','not_equals','regex'].forEach(function(v) {
            var opt = document.createElement('option');
            opt.value = v;
            opt.textContent = v;
            opSelect.appendChild(opt);
        });
        var valInput = document.createElement('input');
        valInput.type = 'text';
        valInput.className = 'form-input';
        valInput.placeholder = 'value';
        valInput.setAttribute('data-rf', 'value');
        var removeBtn = document.createElement('button');
        removeBtn.className = 'rule-remove';
        removeBtn.textContent = '\u00D7';
        removeBtn.addEventListener('click', function() { row.remove(); });
        row.appendChild(fieldInput);
        row.appendChild(opSelect);
        row.appendChild(valInput);
        row.appendChild(removeBtn);
        $rules.appendChild(row);
    });

    // Submit
    document.getElementById('ap-submit').addEventListener('click', async function() {
        var name = document.getElementById('ap-name').value.trim();
        if (!name) return;
        var rules = [];
        $rules.querySelectorAll('.rule-row').forEach(function(r) {
            var field = r.querySelector('[data-rf="field"]').value.trim();
            var op = r.querySelector('[data-rf="operator"]').value;
            var val = r.querySelector('[data-rf="value"]').value.trim();
            if (field && val) rules.push({ field: field, operator: op, value: val });
        });
        try {
            await controlFetch(CTRL_API + '/policies', {
                method: 'POST',
                body: JSON.stringify({
                    policy_name: name,
                    enforcement_level: document.getElementById('ap-enforcement').value,
                    description: document.getElementById('ap-desc').value.trim(),
                    rules: rules
                })
            });
            navigate('control', { sub: 'policies' });
        } catch (e) {
            if (e.message === 'AUTH') { renderControlAuth(); return; }
        }
    });

    // Delete buttons
    $el.querySelectorAll('[data-action="delete-policy"]').forEach(function(el) {
        el.addEventListener('click', async function() {
            try {
                await controlFetch(CTRL_API + '/policies/' + el.dataset.pid, { method: 'DELETE' });
                navigate('control', { sub: 'policies' });
            } catch (e) { if (e.message === 'AUTH') renderControlAuth(); }
        });
    });
}

// ─── Control: Budgets ─────────────────────────────────────────────

async function renderControlBudgets($el) {
    var results = await Promise.all([
        fetchControlJSON(CTRL_API + '/budgets'),
        fetchJSON(HEALTH_URL)
    ]);
    var budData = results[0];
    var health = results[1];
    var budgets = budData.budgets || [];
    var tokenBudget = health.token_budget || null;

    var html = '<div class="card"><div class="card-head">' +
        '<span class="card-title">Token Budgets (' + budgets.length + ')</span>' +
        '<button class="btn-primary btn-sm" id="toggle-add-budget">+ Add Budget</button>' +
    '</div>';

    // Inline add form
    html += '<div id="add-budget-form" style="display:none">' +
        '<div class="inline-form">' +
            '<div class="inline-form-title">&#9670; Create Budget</div>' +
            '<div class="form-row">' +
                '<div class="form-group"><label class="form-label">Tenant ID</label>' +
                    '<input type="text" class="form-input" id="ab-tenant" placeholder="e.g. dev-tenant"></div>' +
                '<div class="form-group"><label class="form-label">User (optional)</label>' +
                    '<input type="text" class="form-input" id="ab-user" placeholder="Leave empty for tenant-wide"></div>' +
            '</div>' +
            '<div class="form-row">' +
                '<div class="form-group"><label class="form-label">Period</label>' +
                    '<select class="form-select" id="ab-period">' +
                        '<option value="monthly">monthly</option>' +
                        '<option value="daily">daily</option>' +
                    '</select></div>' +
                '<div class="form-group"><label class="form-label">Max Tokens</label>' +
                    '<input type="number" class="form-input" id="ab-max" placeholder="e.g. 1000000"></div>' +
            '</div>' +
            '<div class="form-actions">' +
                '<button class="btn-primary" id="ab-submit">Create Budget</button>' +
                '<button class="btn-ghost" id="ab-cancel">Cancel</button>' +
            '</div>' +
        '</div></div>';

    // Usage summary card
    if (tokenBudget) {
        var pct = tokenBudget.percent_used || 0;
        var barCls = pct < 60 ? 'green' : pct < 85 ? 'amber' : 'red';
        html += '<div class="card" style="margin-bottom:16px">' +
            '<div class="detail-section-title">Current Usage</div>' +
            '<div class="progress-bar"><div class="progress-fill ' + barCls + '" style="width:' + Math.min(pct, 100) + '%"></div></div>' +
            '<div class="progress-label">' +
                '<span>' + formatNumber(tokenBudget.tokens_used) + ' used</span>' +
                '<span>' + formatNumber(tokenBudget.max_tokens) + ' limit (' + esc(pct.toFixed(1)) + '%)</span>' +
            '</div>' +
        '</div>';
    }

    if (!budgets.length) {
        html += '<div class="empty-state"><p>No budgets configured. Add a budget to enforce token limits.</p></div>';
    } else {
        html += '<div class="table-wrap"><table><thead><tr>' +
            '<th>Tenant</th><th>User</th><th>Period</th><th>Max Tokens</th><th style="text-align:right">Actions</th>' +
        '</tr></thead><tbody>';
        for (var i = 0; i < budgets.length; i++) {
            var b = budgets[i];
            html += '<tr class="control-table-row">' +
                '<td class="mono">' + esc(b.tenant_id) + '</td>' +
                '<td class="mono">' + esc(b.user || '(all)') + '</td>' +
                '<td><span class="badge badge-muted">' + esc(b.period) + '</span></td>' +
                '<td class="mono">' + formatNumber(b.max_tokens) + '</td>' +
                '<td><div class="actions-cell">' +
                    '<button class="btn-danger btn-sm" data-action="delete-budget" data-bid="' + esc(b.budget_id) + '">Delete</button>' +
                '</div></td>' +
            '</tr>';
        }
        html += '</tbody></table></div>';
    }
    html += '</div>';

    setHTML($el, html);

    // Form toggle
    var $form = document.getElementById('add-budget-form');
    document.getElementById('toggle-add-budget').addEventListener('click', function() {
        $form.style.display = $form.style.display === 'none' ? 'block' : 'none';
    });
    var cancelBtn = document.getElementById('ab-cancel');
    if (cancelBtn) cancelBtn.addEventListener('click', function() { $form.style.display = 'none'; });

    // Submit
    document.getElementById('ab-submit').addEventListener('click', async function() {
        var maxTokens = parseInt(document.getElementById('ab-max').value);
        if (!maxTokens || maxTokens <= 0) return;
        try {
            await controlFetch(CTRL_API + '/budgets', {
                method: 'POST',
                body: JSON.stringify({
                    tenant_id: document.getElementById('ab-tenant').value.trim(),
                    user: document.getElementById('ab-user').value.trim(),
                    period: document.getElementById('ab-period').value,
                    max_tokens: maxTokens
                })
            });
            navigate('control', { sub: 'budgets' });
        } catch (e) {
            if (e.message === 'AUTH') { renderControlAuth(); return; }
        }
    });

    // Delete buttons
    $el.querySelectorAll('[data-action="delete-budget"]').forEach(function(el) {
        el.addEventListener('click', async function() {
            try {
                await controlFetch(CTRL_API + '/budgets/' + el.dataset.bid, { method: 'DELETE' });
                navigate('control', { sub: 'budgets' });
            } catch (e) { if (e.message === 'AUTH') renderControlAuth(); }
        });
    });
}

// ─── Control: Status ──────────────────────────────────────────────

async function renderControlStatus($el) {
    var results = await Promise.all([
        fetchControlJSON(CTRL_API + '/status'),
        fetchJSON(HEALTH_URL)
    ]);
    var status = results[0];
    var health = results[1];

    var html = '<div class="status-grid">';

    // Gateway info
    html += '<div class="card"><div class="status-card-header"><span class="icon">&#9670;</span> Gateway</div>' +
        '<div class="detail-grid">' +
            detailRow('Gateway ID', status.gateway_id, 'mono') +
            detailRow('Tenant', status.tenant_id) +
            detailRow('Enforcement', status.enforcement_mode) +
            detailRow('Sync Mode', status.sync_mode) +
            detailRow('Uptime', formatUptime(status.uptime_seconds || 0)) +
        '</div></div>';

    // Cache status
    html += '<div class="card"><div class="status-card-header"><span class="icon">&#9670;</span> Caches</div>' +
        '<div class="detail-grid">';
    if (status.attestation_cache) {
        html += detailRow('Attestation Entries', status.attestation_cache.entries);
    }
    if (status.policy_cache) {
        html += detailRow('Policy Version', status.policy_cache.version) +
            detailRow('Policy Stale', status.policy_cache.stale ? 'YES' : 'no');
        if (status.policy_cache.last_sync) {
            html += detailRow('Last Sync', formatTime(status.policy_cache.last_sync));
        }
    }
    html += '</div></div>';

    // WAL status
    if (status.wal) {
        html += '<div class="card"><div class="status-card-header"><span class="icon">&#9670;</span> WAL Storage</div>' +
            '<div class="detail-grid">' +
                detailRow('Pending Records', status.wal.pending_records) +
                detailRow('Disk Usage', formatNumber(status.wal.disk_usage_bytes) + ' bytes') +
            '</div></div>';
    }

    // Model capabilities
    var caps = status.model_capabilities || health.model_capabilities || {};
    var capKeys = Object.keys(caps);
    if (capKeys.length > 0) {
        html += '<div class="card"><div class="status-card-header"><span class="icon">&#9881;</span> Model Capabilities</div>' +
            '<div class="table-wrap"><table><thead><tr><th>Model</th><th>Supports Tools</th></tr></thead><tbody>';
        for (var i = 0; i < capKeys.length; i++) {
            var mid = capKeys[i];
            var st = caps[mid].supports_tools;
            html += '<tr><td class="id">' + esc(mid) + '</td>' +
                '<td><span class="badge ' + (st ? 'badge-pass' : 'badge-muted') + '">' + (st ? 'yes' : 'no') + '</span></td></tr>';
        }
        html += '</tbody></table></div></div>';
    }

    // Health overview
    html += '<div class="card"><div class="status-card-header"><span class="icon">&#9670;</span> Health</div>' +
        '<div class="detail-grid">' +
            detailRow('Status', health.status) +
            detailRow('Uptime', formatUptime(health.uptime_seconds || 0));
    if (health.session_chain) {
        html += detailRow('Active Sessions', health.session_chain.active_sessions);
    }
    if (health.token_budget) {
        html += detailRow('Token Usage', esc(health.token_budget.tokens_used + ' / ' + health.token_budget.max_tokens));
    }
    html += '</div></div>';

    html += '</div>'; // close status-grid

    setHTML($el, html);
}

// ─── Init ────────────────────────────────────────────────────────

navigate('overview');
