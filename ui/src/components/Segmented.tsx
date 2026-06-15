// Segmented — a small radiogroup with 2-3 options (settings spec §2.8).
// Selection is shown by filled background + bold label (shape/weight), never
// hue-only, so it survives the all-red night palette. 44px-tall on touch.

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  disabled = false,
}: {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (v: T) => void;
  ariaLabel?: string;
  disabled?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className="inline-flex border border-line2 overflow-hidden"
    >
      {options.map((opt) => {
        const selected = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => !disabled && onChange(opt.value)}
            className={`min-h-11 sm:min-h-0 px-3 py-1.5 text-xs uppercase tracking-wider
              transition-colors border-r border-line2 last:border-r-0
              ${selected ? "bg-accent2/40 text-accent font-bold" : "bg-raise text-dim font-medium"}
              ${disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
