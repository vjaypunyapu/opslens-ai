import { redirect } from "next/navigation";

// Root → redirect to /chat
export default function Home() {
  redirect("/chat");
}
