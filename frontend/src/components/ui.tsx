import type { ButtonHTMLAttributes, PropsWithChildren } from "react";

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

export function ErrorBanner({ message }: { message: string }) {
  return (
    <p className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      {message}
    </p>
  );
}

export function Field({ label, children }: PropsWithChildren<{ label: string }>) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
      {children}
    </label>
  );
}

export const inputClass =
  "w-full rounded border border-slate-300 px-2.5 py-1.5 text-sm focus:border-slate-500 focus:outline-none";

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
        className="w-full max-w-md rounded-lg bg-white p-5 shadow-lg"
        onClick={(event) => event.stopPropagation()}
      >
        <SectionTitle>{title}</SectionTitle>
        {children}
      </div>
    </div>
  );
}
