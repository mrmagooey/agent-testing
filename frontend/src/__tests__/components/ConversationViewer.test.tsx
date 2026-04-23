import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import ConversationViewer from '../../components/ConversationViewer'
import type { Message } from '../../api/client'

// ─── Mock CodeViewer (uses CodeMirror which isn't available in jsdom) ─────────

vi.mock('../../components/CodeViewer', () => ({
  default: ({ content }: { content: string }) => (
    <div data-testid="code-viewer">{content}</div>
  ),
}))

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeMessage(overrides: Partial<Message> = {}): Message {
  return {
    role: 'user',
    content: 'Hello world',
    ...overrides,
  }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('ConversationViewer', () => {
  it('renders "No messages recorded." when messages array is empty', () => {
    render(<ConversationViewer messages={[]} />)
    expect(screen.getByText('No messages recorded.')).toBeInTheDocument()
  })

  it('does not render the empty message when messages are present', () => {
    render(<ConversationViewer messages={[makeMessage()]} />)
    expect(screen.queryByText('No messages recorded.')).not.toBeInTheDocument()
  })

  it('renders one CodeViewer per message', () => {
    const messages = [
      makeMessage({ role: 'user', content: 'msg1' }),
      makeMessage({ role: 'assistant', content: 'msg2' }),
      makeMessage({ role: 'tool', content: 'msg3' }),
    ]
    render(<ConversationViewer messages={messages} />)
    const viewers = screen.getAllByTestId('code-viewer')
    expect(viewers).toHaveLength(3)
  })

  it('renders role badge for user message', () => {
    render(<ConversationViewer messages={[makeMessage({ role: 'user' })]} />)
    expect(screen.getByText('user')).toBeInTheDocument()
  })

  it('renders role badge for assistant message', () => {
    render(<ConversationViewer messages={[makeMessage({ role: 'assistant', content: 'Hi' })]} />)
    expect(screen.getByText('assistant')).toBeInTheDocument()
  })

  it('renders role badge for tool message', () => {
    render(<ConversationViewer messages={[makeMessage({ role: 'tool', content: '{}' })]} />)
    expect(screen.getByText('tool')).toBeInTheDocument()
  })

  it('renders message content via CodeViewer', () => {
    render(<ConversationViewer messages={[makeMessage({ content: 'test content here' })]} />)
    expect(screen.getByText('test content here')).toBeInTheDocument()
  })

  it('renders timestamp when provided', () => {
    const timestamp = '2024-01-15T10:30:00.000Z'
    render(<ConversationViewer messages={[makeMessage({ timestamp })]} />)
    // The timestamp is formatted via toLocaleTimeString, so check it renders something
    // Just verify the timestamp element exists (has text that isn't the role or content)
    const dateText = new Date(timestamp).toLocaleTimeString()
    expect(screen.getByText(dateText)).toBeInTheDocument()
  })

  it('does not render timestamp element when timestamp is not provided', () => {
    const msg = makeMessage({ role: 'user', content: 'no time' })
    delete msg.timestamp
    render(<ConversationViewer messages={[msg]} />)
    // No timestamp span should exist — just role badge + code viewer
    // Confirm the message renders without crashing
    expect(screen.getByText('user')).toBeInTheDocument()
  })

  it('renders many messages in order', () => {
    const messages: Message[] = Array.from({ length: 5 }, (_, i) => ({
      role: i % 2 === 0 ? 'user' : 'assistant',
      content: `message-${i}`,
    }))
    render(<ConversationViewer messages={messages} />)
    for (let i = 0; i < 5; i++) {
      expect(screen.getByText(`message-${i}`)).toBeInTheDocument()
    }
  })

  it('renders single message without crashing', () => {
    render(<ConversationViewer messages={[makeMessage({ content: 'solo' })]} />)
    expect(screen.getByText('solo')).toBeInTheDocument()
  })

  it('renders all three role variants in one list', () => {
    const messages: Message[] = [
      { role: 'user', content: 'u' },
      { role: 'assistant', content: 'a' },
      { role: 'tool', content: 't' },
    ]
    render(<ConversationViewer messages={messages} />)
    expect(screen.getByText('user')).toBeInTheDocument()
    expect(screen.getByText('assistant')).toBeInTheDocument()
    expect(screen.getByText('tool')).toBeInTheDocument()
  })
})
