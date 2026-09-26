import { Suspense, useCallback, useEffect, useState } from 'react';
import { Bell, CircleHelp, Hash, KeyRound, Link2, LogOut, Menu, MessageSquareText, RadioTower, Sparkles, X } from 'lucide-react';
import { api, clearToken, getToken } from './api';
import { ErrorBoundary, NoticeBar, Spinner, type Notice } from './components';
import FeedPage from './pages/FeedPage';
import KeywordsPage from './pages/KeywordsPage';
import LoginPage from './pages/LoginPage';
import NotificationsPage from './pages/NotificationsPage';
import ThreadsSettingsPage, { type ThreadsSection } from './pages/ThreadsSettingsPage';
import type { User } from './types';

type Page = 'feed' | 'keywords' | 'notifications' | 'threads-collection' | 'threads-relevance' | 'threads-graph' | 'threads-webhook';

const pagePaths: Record<Page, string> = {
  feed: '/feed',
  keywords: '/keywords',
  notifications: '/notifications',
  'threads-collection': '/threads/collection',
  'threads-relevance': '/threads/relevance',
  'threads-graph': '/threads/graph-api',
  'threads-webhook': '/threads/webhook',
};

const routePages = Object.fromEntries(Object.entries(pagePaths).map(([page, path]) => [path, page])) as Record<string, Page>;
routePages['/'] = 'feed';

function pageFromLocation(): Page {
  if (new URLSearchParams(window.location.search).has('threads_oauth')) return 'threads-graph';
  return routePages[window.location.pathname.replace(/\/$/, '') || '/'] ?? 'feed';
}

const navItems: { id: Page; label: string; icon: typeof MessageSquareText; section: string }[] = [
  { id: 'feed', label: 'Лента упоминаний', icon: MessageSquareText, section: 'МОНИТОРИНГ' },
  { id: 'keywords', label: 'Ключевые слова', icon: Hash, section: 'МОНИТОРИНГ' },
  { id: 'notifications', label: 'Уведомления', icon: Bell, section: 'ИНТЕГРАЦИИ' },
  { id: 'threads-collection', label: 'Сбор публикаций', icon: RadioTower, section: 'THREADS API' },
  { id: 'threads-relevance', label: 'Релевантность', icon: Sparkles, section: 'THREADS API' },
  { id: 'threads-graph', label: 'Graph API', icon: KeyRound, section: 'THREADS API' },
  { id: 'threads-webhook', label: 'Webhook', icon: Link2, section: 'THREADS API' },
];

function Dashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [page, setPage] = useState<Page>(pageFromLocation);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const notify = useCallback((next: Notice) => setNotice(next), []);

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(null), 6500);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  useEffect(() => {
    const url = new URL(window.location.href);
    const oauthResult = url.searchParams.get('threads_oauth');
    if (!oauthResult) return;
    if (oauthResult === 'connected') {
      setNotice({ kind: 'success', message: 'Threads аккаунт подключён. Access Token и User ID сохранены.' });
    } else if (oauthResult === 'cancelled') {
      setNotice({ kind: 'error', message: 'Подключение Threads отменено в Meta.' });
    } else {
      setNotice({ kind: 'error', message: 'Не удалось подключить Threads. Проверьте callback URL и настройки Threads App.' });
    }
    url.searchParams.delete('threads_oauth');
    url.pathname = pagePaths['threads-graph'];
    window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
  }, []);

  useEffect(() => {
    const syncPage = () => setPage(pageFromLocation());
    window.addEventListener('popstate', syncPage);
    return () => window.removeEventListener('popstate', syncPage);
  }, []);

  const currentLabel = navItems.find((item) => item.id === page)?.label ?? 'Обзор';
  const sections = [...new Set(navItems.map((item) => item.section))];

  function selectPage(target: Page) {
    const nextPath = pagePaths[target];
    if (window.location.pathname !== nextPath || window.location.search) {
      window.history.pushState({}, '', nextPath);
    }
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
                <a key={id} href={pagePaths[id]} onClick={(event) => { if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); selectPage(id); }} aria-current={page === id ? 'page' : undefined} className={`group flex h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-[13px] font-medium transition ${page === id ? 'bg-white/[.09] text-white' : 'text-white/55 hover:bg-white/[.055] hover:text-white/85'}`}>
                  <Icon className={`h-[17px] w-[17px] ${page === id ? 'text-[#c5f36a]' : 'text-white/45 group-hover:text-white/75'}`} aria-hidden="true" />
                  {label}
                  {page === id && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-[#c5f36a]" />}
                </a>
              ))}
            </nav>
          </div>)}
        </div>

        <div className="mt-auto px-3 pb-4">
          <div className="flex items-center gap-3 rounded-xl px-2 py-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#344449] text-xs font-bold text-[#d5efb1]">{user.username.slice(0, 1).toUpperCase()}</div>
            <div className="min-w-0 flex-1"><div className="truncate text-xs font-semibold text-white/90">{user.username}</div><div className="mt-0.5 text-[10px] text-white/40">Администратор</div></div>
            <button type="button" onClick={onLogout} title="Выйти" aria-label="Выйти из системы" className="rounded-lg p-2 text-white/40 transition hover:bg-white/10 hover:text-white"><LogOut className="h-4 w-4" /></button>
          </div>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex h-[68px] items-center border-b border-[#e7ebec] bg-white/90 px-4 backdrop-blur-md sm:px-7 lg:px-10">
          <div className="flex min-w-0 items-center gap-3">
            <button type="button" className="-ml-2 rounded-lg p-2 text-[#647278] hover:bg-[#f0f2f2] lg:hidden" aria-label="Открыть меню" onClick={() => setMobileNavOpen(true)}><Menu className="h-5 w-5" /></button>
            <div className="truncate text-sm text-[#869196]"><span className="hidden sm:inline">{navItems.find((item) => item.id === page)?.section}&nbsp; / &nbsp;</span><span className="font-semibold text-ink">{currentLabel}</span></div>
          </div>
        </header>
        <main className="mx-auto max-w-[1360px] px-4 py-6 sm:px-7 sm:py-8 lg:px-10 lg:py-9">
          {notice && <div className="mb-5"><NoticeBar notice={notice} onDismiss={() => setNotice(null)} /></div>}
          <Suspense fallback={<div className="flex min-h-72 items-center justify-center"><Spinner label="Открываем раздел…" /></div>}>
            {page === 'feed' && <FeedPage notify={notify} onGoToKeywords={() => selectPage('keywords')} />}
            {page === 'keywords' && <KeywordsPage notify={notify} />}
            {page === 'notifications' && <NotificationsPage notify={notify} />}
            {page.startsWith('threads-') && <ThreadsSettingsPage notify={notify} section={page.slice('threads-'.length) as ThreadsSection} />}
          </Suspense>
          <footer className="mt-9 flex flex-wrap items-center justify-between gap-3 border-t border-[#e5e9ea] pt-4 text-[10px] text-[#a1aaad]">
            <span>Example Organization · Threads Brand Monitoring</span>
            <span className="inline-flex flex-wrap items-center gap-x-4 gap-y-2">
              <a href="/privacy-policy/" className="hover:text-ink">Конфиденциальность</a>
              <a href="/terms-of-service/" className="hover:text-ink">Условия</a>
              <a href="/data-deletion/" className="hover:text-ink">Удаление данных</a>
              <span className="inline-flex items-center gap-1.5"><CircleHelp className="h-3 w-3" />Проверьте настройки интеграции, если что-то не работает.</span>
            </span>
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
