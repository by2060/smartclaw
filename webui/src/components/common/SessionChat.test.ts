import { describe, expect, it } from 'vitest';

import type { Message } from '@/types';

import { getKnowledgeSearchResult, getMessageBubbleClassName, getRegenerateTruncateTarget } from './SessionChat';

function makeMessage(overrides: Partial<Message> & { id: string }): Message {
  return {
    id: overrides.id,
    sessionID: 'sess-1',
    role: 'assistant',
    parts: [],
    timestamp: 0,
    ...overrides,
  } as Message;
}

describe('getMessageBubbleClassName', () => {
  it('keeps non-editing user bubbles auto-sized in full layout', () => {
    const className = getMessageBubbleClassName({
      compact: false,
      isUser: true,
      isEditing: false,
    });

    expect(className).toContain('max-w-2xl w-auto');
  });

  it('expands editing user bubbles to full width in full layout', () => {
    const className = getMessageBubbleClassName({
      compact: false,
      isUser: true,
      isEditing: true,
    });

    expect(className).toContain('max-w-2xl w-full');
    expect(className).not.toContain('w-auto');
  });

  it('keeps assistant bubbles full width regardless of editing state', () => {
    const className = getMessageBubbleClassName({
      compact: false,
      isUser: false,
      isEditing: true,
    });

    expect(className).toContain('max-w-2xl w-full');
  });
});

describe('getRegenerateTruncateTarget', () => {
  it('truncates back to the parent user message for assistant regenerations', () => {
    const target = getRegenerateTruncateTarget([
      makeMessage({ id: 'user-1', role: 'user' }),
      makeMessage({ id: 'assistant-1', role: 'assistant', parentID: 'user-1' }),
      makeMessage({ id: 'assistant-2', role: 'assistant', parentID: 'user-1' }),
    ], 'assistant-2');

    expect(target).toEqual({ messageId: 'user-1' });
  });

  it('falls back to removing the target message when parent linkage is unavailable', () => {
    const target = getRegenerateTruncateTarget([
      makeMessage({ id: 'assistant-1', role: 'assistant' }),
    ], 'assistant-1');

    expect(target).toEqual({ messageId: 'assistant-1', includeTarget: true });
  });
});

describe('getKnowledgeSearchResult', () => {
  it('reads the normalized knowledge search event from tool metadata', () => {
    const event = {
      schema: 'knowledge_search_result.v1',
      event_type: 'knowledge.search.result.v1',
      markdown: '### Sources',
      records: [{ document: { name: 'Guide.docx' } }],
    };

    expect(getKnowledgeSearchResult({
      status: 'completed',
      metadata: { knowledge_search_result: event },
    })).toBe(event);
  });

  it('falls back to markdown fields on Dify output for compatibility', () => {
    const result = getKnowledgeSearchResult({
      status: 'completed',
      metadata: { source: 'Dify' },
      output: {
        records: [{ segment: { position: 1 } }],
        markdown: '### Knowledge search result',
      },
    });

    expect(result?.schema).toBe('knowledge_search_result.v1');
    expect(result?.count).toBe(1);
    expect(result?.markdown).toContain('Knowledge search');
  });
});
