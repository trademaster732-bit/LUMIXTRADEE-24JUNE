import { useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { api, apiGet, apiPost, apiDelete, errMessage } from "@/api/client";
import { useIsAdmin } from "@/hooks/useIsAdmin";
import { AppShell } from "@/components/AppShell";
import { Panel, Stat } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { Activity, Filter, Gauge, RefreshCw, RotateCcw, Save, ShieldAlert, Sliders, Target, Layers, Compass, Crosshair, TrendingUp, Shield } from "lucide-react";

type EngineConfig = {
  score_weights: Record<string, number>;
  min_score: number;
  near_miss_lower: number;
  adx_threshold: number;
  vwap_max_distance_atr: number;
  cooldown_consecutive_losses: number;
  cooldown_min: number;
  session_windows: Record<string, { start: number; end: number }>;
  metals_blocked_sessions: string[];
  daily_bias_enabled: boolean;
  daily_bias_neutral_mode: string;
  daily_bias_neutral_penalty: number;
  atr_ratio_min: number;
  atr_ratio_max: number;
  symbol_overrides: Record<string, Record<string, number>>;
  // Phase-2 / Modules 1-4
  entry_quality?: any;
  market_regime?: any;
  mtf_alignment?: any;
  adaptive_tp?: any;
  adaptive_sl?: any;
  updated_at?: number;
};

const SCORE_KEYS = ["h4_trend", "h1_trend", "adx", "vwap", "sr", "atr_ratio", "spread", "entry_confirmation"];
const SYMBOLS_KNOWN = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDCAD", "USDJPY", "AUDUSD", "NZDUSD", "USDCHF"];
const REGIME_NAMES = ["strong_trend", "weak_trend", "range", "breakout", "high_volatility", "low_volatility"];
const TF_NAMES = ["D1", "H4", "H1", "M15"];
const TP_STRATEGIES = ["static_rr", "atr", "swing", "sr", "structure"];
const CONFIRMATION_PATTERNS = ["engulfing", "pin", "momentum", "break", "any"];
const AGGRESSIVENESS = ["high", "medium", "low"];

export default function EngineConfigPage() {
  const isAdmin = useIsAdmin();
  const [tab, setTab] = useState("scoring");
  if (isAdmin === null) return null;
  if (!isAdmin) return <Navigate to="/app" replace />;

  return (
    <AppShell>
      <div className="container py-6 space-y-4" data-testid="engine-config-page">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="font-display text-2xl font-bold">Engine Configuration</h1>
            <p className="text-sm text-muted-foreground">Phase-1 quality + cooldown + session + bias + ATR filters. Changes apply within 60s (cache TTL).</p>
          </div>
        </div>
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="font-mono text-[11px] tracking-widest overflow-x-auto">
            <TabsTrigger value="scoring" data-testid="ec-tab-scoring"><Target className="w-3 h-3 mr-1" /> SCORE</TabsTrigger>
            <TabsTrigger value="cooldown" data-testid="ec-tab-cooldown"><ShieldAlert className="w-3 h-3 mr-1" /> COOLDOWN</TabsTrigger>
            <TabsTrigger value="filters" data-testid="ec-tab-filters"><Filter className="w-3 h-3 mr-1" /> FILTERS</TabsTrigger>
            <TabsTrigger value="symbols" data-testid="ec-tab-symbols"><Sliders className="w-3 h-3 mr-1" /> PER-SYMBOL</TabsTrigger>
            <TabsTrigger value="entry" data-testid="ec-tab-entry"><Crosshair className="w-3 h-3 mr-1" /> ENTRY-Q</TabsTrigger>
            <TabsTrigger value="regime" data-testid="ec-tab-regime"><Compass className="w-3 h-3 mr-1" /> REGIME</TabsTrigger>
            <TabsTrigger value="mtf" data-testid="ec-tab-mtf"><Layers className="w-3 h-3 mr-1" /> MTF</TabsTrigger>
            <TabsTrigger value="atp" data-testid="ec-tab-atp"><TrendingUp className="w-3 h-3 mr-1" /> ADAPTIVE-TP</TabsTrigger>
            <TabsTrigger value="asl" data-testid="ec-tab-asl"><Shield className="w-3 h-3 mr-1" /> ADAPTIVE-SL</TabsTrigger>
            <TabsTrigger value="telemetry" data-testid="ec-tab-telemetry"><Activity className="w-3 h-3 mr-1" /> TELEMETRY</TabsTrigger>
          </TabsList>
          <TabsContent value="scoring" className="mt-4"><ScoringTab /></TabsContent>
          <TabsContent value="cooldown" className="mt-4"><CooldownTab /></TabsContent>
          <TabsContent value="filters" className="mt-4"><FiltersTab /></TabsContent>
          <TabsContent value="symbols" className="mt-4"><SymbolsTab /></TabsContent>
          <TabsContent value="entry" className="mt-4"><EntryQualityTab /></TabsContent>
          <TabsContent value="regime" className="mt-4"><MarketRegimeTab /></TabsContent>
          <TabsContent value="mtf" className="mt-4"><MTFAlignmentTab /></TabsContent>
          <TabsContent value="atp" className="mt-4"><AdaptiveTPTab /></TabsContent>
          <TabsContent value="asl" className="mt-4"><AdaptiveSLTab /></TabsContent>
          <TabsContent value="telemetry" className="mt-4"><TelemetryTab /></TabsContent>
        </Tabs>
      </div>
    </AppShell>
  );
}

function useEngineConfig() {
  const [cfg, setCfg] = useState<EngineConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const load = async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ config: EngineConfig }>("/admin/engine-config");
      setCfg(r.config);
    } catch (e) { toast.error(errMessage(e)); }
    setLoading(false);
  };
  useEffect(() => { load(); }, []);
  const save = async (patch: Partial<EngineConfig>) => {
    try {
      const r = await api.put<{ config: EngineConfig }>("/admin/engine-config", patch);
      setCfg(r.data.config);
      toast.success("Saved");
    } catch (e) { toast.error(errMessage(e)); }
  };
  const reset = async () => {
    if (!confirm("Reset ALL engine settings to defaults?")) return;
    try {
      const r = await apiPost<{ config: EngineConfig }>("/admin/engine-config/reset-defaults");
      setCfg(r.config);
      toast.success("Defaults restored");
    } catch (e) { toast.error(errMessage(e)); }
  };
  return { cfg, setCfg, loading, save, reset, reload: load };
}

