import { NextMessageInputOnPressEnterParameter } from '@/components/message-input/next';
import message from '@/components/ui/message';
import { MessageType, SharedFrom } from '@/constants/chat';
import {
  useHandleMessageInputChange,
  useSelectDerivedMessages,
  useSendMessageWithSse,
} from '@/hooks/logic-hooks';
import { useFetchExternalChatInfo } from '@/hooks/use-chat-request';
import { IMessage, IReference, Message } from '@/interfaces/database/chat';
import { buildMessageListWithUuid } from '@/utils/chat';
import request from '@/utils/next-request';
import { get } from 'lodash';
import trim from 'lodash/trim';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { v4 as uuid } from 'uuid';

const isCompletionError = (res: any) =>
  res && (res?.response.status !== 200 || res?.data?.code !== 0);

interface SharedSessionHistory {
  messages?: Message[];
  reference?: IReference[];
}

export const buildSharedSessionHistoryUrl = (
  from: SharedFrom,
  conversationId: string | null,
  sessionId: string,
) => {
  const botType = from === SharedFrom.Agent ? 'agentbots' : 'chatbots';
  return `/api/v1/${botType}/${conversationId}/sessions/${sessionId}`;
};

export const buildSharedSessionMessages = (
  history: SharedSessionHistory | undefined,
  sessionId: string,
): IMessage[] => {
  const messages = buildMessageListWithUuid(history?.messages) as IMessage[];
  if (messages.length === 0) {
    return messages;
  }
  return messages.map((message, index) =>
    index === 0 ? { ...message, session_id: sessionId } : message,
  );
};

export const useSendButtonDisabled = (value: string) => {
  return trim(value) === '';
};

export const useGetSharedChatSearchParams = () => {
  const [searchParams] = useSearchParams();
  const data_prefix = 'data_';
  const data = useMemo(
    () =>
      Object.fromEntries(
        Array.from(searchParams.entries())
          .filter(([key]) => key.startsWith(data_prefix))
          .map(([key, value]) => [key.replace(data_prefix, ''), value]),
      ),
    [searchParams],
  );
  return {
    from: searchParams.get('from') as SharedFrom,
    sharedId: searchParams.get('shared_id'),
    locale: searchParams.get('locale'),
    theme: searchParams.get('theme'),
    sessionId: searchParams.get('session_id'),
    data: data,
    visibleAvatar: searchParams.get('visible_avatar')
      ? searchParams.get('visible_avatar') !== '1'
      : true,
  };
};

export const useSendSharedMessage = () => {
  const {
    from,
    sharedId: conversationId,
    sessionId,
    data: data,
  } = useGetSharedChatSearchParams();
  const { handleInputChange, value, setValue } = useHandleMessageInputChange();
  const completionUrl = `/api/v1/${from === SharedFrom.Agent ? 'agentbots' : 'chatbots'}/${conversationId}/completions`;
  const { data: chatInfo } = useFetchExternalChatInfo();
  const { send, answer, done, stopOutputMessage } = useSendMessageWithSse();
  const {
    derivedMessages,
    removeLatestMessage,
    addNewestAnswer,
    addNewestQuestion,
    scrollRef,
    messageContainerRef,
    removeAllMessages,
    removeAllMessagesExceptFirst,
    setDerivedMessages,
  } = useSelectDerivedMessages();
  const [hasError, setHasError] = useState(false);
  const [conversationReference, setConversationReference] = useState<
    IReference[]
  >([]);

  const sendMessage = useCallback(
    async (
      message: Message,
      id?: string,
      enableThinking?: boolean,
      enableInternet?: boolean,
    ) => {
      // Slice 33:SSE 流式开始/结束通知 parent(portal 拦截会话切换避免消息丢失)
      // start 在 try 内、end 在 finally,保证 error 时也配对发 end,避免永久禁用
      try {
        window.parent?.postMessage({ type: 'ragflow:completions:start' }, '*');
        const res = await send(completionUrl, {
          conversation_id: id ?? conversationId,
          quote: true,
          question: message.content,
          session_id: sessionId ?? get(derivedMessages, '0.session_id'),
          reasoning: enableThinking,
          internet: enableInternet,
          ...(chatInfo?.llm_id ? { model_name: chatInfo.llm_id } : {}),
        });

        if (isCompletionError(res)) {
          // cancel loading
          setValue(message.content);
          removeLatestMessage();
        }
      } finally {
        window.parent?.postMessage({ type: 'ragflow:completions:end' }, '*');
      }
    },
    [
      send,
      completionUrl,
      conversationId,
      derivedMessages,
      setValue,
      removeLatestMessage,
      chatInfo,
      sessionId,
    ],
  );

  const handleSendMessage = useCallback(
    async (
      message: Message,
      enableThinking?: boolean,
      enableInternet?: boolean,
    ) => {
      sendMessage(message, undefined, enableThinking, enableInternet);
    },
    [sendMessage],
  );

  const fetchSessionId = useCallback(async () => {
    const payload = { question: '' };
    // Slice 33:fetchSessionId 也走 SSE,通知 parent 流式开始/结束
    try {
      window.parent?.postMessage({ type: 'ragflow:completions:start' }, '*');
      const ret = await send(completionUrl, { ...payload, ...data });
      if (isCompletionError(ret)) {
        message.error(ret?.data.message ?? 'Unknown error');
        setHasError(true);
      }
    } finally {
      window.parent?.postMessage({ type: 'ragflow:completions:end' }, '*');
    }
  }, [send, completionUrl, data]);

  const fetchSessionHistory = useCallback(async () => {
    if (!sessionId) {
      return;
    }
    const url = buildSharedSessionHistoryUrl(from, conversationId, sessionId);
    try {
      const ret = await request.get(url);
      if (ret?.data?.code !== 0) {
        message.error(ret?.data?.message ?? 'Unknown error');
        setHasError(true);
        return;
      }
      setDerivedMessages(buildSharedSessionMessages(ret.data.data, sessionId));
      setConversationReference(ret.data.data.reference ?? []);
    } catch (error: any) {
      message.error(error?.response?.data?.message ?? 'Unknown error');
      setHasError(true);
    }
  }, [conversationId, from, sessionId, setDerivedMessages]);

  useEffect(() => {
    // 切换 session_id 时先重置会话级 reference,避免 fetchSessionHistory 失败时残留旧会话引用(Issue 36)
    setConversationReference([]);
    if (sessionId) {
      fetchSessionHistory();
    } else {
      fetchSessionId();
    }
  }, [fetchSessionHistory, fetchSessionId, sessionId]);

  useEffect(() => {
    if (answer.answer) {
      addNewestAnswer(answer);
    }
  }, [answer, addNewestAnswer]);

  const handlePressEnter = useCallback(
    ({
      enableThinking,
      enableInternet,
    }: NextMessageInputOnPressEnterParameter) => {
      if (trim(value) === '') return;
      const id = uuid();
      if (done) {
        setValue('');
        addNewestQuestion({
          content: value,
          doc_ids: [],
          id,
          role: MessageType.User,
        });
        handleSendMessage(
          {
            content: value.trim(),
            id,
            role: MessageType.User,
          },
          enableThinking,
          enableInternet,
        );
      }
    },
    [addNewestQuestion, done, handleSendMessage, setValue, value],
  );

  return {
    handlePressEnter,
    handleInputChange,
    value,
    sendLoading: !done,
    loading: false,
    derivedMessages,
    conversationReference,
    hasError,
    stopOutputMessage,
    scrollRef,
    messageContainerRef,
    removeAllMessages,
    removeAllMessagesExceptFirst,
  };
};
