import { useCallback, useEffect, useRef, useState } from 'react';
import { Mail, MessageCircle, QrCode, RefreshCw, Send, ShieldCheck, Wifi, WifiOff } from 'lucide-react';
import { api, apiBlob, userMessage } from '../api';
import { Badge, Button, Input, PageHeading, Spinner, Textarea, type Notice } from '../components';
import type { Settings, WhatsAppStatus } from '../types';

type Props = { notify: (notice: Notice) => void };

const splitRecipients = (value: string) => value.split(/[\n,;]+/).map((item) => item.trim()).filter(Boolean);

export default function NotificationsPage({ notify }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingWhatsApp, setSavingWhatsApp] = useState(false);
  const [savingEmail, setSavingEmail] = useState(false);
  const [testing, setTesting] = useState<'email' | 'whatsapp' | null>(null);
  const [status, setStatus] = useState<WhatsAppStatus | null>(null);
  const [statusError, setStatusError] = useState('');
  const [qrUrl, setQrUrl] = useState('');
  const [refreshingQR, setRefreshingQR] = useState(false);
  const oldQrUrl = useRef('');
  const [whatsappRecipients, setWhatsappRecipients] = useState('');
  const [emailRecipients, setEmailRecipients] = useState('');
  const [smtpHost, setSmtpHost] = useState('');
  const [smtpPort, setSmtpPort] = useState('587');
  const [smtpUser, setSmtpUser] = useState('');
  const [smtpPass, setSmtpPass] = useState('');
  const [smtpFrom, setSmtpFrom] = useState('');
  const [smtpUseTls, setSmtpUseTls] = useState(true);

  const isMounted = useRef(true);

  const loadSettings = useCallback(async () => {
    try {
      const result = await api<Settings>('/settings');
      if (!isMounted.current) return;
      setSettings(result);
      setWhatsappRecipients(result.whatsapp_recipients.join('\n'));
      setEmailRecipients(result.email_recipients.join('\n'));
      setSmtpHost(result.smtp_host);
      setSmtpPort(String(result.smtp_port));
      setSmtpUser(result.smtp_user);
      setSmtpFrom(result.smtp_from);
      setSmtpUseTls(result.smtp_use_tls);
    } catch (error) {
      if (isMounted.current) notify({ kind: 'error', message: userMessage(error) });
    } finally {
      if (isMounted.current) setLoading(false);
    }
  }, [notify]);

  const loadStatus = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await api<WhatsAppStatus>('/whatsapp/status', { signal });
      if (!isMounted.current) return null;
      setStatus(result);
      setStatusError('');
      return result;
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return null;
      if (!isMounted.current) return null;
      setStatus(null);
      setStatusError(userMessage(error));
      return null;
    }
  }, []);

  const loadQR = useCallback(async (quiet = true, signal?: AbortSignal) => {
    setRefreshingQR(true);
    try {
      const blob = await apiBlob('/whatsapp/qr', { signal });
      if (!isMounted.current) return;
      const nextUrl = URL.createObjectURL(blob);
      if (oldQrUrl.current) URL.revokeObjectURL(oldQrUrl.current);
      oldQrUrl.current = nextUrl;
      setQrUrl(nextUrl);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      if (!isMounted.current) return;
      if (oldQrUrl.current) URL.revokeObjectURL(oldQrUrl.current);
      oldQrUrl.current = '';
      setQrUrl('');
      if (!quiet) notify({ kind: 'error', message: userMessage(error) });
    } finally {
      if (isMounted.current) setRefreshingQR(false);
    }
  }, [notify]);

  useEffect(() => {
    isMounted.current = true;
    void loadSettings();
    const controller = new AbortController();

    const refresh = async () => {
      const current = await loadStatus(controller.signal);
      if (isMounted.current && current && !current.logged_in) {
        await loadQR(true, controller.signal);
      }
    };
    void refresh();
    const interval = window.setInterval(() => void refresh(), 9000);
    return () => {
      isMounted.current = false;
      controller.abort();
      window.clearInterval(interval);
      if (oldQrUrl.current) {
        URL.revokeObjectURL(oldQrUrl.current);
        oldQrUrl.current = '';
      }
    };
  }, [loadSettings, loadStatus, loadQR]);

  async function saveWhatsApp(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSavingWhatsApp(true);
    try {
      const result = await api<Settings>('/settings', { method: 'PUT', body: JSON.stringify({ whatsapp_recipients: splitRecipients(whatsappRecipients) }) });
      setSettings(result);
      setWhatsappRecipients(result.whatsapp_recipients.join('\n'));
      notify({ kind: 'success', message: 'Получатели WhatsApp сохранены.' });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setSavingWhatsApp(false);
    }
  }

  async function saveEmail(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSavingEmail(true);
    try {
      const result = await api<Settings>('/settings', {
        method: 'PUT',
        body: JSON.stringify({
          email_recipients: splitRecipients(emailRecipients),
          smtp_host: smtpHost.trim(),
          smtp_port: Number(smtpPort),
          smtp_user: smtpUser.trim(),
          smtp_pass: smtpPass,
          smtp_from: smtpFrom.trim(),
          smtp_use_tls: smtpUseTls,
        }),
      });
      setSettings(result);
      setEmailRecipients(result.email_recipients.join('\n'));
      setSmtpPass('');
      notify({ kind: 'success', message: 'Настройки SMTP и адреса сохранены.' });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setSavingEmail(false);
    }
  }

  async function testChannel(channel: 'email' | 'whatsapp') {
    setTesting(channel);
    try {
      const result = await api<{ channel: string; sent: number }>('/test-notification', { method: 'POST', body: JSON.stringify({ channel }) });
      notify({ kind: 'success', message: `Тест отправлен (${result.sent}). Проверьте получателей.` });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setTesting(null);
    }
  }

  async function disconnectWhatsApp() {
    if (!window.confirm('Вы действительно хотите отключить привязанный номер WhatsApp? Уведомления перестанут поступать до повторного сканирования QR-кода.')) {
      return;
    }
    try {
      await api<{ status: string }>('/whatsapp/logout', { method: 'POST' });
      notify({ kind: 'success', message: 'Сессия WhatsApp завершена. Можно подключить другой номер по QR-коду.' });
      await new Promise((resolve) => window.setTimeout(resolve, 900));
      const current = await loadStatus();
      if (current && !current.logged_in) await loadQR(false);
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    }
  }

  return (
    <div className="animate-enter">
      <PageHeading eyebrow="Каналы оповещений" title="WhatsApp и Email" description="Подключите WhatsApp и настройте получателей уведомлений о новых публикациях." />

      <section className="mb-5 rounded-2xl border border-[#e5e9ea] bg-white p-5 shadow-card sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#e8f5e9] text-[#378354]"><MessageCircle className="h-5 w-5" /></div>
            <div><h2 className="font-bold text-ink">Подключение WhatsApp</h2><p className="mt-1 text-sm text-muted">Отсканируйте QR-код приложением WhatsApp на телефоне.</p></div>
          </div>
          {status?.logged_in ? <Badge tone={status.connected ? 'green' : 'amber'}><span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-current" />{status.connected ? 'Подключён' : 'Переподключается'}</Badge> : <Badge tone={statusError ? 'red' : 'amber'}><span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-current" />{statusError ? 'Недоступен' : 'Не подключён'}</Badge>}
        </div>
        {statusError && <div className="mt-4 rounded-xl bg-[#fff7f5] px-4 py-3 text-sm text-[#98473b]">Не удалось получить статус: {statusError}</div>}
        <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(230px,.72fr)]">
          <div className="flex min-h-[245px] flex-col items-center justify-center rounded-2xl border border-[#e7ebec] bg-[#fafbfb] p-5 text-center">
            {status?.logged_in ? <>
              <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-[#e7f5e9] text-[#3c8a59]"><Wifi className="h-5 w-5" /></div>
              <p className="font-semibold text-ink">WhatsApp привязан</p>
              <p className="mt-1 text-sm text-muted">{status.phone ? `Номер +${status.phone}` : 'Сессия активна'}</p>
              <Button variant="danger" className="mt-5" onClick={() => void disconnectWhatsApp()}><WifiOff className="h-4 w-4" />Отключить номер</Button>
            </> : qrUrl ? <>
              <img src={qrUrl} alt="QR-код для подключения WhatsApp" className="h-48 w-48 rounded-lg bg-white p-2 shadow-sm" />
              <p className="mt-3 text-xs text-muted">WhatsApp → Связанные устройства → Привязать устройство</p>
            </> : <>
              <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-[#edf1f2] text-[#6b7a80]"><QrCode className="h-5 w-5" /></div>
              <p className="font-semibold text-ink">Ожидаем QR-код</p>
              <p className="mt-1 max-w-xs text-sm leading-5 text-muted">{statusError ? 'Проверьте, что WhatsApp Bridge запущен.' : 'Код появится автоматически, как только мост будет готов.'}</p>
            </>}
            {!status?.logged_in && <Button variant="secondary" className="mt-4 min-h-9" onClick={() => void loadQR(false)} disabled={refreshingQR}><RefreshCw className={`h-3.5 w-3.5 ${refreshingQR ? 'animate-spin' : ''}`} />Обновить QR-код</Button>}
          </div>
          <form className="flex flex-col" onSubmit={(event) => void saveWhatsApp(event)}>
            <Textarea label="Номера для уведомлений" value={whatsappRecipients} onChange={(event) => setWhatsappRecipients(event.target.value)} placeholder="77011234567&#10;77027654321" />
            <p className="mt-2 text-xs leading-5 text-muted">По одному номеру с кодом страны на строку. Допускаются пробелы и знаки +, скобки и дефисы.</p>
            <div className="mt-auto flex flex-wrap gap-2 pt-4">
              <Button type="submit" disabled={savingWhatsApp}>{savingWhatsApp && <Spinner small label="" />}Сохранить номера</Button>
              <Button variant="secondary" onClick={() => void testChannel('whatsapp')} disabled={testing !== null || (settings?.whatsapp_recipients.length ?? 0) === 0}><Send className="h-4 w-4" />{testing === 'whatsapp' ? 'Отправляем…' : 'Отправить тест'}</Button>
            </div>
          </form>
        </div>
      </section>

      <section className="rounded-2xl border border-[#e5e9ea] bg-white p-5 shadow-card sm:p-6">
        <div className="mb-5 flex items-start gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#eaf0f8] text-[#5578a3]"><Mail className="h-5 w-5" /></div>
          <div><h2 className="font-bold text-ink">Email через SMTP</h2><p className="mt-1 text-sm text-muted">Настройте сервер исходящей почты и адреса для уведомлений.</p></div>
        </div>
        {loading ? <div className="flex min-h-44 items-center justify-center"><Spinner label="Загружаем настройки…" /></div> : (
          <form onSubmit={(event) => void saveEmail(event)}>
            <div className="grid gap-4 sm:grid-cols-2">
              <Input label="SMTP сервер" value={smtpHost} onChange={(event) => setSmtpHost(event.target.value)} placeholder="smtp.example.com" autoComplete="off" />
              <Input label="Порт" value={smtpPort} onChange={(event) => setSmtpPort(event.target.value)} type="number" min={1} max={65535} required />
              <Input label="Логин" value={smtpUser} onChange={(event) => setSmtpUser(event.target.value)} autoComplete="username" />
              <Input label="Пароль" value={smtpPass} onChange={(event) => setSmtpPass(event.target.value)} type="password" placeholder={settings?.smtp_pass_configured ? 'Пароль сохранён · пустое поле оставит его без изменений' : 'Введите пароль приложения'} autoComplete="new-password" />
              <Input label="Адрес отправителя" type="email" value={smtpFrom} onChange={(event) => setSmtpFrom(event.target.value)} placeholder="alerts@example.com" />
              <Textarea label="Получатели" value={emailRecipients} onChange={(event) => setEmailRecipients(event.target.value)} placeholder={'team@example.com\nowner@example.com'} className="min-h-[76px]" />
            </div>
            <label className="mt-4 inline-flex cursor-pointer items-center gap-2.5 text-sm text-[#526168]"><input type="checkbox" className="h-4 w-4 accent-[#79a93c]" checked={smtpUseTls} onChange={(event) => setSmtpUseTls(event.target.checked)} /><ShieldCheck className="h-4 w-4 text-[#7a8c65]" />Использовать STARTTLS</label>
            <div className="mt-5 flex flex-wrap gap-2 border-t border-[#edf0f1] pt-4">
              <Button type="submit" disabled={savingEmail}>{savingEmail && <Spinner small label="" />}Сохранить настройки</Button>
              <Button variant="secondary" onClick={() => void testChannel('email')} disabled={testing !== null || (settings?.email_recipients.length ?? 0) === 0}><Send className="h-4 w-4" />{testing === 'email' ? 'Отправляем…' : 'Отправить тестовый Email'}</Button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
