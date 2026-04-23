// Shared pill-chip styling for ToggleChip and the CVE-Discovery "Any" button.
// Keep the two controls visually unified — extracted so future tweaks apply
// to both in one place.
export const CHIP_BASE =
  'relative inline-flex items-center gap-1.5 select-none text-xs px-2 py-0.5 rounded-full border transition-colors'
export const CHIP_ACTIVE = 'bg-amber-600 border-amber-600 text-white hover:bg-amber-700'
export const CHIP_INACTIVE =
  'border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:border-amber-500 hover:text-amber-600 dark:hover:text-amber-400'

export function chipClasses(checked: boolean, disabled?: boolean): string {
  return [
    CHIP_BASE,
    'focus-within:ring-2 focus-within:ring-amber-500 focus-within:ring-offset-1',
    disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
    checked ? CHIP_ACTIVE : CHIP_INACTIVE,
  ].join(' ')
}

export default function ToggleChip({
  label,
  checked,
  onChange,
  disabled,
  value,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  value?: string
}) {
  return (
    <label data-value={value} className={chipClasses(checked, disabled)}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
      />
      <span className="pointer-events-none font-mono">{label}</span>
    </label>
  )
}
