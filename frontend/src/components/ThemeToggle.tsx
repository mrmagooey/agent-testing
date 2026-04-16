import { useTheme } from '../hooks/useTheme'

export default function ThemeToggle() {
  const { isDark, toggle } = useTheme()

  return (
    <button
      onClick={toggle}
      title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      className="rounded-full p-2 text-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
    >
      {isDark ? '☀️' : '🌙'}
    </button>
  )
}
