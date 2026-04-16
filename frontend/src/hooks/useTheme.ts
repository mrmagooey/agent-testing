import { useState, useEffect } from 'react'

function getInitialDark(): boolean {
  try {
    const stored = localStorage.getItem('theme')
    if (stored === 'dark') return true
    if (stored === 'light') return false
  } catch {
    // localStorage not available
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export function useTheme(): { isDark: boolean; toggle: () => void } {
  const [isDark, setIsDark] = useState(getInitialDark)

  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
    try {
      localStorage.setItem('theme', isDark ? 'dark' : 'light')
    } catch {
      // ignore
    }
  }, [isDark])

  const toggle = () => setIsDark((prev) => !prev)

  return { isDark, toggle }
}
