import { useEffect, useState } from 'react';
import { Check, Copy, ExternalLink, KeyRound, Link2, Save, ShieldCheck } from 'lucide-react';
import { api, userMessage } from '../api';
import { Badge, Button, Input, PageHeading, Spinner, type Notice } from '../components';
import type { Settings } from '../types';

type Props = { notify: (notice: Notice) => void };
const PRODUCTION_CALLBACK_URL = 'https://monitor.example.com/api/webhooks/threads';
const getCallbackUrl = () => {
  if (typeof window === 'undefined') return PRODUCTION_CALLBACK_URL;
  if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    return PRODUCTION_CALLBACK_URL;
  }
  return `${window.location.origin}/api/webhooks/threads`;
};

export default function ThreadsSettingsPage({ notify }: Props) {
  const callbackUrl = getCallbackUrl();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [accessToken, setAccessToken] = useState('');
  const [userId, setUserId] = useState('');
  const [copied, setCopied] = useState<'callback' | 'verify' | null>(null);

  useEffect(() => {
    let alive = true;
    void api<Settings>('/settings').then((result) => {
      if (!alive) return;
      setSettings(result);
      setUserId(result.threads_user_id);
    }).catch((error: unknown) => {
      if (alive) notify({ kind: 'error', message: userMessage(error) });
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [notify]);

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      const result = await api<Settings>('/settings', { method: 'PUT', body: JSON.stringify({ threads_access_token: accessToken, threads_user_id: userId.trim() }) });
      setSettings(result);
      setAccessToken('');
      notify({ kind: 'success', message: 'Настройки Threads API сохранены.' });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setSaving(false);
    }
  }

  async function copy(value: string, name: 'callback' | 'verify') {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(name);
      window.setTimeout(() => setCopied((current) => current === name ? null : current), 1800);
      notify({ kind: 'success', message: name === 'callback' ? 'Callback URL скопирован.' : 'Verify Token скопирован.' });
    } catch {
      notify({ kind: 'error', message: 'Браузер не разрешил копирование. Выделите и скопируйте значение вручную.' });
    }
  }

  const verifyToken = settings?.threads_verify_token ?? '';

  return (
    <div className="animate-enter">
      <PageHeading eyebrow="Интеграция Meta" title="Настройки Threads API" description="Подключите аккаунт Threads и укажите адрес webhook в панели разработчика Meta." />

      <section className="mb-5 rounded-2xl border border-[#e5e9ea] bg-white p-5 shadow-card sm:p-6">
        <div className="mb-5 flex items-start gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#f0eef9] text-[#6a5c9a]"><KeyRound className="h-5 w-5" /></div>
          <div><h2 className="font-bold text-ink">Доступ к Threads</h2><p className="mt-1 text-sm text-muted">Токен хранится на сервере. После сохранения секретное значение повторно не показывается.</p></div>
          {settings?.threads_access_token_configured && <Badge tone="green"><Check className="mr-1 h-3 w-3" />Подключён</Badge>}
        </div>
        {loading ? <div className="flex min-h-40 items-center justify-center"><Spinner label="Загружаем настройки…" /></div> : (
          <form onSubmit={(event) => void save(event)}>
            <div className="grid gap-4 md:grid-cols-2">
              <Input label="Threads User ID" value={userId} onChange={(event) => setUserId(event.target.value)} placeholder="Идентификатор аккаунта Threads" autoComplete="off" />
              <Input label="Threads User Access Token" type="password" value={accessToken} onChange={(event) => setAccessToken(event.target.value)} placeholder={settings?.threads_access_token_configured ? 'Токен сохранён · пустое поле оставит его без изменений' : 'Вставьте Long-Lived User Access Token'} autoComplete="new-password" />
            </div>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-[#edf0f1] pt-4">
              <p className="text-xs leading-5 text-muted">Для автоматического продления используйте Long-Lived User Access Token.</p>
              <Button type="submit" disabled={saving}><Save className="h-4 w-4" />{saving ? 'Сохраняем…' : 'Сохранить настройки'}</Button>
            </div>
          </form>
        )}
      </section>

      <section className="rounded-2xl border border-[#e5e9ea] bg-white p-5 shadow-card sm:p-6">
        <div className="mb-5 flex items-start gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#eaf2f7] text-[#4d7890]"><Link2 className="h-5 w-5" /></div>
          <div><h2 className="font-bold text-ink">Webhook в панели Meta</h2><p className="mt-1 text-sm text-muted">Добавьте подписку на упоминания и ответы в настройках приложения Meta.</p></div>
        </div>
        <div className="grid gap-4">
          <div className="rounded-xl border border-[#e8ecee] bg-[#fafbfb] p-4 sm:p-5">
            <div className="mb-2 text-xs font-bold uppercase tracking-[.12em] text-[#849096]">Callback URL</div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <code className="break-all text-sm text-[#344248]">{callbackUrl}</code>
              <Button variant="secondary" className="min-h-9 shrink-0 px-3" onClick={() => void copy(callbackUrl, 'callback')}><Copy className="h-3.5 w-3.5" />{copied === 'callback' ? 'Скопировано' : 'Скопировать'}</Button>
            </div>
          </div>
          <div className="rounded-xl border border-[#e8ecee] bg-[#fafbfb] p-4 sm:p-5">
            <div className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-[.12em] text-[#849096]"><ShieldCheck className="h-3.5 w-3.5" />Verify Token</div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              {verifyToken ? <code className="break-all rounded-lg border border-[#e4e8e9] bg-white px-3 py-2 text-sm text-[#344248]">{verifyToken}</code> : <p className="text-sm text-[#a44739]">Токен не настроен. Укажите THREADS_VERIFY_TOKEN в окружении сервера и перезапустите backend.</p>}
              <Button variant="secondary" className="min-h-9 shrink-0 px-3" disabled={!verifyToken} onClick={() => void copy(verifyToken, 'verify')}><Copy className="h-3.5 w-3.5" />{copied === 'verify' ? 'Скопировано' : 'Скопировать'}</Button>
            </div>
          </div>
        </div>
        <div className="mt-5 rounded-xl bg-[#f1f6eb] p-4 text-sm leading-6 text-[#586b46]">
          <p className="font-semibold">Как подключить</p>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-[#68795a]">
            <li>В приложении Meta откройте настройку Webhooks для Threads.</li>
            <li>Вставьте Callback URL и Verify Token, затем пройдите проверку.</li>
            <li>Подпишитесь на события упоминаний и ответов.</li>
          </ol>
          <a href="https://developers.facebook.com/" target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-[#577738] hover:text-ink">Открыть Meta for Developers <ExternalLink className="h-3 w-3" /></a>
        </div>
      </section>
    </div>
  );
}
