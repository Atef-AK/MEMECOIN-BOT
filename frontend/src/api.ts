/**
 * API client for communicating with the bot backend.
 */

const BASE_URL = '/api';

export interface HealthData {
  status: string;
  mode: string;
  uptime_seconds: number;
  kill_switch_active: boolean;
  rpc_connected: boolean;
  telegram_connected: boolean;
  jupiter_connected: boolean;
  discovery_running: boolean;
  active_positions: number;
  total_trades: number;
  timestamp: string;
}

export interface StatsData {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl_sol: number;
  expectancy: number;
  profit_factor: number;
  max_drawdown_sol: number;
  max_consecutive_losses: number;
  average_winner_sol: number;
  average_loser_sol: number;
  tokens_detected: number;
  tokens_rejected: number;
}

export interface TradeData {
  id: string;
  trade_type: string;
  mint_address: string;
  symbol: string;
  entry_time: string | null;
  entry_price_sol: number;
  entry_amount_sol: number;
  exit_time: string | null;
  exit_price_sol: number;
  exit_amount_sol: number;
  net_pnl_sol: number;
  net_pnl_percent: number;
  exit_reason: string | null;
  score: number;
  liquidity_usd: number;
  dex: string;
  time_to_exit_seconds: number;
  strategy_name?: string;
  exit_decision?: string;
}

export interface PositionData {
  trade_id: string;
  symbol: string;
  mint_address: string;
  entry_price: number;
  current_price: number;
  unrealized_pnl_percent: number;
  checks_count: number;
  entry_time: string | null;
}

export interface CandidateData {
  mint_address: string;
  symbol: string;
  name: string;
  status: string;
  total_score: number;
  security_score: number;
  liquidity_score: number;
  holder_score: number;
  dev_score: number;
  social_score: number;
  market_score: number;
  rejection_reason: string | null;
  rejection_detail?: string | null;
  liquidity_usd: number;
  dex: string;
  discovered_at: string;
}

export interface KillSwitchData {
  active: boolean;
  activated_at: string | null;
  reason: string;
}

export interface LogEntry {
  time: string;
  level: string;
  module: string;
  message: string;
}

export interface AdaptiveIntelligenceData {
  capital: {
    balance_sol: number;
    stage: string;
    strategy_health: Record<string, any>;
  };
  regime: {
    regime: string;
    confidence: number;
    since: string;
    observations: number;
    calibrated: boolean;
  };
  selector: {
    preferred_strategy: string;
    total_selections: number;
    exploration_count: number;
    exploration_rate: string;
    strategies: string[];
  };
  performance: {
    total_trades: number;
    strategies: Record<string, any>;
  };
  loss_protection: {
    global_halted: boolean;
    halt_reason: string;
    daily_pnl: string;
    daily_trades: number;
    consecutive_losses: number;
    hourly_trades: number;
    disabled_strategies: Record<string, string>;
  };
  smart_wallets: {
    total_tracked: number;
    qualified: number;
    top_wallets: any[];
  };
}

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${url}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function postJSON<T>(url: string, body: object): Promise<T> {
  const res = await fetch(`${BASE_URL}${url}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export const api = {
  getHealth: () => fetchJSON<HealthData>('/health'),
  getStats: () => fetchJSON<StatsData>('/stats'),
  getTrades: (limit = 50) =>
    fetchJSON<{ trades: TradeData[]; total: number }>(`/trades?limit=${limit}`),
  getPositions: () =>
    fetchJSON<{ positions: PositionData[] }>('/positions'),
  getCandidates: (limit = 100) =>
    fetchJSON<{ candidates: CandidateData[]; total: number }>(`/candidates?limit=${limit}`),
  getKillSwitch: () => fetchJSON<KillSwitchData>('/kill-switch'),
  getLogs: () => fetchJSON<{ logs: LogEntry[] }>('/logs'),
  getAdaptiveIntelligence: () => fetchJSON<AdaptiveIntelligenceData>('/adaptive/intelligence'),
  activateKillSwitch: (reason: string) =>
    postJSON<{ status: string }>('/kill-switch', { action: 'activate', reason }),
  deactivateKillSwitch: () =>
    postJSON<{ status: string }>('/kill-switch', {
      action: 'deactivate',
      confirmation: 'CONFIRM_RESET_KILL_SWITCH',
    }),
};