/* ─────────── 1. SCORING TAB ─────────── */
function ScoringTab() {
  const { cfg, setCfg, loading, save, reset } = useEngineConfig();
  if (loading || !cfg) return <Panel><div className="py-8 text-center font-mono text-xs text-muted-foreground">— Loading config… —</div></Panel>;
  const total = SCORE_KEYS.reduce((s, k) => s + (cfg.score_weights[k] ?? 0), 0);
  const setW = (k: string, v: number) => setCfg({ ...cfg, score_weights: { ...cfg.score_weights, [k]: v } });
  return (
    <div className="space-y-4">
      <div className="grid md:grid-cols-4 gap-4">
        <Panel><Stat label="Min Score Threshold" value={cfg.min_score} delta={{ value: `Near-miss ≥ ${cfg.near_miss_lower}`, positive: true }} /></Panel>
        <Panel><Stat label="Weight Sum" value={total} delta={{ value: total === 100 ? "balanced" : "≠ 100", positive: total === 100 }} /></Panel>
        <Panel><Stat label="ADX Threshold" value={cfg.adx_threshold} /></Panel>
        <Panel><Stat label="VWAP Max (ATR)" value={cfg.vwap_max_distance_atr} /></Panel>
      </div>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// SCORE WEIGHTS (must sum to 100)</div>
        <div className="grid md:grid-cols-2 gap-4">
          {SCORE_KEYS.map((k) => (
            <div key={k} className="flex items-center gap-3">
              <Label className="w-28 font-mono text-xs uppercase">{k}</Label>
              <Input type="number" value={cfg.score_weights[k] ?? 0} onChange={(e) => setW(k, Number(e.target.value))}
                     className="w-24" data-testid={`score-weight-${k}`} />
            </div>
          ))}
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// THRESHOLDS</div>
        <div className="grid md:grid-cols-2 gap-4">
          <NumField label="Min Score" value={cfg.min_score} onChange={(v) => setCfg({ ...cfg, min_score: v })} testid="min-score" />
          <NumField label="Near-miss lower bound" value={cfg.near_miss_lower} onChange={(v) => setCfg({ ...cfg, near_miss_lower: v })} testid="near-miss-lower" />
          <NumField label="ADX Threshold" value={cfg.adx_threshold} step={0.5} onChange={(v) => setCfg({ ...cfg, adx_threshold: v })} testid="adx-threshold" />
          <NumField label="VWAP max distance (× ATR)" value={cfg.vwap_max_distance_atr} step={0.1} onChange={(v) => setCfg({ ...cfg, vwap_max_distance_atr: v })} testid="vwap-max-distance" />
        </div>
      </Panel>
      <div className="flex gap-3">
        <Button onClick={() => save(cfg)} data-testid="save-scoring"><Save className="w-4 h-4 mr-2" /> Save Scoring</Button>
        <Button variant="outline" onClick={reset} data-testid="reset-defaults"><RotateCcw className="w-4 h-4 mr-2" /> Reset All Defaults</Button>
      </div>
    </div>
  );
}

