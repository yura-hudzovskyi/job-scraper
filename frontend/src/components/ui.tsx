import type { ButtonHTMLAttributes, PropsWithChildren, ReactNode } from "react";

export function Card({ children }: PropsWithChildren) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      {children}
    </section>
  );
}

export function SectionTitle({ children }: PropsWithChildren) {
  return <h2 className="mb-3 text-lg font-semibold">{children}</h2>;
}

export function Button({
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={`rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      {...props}
    />
  );
}

export function SecondaryButton({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <Button className={`bg-slate-600 hover:bg-slate-500 ${className}`} {...props} />;
}

export function DangerButton({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <Button className={`bg-red-700 hover:bg-red-600 ${className}`} {...props} />;
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <p className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      {message}
    </p>
  );
}

export function InfoBanner({ tone = "info", children }: PropsWithChildren<{ tone?: "info" | "warn" | "ok" }>) {
  const tones = {
    info: "border-slate-200 bg-slate-50 text-slate-700",
    warn: "border-amber-200 bg-amber-50 text-amber-900",
    ok: "border-green-200 bg-green-50 text-green-800",
  };
  return <div className={`rounded border px-3 py-2 text-sm ${tones[tone]}`}>{children}</div>;
}

export function Field({
  label,
  hint,
  children,
}: PropsWithChildren<{ label: string; hint?: ReactNode }>) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "w-full rounded border border-slate-300 px-2.5 py-1.5 text-sm focus:border-slate-500 focus:outline-none";

export function Badge({
  tone = "neutral",
  children,
  title,
}: PropsWithChildren<{ tone?: "neutral" | "ok" | "warn" | "bad"; title?: string }>) {
  const tones = {
    neutral: "bg-slate-100 text-slate-600",
    ok: "bg-green-100 text-green-800",
    warn: "bg-amber-100 text-amber-800",
    bad: "bg-red-100 text-red-800",
  };
  return (
    <span
      title={title}
      className={`rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function Stat({ value, label }: { value: ReactNode; label: string }) {
  return (
    <div>
      <p className="text-2xl font-bold tabular-nums">{value}</p>
      <p className="text-sm text-slate-500">{label}</p>
    </div>
  );
}

export function Modal({
  title,
  onClose,
  children,
}: PropsWithChildren<{ title: string; onClose: () => void }>) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-lg bg-white p-5 shadow-lg"
        onClick={(event) => event.stopPropagation()}
      >
        <SectionTitle>{title}</SectionTitle>
        {children}
      </div>
    </div>
  );
}
