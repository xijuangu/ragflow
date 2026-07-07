import { MessageType, SharedFrom } from '@/constants/chat';
import {
  buildSharedSessionHistoryUrl,
  buildSharedSessionMessages,
} from './use-send-shared-message';

describe('shared chat session recovery helpers', () => {
  it('builds the chatbot history URL from a shared iframe session_id', () => {
    expect(
      buildSharedSessionHistoryUrl(SharedFrom.Chat, 'dialog-1', 'session-1'),
    ).toBe('/api/v1/chatbots/dialog-1/sessions/session-1');
  });

  it('builds the agentbot history URL from a shared iframe session_id', () => {
    expect(
      buildSharedSessionHistoryUrl(SharedFrom.Agent, 'agent-1', 'session-1'),
    ).toBe('/api/v1/agentbots/agent-1/sessions/session-1');
  });

  it('restores history messages and keeps session_id on the first message for later sends', () => {
    const messages = buildSharedSessionMessages(
      {
        messages: [
          {
            id: 'greeting',
            role: MessageType.Assistant,
            content: 'Hello',
          },
          {
            id: 'question',
            role: MessageType.User,
            content: 'Hi',
          },
          {
            id: 'answer',
            role: MessageType.Assistant,
            content: 'Answer',
          },
        ],
      },
      'session-1',
    );

    expect(messages).toHaveLength(3);
    expect(messages[0]).toMatchObject({
      id: 'greeting',
      role: MessageType.Assistant,
      content: 'Hello',
      session_id: 'session-1',
    });
    expect(messages[1]).toMatchObject({
      id: 'question',
      role: MessageType.User,
      content: 'Hi',
    });
    expect(messages[2]).toMatchObject({
      id: 'answer',
      role: MessageType.Assistant,
      content: 'Answer',
    });
  });
});
