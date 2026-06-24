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
import { Activity, Filter, Gauge, RefreshCw, RotateCcw, Save, ShieldAlert, Sliders, Target } from "lucide-react";

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
  updated_at?: number;
};

const SCORE_KEYS = ["h4_trend", "h1_trend", "adx", "vwap", "sr", "atr_ratio", "spread"];
const SYMBOLS_KNOWN = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDCAD", "USDJPY", "AUDUSD", "NZDUSD", "USDCHF"];

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
            <TabsTrigger value="telemetry" data-testid="ec-tab-telemetry"><Activity className="w-3 h-3 mr-1" /> TELEMETRY</TabsTrigger>
          </TabsList>
          <TabsContent value="scoring" className="mt-4"><ScoringTab /></TabsContent>
          <TabsContent value="cooldown" className="mt-4"><CooldownTab /></TabsContent>
          <TabsContent value="filters" className="mt-4"><FiltersTab /></TabsContent>
          <TabsContent value="symbols" className="mt-4"><SymbolsTab /></TabsContent>
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
