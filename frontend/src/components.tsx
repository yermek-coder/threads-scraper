import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertCircle, Check, LoaderCircle, type LucideIcon } from 'lucide-react';

export type Notice = { message: string; kind: 'success' | 'error' };

export function Spinner({ label = 'Загрузка…', small = false }: { label?: string; small?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted" role="status">
      <LoaderCircle className={`animate-spin ${small ? 'h-4 w-4' : 'h-5 w-5'}`} aria-hidden="true" />
      {label}
    </span>
  );
}

export function Button({
  children,
  variant = 'primary',
  className = '',
  type = 'button',
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'quiet' | 'danger' }) {
  const styles = {
    primary: 'bg-ink text-white hover:bg-[#26343a] disabled:bg-[#8b979c]',
    secondary: 'border border-[#dde2e5] bg-white text-ink hover:bg-[#f5f7f7] disabled:opacity-50',
    quiet: 'text-[#58666c] hover:bg-[#edf0f1] disabled:opacity-50',
    danger: 'border border-[#f0d5d0] bg-[#fff8f6] text-[#a44739] hover:bg-[#fff0ec] disabled:opacity-50',
  };
  return (
    <button
      type={type}
      className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold transition disabled:cursor-not-allowed ${styles[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function IconButton({
  label,
  icon: Icon,
  className = '',
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { label: string; icon: LucideIcon }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`inline-flex h-9 w-9 items-center justify-center rounded-lg text-[#637177] transition hover:bg-[#eef1f2] hover:text-ink disabled:opacity-50 ${className}`}
      {...props}
    >
      <Icon className="h-[17px] w-[17px]" aria-hidden="true" />
    </button>
  );
}

export function Input({ label, className = '', hint, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { label?: string; hint?: string }) {
  return (
    <label className="grid gap-2 text-sm font-medium text-[#344248]">
      {label && <span>{label}</span>}
      <input
        className={`h-11 w-full rounded-xl border border-[#dfe4e6] bg-white px-3.5 text-sm text-ink placeholder:text-[#a0aaae] focus:border-[#9abd61] focus:outline-none ${className}`}
        {...props}
      />
      {hint && <span className="text-xs font-normal leading-5 text-muted">{hint}</span>}
    </label>
  );
}

export function Select({ label, className = '', children, ...props }: React.SelectHTMLAttributes<HTMLSelectElement> & { label?: string }) {
  return (
    <label className="grid gap-2 text-sm font-medium text-[#344248]">
      {label && <span>{label}</span>}
      <select
        className={`h-11 w-full rounded-xl border border-[#dfe4e6] bg-white px-3.5 text-sm text-ink focus:border-[#9abd61] focus:outline-none ${className}`}
        {...props}
      >
        {children}
      </select>
    </label>
  );
}

export function Textarea({ label, className = '', ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label?: string }) {
  return (
    <label className="grid gap-2 text-sm font-medium text-[#344248]">
      {label && <span>{label}</span>}
      <textarea
        className={`min-h-28 w-full resize-y rounded-xl border border-[#dfe4e6] bg-white px-3.5 py-3 text-sm text-ink placeholder:text-[#a0aaae] focus:border-[#9abd61] focus:outline-none ${className}`}
        {...props}
      />
    </label>
  );
}

export function PageHeading({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow && <p className="mb-2 text-xs font-bold uppercase tracking-[.16em] text-[#839096]">{eyebrow}</p>}
        <h1 className="text-[26px] font-bold tracking-[-.04em] text-ink sm:text-[30px]">{title}</h1>
        {description && <p className="mt-2 max-w-2xl text-sm leading-6 text-muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function NoticeBar({ notice, onDismiss }: { notice: Notice; onDismiss?: () => void }) {
  const success = notice.kind === 'success';
  return (
    <div
      className={`flex items-start gap-3 rounded-xl border px-4 py-3 text-sm ${success ? 'border-[#d8e9bd] bg-[#f6fbeF] text-[#456629]' : 'border-[#f0d4cf] bg-[#fff7f5] text-[#98473b]'}`}
      role={success ? 'status' : 'alert'}
    >
      {success ? <Check className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />}
      <span className="flex-1">{notice.message}</span>
      {onDismiss && <button type="button" className="font-semibold opacity-70 hover:opacity-100" onClick={onDismiss} aria-label="Закрыть уведомление">×</button>}
    </div>
  );
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'green' | 'amber' | 'red' | 'blue' }) {
  const tones = {
    neutral: 'bg-[#eef1f2] text-[#5d6b71]',
    green: 'bg-[#edf6df] text-[#57762c]',
    amber: 'bg-[#fff4dd] text-[#936a1e]',
    red: 'bg-[#fff0ec] text-[#a54f41]',
    blue: 'bg-[#eaf3f7] text-[#487487]',
  };
  return <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold ${tones[tone]}`}>{children}</span>;
}

export function EmptyState({ icon: Icon, title, description, action }: { icon: LucideIcon; title: string; description: string; action?: ReactNode }) {
  return (
    <div className="flex min-h-64 flex-col items-center justify-center rounded-2xl border border-dashed border-[#d9dfe2] bg-white/70 px-6 py-10 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-[#edf3e4] text-[#678c36]"><Icon className="h-5 w-5" aria-hidden="true" /></div>
      <h3 className="font-semibold text-ink">{title}</h3>
      <p className="mt-2 max-w-md text-sm leading-6 text-muted">{description}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

type BoundaryState = { hasError: boolean };
export class ErrorBoundary extends Component<{ children: ReactNode }, BoundaryState> {
  state: BoundaryState = { hasError: false };

  static getDerivedStateFromError(): BoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Dashboard rendering failed', error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="grid min-h-screen place-items-center bg-canvas p-6">
          <div className="max-w-md rounded-2xl border border-[#e4e8e9] bg-white p-8 text-center shadow-card">
            <AlertCircle className="mx-auto h-8 w-8 text-[#b45848]" />
            <h1 className="mt-4 text-xl font-bold">Не удалось открыть страницу</h1>
            <p className="mt-2 text-sm leading-6 text-muted">Обновите страницу. Если ошибка повторится, войдите в систему заново.</p>
            <button className="mt-5 rounded-xl bg-ink px-4 py-2.5 text-sm font-semibold text-white" onClick={() => window.location.reload()}>Обновить</button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
