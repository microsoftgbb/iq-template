import type { Session } from '../live/types.ts'

interface HeaderProps {
  session?: Session
  presenting: boolean
  onSignIn: () => void
  onSignOut: () => void
  onTogglePresenter: () => void
}

function Logo() {
  return (
    <svg className="logo" width="28" height="28" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect x="2" y="2" width="13" height="13" rx="3" className="logo-fabric" />
      <rect x="17" y="2" width="13" height="13" rx="3" className="logo-foundry" />
      <rect x="2" y="17" width="13" height="13" rx="3" className="logo-web" />
      <rect x="17" y="17" width="13" height="13" rx="3" className="logo-work" />
    </svg>
  )
}

export function Header({ session, presenting, onSignIn, onSignOut, onTogglePresenter }: HeaderProps) {
  return (
    <header className="topbar">
      <div className="brand">
        <Logo />
        <h1>Scenario assistant</h1>
        {session?.notice && <span className="badge">{session.notice}</span>}
      </div>
      <div className="account">
        <button type="button" className="quiet" aria-pressed={presenting} onClick={onTogglePresenter}>Presenter mode</button>
        {session?.mode === 'live' && (session.identity ? (
          <>
            <span className="user">{session.identity.name}</span>
            <button type="button" className="secondary" onClick={onSignOut}>Sign out</button>
          </>
        ) : (
          <button type="button" className="primary" onClick={onSignIn}>Sign in</button>
        ))}
      </div>
    </header>
  )
}
