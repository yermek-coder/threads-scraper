import { Suspense, useCallback, useEffect, useState } from 'react';
import { Bell, ChevronDown, CircleHelp, Hash, LogOut, Menu, MessageSquareText, RadioTower, Settings2, ShieldCheck, X } from 'lucide-react';
import { api, clearToken, getToken } from './api';
import { ErrorBoundary, NoticeBar, Spinner, type Notice } from './components';
import FeedPage from './pages/FeedPage';
import KeywordsPage from './pages/KeywordsPage';
import LoginPage from './pages/LoginPage';
import NotificationsPage from './pages/NotificationsPage';
import ThreadsSettingsPage from './pages/ThreadsSettingsPage';
import type { User } from './types';

type Page = 'feed' | 'keywords' | 'notifications' | 'threads';

const navItems: { id: Page; label: string; icon: typeof MessageSquareText; section: string }[] = [
  { id: 'feed', label: 'Лента упоминаний', icon: MessageSquareText, section: 'МОНИТОРИНГ' },
  { id: 'keywords', label: 'Ключевые слова', icon: Hash, section: 'МОНИТОРИНГ' },
  { id: 'notifications', label: 'Уведомления', icon: Bell, section: 'ИНТЕГРАЦИИ' },
  { id: 'threads', label: 'Threads API', icon: Settings2, section: 'ИНТЕГРАЦИИ' },
];

function Dashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [page, setPage] = useState<Page>('feed');
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const notify = useCallback((next: Notice) => setNotice(next), []);

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(null), 6500);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  const currentLabel = navItems.find((item) => item.id === page)?.label ?? 'Обзор';
  const sections = [...new Set(navItems.map((item) => item.section))];

  function selectPage(target: Page) {
    setPage(target);
    setMobileNavOpen(false);
  }

  return (
    <div className="min-h-screen bg-canvas lg:flex">
      {mobileNavOpen && <button type="button" aria-label="Закрыть меню" className="fixed inset-0 z-30 bg-[#10191d]/45 lg:hidden" onClick={() => setMobileNavOpen(false)} />}
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-[270px] flex-col bg-[#172126] text-white transition-transform duration-200 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${mobileNavOpen ? 'translate-x-0' : '-translate-x-full'}`} aria-label="Навигация">
        <div className="flex h-[76px] items-center justify-between border-b border-white/[.08] px-5">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#c5f36a] text-ink"><RadioTower className="h-[18px] w-[18px]" /></div>
            <div><div className="text-xs font-extrabold tracking-[.11em]">QADAM</div><div className="mt-0.5 text-[9px] font-semibold tracking-[.19em] text-white/40">BRAND MONITOR</div></div>
          </div>
          <button className="rounded-lg p-2 text-white/50 hover:bg-white/10 hover:text-white lg:hidden" onClick={() => setMobileNavOpen(false)} aria-label="Закрыть меню"><X className="h-4 w-4" /></button>
        </div>

        <div className="px-3 pt-7">
          {sections.map((section) => <div key={section} className="mb-6">
            <div className="mb-2 px-3 text-[9px] font-bold tracking-[.19em] text-white/35">{section}</div>
            <nav className="grid gap-1">
              {navItems.filter((item) => item.section === section).map(({ id, label, icon: Icon }) => (
                <button key={id} type="button" onClick={() => selectPage(id)} aria-current={page === id ? 'page' : undefined} className={`group flex h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-[13px] font-medium transition ${page === id ? 'bg-white/[.09] text-white' : 'text-white/55 hover:bg-white/[.055] hover:text-white/85'}`}>
                  <Icon className={`h-[17px] w-[17px] ${page === id ? 'text-[#c5f36a]' : 'text-white/45 group-hover:text-white/75'}`} aria-hidden="true" />
                  {label}
                  {page === id && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-[#c5f36a]" />}
                </button>
              ))}
            </nav>
          </div>)}
        </div>

        <div className="mt-auto px-3 pb-4">
          <div className="mb-3 rounded-xl border border-white/[.08] bg-white/[.035] p-3.5">
            <div className="flex items-center gap-2 text-[11px] font-semibold text-white/80"><ShieldCheck className="h-3.5 w-3.5 text-[#c5f36a]" />Приватное пространство</div>
            <p className="mt-1.5 pl-[22px] text-[10px] leading-4 text-white/40">Публикации видны только вашей команде.</p>
          </div>
          <div className="flex items-center gap-3 rounded-xl px-2 py-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#344449] text-xs font-bold text-[#d5efb1]">{user.username.slice(0, 1).toUpperCase()}</div>
            <div className="min-w-0 flex-1"><div className="truncate text-xs font-semibold text-white/90">{user.username}</div><div className="mt-0.5 text-[10px] text-white/40">Администратор</div></div>
            <button type="button" onClick={onLogout} title="Выйти" aria-label="Выйти из системы" className="rounded-lg p-2 text-white/40 transition hover:bg-white/10 hover:text-white"><LogOut className="h-4 w-4" /></button>
          </div>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex h-[68px] items-center justify-between border-b border-[#e7ebec] bg-white/90 px-4 backdrop-blur-md sm:px-7 lg:px-10">
          <div className="flex min-w-0 items-center gap-3">
            <button type="button" className="-ml-2 rounded-lg p-2 text-[#647278] hover:bg-[#f0f2f2] lg:hidden" aria-label="Открыть меню" onClick={() => setMobileNavOpen(true)}><Menu className="h-5 w-5" /></button>
            <div className="truncate text-sm text-[#869196]"><span className="hidden sm:inline">Мониторинг&nbsp; / &nbsp;</span><span className="font-semibold text-ink">{currentLabel}</span></div>
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden items-center gap-2 rounded-full border border-[#e7ebec] bg-[#fafbfb] px-3 py-1.5 text-[11px] font-medium text-[#718087] sm:flex"><span className="h-1.5 w-1.5 rounded-full bg-[#8bb64e]" />Панель готова</div>
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#edf1e8] text-xs font-bold text-[#597936]">{user.username.slice(0, 1).toUpperCase()}</div>
            <span className="hidden max-w-[120px] truncate text-xs font-semibold text-[#48575d] md:block">{user.username}</span>
            <ChevronDown className="hidden h-3.5 w-3.5 text-[#a0a9ad] md:block" />
          </div>
        </header>
        <main className="mx-auto max-w-[1360px] px-4 py-6 sm:px-7 sm:py-8 lg:px-10 lg:py-9">
          {notice && <div className="mb-5"><NoticeBar notice={notice} onDismiss={() => setNotice(null)} /></div>}
          <Suspense fallback={<div className="flex min-h-72 items-center justify-center"><Spinner label="Открываем раздел…" /></div>}>
            {page === 'feed' && <FeedPage notify={notify} onGoToKeywords={() => selectPage('keywords')} />}
            {page === 'keywords' && <KeywordsPage notify={notify} />}
            {page === 'notifications' && <NotificationsPage notify={notify} />}
            {page === 'threads' && <ThreadsSettingsPage notify={notify} />}
          </Suspense>
          <footer className="mt-9 flex flex-wrap items-center justify-between gap-3 border-t border-[#e5e9ea] pt-4 text-[10px] text-[#a1aaad]">
            <span>Example Organization · Threads Brand Monitoring</span>
            <span className="inline-flex items-center gap-1.5"><CircleHelp className="h-3 w-3" />Если что-то не работает, проверьте настройки интеграции.</span>
          </footer>
        </main>
      </div>
    </div>
  );
}

function AppContent() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    const unauthorized = () => {
      clearToken();
      setUser(null);
    };
    window.addEventListener('threads:unauthorized', unauthorized);
    if (!getToken()) {
      setChecking(false);
    } else {
      void api<User>('/auth/me').then(setUser).catch(() => clearToken()).finally(() => setChecking(false));
    }
    return () => window.removeEventListener('threads:unauthorized', unauthorized);
  }, []);

  function logout() {
    clearToken();
    setUser(null);
  }

  if (checking) return <main className="grid min-h-screen place-items-center bg-canvas"><Spinner label="Проверяем сессию…" /></main>;
  if (!user) return <LoginPage onLogin={setUser} />;
  return <Dashboard user={user} onLogout={logout} />;
}

export default function App() {
  return <ErrorBoundary><AppContent /></ErrorBoundary>;
}
