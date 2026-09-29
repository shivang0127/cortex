import Link from "next/link";
import { AskSecondBrain } from "@/components/AskSecondBrain";

export default function AskPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Ask Second Brain</h1>
        <p className="mt-1 text-sm text-muted">
          Answers are written from passages retrieved out of your own library, and every claim
          links back to the passage it came from. If your sources do not cover the question, it
          says so instead of guessing. To read the passages yourself without a generated answer,
          use{" "}
          <Link href="/search" className="underline hover:text-foreground">
            Search Knowledge
          </Link>
          .
        </p>
      </div>
      <AskSecondBrain />
    </div>
  );
}
