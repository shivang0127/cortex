import Link from "next/link";
import { KnowledgeSearch } from "@/components/KnowledgeSearch";

export default function SearchPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Search Knowledge</h1>
        <p className="mt-1 text-sm text-muted">
          Searches what your sources actually say — every chunk, by meaning and by keyword — and
          shows where each passage came from. Looking for a document by its name? Use{" "}
          <Link href="/library" className="underline hover:text-foreground">
            Find document by title
          </Link>{" "}
          in the Library instead.
        </p>
      </div>
      <KnowledgeSearch />
    </div>
  );
}
