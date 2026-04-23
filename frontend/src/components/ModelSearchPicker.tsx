import React, { useState, useRef, useCallback, useMemo, useEffect } from 'react'
import { Command } from 'cmdk'
import { AlertTriangle, Clock, X } from 'lucide-react'
import type { ModelProviderGroup, ModelStatus, ProviderProbeStatus } from '../api/client'
import ToggleChip from './ToggleChip'

interface ModelSearchPickerProps {
  groups: ModelProviderGroup[]
  selected: string[]
  onChange: (ids: string[]) => void
  error?: string
  label?: string
  allowUnavailableDefault?: boolean
}

// Build a flat lookup: model id → { display_name, status }
function buildModelMap(
  groups: ModelProviderGroup[],
): Map<string, { display_name: string | null; status: ModelStatus }> {
  const map = new Map<string, { display_name: string | null; status: ModelStatus }>()
  for (const g of groups) {
    for (const m of g.models) {
      map.set(m.id, { display_name: m.display_name, status: m.status })
    }
  }
  return map
}

function displayName(id: string, display_name: string | null): string {
  return display_name ?? id
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// --- Sub-components ---

function formatRelativeTime(fetchedAt: string | null): string {
  if (!fetchedAt) return 'never probed'
  const ageMs = Date.now() - Date.parse(fetchedAt)
  const ageMin = Math.floor(ageMs / 60_000)
  if (ageMin < 1) return 'last probed just now'
  if (ageMin === 1) return 'last probed 1 min ago'
  return `last probed ${ageMin} min ago`
}

function ProbeStatusIndicator({
  status,
  fetchedAt,
}: {
  status: ProviderProbeStatus
  fetchedAt: string | null
}) {
  if (status === 'fresh') {
    return (
      <span
        className="text-xs text-gray-400 dark:text-gray-500"
        data-testid="probe-timestamp"
      >
        {formatRelativeTime(fetchedAt)}
      </span>
    )
  }

  if (status === 'stale') {
    return (
      <span className="inline-flex items-center gap-1">
        <span
          title="Last probe stale — results may be out of date"
          className="inline-flex items-center text-amber-500 dark:text-amber-400"
          aria-label="stale probe"
          data-testid="probe-stale"
        >
          <Clock size={12} />
        </span>
        <span
          className="text-xs text-gray-400 dark:text-gray-500"
          data-testid="probe-timestamp"
        >
          {formatRelativeTime(fetchedAt)}
        </span>
      </span>
    )
  }

  if (status === 'failed') {
    return (
      <span className="inline-flex items-center gap-1">
        <span
          title="Catalog probe failed — showing last known state"
          className="inline-flex items-center text-red-500 dark:text-red-400"
          aria-label="probe failed"
          data-testid="probe-failed"
        >
          <AlertTriangle size={12} />
        </span>
        <span
          className="text-xs text-gray-400 dark:text-gray-500"
          data-testid="probe-timestamp"
        >
          {formatRelativeTime(fetchedAt)}
        </span>
      </span>
    )
  }

  if (status === 'disabled') {
    return (
      <span
        className="text-xs text-gray-400 dark:text-gray-500"
        data-testid="probe-disabled"
      >
        (live probing off)
      </span>
    )
  }

  return null
}

function ModelStatusBadge({ status }: { status: ModelStatus }) {
  if (status === 'available') {
    return (
      <span
        className="inline-block w-2 h-2 rounded-full bg-green-500 dark:bg-green-400"
        aria-label="available"
      />
    )
  }
  const text: Record<Exclude<ModelStatus, 'available'>, string> = {
    key_missing: 'no key',
    not_listed: 'not listed',
    probe_failed: 'probe failed',
  }
  return (
    <span className="text-xs text-gray-400 dark:text-gray-500">
      {text[status as Exclude<ModelStatus, 'available'>]}
    </span>
  )
}

function Pill({
  id,
  displayName: name,
  missing,
  onRemove,
}: {
  id: string
  displayName: string
  missing: boolean
  onRemove: (id: string) => void
}) {
  return (
    <span
      className={[
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-mono border',
        missing
          ? 'border-amber-400 text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950'
          : 'border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-800',
      ].join(' ')}
    >
      {missing && (
        <span title="model no longer available" aria-label="model no longer available">
          <AlertTriangle size={10} className="text-amber-500" />
        </span>
      )}
      {name}
      <button
        type="button"
        onClick={() => onRemove(id)}
        aria-label={`Remove ${name}`}
        className="ml-0.5 hover:text-red-500 dark:hover:text-red-400 transition-colors"
      >
        <X size={10} />
      </button>
    </span>
  )
}

// --- Main component ---

export default function ModelSearchPicker({
  groups,
  selected,
  onChange,
  error,
  label = 'Models',
  allowUnavailableDefault = false,
}: ModelSearchPickerProps): React.ReactElement {
  const [search, setSearch] = useState('')
  const [showUnavailable, setShowUnavailable] = useState(allowUnavailableDefault)
  const inputRef = useRef<HTMLInputElement>(null)

  // Sync showUnavailable when the parent flips allowUnavailableDefault from false → true.
  // Only ratchet upward: once the user manually hides unavailables we don't force them
  // back, but if the parent enables the override we surface them automatically.
  useEffect(() => {
    if (allowUnavailableDefault) {
      setShowUnavailable(true)
    }
  }, [allowUnavailableDefault])

  const modelMap = useMemo(() => buildModelMap(groups), [groups])
  const selectedSet = useMemo(() => new Set(selected), [selected])

  const toggle = useCallback(
    (id: string) => {
      if (selectedSet.has(id)) {
        onChange(selected.filter((s) => s !== id))
      } else {
        onChange([...selected, id])
      }
    },
    [selected, selectedSet, onChange],
  )

  const removeLast = useCallback(() => {
    if (selected.length > 0) {
      onChange(selected.slice(0, -1))
    }
  }, [selected, onChange])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Backspace' && search === '') {
        removeLast()
      }
    },
    [search, removeLast],
  )

  // Filter groups: apply search across id, display_name, provider name
  const searchLower = search.toLowerCase()

  const filteredGroups = useMemo(() => {
    return groups
      .map((group) => {
        const providerMatch = group.provider.toLowerCase().includes(searchLower)
        const filteredModels = group.models.filter((m) => {
          if (searchLower) {
            const nameMatch = (m.display_name ?? m.id).toLowerCase().includes(searchLower)
            const idMatch = m.id.toLowerCase().includes(searchLower)
            if (!providerMatch && !nameMatch && !idMatch) return false
          }
          // Hide unavailable unless showUnavailable or selected
          if (!showUnavailable && m.status !== 'available' && !selectedSet.has(m.id)) {
            return false
          }
          return true
        })
        return { group, filteredModels }
      })
      .filter(({ group, filteredModels }) => {
        // Drop groups with no models at all
        if (group.models.length === 0) return false
        // Keep groups that have filtered models OR all models are key_missing (for placeholder)
        return filteredModels.length > 0 || group.models.every((m) => m.status === 'key_missing')
      })
  }, [groups, searchLower, showUnavailable, selectedSet])

  const selectedCount = selected.length

  return (
    <div className="flex flex-col gap-2">
      {/* Label */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">{label}</span>
        <ToggleChip
          label="Show unavailable"
          checked={showUnavailable}
          onChange={setShowUnavailable}
        />
      </div>

      {/* Pill tray */}
      {selected.length > 0 && (
        <div
          className="flex flex-wrap gap-1.5"
          aria-live="polite"
          aria-label={`${selectedCount} model${selectedCount !== 1 ? 's' : ''} selected`}
        >
          {selected.map((id) => {
            const info = modelMap.get(id)
            return (
              <Pill
                key={id}
                id={id}
                displayName={info ? displayName(id, info.display_name) : id}
                missing={!info}
                onRemove={(rid) => onChange(selected.filter((s) => s !== rid))}
              />
            )
          })}
        </div>
      )}

      {/* cmdk Command */}
      <div
        className={[
          'rounded-md border bg-white dark:bg-gray-900',
          error
            ? 'border-red-500 dark:border-red-400'
            : 'border-gray-200 dark:border-gray-700',
        ].join(' ')}
      >
        <Command shouldFilter={false}>
          <Command.Input
            ref={inputRef}
            value={search}
            onValueChange={setSearch}
            onKeyDown={handleKeyDown}
            placeholder="Search models…"
            aria-label={label}
            className={[
              'w-full px-3 py-2 text-sm bg-transparent outline-none',
              'text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500',
              'border-b border-gray-200 dark:border-gray-700',
            ].join(' ')}
          />

          <Command.List className="max-h-72 overflow-y-auto">
            {filteredGroups.map(({ group, filteredModels }) => {
              const allKeyMissing =
                filteredModels.length === 0 && group.models.every((m) => m.status === 'key_missing')

              return (
                <Command.Group
                  key={group.provider}
                  heading={
                    <div className="flex flex-col px-3 py-1.5 sticky top-0 bg-gray-50 dark:bg-gray-800 border-b border-gray-100 dark:border-gray-700">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                          {capitalize(group.provider)}
                        </span>
                        <ProbeStatusIndicator
                          status={group.probe_status}
                          fetchedAt={group.fetched_at ?? null}
                        />
                      </div>
                      {group.last_error && (
                        <span
                          className="text-xs text-gray-400 dark:text-gray-500 truncate mt-0.5"
                          data-testid="probe-last-error"
                          title={group.last_error}
                        >
                          {group.last_error}
                        </span>
                      )}
                    </div>
                  }
                >
                  {allKeyMissing ? (
                    <div className="px-3 py-2 text-xs text-gray-400 dark:text-gray-500 italic">
                      No {capitalize(group.provider)} key configured — set{' '}
                      {group.provider.toUpperCase()}_API_KEY to see models here.
                    </div>
                  ) : (
                    filteredModels.map((model) => {
                      const isSelected = selectedSet.has(model.id)
                      const name = displayName(model.id, model.display_name)
                      const showId = name !== model.id
                      return (
                        <Command.Item
                          key={model.id}
                          value={`${group.provider} ${model.id} ${model.display_name ?? ''}`}
                          onSelect={() => toggle(model.id)}
                          className={[
                            'flex items-center justify-between px-3 py-2 text-sm cursor-pointer',
                            'hover:bg-indigo-50 dark:hover:bg-indigo-950/40',
                            'aria-selected:bg-indigo-50 dark:aria-selected:bg-indigo-950/40',
                            isSelected
                              ? 'text-indigo-700 dark:text-indigo-300 font-medium'
                              : 'text-gray-800 dark:text-gray-200',
                          ].join(' ')}
                          data-selected={isSelected}
                        >
                          <span className="flex flex-col min-w-0">
                            <span className="truncate">{name}</span>
                            {showId && (
                              <span className="text-xs text-gray-400 dark:text-gray-500 font-mono truncate">
                                {model.id}
                              </span>
                            )}
                          </span>
                          <span className="ml-2 flex items-center gap-1.5 shrink-0">
                            {isSelected && (
                              <span className="text-xs text-indigo-600 dark:text-indigo-400">✓</span>
                            )}
                            <ModelStatusBadge status={model.status} />
                          </span>
                        </Command.Item>
                      )
                    })
                  )}
                </Command.Group>
              )
            })}

            {filteredGroups.length === 0 && (
              <div className="px-3 py-6 text-center text-sm text-gray-400 dark:text-gray-500">
                No models match your search.
              </div>
            )}
          </Command.List>
        </Command>
      </div>

      {/* Error message */}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
    </div>
  )
}
