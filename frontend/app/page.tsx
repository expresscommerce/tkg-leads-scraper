"use client";

import { useState, useEffect, useRef } from "react";
import {
  Search,
  Square,
  Download,
  Loader2,
  AlertCircle,
  CheckCircle2,
  Settings,
} from "lucide-react";
import { API_URL, cn } from "@/lib/utils";

type JobStatus = {
  status: "queued" | "running" | "finished" | "failed" | "stopped";
  stage?: string;
  current?: number;
  total?: number;
  message?: string;
  count?: number;
  error?: string;
  csv_path?: string;
};

const STAGE_LABELS: Record<string, string> = {
  start: "Starting…",
  maps: "Scraping Google Maps",
  maps_location: "Searching location",
  maps_location_done: "Location complete",
  maps_done: "Maps complete",
  website: "Enriching websites",
  website_done: "Websites complete",
  meta: "Checking Meta ads",
  meta_done: "Meta complete",
  done: "Done",
};

export default function Home() {
  const [query, setQuery] = useState("Garage Door Repair\nOverhead Door Repair");
  const [location, setLocation] = useState("Jacksonville, Florida\nMiami, Florida\nTampa, Florida");
  const [mode, setMode] = useState<"per" | "target">("per");
  const [perSearch, setPerSearch] = useState(20);
  const [targetTotal, setTargetTotal] = useState(2500);
  const [dedupeBuffer, setDedupeBuffer] = useState(20);
  const [skipWebsites, setSkipWebsites] = useState(false);
  const [skipMeta, setSkipMeta] = useState(false);
  const [outputName, setOutputName] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  const keywords = query.split("\n").map((s) => s.trim()).filter(Boolean);
  const locations = location.split("\n").map((s) => s.trim()).filter(Boolean);
  const combos = keywords.length * locations.length;

  const computedMaxResults =
    mode === "target" && combos > 0
      ? Math.max(1, Math.floor((targetTotal * (1 + dedupeBuffer / 100)) / combos))
      : perSearch;

  const isRunning = status?.status === "queued" || status?.status === "running";

  useEffect(() => {
    if (!jobId || !isRunning) return;
    pollRef.current = setInterval(async () => {
      try {
        const [statusRes, logsRes] = await Promise.all([
          fetch(`${API_URL}/status/${jobId}`),
          fetch(`${API_URL}/logs/${jobId}?tail=200`),
        ]);
        if (statusRes.ok) {
          const s = await statusRes.json();
          setStatus(s);
        }
        if (logsRes.ok) {
          const l = await logsRes.json();
          setLogs(l.lines || []);
        }
      } catch (e) {
        console.error("Poll error:", e);
      }
    }, 1500);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [jobId, isRunning]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const handleRun = async () => {
    setError(null);
    setLogs([]);
    setStatus(null);
    if (!keywords.length || !locations.length) {
      setError("Please enter at least one keyword and one location.");
      return;
    }
    try {
      const res = await fetch(`${API_URL}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: keywords,
          location: locations,
          max_results: computedMaxResults,
          skip_websites: skipWebsites,
          skip_meta: skipMeta,
          output_basename: outputName || null,
        }),
      });
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      const data = await res.json();
      setJobId(data.job_id);
      setStatus({ status: "queued" });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start job");
    }
  };

  const handleStop = async () => {
    if (!jobId) return;
    try {
      await fetch(`${API_URL}/stop/${jobId}`, { method: "POST" });
    } catch (e) {
      console.error("Stop error:", e);
    }
  };

  const handleDownload = () => {
    if (!jobId) return;
    window.open(`${API_URL}/download/${jobId}`, "_blank");
  };

  const progressPct =
    status?.total && status?.current
      ? Math.min(100, (status.current / status.total) * 100)
      : 0;

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-5xl px-4 py-10">
        {/* Hero */}
        <div className="mb-8 rounded-2xl bg-gradient-to-br from-slate-900 to-slate-800 p-8 shadow-xl">
          <div className="flex items-center gap-3">
            <Search className="h-8 w-8 text-blue-400" />
            <h1 className="text-3xl font-bold tracking-tight">Lead Scraper</h1>
          </div>
          <p className="mt-2 text-slate-300">
            Find local businesses, enrich them with contact data, and spot active Meta advertisers.
          </p>
        </div>

        {/* Form Card */}
        <div className="rounded-2xl border border-border bg-card p-6 shadow-lg">
          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <label className="mb-2 block text-sm font-medium">
                Business type / keyword(s)
              </label>
              <textarea
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                rows={5}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="One keyword per line"
              />
            </div>
            <div>
              <label className="mb-2 block text-sm font-medium">Location(s)</label>
              <textarea
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                rows={5}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="One location per line"
              />
            </div>
          </div>

          {/* Mode selector */}
          <div className="mt-6">
            <label className="mb-2 block text-sm font-medium">Set results by:</label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setMode("per")}
                className={cn(
                  "rounded-lg px-4 py-2 text-sm font-medium transition",
                  mode === "per"
                    ? "bg-blue-600 text-white"
                    : "bg-muted text-muted-foreground hover:bg-muted/80"
                )}
              >
                Per search
              </button>
              <button
                type="button"
                onClick={() => setMode("target")}
                className={cn(
                  "rounded-lg px-4 py-2 text-sm font-medium transition",
                  mode === "target"
                    ? "bg-blue-600 text-white"
                    : "bg-muted text-muted-foreground hover:bg-muted/80"
                )}
              >
                Target total
              </button>
            </div>
          </div>

          {mode === "per" ? (
            <div className="mt-4">
              <label className="mb-2 block text-sm font-medium">
                Results per (keyword × location)
              </label>
              <input
                type="number"
                min={1}
                max={5000}
                value={perSearch}
                onChange={(e) => setPerSearch(Number(e.target.value))}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          ) : (
            <div className="mt-4 space-y-4">
              <div>
                <label className="mb-2 block text-sm font-medium">
                  Target unique records
                </label>
                <input
                  type="number"
                  min={1}
                  max={100000}
                  value={targetTotal}
                  onChange={(e) => setTargetTotal(Number(e.target.value))}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="mb-2 block text-sm font-medium">
                  Deduplication buffer: {dedupeBuffer}%
                </label>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={dedupeBuffer}
                  onChange={(e) => setDedupeBuffer(Number(e.target.value))}
                  className="w-full accent-blue-500"
                />
              </div>
              {combos > 0 && (
                <p className="text-sm text-muted-foreground">
                  Target: <span className="font-semibold text-foreground">{targetTotal}</span> unique × {1 + dedupeBuffer / 100}x buffer
                  → <span className="font-semibold text-foreground">{computedMaxResults}</span> per search
                </p>
              )}
            </div>
          )}

          {combos > 0 && (
            <p className="mt-3 text-sm text-muted-foreground">
              <span className="font-semibold text-foreground">{keywords.length}</span> keyword(s) ×{" "}
              <span className="font-semibold text-foreground">{locations.length}</span> location(s) ={" "}
              <span className="font-semibold text-foreground">{combos}</span> searches → up to{" "}
              <span className="font-semibold text-foreground">
                {combos * computedMaxResults}
              </span>{" "}
              raw listings before dedupe
            </p>
          )}

          {/* Options */}
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={skipWebsites}
                onChange={(e) => setSkipWebsites(e.target.checked)}
                className="h-4 w-4 accent-blue-500"
              />
              Skip website enrichment
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={skipMeta}
                onChange={(e) => setSkipMeta(e.target.checked)}
                className="h-4 w-4 accent-blue-500"
              />
              Skip Meta Ad Library check
            </label>
            <input
              type="text"
              value={outputName}
              onChange={(e) => setOutputName(e.target.value)}
              placeholder="Output filename (optional)"
              className="rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>

          {/* Run button */}
          <button
            onClick={handleRun}
            disabled={isRunning}
            className="mt-6 w-full rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 py-3 text-sm font-semibold text-white shadow-lg transition hover:from-blue-700 hover:to-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isRunning ? (
              <span className="flex items-center justify-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                Running…
              </span>
            ) : (
              <span className="flex items-center justify-center gap-2">
                <Search className="h-4 w-4" />
                Run pipeline
              </span>
            )}
          </button>

          {error && (
            <div className="mt-4 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400">
              <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </div>

        {/* Progress */}
        {status && (
          <div className="mt-6 rounded-2xl border border-border bg-card p-6 shadow-lg">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold">
                {status.status === "finished" ? (
                  <span className="flex items-center gap-2 text-green-400">
                    <CheckCircle2 className="h-5 w-5" /> Completed
                  </span>
                ) : status.status === "failed" ? (
                  <span className="flex items-center gap-2 text-red-400">
                    <AlertCircle className="h-5 w-5" /> Failed
                  </span>
                ) : status.status === "stopped" ? (
                  <span className="flex items-center gap-2 text-yellow-400">
                    <Square className="h-5 w-5" /> Stopped
                  </span>
                ) : (
                  <span className="flex items-center gap-2 text-blue-400">
                    <Loader2 className="h-5 w-5 animate-spin" /> Running
                  </span>
                )}
              </h2>
              <div className="flex gap-2">
                {isRunning && (
                  <button
                    onClick={handleStop}
                    className="flex items-center gap-1 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-sm font-medium text-red-400 transition hover:bg-red-500/20"
                  >
                    <Square className="h-3 w-3" />
                    Stop
                  </button>
                )}
                {status.status === "finished" && status.csv_path && (
                  <button
                    onClick={handleDownload}
                    className="flex items-center gap-1 rounded-lg bg-green-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-green-700"
                  >
                    <Download className="h-3 w-3" />
                    Download CSV
                  </button>
                )}
              </div>
            </div>

            {/* Progress bar */}
            {isRunning && (
              <div className="mb-4">
                <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                  <span>{STAGE_LABELS[status.stage || ""] || status.stage}</span>
                  <span>
                    {status.current || 0} / {status.total || 0}
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full bg-gradient-to-r from-blue-500 to-indigo-500 transition-all"
                    style={{ width: `${progressPct}%` }}
                  />
                </div>
                {status.message && (
                  <p className="mt-2 text-xs text-muted-foreground">{status.message}</p>
                )}
              </div>
            )}

            {status.status === "finished" && (
              <p className="text-sm text-muted-foreground">
                Found <span className="font-semibold text-foreground">{status.count}</span> unique businesses.
              </p>
            )}

            {status.error && (
              <p className="mt-2 text-sm text-red-400">{status.error}</p>
            )}

            {/* Logs */}
            {logs.length > 0 && (
              <div className="mt-4">
                <details>
                  <summary className="cursor-pointer text-sm font-medium text-muted-foreground hover:text-foreground">
                    Logs ({logs.length})
                  </summary>
                  <div className="mt-2 max-h-64 overflow-y-auto rounded-lg bg-background p-3 font-mono text-xs">
                    {logs.map((line, i) => (
                      <div key={i} className="text-muted-foreground">
                        {line}
                      </div>
                    ))}
                    <div ref={logsEndRef} />
                  </div>
                </details>
              </div>
            )}
          </div>
        )}

        {/* Footer */}
        <div className="mt-8 flex items-center justify-between text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Settings className="h-3 w-3" /> API: {API_URL}
          </span>
          <span>Built with Next.js + FastAPI + Playwright</span>
        </div>
      </div>
    </main>
  );
}
