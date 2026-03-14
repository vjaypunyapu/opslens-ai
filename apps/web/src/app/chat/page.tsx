import { Sparkles } from "lucide-react";

export default function ChatIndexPage() {
  return (
    <div className="h-full flex items-center justify-center text-center px-4">
      <div className="space-y-2">
        <div className="mx-auto h-10 w-10 rounded-2xl bg-brand-navy flex items-center justify-center">
          <Sparkles className="h-5 w-5 text-brand-teal" />
        </div>
        <p className="text-sm text-gray-400">
          Select a conversation or start a new one
        </p>
      </div>
    </div>
  );
}
