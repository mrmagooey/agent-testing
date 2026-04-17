import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import PromptInjectionViewer from '../../components/PromptInjectionViewer'
import type { PromptSnapshot } from '../../api/client'

function makeSnapshot(overrides: Partial<PromptSnapshot> = {}): PromptSnapshot {
  return {
    system_prompt: 'You are a security reviewer.',
    user_message_template: 'Review this codebase for vulnerabilities.',
    finding_output_format: 'Output as JSON.',
    ...overrides,
  }
}

describe('PromptInjectionViewer', () => {
  describe('no-injection path', () => {
    it('renders system prompt and user message panes when no injection', () => {
      render(<PromptInjectionViewer promptSnapshot={makeSnapshot()} />)

      expect(screen.getByTestId('system-prompt-pane')).toBeInTheDocument()
      expect(screen.getByTestId('user-message-pane')).toBeInTheDocument()
    })

    it('shows clean prompt text in panes when no injection', () => {
      render(<PromptInjectionViewer promptSnapshot={makeSnapshot()} />)

      expect(screen.getByText('You are a security reviewer.')).toBeInTheDocument()
      expect(screen.getByText('Review this codebase for vulnerabilities.')).toBeInTheDocument()
    })

    it('shows no-injection note when clean_prompt present but injected_prompt is null', () => {
      const snapshot = makeSnapshot({
        clean_prompt: 'Review the code.',
        injected_prompt: null,
      })
      render(<PromptInjectionViewer promptSnapshot={snapshot} />)

      expect(screen.getByText(/No injection applied for this run/)).toBeInTheDocument()
    })

    it('shows profile modifier banner when review_profile_modifier present', () => {
      const snapshot = makeSnapshot({ review_profile_modifier: 'Focus on OWASP Top 10.' })
      render(<PromptInjectionViewer promptSnapshot={snapshot} />)

      expect(screen.getByText(/Profile modifier active/)).toBeInTheDocument()
    })
  })

  describe('with-injection path', () => {
    it('renders clean-prompt-pane and injected-prompt-pane when both prompts present', () => {
      const snapshot = makeSnapshot({
        clean_prompt: 'Line one\nLine two\nLine three',
        injected_prompt: 'Line one\nINJECTED LINE\nLine two\nLine three',
        injection_template_id: 'test-template',
      })
      render(<PromptInjectionViewer promptSnapshot={snapshot} />)

      expect(screen.getByTestId('clean-prompt-pane')).toBeInTheDocument()
      expect(screen.getByTestId('injected-prompt-pane')).toBeInTheDocument()
    })

    it('shows injection template id in banner', () => {
      const snapshot = makeSnapshot({
        clean_prompt: 'Line one',
        injected_prompt: 'Line one\nExtra line',
        injection_template_id: 'sqli-injection-v2',
      })
      render(<PromptInjectionViewer promptSnapshot={snapshot} />)

      expect(screen.getByText('sqli-injection-v2')).toBeInTheDocument()
    })

    it('shows injection applied banner when injection is present', () => {
      const snapshot = makeSnapshot({
        clean_prompt: 'Base prompt text.',
        injected_prompt: 'Base prompt text.\nAdded line.',
        injection_template_id: 'my-template',
      })
      render(<PromptInjectionViewer promptSnapshot={snapshot} />)

      expect(screen.getByText(/Injection applied/)).toBeInTheDocument()
    })

    it('renders prompt-panes wrapper', () => {
      const snapshot = makeSnapshot({
        clean_prompt: 'clean',
        injected_prompt: 'clean\ninjected',
        injection_template_id: 'tmpl',
      })
      render(<PromptInjectionViewer promptSnapshot={snapshot} />)

      expect(screen.getByTestId('prompt-panes')).toBeInTheDocument()
    })
  })
})