/* ─────────── 2. COOLDOWN TAB ─────────── */
function CooldownTab() {
  const { cfg, setCfg, loading, save } = useEngineConfig();
  const [active, setActive] = useState<any[]>([]);
  const loadActive = async () => {
    try {
      const r = await apiGet<{ active: any[] }>("/admin/cooldowns");
      setActive(r.active || []);
    } catch (e) { /* silent */ }
  };
  useEffect(() => { loadActive(); }, []);
  const clearOne = async (pair: string, userId: string) => {
    try { await apiDelete(`/admin/cooldowns/${pair}/${userId}`); toast.success("Cleared"); loadActive(); }
    catch (e) { toast.error(errMessage(e)); }
  };
  if (loading || !cfg) return <Panel><div className="py-8 text-center font-mono text-xs text-muted-foreground">— Loading… —</div></Panel>;
  return (
    <div className="space-y-4">
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// COOLDOWN RULE</div>
        <div className="grid md:grid-cols-2 gap-4">
          <NumField label="Consecutive losses to trigger" value={cfg.cooldown_consecutive_losses} onChange={(v) => setCfg({ ...cfg, cooldown_consecutive_losses: v })} testid="cd-losses" />
          <NumField label="Default cooldown duration (min)" value={cfg.cooldown_min} onChange={(v) => setCfg({ ...cfg, cooldown_min: v })} testid="cd-min" />
        </div>
        <div className="mt-3 text-xs text-muted-foreground">Per-symbol overrides for these values live under <strong>Per-Symbol</strong>.</div>
      </Panel>
      <Panel>
        <div className="flex items-center justify-between mb-3">
          <div className="font-mono text-[11px] tracking-widest text-primary">// ACTIVE COOLDOWNS</div>
          <Button variant="outline" size="sm" onClick={loadActive} data-testid="cd-refresh"><RefreshCw className="w-3 h-3 mr-1" /> Refresh</Button>
        </div>
        {active.length === 0 ? (
          <div className="py-6 text-center font-mono text-xs text-muted-foreground">No active cooldowns.</div>
        ) : (
          <table className="w-full text-sm" data-testid="cd-table">
            <thead><tr className="text-left text-xs text-muted-foreground"><th>Pair</th><th>User</th><th>Triggered</th><th>Expires</th><th>Reason</th><th></th></tr></thead>
            <tbody>
              {active.map((c, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="font-mono">{c.pair}</td>
                  <td className="font-mono text-xs">{(c.user_id || "").slice(0, 8)}…</td>
                  <td className="font-mono text-xs">{new Date(c.triggered_at).toLocaleString()}</td>
                  <td className="font-mono text-xs">{new Date(c.expires_at).toLocaleString()}</td>
                  <td className="text-xs">{c.reason}</td>
                  <td><Button size="sm" variant="ghost" onClick={() => clearOne(c.pair, c.user_id)} data-testid={`cd-clear-${c.pair}`}>Clear</Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      <Button onClick={() => save(cfg)} data-testid="save-cooldown"><Save className="w-4 h-4 mr-2" /> Save Cooldown</Button>
    </div>
  );
}

/* ─────────── 3. FILTERS TAB (session + daily bias + ATR ratio) ─────────── */
function FiltersTab() {
  const { cfg, setCfg, loading, save } = useEngineConfig();
  if (loading || !cfg) return <Panel><div className="py-8 text-center font-mono text-xs text-muted-foreground">— Loading… —</div></Panel>;
  const setSessionWin = (s: string, key: "start" | "end", v: number) =>
    setCfg({ ...cfg, session_windows: { ...cfg.session_windows, [s]: { ...cfg.session_windows[s], [key]: v } } });
  const toggleMetalsSession = (s: string) => {
    const next = cfg.metals_blocked_sessions.includes(s)
      ? cfg.metals_blocked_sessions.filter(x => x !== s)
      : [...cfg.metals_blocked_sessions, s];
    setCfg({ ...cfg, metals_blocked_sessions: next });
  };
  return (
    <div className="space-y-4">
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// SESSION WINDOWS (UTC hours)</div>
        {Object.entries(cfg.session_windows).map(([s, w]) => (
          <div key={s} className="flex items-center gap-3 mb-2">
            <Label className="w-20 font-mono text-xs uppercase">{s}</Label>
            <span className="text-xs">Start</span>
            <Input type="number" min={0} max={23} value={w.start} onChange={(e) => setSessionWin(s, "start", Number(e.target.value))} className="w-20" data-testid={`sess-${s}-start`} />
            <span className="text-xs">End</span>
            <Input type="number" min={1} max={24} value={w.end} onChange={(e) => setSessionWin(s, "end", Number(e.target.value))} className="w-20" data-testid={`sess-${s}-end`} />
            <label className="ml-4 flex items-center gap-2 text-xs">
              <Switch checked={cfg.metals_blocked_sessions.includes(s)} onCheckedChange={() => toggleMetalsSession(s)} data-testid={`sess-${s}-metals-block`} />
              Block metals
            </label>
          </div>
        ))}
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// DAILY BIAS (D1 EMA21/EMA55)</div>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={cfg.daily_bias_enabled} onCheckedChange={(v) => setCfg({ ...cfg, daily_bias_enabled: v })} data-testid="bias-enabled" />
          <Label className="text-sm">Enable Daily Bias Filter</Label>
        </div>
        <div className="grid md:grid-cols-2 gap-4">
          <div>
            <Label className="text-xs">Neutral mode</Label>
            <select className="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" value={cfg.daily_bias_neutral_mode}
                    onChange={(e) => setCfg({ ...cfg, daily_bias_neutral_mode: e.target.value })} data-testid="bias-neutral-mode">
              <option value="score_penalty">Score Penalty (Option B — default)</option>
              <option value="block">Block trades (Option A)</option>
              <option value="carry_forward">Carry forward (Option C)</option>
            </select>
          </div>
          <NumField label="Neutral score penalty" value={cfg.daily_bias_neutral_penalty} onChange={(v) => setCfg({ ...cfg, daily_bias_neutral_penalty: v })} testid="bias-penalty" />
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// ATR RATIO BAND</div>
        <div className="grid md:grid-cols-2 gap-4">
          <NumField label="ATR Ratio min (dead market below)" value={cfg.atr_ratio_min} step={0.05} onChange={(v) => setCfg({ ...cfg, atr_ratio_min: v })} testid="atr-min" />
          <NumField label="ATR Ratio max (explosive above)" value={cfg.atr_ratio_max} step={0.05} onChange={(v) => setCfg({ ...cfg, atr_ratio_max: v })} testid="atr-max" />
        </div>
      </Panel>
      <Button onClick={() => save(cfg)} data-testid="save-filters"><Save className="w-4 h-4 mr-2" /> Save Filters</Button>
    </div>
  );
}

/* ─────────── 4. PER-SYMBOL OVERRIDES ─────────── */
function SymbolsTab() {
  const { cfg, setCfg, loading, save } = useEngineConfig();
  if (loading || !cfg) return <Panel><div className="py-8 text-center font-mono text-xs text-muted-foreground">— Loading… —</div></Panel>;
  const setSym = (sym: string, key: string, v: number | "") => {
    const next = { ...(cfg.symbol_overrides[sym] || {}) };
    if (v === "" || v === null) delete next[key]; else next[key] = Number(v);
    setCfg({ ...cfg, symbol_overrides: { ...cfg.symbol_overrides, [sym]: next } });
  };
  return (
    <div className="space-y-4">
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// PER-SYMBOL OVERRIDES (leave blank to use global)</div>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted-foreground"><th>Symbol</th><th>Min Score</th><th>Cooldown (min)</th></tr></thead>
          <tbody>
            {SYMBOLS_KNOWN.map((sym) => {
              const o = cfg.symbol_overrides[sym] || {};
              return (
                <tr key={sym} className="border-t border-border">
                  <td className="font-mono py-2">{sym}</td>
                  <td><Input type="number" value={o.min_score ?? ""} placeholder={String(cfg.min_score)}
                             onChange={(e) => setSym(sym, "min_score", e.target.value === "" ? "" : Number(e.target.value))}
                             className="w-24" data-testid={`sym-${sym}-min-score`} /></td>
                  <td><Input type="number" value={o.cooldown_min ?? ""} placeholder={String(cfg.cooldown_min)}
                             onChange={(e) => setSym(sym, "cooldown_min", e.target.value === "" ? "" : Number(e.target.value))}
                             className="w-24" data-testid={`sym-${sym}-cooldown`} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>
      <Button onClick={() => save(cfg)} data-testid="save-symbols"><Save className="w-4 h-4 mr-2" /> Save Symbol Overrides</Button>
    </div>
  );
}

/* ─────────── 5. TELEMETRY (filter stats + symbol metrics) ─────────── */
function TelemetryTab() {
  const [filterStats, setFilterStats] = useState<any>(null);
  const [metrics, setMetrics] = useState<any>(null);
  const [days, setDays] = useState(7);
  const load = async () => {
    try {
      const [f, m] = await Promise.all([
        apiGet<any>(`/admin/filter-stats?days=${days}`),
        apiGet<any>(`/admin/symbol-metrics?days=${days}`),
      ]);
      setFilterStats(f); setMetrics(m);
    } catch (e) { toast.error(errMessage(e)); }
  };
  useEffect(() => { load(); }, [days]);
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Label className="text-xs">Window (days):</Label>
        <Input type="number" min={1} max={365} value={days} onChange={(e) => setDays(Math.max(1, Number(e.target.value || 7)))} className="w-24" data-testid="telemetry-days" />
        <Button size="sm" variant="outline" onClick={load} data-testid="telemetry-refresh"><RefreshCw className="w-3 h-3 mr-1" /> Refresh</Button>
      </div>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// REJECTIONS BY FILTER (last {days}d)</div>
        {filterStats && Object.keys(filterStats.by_filter || {}).length > 0 ? (
          <div className="grid md:grid-cols-3 gap-4">
            {Object.entries(filterStats.by_filter as Record<string, number>).map(([k, v]) => (
              <div key={k} className="flex justify-between border-b border-border py-2">
                <span className="font-mono text-xs uppercase">{k}</span>
                <span className="font-mono">{v}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="py-6 text-center font-mono text-xs text-muted-foreground">No rejections in window.</div>
        )}
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// PER-SYMBOL PERFORMANCE (last {days}d)</div>
        {metrics && Object.keys(metrics.metrics || {}).length > 0 ? (
          <table className="w-full text-sm" data-testid="symbol-metrics-table">
            <thead><tr className="text-left text-xs text-muted-foreground">
              <th>Pair</th><th>Trades</th><th>WR%</th><th>PF</th><th>Net P&L</th><th>Avg RR</th><th>Filtered</th>
            </tr></thead>
            <tbody>
              {Object.entries(metrics.metrics as Record<string, any>).map(([pair, m]) => (
                <tr key={pair} className="border-t border-border">
                  <td className="font-mono py-2">{pair}</td>
                  <td className="font-mono">{m.trades}</td>
                  <td className="font-mono">{m.win_rate_pct}</td>
                  <td className="font-mono">{m.profit_factor ?? "—"}</td>
                  <td className={`font-mono ${m.net_pnl >= 0 ? "text-green-500" : "text-red-500"}`}>{(m.net_pnl ?? 0).toFixed(2)}</td>
                  <td className="font-mono">{m.avg_rr ?? "—"}</td>
                  <td className="font-mono">{m.filtered_count ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="py-6 text-center font-mono text-xs text-muted-foreground">No closed trades in window.</div>
        )}
      </Panel>
    </div>
  );
}

/* ─────────── helper ─────────── */
function NumField({ label, value, onChange, step = 1, testid }: { label: string; value: number; onChange: (v: number) => void; step?: number; testid?: string }) {
  return (
    <div>
      <Label className="text-xs">{label}</Label>
      <Input type="number" step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} data-testid={testid} />
    </div>
  );
}


/* ════════════════════════════════════════════════════════════════════════════
 *  PHASE-2 + MODULES 1-3 ADMIN TABS
 *  Each tab follows the same shape:
 *    1. Master enable/disable toggle
 *    2. Module-specific config controls (toggles, sliders, selects, per-symbol)
 *    3. Save / Reset buttons
 *    4. Live monitoring (filter-stats counter + recent rejections)
 * ════════════════════════════════════════════════════════════════════════════ */

/* ─────────── shared widgets ─────────── */
function LiveRejections({ filter, label, days = 7 }: { filter: string; label: string; days?: number }) {
  const [stats, setStats] = useState<{ count: number; by_pair: Record<string, number> }>({ count: 0, by_pair: {} });
  const [examples, setExamples] = useState<any[]>([]);
  const load = async () => {
    try {
      const [fs, rj] = await Promise.all([
        apiGet<any>(`/admin/filter-stats?days=${days}`),
        apiGet<any>(`/admin/recent-rejections?filter=${filter}&limit=10`),
      ]);
      const count = (fs.by_filter || {})[filter] || 0;
      const by_pair: Record<string, number> = {};
      for (const r of (fs.details || [])) {
        if ((r._id || {}).filter === filter) {
          const p = (r._id || {}).pair || "?";
          by_pair[p] = (by_pair[p] || 0) + (r.count || 0);
        }
      }
      setStats({ count, by_pair });
      setExamples(rj.rejections || []);
    } catch (e) { /* silent */ }
  };
  useEffect(() => { load(); }, [filter, days]);
  return (
    <Panel data-testid={`live-reject-${filter}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="font-mono text-[11px] tracking-widest text-primary">// LIVE — {label} (last {days}d)</div>
        <Button variant="outline" size="sm" onClick={load} data-testid={`reload-${filter}`}><RefreshCw className="w-3 h-3 mr-1" /> Refresh</Button>
      </div>
      <div className="grid md:grid-cols-2 gap-4 mb-3">
        <Stat label={`${label} rejections`} value={stats.count} />
        <div>
          <div className="text-xs text-muted-foreground mb-1">By pair</div>
          {Object.keys(stats.by_pair).length === 0 ? (
            <div className="text-xs font-mono text-muted-foreground">— none —</div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {Object.entries(stats.by_pair).sort((a, b) => b[1] - a[1]).map(([p, n]) => (
                <span key={p} className="font-mono text-[11px] bg-muted/40 px-2 py-1 rounded">{p}: {n}</span>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="text-xs text-muted-foreground mb-1">Last 10 examples</div>
      {examples.length === 0 ? (
        <div className="py-3 text-center font-mono text-xs text-muted-foreground">— none —</div>
      ) : (
        <div className="max-h-72 overflow-auto text-[11px] font-mono space-y-1">
          {examples.map((e, i) => (
            <div key={i} className="border-b border-border/40 pb-1">
              <span className="text-muted-foreground">{(e.ts || "").replace("T", " ").slice(5, 19)}</span>{" "}
              <span className="font-semibold">{e.pair}</span>{" "}
              <span className="text-muted-foreground">{e.side}</span>{" "}
              → <span className="text-red-400">{e.reason}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function SymbolOverridesEditor({
  label, ovs, columns, onChange, testidPrefix,
}: {
  label: string;
  ovs: Record<string, Record<string, any>>;
  columns: Array<{ key: string; type: "number" | "text" | "list"; placeholder?: string }>;
  onChange: (next: Record<string, Record<string, any>>) => void;
  testidPrefix: string;
}) {
  const setVal = (sym: string, key: string, type: string, raw: string) => {
    const next = { ...(ovs[sym] || {}) };
    if (raw === "") { delete next[key]; }
    else if (type === "number") { next[key] = Number(raw); }
    else if (type === "list") { next[key] = raw.split(",").map(s => s.trim()).filter(Boolean); }
    else { next[key] = raw; }
    const out = { ...ovs, [sym]: next };
    if (Object.keys(next).length === 0) delete out[sym];
    onChange(out);
  };
  return (
    <Panel>
      <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// PER-SYMBOL OVERRIDES — {label}</div>
      <table className="w-full text-sm">
        <thead><tr className="text-left text-xs text-muted-foreground"><th className="py-1">Symbol</th>{columns.map(c => <th key={c.key}>{c.key}</th>)}</tr></thead>
        <tbody>
          {SYMBOLS_KNOWN.map((sym) => {
            const o = ovs[sym] || {};
            return (
              <tr key={sym} className="border-t border-border">
                <td className="font-mono py-1.5">{sym}</td>
                {columns.map(c => (
                  <td key={c.key}>
                    <Input
                      type={c.type === "number" ? "number" : "text"}
                      value={c.type === "list" ? (Array.isArray(o[c.key]) ? o[c.key].join(",") : "") : (o[c.key] ?? "")}
                      placeholder={c.placeholder || ""}
                      onChange={(e) => setVal(sym, c.key, c.type, e.target.value)}
                      className="w-full"
                      data-testid={`${testidPrefix}-${sym}-${c.key}`}
                    />
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

/* ─────────── 6. ENTRY QUALITY TAB (Phase-2) ─────────── */
function EntryQualityTab() {
  const { cfg, setCfg, loading, save } = useEngineConfig();
  if (loading || !cfg) return <Panel><div className="py-8 text-center font-mono text-xs text-muted-foreground">— Loading… —</div></Panel>;
  const eq = cfg.entry_quality || {};
  const setEq = (patch: any) => setCfg({ ...cfg, entry_quality: { ...eq, ...patch } });
  const setProfile = (which: "metals" | "forex", patch: any) =>
    setEq({ profiles: { ...(eq.profiles || {}), [which]: { ...((eq.profiles || {})[which] || {}), ...patch } } });
  return (
    <div className="space-y-4">
      <div className="grid md:grid-cols-4 gap-4">
        <Panel><Stat label="Engine" value={eq.enabled ? "ON" : "OFF"} delta={{ value: eq.enabled ? "active" : "bypass", positive: !!eq.enabled }} /></Panel>
        <Panel><Stat label="Min Pullback" value={`${eq.min_entry_confirmation_score ?? 10}/20`} /></Panel>
        <Panel><Stat label="Min SR distance" value={`${eq.min_sr_distance_atr ?? 0.30}× ATR`} /></Panel>
        <Panel><Stat label="Candle gate" value={eq.confirmation_candle_required ? "REQUIRED" : "OPTIONAL"} /></Panel>
      </div>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// MASTER + MODULE-1 (PULLBACK COMPLETION)</div>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={!!eq.enabled} onCheckedChange={(v) => setEq({ enabled: v })} data-testid="eq-enabled" />
          <Label className="text-sm">Enable Entry-Quality Engine</Label>
        </div>
        <div className="grid md:grid-cols-3 gap-4">
          <NumField label="Pullback floor (0-20)" value={eq.min_entry_confirmation_score ?? 10} onChange={(v) => setEq({ min_entry_confirmation_score: v })} testid="eq-pullback-floor" />
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// MODULE-2 (SR DISTANCE) + MODULE-4 (CANDLE)</div>
        <div className="grid md:grid-cols-3 gap-4">
          <NumField label="Min SR distance (×ATR)" value={eq.min_sr_distance_atr ?? 0.30} step={0.05} onChange={(v) => setEq({ min_sr_distance_atr: v })} testid="eq-sr-dist" />
          <NumField label="Min candle body %" value={eq.min_candle_body_pct ?? 0.55} step={0.05} onChange={(v) => setEq({ min_candle_body_pct: v })} testid="eq-body-pct" />
          <div className="flex items-center gap-3">
            <Switch checked={!!eq.confirmation_candle_required} onCheckedChange={(v) => setEq({ confirmation_candle_required: v })} data-testid="eq-candle-required" />
            <Label className="text-xs">Require confirmation candle</Label>
          </div>
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// MODULE-3 (TREND MATURITY)</div>
        <div className="grid md:grid-cols-3 gap-4">
          <NumField label="Fresh-trend threshold" value={eq.fresh_trend_threshold ?? 1.0} step={0.1} onChange={(v) => setEq({ fresh_trend_threshold: v })} testid="eq-fresh-thresh" />
          <NumField label="Exhaustion threshold (×ATR)" value={eq.trend_exhaustion_threshold ?? 3.5} step={0.1} onChange={(v) => setEq({ trend_exhaustion_threshold: v })} testid="eq-exhaust" />
          <NumField label="Momentum threshold" value={eq.momentum_threshold ?? 0.0} step={0.1} onChange={(v) => setEq({ momentum_threshold: v })} testid="eq-momentum-th" />
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// SYMBOL PROFILES</div>
        <div className="grid md:grid-cols-2 gap-4">
          {(["metals", "forex"] as const).map(p => {
            const prof = (eq.profiles || {})[p] || {};
            return (
              <div key={p} className="border border-border/60 rounded p-3 space-y-2">
                <div className="font-mono text-xs uppercase">{p}</div>
                <NumField label="Min candle body %" value={prof.min_candle_body_pct ?? (p === "metals" ? 0.65 : 0.50)} step={0.05} onChange={(v) => setProfile(p, { min_candle_body_pct: v })} testid={`eq-prof-${p}-body`} />
                <NumField label="Min entry-confirmation" value={prof.min_entry_confirmation_score ?? (p === "metals" ? 9 : 12)} onChange={(v) => setProfile(p, { min_entry_confirmation_score: v })} testid={`eq-prof-${p}-min-ec`} />
                {p === "metals" && <NumField label="Exhaustion threshold (×ATR)" value={prof.trend_exhaustion_threshold ?? 3.0} step={0.1} onChange={(v) => setProfile(p, { trend_exhaustion_threshold: v })} testid="eq-prof-metals-exh" />}
              </div>
            );
          })}
        </div>
      </Panel>
      <div className="flex gap-3">
        <Button onClick={() => save({ entry_quality: cfg.entry_quality } as any)} data-testid="save-entry-quality"><Save className="w-4 h-4 mr-2" /> Save Entry-Quality</Button>
      </div>
      <LiveRejections filter="entry_quality" label="ENTRY-QUALITY" />
    </div>
  );
}

/* ─────────── 7. MARKET REGIME TAB (Module 1) ─────────── */
function MarketRegimeTab() {
  const { cfg, setCfg, loading, save } = useEngineConfig();
  if (loading || !cfg) return <Panel><div className="py-8 text-center font-mono text-xs text-muted-foreground">— Loading… —</div></Panel>;
  const mr = cfg.market_regime || {};
  const regimes = mr.regimes || {};
  const setMR = (patch: any) => setCfg({ ...cfg, market_regime: { ...mr, ...patch } });
  const setRegime = (name: string, patch: any) =>
    setMR({ regimes: { ...regimes, [name]: { ...(regimes[name] || {}), ...patch } } });
  const setSymPref = (sym: string, raw: string) => {
    const next = { ...(mr.symbol_preferences || {}) };
    const list = raw.split(",").map(s => s.trim()).filter(Boolean);
    if (list.length === 0) delete next[sym]; else next[sym] = list;
    setMR({ symbol_preferences: next });
  };
  return (
    <div className="space-y-4">
      <div className="grid md:grid-cols-3 gap-4">
        <Panel><Stat label="Engine" value={mr.enabled ? "ON" : "OFF"} delta={{ value: mr.enabled ? "active" : "bypass", positive: !!mr.enabled }} /></Panel>
        <Panel><Stat label="Regimes" value={`${Object.values(regimes).filter((r: any) => r.enabled).length}/${REGIME_NAMES.length} on`} /></Panel>
        <Panel><Stat label="Symbol Preferences" value={Object.keys(mr.symbol_preferences || {}).length} /></Panel>
      </div>
      <Panel>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={!!mr.enabled} onCheckedChange={(v) => setMR({ enabled: v })} data-testid="mr-enabled" />
          <Label className="text-sm">Enable Market-Regime Engine</Label>
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// REGIME CONFIGURATION</div>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted-foreground">
            <th className="py-1">Regime</th><th>Enable</th><th>Min Score</th><th>Aggressiveness</th><th>Preferred Confirmation (comma)</th>
          </tr></thead>
          <tbody>
            {REGIME_NAMES.map(name => {
              const r = regimes[name] || {};
              return (
                <tr key={name} className="border-t border-border">
                  <td className="font-mono py-2">{name}</td>
                  <td><Switch checked={!!r.enabled} onCheckedChange={(v) => setRegime(name, { enabled: v })} data-testid={`mr-${name}-enabled`} /></td>
                  <td><Input type="number" value={r.min_score ?? ""} placeholder="—" onChange={(e) => setRegime(name, { min_score: e.target.value === "" ? undefined : Number(e.target.value) })} className="w-20" data-testid={`mr-${name}-min-score`} /></td>
                  <td>
                    <select className="bg-background border border-border rounded px-2 py-1 text-sm" value={r.entry_aggressiveness || "medium"} onChange={(e) => setRegime(name, { entry_aggressiveness: e.target.value })} data-testid={`mr-${name}-agg`}>
                      {AGGRESSIVENESS.map(a => <option key={a} value={a}>{a}</option>)}
                    </select>
                  </td>
                  <td>
                    <Input value={Array.isArray(r.preferred_confirmation) ? r.preferred_confirmation.join(",") : ""}
                           placeholder={CONFIRMATION_PATTERNS.join(",")}
                           onChange={(e) => setRegime(name, { preferred_confirmation: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })}
                           className="w-full text-xs" data-testid={`mr-${name}-prefs`} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// SYMBOL → ALLOWED REGIMES (comma-separated; blank = all enabled)</div>
        <div className="grid md:grid-cols-2 gap-2">
          {SYMBOLS_KNOWN.map(sym => (
            <div key={sym} className="flex items-center gap-2">
              <Label className="w-20 font-mono text-xs">{sym}</Label>
              <Input value={(mr.symbol_preferences || {})[sym]?.join(",") || ""}
                     placeholder="strong_trend,breakout,…"
                     onChange={(e) => setSymPref(sym, e.target.value)}
                     className="flex-1 text-xs" data-testid={`mr-pref-${sym}`} />
            </div>
          ))}
        </div>
      </Panel>
      <div className="flex gap-3">
        <Button onClick={() => save({ market_regime: cfg.market_regime } as any)} data-testid="save-mr"><Save className="w-4 h-4 mr-2" /> Save Market-Regime</Button>
      </div>
      <LiveRejections filter="market_regime" label="MARKET-REGIME" />
    </div>
  );
}

/* ─────────── 8. MTF ALIGNMENT TAB (Module 2) ─────────── */
function MTFAlignmentTab() {
  const { cfg, setCfg, loading, save } = useEngineConfig();
  if (loading || !cfg) return <Panel><div className="py-8 text-center font-mono text-xs text-muted-foreground">— Loading… —</div></Panel>;
  const mtf = cfg.mtf_alignment || {};
  const tfs = mtf.timeframes || {};
  const setMTF = (patch: any) => setCfg({ ...cfg, mtf_alignment: { ...mtf, ...patch } });
  const setTF = (name: string, patch: any) => setMTF({ timeframes: { ...tfs, [name]: { ...(tfs[name] || {}), ...patch } } });
  const weightSum = TF_NAMES.reduce((s, n) => s + ((tfs[n]?.enabled ? (tfs[n]?.weight || 0) : 0)), 0);
  return (
    <div className="space-y-4">
      <div className="grid md:grid-cols-4 gap-4">
        <Panel><Stat label="Engine" value={mtf.enabled ? "ON" : "OFF"} delta={{ value: mtf.enabled ? "active" : "bypass", positive: !!mtf.enabled }} /></Panel>
        <Panel><Stat label="Min alignment %" value={mtf.min_alignment_pct ?? 60} /></Panel>
        <Panel><Stat label="Enabled TF weight Σ" value={weightSum} /></Panel>
        <Panel><Stat label="HTF/LTF reject" value={mtf.htf_ltf_disagreement_reject ? "ON" : "OFF"} /></Panel>
      </div>
      <Panel>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={!!mtf.enabled} onCheckedChange={(v) => setMTF({ enabled: v })} data-testid="mtf-enabled" />
          <Label className="text-sm">Enable MTF-Alignment Engine</Label>
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// TIMEFRAMES</div>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted-foreground"><th className="py-1">TF</th><th>Enable</th><th>Weight</th><th>Min ADX</th><th>Min EMA-angle (bps)</th></tr></thead>
          <tbody>
            {TF_NAMES.map(name => {
              const t = tfs[name] || {};
              return (
                <tr key={name} className="border-t border-border">
                  <td className="font-mono py-2">{name}</td>
                  <td><Switch checked={!!t.enabled} onCheckedChange={(v) => setTF(name, { enabled: v })} data-testid={`mtf-${name}-enabled`} /></td>
                  <td><Input type="number" value={t.weight ?? 0} onChange={(e) => setTF(name, { weight: Number(e.target.value) })} className="w-20" data-testid={`mtf-${name}-weight`} /></td>
                  <td><Input type="number" step={0.5} value={t.min_strength_adx ?? 18} onChange={(e) => setTF(name, { min_strength_adx: Number(e.target.value) })} className="w-24" data-testid={`mtf-${name}-adx`} /></td>
                  <td><Input type="number" step={0.1} value={t.min_ema_angle_bps ?? 0.3} onChange={(e) => setTF(name, { min_ema_angle_bps: Number(e.target.value) })} className="w-24" data-testid={`mtf-${name}-angle`} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// GLOBAL GATES</div>
        <div className="grid md:grid-cols-3 gap-4">
          <NumField label="Min alignment %" value={mtf.min_alignment_pct ?? 60} onChange={(v) => setMTF({ min_alignment_pct: v })} testid="mtf-min-pct" />
          <div className="flex items-center gap-3">
            <Switch checked={!!mtf.htf_ltf_disagreement_reject} onCheckedChange={(v) => setMTF({ htf_ltf_disagreement_reject: v })} data-testid="mtf-htf-ltf" />
            <Label className="text-xs">Reject on HTF/LTF disagree</Label>
          </div>
          <div className="flex items-center gap-3">
            <Switch checked={!!mtf.require_momentum_agreement} onCheckedChange={(v) => setMTF({ require_momentum_agreement: v })} data-testid="mtf-req-momentum" />
            <Label className="text-xs">Require momentum agreement</Label>
          </div>
          <NumField label="Min momentum-agree count" value={mtf.min_momentum_agreement_count ?? 2} onChange={(v) => setMTF({ min_momentum_agreement_count: v })} testid="mtf-mom-count" />
        </div>
      </Panel>
      <div className="flex gap-3">
        <Button onClick={() => save({ mtf_alignment: cfg.mtf_alignment } as any)} data-testid="save-mtf"><Save className="w-4 h-4 mr-2" /> Save MTF-Alignment</Button>
      </div>
      <LiveRejections filter="mtf_alignment" label="MTF-ALIGNMENT" />
    </div>
  );
}

/* ─────────── 9. ADAPTIVE TP TAB (Module 3) ─────────── */
function AdaptiveTPTab() {
  const { cfg, setCfg, loading, save } = useEngineConfig();
  const [signals, setSignals] = useState<any[]>([]);
  const loadSignals = async () => {
    try { const r = await apiGet<any>("/admin/recent-signals?limit=30"); setSignals(r.signals || []); }
    catch (e) { /* silent */ }
  };
  useEffect(() => { loadSignals(); }, []);
  if (loading || !cfg) return <Panel><div className="py-8 text-center font-mono text-xs text-muted-foreground">— Loading… —</div></Panel>;
  const atp = cfg.adaptive_tp || {};
  const setATP = (patch: any) => setCfg({ ...cfg, adaptive_tp: { ...atp, ...patch } });
  const setPartial = (patch: any) => setATP({ partial_tp: { ...(atp.partial_tp || {}), ...patch } });
  const setTrailing = (patch: any) => setATP({ trailing: { ...(atp.trailing || {}), ...patch } });
  const setLevel = (idx: number, key: "rr" | "close_pct", v: number) => {
    const levels = [...((atp.partial_tp || {}).levels || [])];
    levels[idx] = { ...levels[idx], [key]: v };
    setPartial({ levels });
  };
  // strategy usage stats from recent signals
  const stratStats: Record<string, number> = {};
  for (const s of signals) {
    const st = (s.adaptive_tp || {}).tp_strategy || "—";
    stratStats[st] = (stratStats[st] || 0) + 1;
  }
  return (
    <div className="space-y-4">
      <div className="grid md:grid-cols-4 gap-4">
        <Panel><Stat label="Engine" value={atp.enabled ? "ON" : "OFF"} delta={{ value: atp.enabled ? "active" : "static R:R", positive: !!atp.enabled }} /></Panel>
        <Panel><Stat label="Static RR" value={atp.static_rr ?? 2.0} /></Panel>
        <Panel><Stat label="RR bounds" value={`${atp.min_rr_floor ?? 1.5} – ${atp.max_rr_cap ?? 6.0}`} /></Panel>
        <Panel><Stat label="Symbol overrides" value={Object.keys(atp.symbol_overrides || {}).length} /></Panel>
      </div>
      <Panel>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={!!atp.enabled} onCheckedChange={(v) => setATP({ enabled: v })} data-testid="atp-enabled" />
          <Label className="text-sm">Enable Adaptive TP (SL never modified)</Label>
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// PRIORITY ORDER (first valid candidate wins)</div>
        <Input value={(atp.priority || []).join(",")}
               placeholder={TP_STRATEGIES.join(",")}
               onChange={(e) => setATP({ priority: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })}
               data-testid="atp-priority" />
        <div className="text-xs text-muted-foreground mt-2">Available: {TP_STRATEGIES.join(", ")}</div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// STRATEGY PARAMS</div>
        <div className="grid md:grid-cols-3 gap-4">
          <NumField label="static_rr" value={atp.static_rr ?? 2.0} step={0.1} onChange={(v) => setATP({ static_rr: v })} testid="atp-static-rr" />
          <NumField label="atr_multiplier" value={atp.atr_multiplier ?? 2.5} step={0.1} onChange={(v) => setATP({ atr_multiplier: v })} testid="atp-atr-mult" />
          <NumField label="swing_lookback" value={atp.swing_lookback ?? 50} onChange={(v) => setATP({ swing_lookback: v })} testid="atp-swing-lb" />
          <NumField label="sr_lookback" value={atp.sr_lookback ?? 120} onChange={(v) => setATP({ sr_lookback: v })} testid="atp-sr-lb" />
          <NumField label="sr_cluster_atr" value={atp.sr_cluster_atr ?? 0.5} step={0.05} onChange={(v) => setATP({ sr_cluster_atr: v })} testid="atp-sr-cl" />
          <NumField label="structure_lookback" value={atp.structure_lookback ?? 40} onChange={(v) => setATP({ structure_lookback: v })} testid="atp-st-lb" />
          <NumField label="min_rr_floor" value={atp.min_rr_floor ?? 1.5} step={0.1} onChange={(v) => setATP({ min_rr_floor: v })} testid="atp-min-rr" />
          <NumField label="max_rr_cap" value={atp.max_rr_cap ?? 6.0} step={0.1} onChange={(v) => setATP({ max_rr_cap: v })} testid="atp-max-rr" />
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// PARTIAL TP</div>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={!!(atp.partial_tp || {}).enabled} onCheckedChange={(v) => setPartial({ enabled: v })} data-testid="atp-partial-enabled" />
          <Label className="text-xs">Enable partial closes (requires bridge support)</Label>
        </div>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted-foreground"><th>#</th><th>RR</th><th>Close %</th></tr></thead>
          <tbody>
            {((atp.partial_tp || {}).levels || []).map((lvl: any, idx: number) => (
              <tr key={idx} className="border-t border-border">
                <td className="py-1.5 font-mono">{idx + 1}</td>
                <td><Input type="number" step={0.1} value={lvl.rr} onChange={(e) => setLevel(idx, "rr", Number(e.target.value))} className="w-24" data-testid={`atp-lvl-${idx}-rr`} /></td>
                <td><Input type="number" value={lvl.close_pct} onChange={(e) => setLevel(idx, "close_pct", Number(e.target.value))} className="w-24" data-testid={`atp-lvl-${idx}-pct`} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// TRAILING</div>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={!!(atp.trailing || {}).enabled} onCheckedChange={(v) => setTrailing({ enabled: v })} data-testid="atp-trail-enabled" />
          <Label className="text-xs">Enable trailing stop (bridge-side execution)</Label>
        </div>
        <div className="grid md:grid-cols-2 gap-4">
          <NumField label="Activate at RR" value={(atp.trailing || {}).activate_at_rr ?? 1.0} step={0.1} onChange={(v) => setTrailing({ activate_at_rr: v })} testid="atp-trail-act" />
          <NumField label="Trail distance (× ATR)" value={(atp.trailing || {}).trail_distance_atr ?? 0.8} step={0.1} onChange={(v) => setTrailing({ trail_distance_atr: v })} testid="atp-trail-dist" />
        </div>
      </Panel>
      <SymbolOverridesEditor
        label="Adaptive TP"
        ovs={atp.symbol_overrides || {}}
        columns={[
          { key: "priority", type: "list", placeholder: "structure,swing,atr" },
          { key: "atr_multiplier", type: "number", placeholder: "2.5" },
          { key: "min_rr_floor", type: "number", placeholder: "1.5" },
        ]}
        onChange={(next) => setATP({ symbol_overrides: next })}
        testidPrefix="atp-sym"
      />
      <div className="flex gap-3">
        <Button onClick={() => save({ adaptive_tp: cfg.adaptive_tp } as any)} data-testid="save-atp"><Save className="w-4 h-4 mr-2" /> Save Adaptive-TP</Button>
      </div>
      <Panel>
        <div className="flex items-center justify-between mb-3">
          <div className="font-mono text-[11px] tracking-widest text-primary">// LIVE — STRATEGY USAGE (last 30 approved signals)</div>
          <Button variant="outline" size="sm" onClick={loadSignals} data-testid="atp-reload-signals"><RefreshCw className="w-3 h-3 mr-1" /> Refresh</Button>
        </div>
        {Object.keys(stratStats).length === 0 ? (
          <div className="py-3 text-center font-mono text-xs text-muted-foreground">— no recent signals —</div>
        ) : (
          <div className="flex flex-wrap gap-2 mb-4">
            {Object.entries(stratStats).sort((a, b) => b[1] - a[1]).map(([k, n]) => (
              <span key={k} className="font-mono text-[11px] bg-muted/40 px-2 py-1 rounded">{k}: {n}</span>
            ))}
          </div>
        )}
        {signals.length > 0 && (
          <div className="max-h-72 overflow-auto text-[11px] font-mono space-y-1">
            {signals.slice(0, 15).map((s, i) => (
              <div key={i} className="border-b border-border/40 pb-1">
                <span className="text-muted-foreground">{(s.created_at || "").replace("T", " ").slice(5, 19)}</span>{" "}
                <span className="font-semibold">{s.pair}</span> <span className="text-muted-foreground">{s.side}</span>{" "}
                entry=<span>{s.entry}</span> sl=<span>{s.sl}</span> tp=<span className="text-green-400">{s.tp}</span>{" "}
                {s.adaptive_tp?.tp_strategy && <span className="text-primary">[{s.adaptive_tp.tp_strategy}]</span>}{" "}
                {s.adaptive_tp?.tp_rr_realized && <span className="text-muted-foreground">RR={s.adaptive_tp.tp_rr_realized}</span>}
                {s.tp_levels && <span className="text-amber-400"> · partial({s.tp_levels.length})</span>}
                {s.trailing?.enabled && <span className="text-cyan-400"> · trail</span>}
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

/* ─────────── 10. ADAPTIVE SL TAB (Module 4) ─────────── */
function AdaptiveSLTab() {
  const { cfg, setCfg, loading, save } = useEngineConfig();
  if (loading || !cfg) return <Panel><div className="py-8 text-center font-mono text-xs text-muted-foreground">— Loading… —</div></Panel>;
  const asl = cfg.adaptive_sl || {};
  const setASL = (patch: any) => setCfg({ ...cfg, adaptive_sl: { ...asl, ...patch } });
  const setBE = (patch: any) => setASL({ break_even: { ...(asl.break_even || {}), ...patch } });
  const setTrail = (patch: any) => setASL({ trailing: { ...(asl.trailing || {}), ...patch } });
  const setExpand = (patch: any) => setASL({ dynamic_expansion: { ...(asl.dynamic_expansion || {}), ...patch } });
  const setTighten = (patch: any) => setASL({ tightening: { ...(asl.tightening || {}), ...patch } });
  const setExpRegime = (r: string, v: number | "") => {
    const map = { ...((asl.dynamic_expansion || {}).per_regime || {}) };
    if (v === "" || isNaN(Number(v))) delete map[r]; else map[r] = Number(v);
    setExpand({ per_regime: map });
  };
  const setTightRegime = (r: string, v: number | "") => {
    const map = { ...((asl.tightening || {}).per_regime || {}) };
    if (v === "" || isNaN(Number(v))) delete map[r]; else map[r] = Number(v);
    setTighten({ per_regime: map });
  };
  const expMap = (asl.dynamic_expansion || {}).per_regime || {};
  const tiMap = (asl.tightening || {}).per_regime || {};
  return (
    <div className="space-y-4">
      <div className="grid md:grid-cols-4 gap-4">
        <Panel><Stat label="Engine" value={asl.enabled ? "ON" : "OFF"} delta={{ value: asl.enabled ? "active" : "strategy SL", positive: !!asl.enabled }} /></Panel>
        <Panel><Stat label="ATR mult" value={asl.atr_multiplier ?? 1.5} /></Panel>
        <Panel><Stat label="SL bounds (xATR)" value={`${asl.min_sl_atr ?? 0.5} - ${asl.max_sl_atr ?? 4.0}`} /></Panel>
        <Panel><Stat label="Symbol overrides" value={Object.keys(asl.symbol_overrides || {}).length} /></Panel>
      </div>
      <Panel>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={!!asl.enabled} onCheckedChange={(v) => setASL({ enabled: v })} data-testid="asl-enabled" />
          <Label className="text-sm">Enable Adaptive SL</Label>
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// PRIORITY ORDER (first valid candidate wins)</div>
        <Input value={(asl.priority || []).join(",")}
               placeholder="structure,swing,atr"
               onChange={(e) => setASL({ priority: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })}
               data-testid="asl-priority" />
        <div className="text-xs text-muted-foreground mt-2">Available: structure, swing, atr</div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// LEVEL STRATEGY PARAMS</div>
        <div className="grid md:grid-cols-3 gap-4">
          <NumField label="atr_multiplier" value={asl.atr_multiplier ?? 1.5} step={0.1} onChange={(v) => setASL({ atr_multiplier: v })} testid="asl-atr-mult" />
          <NumField label="swing_lookback" value={asl.swing_lookback ?? 50} onChange={(v) => setASL({ swing_lookback: v })} testid="asl-swing-lb" />
          <NumField label="structure_lookback" value={asl.structure_lookback ?? 40} onChange={(v) => setASL({ structure_lookback: v })} testid="asl-st-lb" />
          <NumField label="swing_buffer (xATR)" value={asl.swing_buffer_atr ?? 0.2} step={0.05} onChange={(v) => setASL({ swing_buffer_atr: v })} testid="asl-buf" />
          <NumField label="volatility_buffer (xATR)" value={asl.volatility_buffer_atr ?? 0.0} step={0.05} onChange={(v) => setASL({ volatility_buffer_atr: v })} testid="asl-vb" />
          <NumField label="min_sl (xATR)" value={asl.min_sl_atr ?? 0.5} step={0.05} onChange={(v) => setASL({ min_sl_atr: v })} testid="asl-min" />
          <NumField label="max_sl (xATR)" value={asl.max_sl_atr ?? 4.0} step={0.1} onChange={(v) => setASL({ max_sl_atr: v })} testid="asl-max" />
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// DYNAMIC EXPANSION (regime-aware; factor &gt; 1.0 widens SL)</div>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={!!(asl.dynamic_expansion || {}).enabled} onCheckedChange={(v) => setExpand({ enabled: v })} data-testid="asl-exp-enabled" />
          <Label className="text-xs">Enable dynamic expansion</Label>
        </div>
        <div className="grid md:grid-cols-3 gap-3">
          {REGIME_NAMES.map(r => (
            <div key={r} className="flex items-center gap-2">
              <Label className="w-32 text-xs font-mono">{r}</Label>
              <Input type="number" step={0.05} value={expMap[r] ?? ""} placeholder="1.00"
                     onChange={(e) => setExpRegime(r, e.target.value === "" ? "" : Number(e.target.value))}
                     className="w-24" data-testid={`asl-exp-${r}`} />
            </div>
          ))}
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// TIGHTENING (regime-aware; factor &lt; 1.0 narrows SL)</div>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={!!(asl.tightening || {}).enabled} onCheckedChange={(v) => setTighten({ enabled: v })} data-testid="asl-ti-enabled" />
          <Label className="text-xs">Enable tightening</Label>
        </div>
        <div className="grid md:grid-cols-3 gap-3">
          {REGIME_NAMES.map(r => (
            <div key={r} className="flex items-center gap-2">
              <Label className="w-32 text-xs font-mono">{r}</Label>
              <Input type="number" step={0.05} value={tiMap[r] ?? ""} placeholder="1.00"
                     onChange={(e) => setTightRegime(r, e.target.value === "" ? "" : Number(e.target.value))}
                     className="w-24" data-testid={`asl-ti-${r}`} />
            </div>
          ))}
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// BREAK-EVEN (bridge moves SL to entry once RR reached)</div>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={!!(asl.break_even || {}).enabled} onCheckedChange={(v) => setBE({ enabled: v })} data-testid="asl-be-enabled" />
          <Label className="text-xs">Enable break-even</Label>
        </div>
        <div className="grid md:grid-cols-2 gap-4">
          <NumField label="Activate at RR" value={(asl.break_even || {}).activate_at_rr ?? 1.0} step={0.1} onChange={(v) => setBE({ activate_at_rr: v })} testid="asl-be-rr" />
          <NumField label="Lock-in (xATR)" value={(asl.break_even || {}).lock_pips_atr ?? 0.1} step={0.05} onChange={(v) => setBE({ lock_pips_atr: v })} testid="asl-be-lock" />
        </div>
      </Panel>
      <Panel>
        <div className="font-mono text-[11px] tracking-widest text-primary mb-3">// TRAILING STOP (bridge trails SL once RR reached)</div>
        <div className="flex items-center gap-3 mb-3">
          <Switch checked={!!(asl.trailing || {}).enabled} onCheckedChange={(v) => setTrail({ enabled: v })} data-testid="asl-trail-enabled" />
          <Label className="text-xs">Enable trailing</Label>
        </div>
        <div className="grid md:grid-cols-2 gap-4">
          <NumField label="Activate at RR" value={(asl.trailing || {}).activate_at_rr ?? 1.5} step={0.1} onChange={(v) => setTrail({ activate_at_rr: v })} testid="asl-trail-rr" />
          <NumField label="Trail distance (xATR)" value={(asl.trailing || {}).trail_distance_atr ?? 1.0} step={0.1} onChange={(v) => setTrail({ trail_distance_atr: v })} testid="asl-trail-dist" />
        </div>
      </Panel>
      <SymbolOverridesEditor
        label="Adaptive SL"
        ovs={asl.symbol_overrides || {}}
        columns={[
          { key: "atr_multiplier", type: "number", placeholder: "1.5" },
          { key: "max_sl_atr", type: "number", placeholder: "4.0" },
          { key: "min_sl_atr", type: "number", placeholder: "0.5" },
        ]}
        onChange={(next) => setASL({ symbol_overrides: next })}
        testidPrefix="asl-sym"
      />
      <div className="flex gap-3">
        <Button onClick={() => save({ adaptive_sl: cfg.adaptive_sl } as any)} data-testid="save-asl"><Save className="w-4 h-4 mr-2" /> Save Adaptive-SL</Button>
      </div>
    </div>
  );
}

