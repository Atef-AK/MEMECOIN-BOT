import { useState, useEffect, useCallback, useRef } from 'react';
import {
  api,
  HealthData,
  StatsData,
  TradeData,
  PositionData,
  CandidateData,
  KillSwitchData,
  AdaptiveIntelligenceData,
  LogEntry,
} from './api';

function formatSOL(val: number): string {
  return val >= 0 ? `+${val.toFixed(4)}` : val.toFixed(4);
}

function formatPercent(val: number): string {
  return val >= 0 ? `+${val.toFixed(1)}%` : `${val.toFixed(1)}%`;
}

function formatUSD(val: number): string {
  return `$${val.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

function formatPrice(val: number): string {
  if (!val || val === 0) return '0.00';
  if (val >= 1) return val.toFixed(4);
  if (val >= 0.0001) return val.toFixed(6);
  if (val < 1e-6) return val.toExponential(3);
  return val.toFixed(8);
}

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '—';
  const diff = (Date.now() - new Date(dateStr).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function scoreColor(score: number): string {
  if (score >= 90) return 'text-green';
  if (score >= 80) return 'text-cyan';
  if (score >= 70) return 'text-amber';
  return 'text-red';
}

function scoreBarClass(score: number): string {
  if (score >= 80) return 'high';
  if (score >= 60) return 'medium';
  return 'low';
}

function pnlColor(val: number): string {
  return val >= 0 ? 'text-green' : 'text-red';
}

function formatStrategyName(name: string): string {
  if (!name || name === 'none') return 'EXPLORING / SEEDING';
  return name.replace(/_/g, ' ').toUpperCase();
}

export default function App() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [stats, setStats] = useState<StatsData | null>(null);
  const [adaptive, setAdaptive] = useState<AdaptiveIntelligenceData | null>(null);
  const [trades, setTrades] = useState<TradeData[]>([]);
  const [positions, setPositions] = useState<PositionData[]>([]);
  const [candidates, setCandidates] = useState<CandidateData[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [killSwitch, setKillSwitch] = useState<KillSwitchData | null>(null);
  const [connected, setConnected] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [error, setError] = useState('');
  
  const wsRef = useRef<WebSocket | null>(null);
  const logTerminalRef = useRef<HTMLDivElement | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [h, s, a, t, p, c, k, l] = await Promise.all([
        api.getHealth(),
        api.getStats().catch(() => null),
        api.getAdaptiveIntelligence().catch(() => null),
        api.getTrades(20).catch(() => ({ trades: [], total: 0 })),
        api.getPositions().catch(() => ({ positions: [] })),
        api.getCandidates(50).catch(() => ({ candidates: [], total: 0 })),
        api.getKillSwitch().catch(() => null),
        api.getLogs().catch(() => ({ logs: [] })),
      ]);
      setHealth(h);
      if (s) setStats(s);
      if (a) setAdaptive(a);
      setTrades(t.trades);
      setPositions(p.positions);
      setCandidates(c.candidates);
      setKillSwitch(k);
      if (l.logs && l.logs.length > 0) {
        setLogs((prev) => (prev.length === 0 ? l.logs : prev));
      }
      setConnected(true);
      setError('');
    } catch (e: any) {
      setConnected(false);
      setError(e.message || 'Connection failed');
    }
  }, []);

  // Periodic poll
  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 4000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  // WebSocket connection & live streaming
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    function connect() {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        setTimeout(connect, 3000);
      };
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'log_message' && msg.data) {
            setLogs((prev) => [...prev.slice(-250), msg.data]);
          } else if (msg.type === 'token_discovered') {
            fetchAll();
          } else if (msg.type === 'trade_update' || msg.type === 'stats_update' || msg.type === 'position_update') {
            fetchAll();
          }
        } catch {}
      };
    }

    connect();
    return () => wsRef.current?.close();
  }, [fetchAll]);

  // Auto-scroll logs
  useEffect(() => {
    if (autoScroll && logTerminalRef.current) {
      logTerminalRef.current.scrollTop = logTerminalRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const handleKillSwitch = async () => {
    if (!killSwitch) return;
    try {
      if (killSwitch.active) {
        await api.deactivateKillSwitch();
      } else {
        const reason = prompt('Reason for activating kill switch:');
        if (reason) await api.activateKillSwitch(reason);
      }
      fetchAll();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const uptime = health
    ? `${Math.floor(health.uptime_seconds / 3600)}h ${Math.floor((health.uptime_seconds % 3600) / 60)}m`
    : '—';

  const preferredStrategy = adaptive?.selector?.preferred_strategy || 'none';
  const currentRegime = adaptive?.regime?.regime || 'NORMAL';
  const capitalStage = adaptive?.capital?.stage || 'STAGE_1';

  return (
    <div className="app">
      {/* ── Header ──────────────────────────────────── */}
      <header className="header">
        <div className="header-left">
          <span className="header-logo">⚡ SOLANA MEMECOIN BOT</span>
          <span className={`header-mode ${health?.mode === 'PAPER' ? 'mode-paper' : 'mode-live'}`}>
            {health?.mode || 'OFFLINE'}
          </span>
          <span className="adaptive-badge strategy">
            🎯 {formatStrategyName(preferredStrategy)}
          </span>
          <span className="adaptive-badge regime">
            🌐 REGIME: {currentRegime.toUpperCase()}
          </span>
          <span className="adaptive-badge stage">
            💰 {capitalStage.toUpperCase()}
          </span>
          <span style={{ display: 'flex', alignItems: 'center', fontSize: 12, color: 'var(--text-muted)' }}>
            <span className={`connection-dot ${connected ? 'connected' : 'disconnected'}`} />
            {connected ? 'Live Feed' : 'Disconnected'}
          </span>
        </div>
        <div className="header-right">
          <div className="header-stat">
            <span className="header-stat-label">Uptime</span>
            <span className="header-stat-value">{uptime}</span>
          </div>
          <div className="header-stat">
            <span className="header-stat-label">Positions</span>
            <span className="header-stat-value text-cyan">{health?.active_positions ?? 0}</span>
          </div>
          <button
            id="kill-switch-btn"
            className={`kill-switch-btn ${killSwitch?.active ? 'active' : ''}`}
            onClick={handleKillSwitch}
          >
            {killSwitch?.active ? '🛑 KILL SWITCH ON' : '⚠️ KILL SWITCH'}
          </button>
        </div>
      </header>

      {/* ── Main Content ────────────────────────────── */}
      <main className="main">
        {/* Metrics Row */}
        <div className="metric-grid">
          <div className="metric-card">
            <div className="metric-label">Total Trades</div>
            <div className="metric-value text-indigo">{stats?.total_trades ?? 0}</div>
            <div className="metric-sub">
              W: {stats?.winning_trades ?? 0} / L: {stats?.losing_trades ?? 0}
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Win Rate & Expectancy</div>
            <div className={`metric-value ${(stats?.win_rate ?? 0) >= 50 ? 'text-green' : 'text-red'}`}>
              {(stats?.win_rate ?? 0).toFixed(1)}%
            </div>
            <div className="metric-sub text-cyan">
              Net Exp: {(stats?.expectancy ?? 0).toFixed(4)} SOL
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Net Paper PnL</div>
            <div className={`metric-value ${pnlColor(stats?.total_pnl_sol ?? 0)}`}>
              {formatSOL(stats?.total_pnl_sol ?? 0)} SOL
            </div>
            <div className="metric-sub">
              Profit Factor: {(stats?.profit_factor ?? 0).toFixed(2)}
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Discovery Pipeline</div>
            <div className="metric-value text-cyan">{stats?.tokens_detected ?? 0}</div>
            <div className="metric-sub">
              Filtered: {stats?.tokens_rejected ?? 0}
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Avg Winner / Loser</div>
            <div className="metric-value text-green" style={{ fontSize: 18 }}>
              {formatSOL(stats?.average_winner_sol ?? 0)}
            </div>
            <div className="metric-sub text-red">
              {formatSOL(-(stats?.average_loser_sol ?? 0))} SOL
            </div>
          </div>
        </div>

        {/* ── Adaptive Multi-Strategy Engine Section ── */}
        <div className="card card-full">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="card-title">🧠 Adaptive Multi-Strategy Engine</div>
            <div style={{ display: 'flex', gap: 10, fontSize: 12, color: 'var(--text-muted)' }}>
              <span>Exploration: <strong className="text-cyan">{adaptive?.selector?.exploration_rate || '10.0%'}</strong></span>
              <span>Decisions: <strong className="text-indigo">{adaptive?.selector?.total_selections || 0}</strong></span>
              <span>Daily Loss: <strong className="text-green">{adaptive?.loss_protection?.daily_pnl || '0.0%'}</strong></span>
            </div>
          </div>
          <div className="strategy-card-grid">
            <div className={`strategy-mini-card ${preferredStrategy === 'fast_scalper' ? 'active' : ''}`}>
              <div className="strategy-mini-title">
                <span>🚀 Fast Scalper</span>
                <span className="badge badge-green">+10% Target</span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                Single-target quick exit with 10s-5m holding window.
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                Status: {preferredStrategy === 'fast_scalper' ? '⭐ Active Selection' : 'Evaluating (Paper)'}
              </div>
            </div>

            <div className={`strategy-mini-card ${preferredStrategy === 'momentum_runner' ? 'active' : ''}`}>
              <div className="strategy-mini-title">
                <span>📈 Momentum Runner</span>
                <span className="badge badge-cyan">3-Tier Exit</span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                Partial profit-taking (+10%/+25%/+50%) + 15% trailing stop runner.
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                Status: {preferredStrategy === 'momentum_runner' ? '⭐ Active Selection' : 'Evaluating (Paper)'}
              </div>
            </div>

            <div className={`strategy-mini-card ${preferredStrategy === 'smart_wallet_follower' ? 'active' : ''}`}>
              <div className="strategy-mini-title">
                <span>🕵️ Smart Wallet Follower</span>
                <span className="badge badge-purple">Cluster Signal</span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                Tracks top historical profit wallets and enters alongside whale clusters.
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                Status: {preferredStrategy === 'smart_wallet_follower' ? '⭐ Active Selection' : 'Evaluating (Paper)'}
              </div>
            </div>
          </div>
        </div>

        {/* ── Live Bot Logs Terminal ────────────────── */}
        <div className="card card-full">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div className="card-title">🖥️ Real-Time Bot Execution Logs ({logs.length})</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                style={{
                  background: autoScroll ? 'rgba(34, 211, 153, 0.2)' : 'rgba(107, 114, 128, 0.2)',
                  color: autoScroll ? 'var(--accent-green)' : 'var(--text-muted)',
                  border: '1px solid var(--border-primary)',
                  borderRadius: 4,
                  padding: '3px 8px',
                  fontSize: 11,
                  cursor: 'pointer',
                }}
                onClick={() => setAutoScroll(!autoScroll)}
              >
                {autoScroll ? '✓ Auto-Scroll On' : 'Auto-Scroll Off'}
              </button>
              <button
                style={{
                  background: 'rgba(239, 68, 68, 0.1)',
                  color: 'var(--accent-red)',
                  border: '1px solid rgba(239, 68, 68, 0.3)',
                  borderRadius: 4,
                  padding: '3px 8px',
                  fontSize: 11,
                  cursor: 'pointer',
                }}
                onClick={() => setLogs([])}
              >
                Clear
              </button>
            </div>
          </div>
          <div className="log-terminal" ref={logTerminalRef}>
            {logs.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontStyle: 'italic', padding: 12 }}>
                Connecting to live bot log stream...
              </div>
            ) : (
              logs.map((log, i) => (
                <div key={i} className="log-line">
                  <span className="log-time">{log.time}</span>
                  <span className={`log-level ${log.level}`}>{log.level}</span>
                  <span className="log-module">[{log.module}]</span>
                  <span className="log-msg">{log.message}</span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* ── Active Positions ──────────────────────── */}
        <div className="card card-wide">
          <div className="card-title">🔴 Active Paper Positions ({positions.length})</div>
          {positions.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">📭</div>
              <div className="empty-state-text">No active positions currently open</div>
            </div>
          ) : (
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Token</th>
                    <th>Entry Price</th>
                    <th>Current</th>
                    <th>PnL</th>
                    <th>Checks</th>
                    <th>Age</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p) => (
                    <tr key={p.trade_id}>
                      <td style={{ fontWeight: 600 }}>${p.symbol}</td>
                      <td>{formatPrice(p.entry_price)}</td>
                      <td>{formatPrice(p.current_price)}</td>
                      <td className={pnlColor(p.unrealized_pnl_percent)}>
                        {formatPercent(p.unrealized_pnl_percent)}
                      </td>
                      <td>{p.checks_count}</td>
                      <td>{timeAgo(p.entry_time)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ── Recent Candidates & Security Filter ───── */}
        <div className="card card-wide">
          <div className="card-title">🔍 Recent Token Candidates ({candidates.length})</div>
          {candidates.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">🔎</div>
              <div className="empty-state-text">Scanning Solana DEXes for new pools...</div>
            </div>
          ) : (
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Token</th>
                    <th>Score</th>
                    <th>Status</th>
                    <th>Reason / Detail</th>
                    <th>Liquidity</th>
                    <th>DEX</th>
                    <th>Discovered</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.slice(-15).reverse().map((c) => (
                    <tr key={c.mint_address}>
                      <td style={{ fontWeight: 600 }}>${c.symbol || c.name || c.mint_address.slice(0, 8)}</td>
                      <td>
                        <span className={scoreColor(c.total_score)}>
                          {c.total_score.toFixed(0)}
                        </span>
                        <div className="score-bar-container">
                          <div
                            className={`score-bar ${scoreBarClass(c.total_score)}`}
                            style={{ width: `${c.total_score}%` }}
                          />
                        </div>
                      </td>
                      <td>
                        <span className={`badge ${
                          c.status === 'candidate' ? 'badge-green' :
                          c.status === 'rejected' ? 'badge-red' :
                          c.status === 'watching' ? 'badge-amber' : 'badge-indigo'
                        }`}>
                          {c.status}
                        </span>
                      </td>
                      <td style={{ fontSize: 12, color: c.status === 'rejected' ? 'var(--accent-red)' : 'var(--text-secondary)', maxWidth: 220, whiteSpace: 'normal', wordBreak: 'break-word' }}>
                        {c.rejection_detail || c.rejection_reason || (c.status === 'candidate' ? 'Passed all checks ✅' : '—')}
                      </td>
                      <td>{formatUSD(c.liquidity_usd)}</td>
                      <td><span className="badge badge-cyan">{c.dex}</span></td>
                      <td>{timeAgo(c.discovered_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ── Trade History ─────────────────────────── */}
        <div className="card card-full">
          <div className="card-title">📊 Paper Trade History ({trades.length})</div>
          {trades.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">📋</div>
              <div className="empty-state-text">No closed trades yet</div>
            </div>
          ) : (
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Token</th>
                    <th>Strategy</th>
                    <th>Type</th>
                    <th>Entry</th>
                    <th>Exit</th>
                    <th>Net PnL</th>
                    <th>PnL %</th>
                    <th>Exit Decision / Reason</th>
                    <th>Score</th>
                    <th>Duration</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <tr key={t.id}>
                      <td style={{ fontWeight: 600 }}>${t.symbol}</td>
                      <td>
                        <span className={`badge ${
                          t.strategy_name?.includes('Momentum') ? 'badge-amber' :
                          t.strategy_name?.includes('Wallet') ? 'badge-indigo' : 'badge-green'
                        }`}>
                          {t.strategy_name || 'Fast Scalper'}
                        </span>
                      </td>
                      <td>
                        <span className={`badge ${t.trade_type === 'paper' ? 'badge-indigo' : 'badge-red'}`}>
                          {t.trade_type}
                        </span>
                      </td>
                      <td>{t.entry_amount_sol.toFixed(4)} SOL</td>
                      <td>{t.exit_amount_sol.toFixed(4)} SOL</td>
                      <td className={pnlColor(t.net_pnl_sol)}>{formatSOL(t.net_pnl_sol)} SOL</td>
                      <td className={pnlColor(t.net_pnl_percent)}>{formatPercent(t.net_pnl_percent)}</td>
                      <td>
                        <span style={{ fontSize: 12, fontWeight: 500, color: t.exit_reason?.includes('target') ? 'var(--accent-green)' : t.exit_reason?.includes('emergency') ? 'var(--accent-red)' : 'var(--accent-amber)' }}>
                          {t.exit_decision || t.exit_reason || '—'}
                        </span>
                      </td>
                      <td className={scoreColor(t.score)}>{t.score.toFixed(0)}</td>
                      <td>{t.time_to_exit_seconds.toFixed(0)}s</td>
                      <td>{timeAgo(t.entry_time)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ── Error Display ─────────────────────────── */}
        {error && (
          <div className="card card-full" style={{ borderColor: 'var(--accent-red)' }}>
            <div className="card-title" style={{ color: 'var(--accent-red)' }}>⚠️ Connection Notice</div>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
              {error}
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
