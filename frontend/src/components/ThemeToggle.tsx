import { useTheme } from '../hooks/useTheme'

export default function ThemeToggle() {
  const { isDark, toggle } = useTheme()

  return (
    <button
      onClick={toggle}
      title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      className="font-mono text-xs tracking-wider uppercase px-2 py-1 rounded hover:bg-muted transition-colors"
    >
      <span className={isDark ? 'text-muted-foreground' : 'text-primary'}>[ LIGHT ]</span>
      {' '}
      <span className={isDark ? 'text-primary' : 'text-muted-foreground'}>[ DARK ]</span>
    </button>
  )
}
