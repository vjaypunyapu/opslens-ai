import { redirect } from "next/navigation";

// Root → redirect to /dashboard
export default function Home() {
  redirect("/dashboard");
}
