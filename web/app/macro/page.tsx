import { redirect } from "next/navigation";

/** The old home. A bookmark to it lands on the Desk, which replaced it. */
export default function MacroPage() {
  redirect("/");
}
