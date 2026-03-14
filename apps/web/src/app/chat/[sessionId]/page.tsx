import { ChatWindow } from "@/components/chat/ChatWindow";

interface Props {
  params: { sessionId: string };
}

export default function ChatSessionPage({ params }: Props) {
  return <ChatWindow sessionId={params.sessionId} />;
}
