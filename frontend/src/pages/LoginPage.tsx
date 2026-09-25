import { useState } from 'react';
import { ArrowRight, Eye, EyeOff, LockKeyhole, RadioTower, Sparkles } from 'lucide-react';
import { ApiError, api, saveToken, userMessage } from '../api';
import { Button, Input, NoticeBar, Spinner, type Notice } from '../components';
import type { User } from '../types';

type Props = { onLogin: (user: User) => void };

export default function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [notice, setNotice] = useState<Notice | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setNotice(null);
    try {
      const result = await api<{ access_token: string; token_type: string }>('/auth/login', { method: 'POST', body: JSON.stringify({ username: username.trim(), password }) });
      saveToken(result.access_token);
      const user = await api<User>('/auth/me');
      onLogin(user);
    } catch (error) {
      setNotice({ kind: 'error', message: error instanceof ApiError && error.status === 401 ? 'Неверный логин или пароль.' : userMessage(error) });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="grid min-h-screen bg-white lg:grid-cols-[minmax(0,1fr)_minmax(430px,.82fr)]">
      <section className="relative hidden overflow-hidden bg-[#172126] px-12 py-10 text-white lg:flex lg:flex-col lg:justify-between xl:px-20">
        <div className="absolute -right-28 top-28 h-[440px] w-[440px] rounded-full border border-white/[.07]" />
        <div className="absolute -right-8 top-48 h-[280px] w-[280px] rounded-full border border-white/[.08]" />
        <div className="absolute bottom-[-180px] left-[-120px] h-[430px] w-[430px] rounded-full bg-[#c5f36a]/[.06] blur-3xl" />
        <div className="relative flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#c5f36a] text-ink"><RadioTower className="h-5 w-5" /></div>
          <span className="text-sm font-bold tracking-wide">QADAM <span className="font-medium text-white/45">· MONITOR</span></span>
        </div>
        <div className="relative max-w-xl pb-14">
          <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-2xl border border-white/10 bg-white/[.05] text-[#c5f36a]"><Sparkles className="h-5 w-5" /></div>
          <p className="mb-4 text-xs font-bold uppercase tracking-[.2em] text-[#c5f36a]">Threads brand monitoring</p>
          <h1 className="text-5xl font-bold leading-[1.08] tracking-[-.05em] xl:text-[58px]">Слушайте,<br />о чём говорят<br /><span className="text-[#c5f36a]">о вашем бренде.</span></h1>
          <p className="mt-6 max-w-md text-[15px] leading-7 text-white/55">Собирайте упоминания в Threads, быстро находите важные публикации и получайте уведомления в удобном канале.</p>
          <div className="mt-10 flex items-center gap-3 text-xs text-white/40"><span className="h-1.5 w-1.5 rounded-full bg-[#c5f36a]" />Приватная панель мониторинга</div>
        </div>
        <div className="relative text-xs text-white/30">Example Organization · {new Date().getFullYear()}</div>
      </section>

      <section className="flex min-h-screen items-center justify-center bg-[#f8f9f9] px-5 py-10 sm:px-10">
        <div className="w-full max-w-[420px]">
          <div className="mb-9 flex items-center gap-3 lg:hidden">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#172126] text-[#c5f36a]"><RadioTower className="h-5 w-5" /></div>
            <span className="text-sm font-bold tracking-wide">QADAM <span className="font-medium text-[#879398]">· MONITOR</span></span>
          </div>
          <div className="mb-8">
            <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-2xl bg-[#edf5e3] text-[#678c36]"><LockKeyhole className="h-5 w-5" /></div>
            <p className="text-xs font-bold uppercase tracking-[.16em] text-[#829096]">Панель управления</p>
            <h2 className="mt-2 text-3xl font-bold tracking-[-.04em] text-ink">С возвращением</h2>
            <p className="mt-2 text-sm leading-6 text-muted">Войдите, чтобы продолжить мониторинг упоминаний.</p>
          </div>
          <form className="grid gap-5" onSubmit={(event) => void submit(event)}>
            <Input label="Логин" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" autoFocus required />
            <label className="grid gap-2 text-sm font-medium text-[#344248]">
              <span>Пароль</span>
              <span className="relative">
                <input className="h-11 w-full rounded-xl border border-[#dfe4e6] bg-white px-3.5 pr-11 text-sm text-ink placeholder:text-[#a0aaae] focus:border-[#9abd61] focus:outline-none" type={showPassword ? 'text' : 'password'} value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required />
                <button type="button" className="absolute inset-y-0 right-0 inline-flex w-11 items-center justify-center text-[#879398] hover:text-ink" onClick={() => setShowPassword((current) => !current)} aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}>{showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button>
              </span>
            </label>
            {notice && <NoticeBar notice={notice} />}
            <Button type="submit" className="mt-1 h-12 w-full" disabled={submitting}>{submitting ? <Spinner small label="Входим…" /> : <>Войти в панель <ArrowRight className="h-4 w-4" /></>}</Button>
          </form>
          <p className="mt-7 text-center text-xs leading-5 text-[#9aa4a8]">Учетная запись задаётся на сервере<br />через переменные ADMIN_USERNAME и ADMIN_PASSWORD.</p>
        </div>
      </section>
    </main>
  );
}
