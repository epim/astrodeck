import type { ReactNode } from "react";

export function Panel({ title, right, children, className = "" }: {
  title?: string; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`panel p-4 ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between mb-3">
          {title && <h2 className="panel-title">{title}</h2>}
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Led({ on, warn = false }: { on: boolean; warn?: boolean }) {
  return <span className={`led ${on ? (warn ? "led-warn" : "led-on") : "led-off"}`} />;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 min-w-0">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}

export function Stat({ label, value, unit, tone }: {
  label: string; value: string | number; unit?: string; tone?: "good" | "warn" | "bad";
}) {
  const color = tone === "good" ? "text-good" : tone === "warn" ? "text-warn"
    : tone === "bad" ? "text-bad" : "text-ink";
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span className="label">{label}</span>
      <span className={`mono text-sm ${color} truncate`}>
        {value}{unit && <span className="text-dim text-xs ml-1">{unit}</span>}
      </span>
    </div>
  );
}

export function Toggle({ checked, onChange, disabled = false, label, showState = false }: {
  checked: boolean; onChange: (v: boolean) => void; disabled?: boolean;
  label?: string; showState?: boolean;
}) {
  return (
    <span className="inline-flex items-center min-h-11 sm:min-h-0">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative w-9 h-5 border transition-colors shrink-0
          ${checked ? "bg-accent2/40 border-accent" : "bg-raise border-line2"}
          ${disabled ? "opacity-40" : "cursor-pointer"}`}
      >
        <span className={`absolute top-0.5 w-3.5 h-3.5 transition-all
          ${checked ? "left-[18px] bg-accent" : "left-0.5 bg-dim"}`} />
      </button>
      {showState && (
        <span className="label ml-2" aria-hidden>{checked ? "ON" : "OFF"}</span>
      )}
    </span>
  );
}
