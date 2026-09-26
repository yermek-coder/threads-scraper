import { useEffect, useState } from 'react';
import { Check, Copy, ExternalLink, FileUp, Link2, LoaderCircle, Save, ShieldCheck, Trash2 } from 'lucide-react';
import { api, userMessage } from '../api';
import { Badge, Button, Input, PageHeading, Spinner, type Notice } from '../components';
import type { Settings } from '../types';

export type ThreadsSection = 'collection' | 'relevance' | 'graph' | 'webhook';
type Props = { notify: (notice: Notice) => void; section: ThreadsSection };
const PRODUCTION_ORIGIN = 'https://monitor.example.com';
const getPublicOrigin = () => {
  if (typeof window === 'undefined') return PRODUCTION_ORIGIN;
  if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    return PRODUCTION_ORIGIN;
  }
  return window.location.origin;
};

export default function ThreadsSettingsPage({ notify, section }: Props) {
  const publicOrigin = getPublicOrigin();
  const callbackUrl = `${publicOrigin}/api/webhooks/threads`;
  const oauthCallbackUrl = `${publicOrigin}/api/threads/oauth/callback`;
  const uninstallCallbackUrl = `${publicOrigin}/api/threads/meta/uninstall`;
  const deleteCallbackUrl = `${publicOrigin}/api/threads/meta/delete`;
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [accessToken, setAccessToken] = useState('');
  const [userId, setUserId] = useState('');
  const [openRouterKey, setOpenRouterKey] = useState('');
  const [jevPrompt, setJevPrompt] = useState('');
  const [jevEnabled, setJevEnabled] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [methodSaving, setMethodSaving] = useState(false);
  const [jevSaving, setJevSaving] = useState(false);
  const [sessionSaving, setSessionSaving] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void api<Settings>('/settings').then((result) => {
      if (!alive) return;
      setSettings(result);
      setUserId(result.threads_user_id);
      setJevPrompt(result.jev_relevance_prompt);
      setJevEnabled(result.jev_relevance_enabled);
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

  async function connectThreads() {
    setConnecting(true);
    try {
      const result = await api<{ authorization_url: string }>('/threads/oauth/start');
      window.location.assign(result.authorization_url);
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
      setConnecting(false);
    }
  }

  async function saveJevSettings(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setJevSaving(true);
    try {
      const promptChanged = settings?.jev_relevance_prompt !== jevPrompt.trim();
      const result = await api<Settings>('/settings', {
        method: 'PUT',
        body: JSON.stringify({
          openrouter_api_key: openRouterKey,
          jev_relevance_enabled: jevEnabled,
          jev_relevance_prompt: jevPrompt,
        }),
      });
      setSettings(result);
      setOpenRouterKey('');
      setJevPrompt(result.jev_relevance_prompt);
      setJevEnabled(result.jev_relevance_enabled);
      const reprocessingStarted = promptChanged && result.jev_relevance_enabled && result.openrouter_api_key_configured;
      notify({
        kind: 'success',
        message: reprocessingStarted
          ? 'Настройки сохранены. Посты за последние 7 дней перепроверяются в фоне.'
          : 'Настройки фильтра Jev сохранены.',
      });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setJevSaving(false);
    }
  }

  async function changeCollectionMethod(method: 'web' | 'api') {
    setMethodSaving(true);
    try {
      const result = await api<Settings>('/settings', {
        method: 'PUT',
        body: JSON.stringify({ threads_collection_method: method }),
      });
      setSettings(result);
      notify({ kind: 'success', message: method === 'web' ? 'Основной сбор переключён на Threads web.' : 'Сбор переключён на Graph API.' });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setMethodSaving(false);
    }
  }

  async function importWebSession(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file) return;
    setSessionSaving(true);
    try {
      const parsed: unknown = JSON.parse(await file.text());
      if ((!Array.isArray(parsed) && (!parsed || typeof parsed !== 'object')) || parsed === null) {
        throw new Error('Файл должен содержать JSON Playwright storage state или список cookies.');
      }
      await api<{ configured: boolean }>('/threads/web-session', {
        method: 'POST',
        body: JSON.stringify({ storage_state: parsed }),
      });
      setSettings((current) => current ? { ...current, threads_web_session_configured: true } : current);
      notify({ kind: 'success', message: 'Web-сессия Threads сохранена.' });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setSessionSaving(false);
    }
  }

  async function clearWebSession() {
    setSessionSaving(true);
    try {
      await api<void>('/threads/web-session', { method: 'DELETE' });
      setSettings((current) => current ? { ...current, threads_web_session_configured: false } : current);
      notify({ kind: 'success', message: 'Web-сессия Threads удалена.' });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setSessionSaving(false);
    }
  }

  async function copy(value: string, name: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(name);
      window.setTimeout(() => setCopied((current) => current === name ? null : current), 1800);
      notify({ kind: 'success', message: name === 'verify' ? 'Verify Token скопирован.' : 'Callback URL скопирован.' });
    } catch {
      notify({ kind: 'error', message: 'Браузер не разрешил копирование. Выделите и скопируйте значение вручную.' });
    }
  }

  const verifyToken = settings?.threads_verify_token ?? '';
  const headings: Record<ThreadsSection, { title: string; description: string }> = {
    collection: { title: 'Сбор публикаций', description: 'Выберите метод сбора и управляйте авторизованной web-сессией Threads.' },
    relevance: { title: 'Фильтр релевантности', description: 'Настройте проверку новых публикаций через Jev и OpenRouter.' },
    graph: { title: 'Threads Graph API', description: 'Подключите API для альтернативного поиска по ключевым словам.' },
    webhook: { title: 'Webhook Threads', description: 'Настройте адрес обратного вызова и подписку на события Threads.' },
  };

  return (
    <div className="animate-enter">
      <PageHeading title={headings[section].title} description={headings[section].description} />

      {section === 'collection' && <section className="rounded-2xl border border-[#e5e9ea] bg-white p-5 shadow-card sm:p-6">
        {loading ? <div className="flex min-h-20 items-center justify-center"><Spinner label="Загружаем настройки…" /></div> : settings && (
          <>
            <label className="block max-w-xl text-sm font-semibold text-ink">
              Метод сбора
              <select
                className="mt-2 h-11 w-full rounded-xl border border-[#dfe4e6] bg-white px-3 text-sm focus:border-[#9abd61] focus:outline-none"
                value={settings.threads_collection_method}
                disabled={methodSaving}
                onChange={(event) => void changeCollectionMethod(event.target.value as 'web' | 'api')}
              >
                <option value="web">Threads web scraping (основной)</option>
                <option value="api">Graph API · threads_keyword_search (альтернативный)</option>
              </select>
            </label>
            {settings.threads_collection_method === 'api' && !settings.threads_access_token_configured && <p className="mt-3 text-xs text-[#a44739]">Для альтернативного метода нужно подключить Threads Graph API и разрешение threads_keyword_search ниже.</p>}
            <div className="mt-5 rounded-xl border border-[#e8ecee] bg-[#fafbfb] p-4 sm:p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-ink">Авторизованная web-сессия</p>
                  <p className="mt-1 text-xs leading-5 text-muted">{settings.threads_web_session_configured ? 'Сессия сохранена; если Threads разлогинился, импортируйте свежий файл.' : 'Сессия пока не импортирована.'} Файл содержит секрет авторизации и хранится в закрытых настройках приложения.</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {settings.threads_web_session_configured && <Badge tone="green"><Check className="mr-1 h-3 w-3" />Сессия сохранена</Badge>}
                  {settings.threads_web_session_configured && <Button variant="secondary" className="min-h-9 px-3" disabled={sessionSaving} onClick={() => void clearWebSession()}><Trash2 className="h-3.5 w-3.5" />Удалить</Button>}
                  <label className={`inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-xl border border-[#dfe4e6] bg-white px-3 text-sm font-semibold text-ink hover:bg-[#f7f9f8] ${sessionSaving ? 'pointer-events-none opacity-60' : ''}`}>
                    <FileUp className="h-4 w-4" />{sessionSaving ? 'Сохраняем…' : 'Импортировать JSON'}
                    <input className="sr-only" type="file" accept="application/json,.json" disabled={sessionSaving} onChange={(event) => void importWebSession(event)} />
                  </label>
                </div>
              </div>
              <details className="mt-4">
                <summary className="cursor-pointer text-xs font-semibold text-muted">Как подготовить файл сессии</summary>
                <ol className="mt-3 list-decimal space-y-1 pl-5 text-xs leading-5 text-muted">
                  <li>На компьютере, где можно открыть браузер, создайте окружение <code>python3 -m venv .venv</code>, активируйте его через <code>source .venv/bin/activate</code> и установите Playwright командой <code>python -m pip install playwright</code>.</li>
                  <li>Установите Chromium: <code>python -m playwright install chromium</code>.</li>
                  <li>Запустите <code>python backend/scripts/export_threads_session.py</code>, войдите в Threads и нажмите Enter в терминале.</li>
                  <li>Импортируйте созданный <code>threads-storage-state.json</code>. Подойдёт также экспорт cookies в JSON со старого парсера.</li>
                </ol>
              </details>
            </div>
          </>
        )}
      </section>}

      {section === 'relevance' && <section className="rounded-2xl border border-[#e5e9ea] bg-white p-5 shadow-card sm:p-6">
        {loading ? <div className="flex min-h-20 items-center justify-center"><Spinner label="Загружаем настройки…" /></div> : settings && (
          <form onSubmit={(event) => void saveJevSettings(event)}>
            <div className="grid gap-4">
              <Input
                label="OpenRouter API key"
                type="password"
                value={openRouterKey}
                onChange={(event) => setOpenRouterKey(event.target.value)}
                placeholder={settings.openrouter_api_key_configured ? 'Ключ сохранён · пустое поле оставит его без изменений' : 'Вставьте OpenRouter API key'}
                autoComplete="new-password"
              />
              <p className="-mt-2 text-xs text-muted">Ключ хранится в настройках backend и не возвращается в браузер. Его также можно задать через OPENROUTER_API_KEY.</p>
              <label className="flex items-start gap-3 rounded-xl border border-[#e8ecee] bg-[#fafbfb] p-4 text-sm">
                <input
                  className="mt-0.5 h-4 w-4 accent-[#7a9d45]"
                  type="checkbox"
                  checked={jevEnabled}
                  disabled={!settings.openrouter_api_key_configured && !openRouterKey.trim()}
                  onChange={(event) => setJevEnabled(event.target.checked)}
                />
                <span>
                  <span className="block font-semibold text-ink">Проверять релевантность новых публикаций</span>
                  <span className="mt-1 block text-xs leading-5 text-muted">Jev вызывается только для публикаций, которые прошли стоп-слова и проверку на дубликаты.</span>
                </span>
              </label>
              <label className="block text-sm font-semibold text-ink">
                Инструкция Jev
                <textarea
                  className="mt-2 min-h-36 w-full rounded-xl border border-[#dfe4e6] bg-white px-3 py-3 text-sm font-normal leading-6 text-ink focus:border-[#9abd61] focus:outline-none"
                  value={jevPrompt}
                  maxLength={4000}
                  required
                  onChange={(event) => setJevPrompt(event.target.value)}
                />
                <span className="mt-1 block text-xs font-normal text-muted">Вопрос будет задан о state.post и state.keyword. Можно описать, какие упоминания считать релевантными.</span>
              </label>
            </div>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-[#edf0f1] pt-4">
              <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-xs font-semibold text-[#577738] hover:text-ink">Получить API key <ExternalLink className="h-3 w-3" /></a>
              <Button type="submit" disabled={jevSaving}><Save className="h-4 w-4" />{jevSaving ? 'Сохраняем…' : 'Сохранить фильтр'}</Button>
            </div>
          </form>
        )}
      </section>}

      {section === 'graph' && <>
      <section className="mb-5 rounded-2xl border border-[#e5e9ea] bg-white p-5 shadow-card sm:p-6">
        {loading ? <div className="flex min-h-40 items-center justify-center"><Spinner label="Загружаем настройки…" /></div> : (
          <>
            <div className="rounded-xl border border-[#e8ecee] bg-[#fafbfb] p-4 sm:p-5">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div><p className="text-sm font-semibold text-ink">Подключение через Threads</p><p className="mt-1 text-xs leading-5 text-muted">Откроется страница Meta для подтверждения разрешения threads_keyword_search.</p></div>
                <Button onClick={() => void connectThreads()} disabled={connecting || !settings?.threads_app_id_configured}>
                  {connecting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />}
                  {connecting ? 'Переходим в Threads…' : settings?.threads_access_token_configured ? 'Переподключить Threads' : 'Подключить Threads'}
                </Button>
              </div>
              {!settings?.threads_app_id_configured && <p className="mt-3 text-xs text-[#a44739]">На сервере не указан THREADS_APP_ID. Добавьте его в окружение приложения.</p>}
            </div>
            <details className="mt-4">
              <summary className="cursor-pointer text-xs font-semibold text-muted">Настроить токен вручную</summary>
              <form className="mt-4" onSubmit={(event) => void save(event)}>
                <div className="grid gap-4 md:grid-cols-2">
                  <Input label="Threads User ID" value={userId} onChange={(event) => setUserId(event.target.value)} placeholder="Идентификатор аккаунта Threads" autoComplete="off" />
                  <Input label="Threads User Access Token" type="password" value={accessToken} onChange={(event) => setAccessToken(event.target.value)} placeholder={settings?.threads_access_token_configured ? 'Токен сохранён · пустое поле оставит его без изменений' : 'Вставьте Long-Lived User Access Token'} autoComplete="new-password" />
                </div>
                <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-[#edf0f1] pt-4">
                  <p className="text-xs leading-5 text-muted">Для автоматического продления используйте Long-Lived User Access Token.</p>
                  <Button type="submit" disabled={saving}><Save className="h-4 w-4" />{saving ? 'Сохраняем…' : 'Сохранить настройки'}</Button>
                </div>
              </form>
            </details>
          </>
        )}
      </section>

      <section className="mb-5 rounded-2xl border border-[#e5e9ea] bg-white p-5 shadow-card sm:p-6">
        <div className="mb-5 flex items-start gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#eaf2f7] text-[#4d7890]"><Link2 className="h-5 w-5" /></div>
          <div><h2 className="font-bold text-ink">Callback URLs приложения Meta</h2><p className="mt-1 text-sm text-muted">Скопируйте адреса в соответствующие поля настроек Threads App.</p></div>
        </div>
        <div className="grid gap-3">
          {[
            ['Redirect Callback URL', oauthCallbackUrl],
            ['Uninstall Callback URL', uninstallCallbackUrl],
            ['Delete Callback URL', deleteCallbackUrl],
          ].map(([label, value]) => (
            <div key={label} className="rounded-xl border border-[#e8ecee] bg-[#fafbfb] p-4">
              <div className="mb-2 text-xs font-bold uppercase tracking-[.12em] text-[#849096]">{label}</div>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <code className="break-all text-sm text-[#344248]">{value}</code>
                <Button variant="secondary" className="min-h-9 shrink-0 px-3" onClick={() => void copy(value, label)}><Copy className="h-3.5 w-3.5" />{copied === label ? 'Скопировано' : 'Скопировать'}</Button>
              </div>
            </div>
          ))}
        </div>
        <p className="mt-4 text-xs leading-5 text-muted">Delete callback проверяет подпись Meta, удаляет сохранённые публикации и отключает связанный Threads аккаунт.</p>
      </section>
      </>}

      {section === 'webhook' && <section className="rounded-2xl border border-[#e5e9ea] bg-white p-5 shadow-card sm:p-6">
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
      </section>}
    </div>
  );
}
